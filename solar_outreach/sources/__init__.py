"""Listing sites. Each module: links(fetch) -> {url: title} and parse_ad(url, html) -> Listing.
Crawling listing pages and filtering by title are shared, in search.py.

Fetcher enforces robots.txt (own parser: the stdlib urllib.robotparser doesn't understand `*` and `$`).
Checked by hand 2026-10-08:
  bazar.bg  /obiavi?q=... and /obiava-* allowed
  alo.bg    /searchq/?q=... allowed, pagination (/searchq/*page=) disallowed, the phone-reveal AJAX (?ajax=) disallowed
Unavailable sites (reason in UNAVAILABLE) are not crawled.
"""
import random
import re
import subprocess
import time
from dataclasses import dataclass, field
from urllib.parse import unquote, urlparse

import requests

UNAVAILABLE = {
    "bulgarianproperties.bg": "robots.txt запрещает страницы поиска (/*search)",
    "businessesforsale.com": "robots.txt запрещает /search?",
    "sales.bcpea.org": "нет поиска по ключевым словам, ФЕЦ не выделены в типах имуществ",
}

# A plant-for-sale listing: the keyword ФЕЦ in the title, no services/equipment/buying.
PV = re.compile(r"фотовол|фотоволт|фец|фвец|соларе?н\w* (парк|централ)|pv парк|\d\s*(mw|mwp|мвт|мегават)", re.I)
# Services/equipment/buying: never a sale.
SERVICES = re.compile(r"изгражд|проектир|монтаж|инвертор|панел|търс|\bкупува|ремарке|^батери|бойлер|компенсир"
                      r"|под наем|сглобен|оборудван|подд?ръжк|профилакт|почиств|кабел|конструкц|система за"
                      r"|бензиностанц|газстанц|автомивк|автосервиз"
                      r"|електротехник|работа|служител|оператор|техник|косене|трева|генератор|преносим", re.I)
LAND = re.compile(r"земеделска земя|декара", re.I)  # a plot of land: kept only when the title names a building permit
# Not an operating plant: a plot «за ФЕЦ», a project. Kept when the title names a building permit (owner 2026-10-19).
# The use permit («разрешение/разрешително за ползване») and «подходяща (за) инвестиция» describe an operating plant.
NOT_OPERATING = re.compile(r"за фотовол|за фец|подходящ(?!\w*\s+(?:за\s+)?инвест)|проект|уведомление"
                           r"|разрешени(?!\w* за ползване)|разрешител(?!\w* за ползване)|за строеж", re.I)
PERMIT = re.compile(r"(разрешени\w*|разрешително) (за|на) строеж|ready[- ]to[- ]build|\bRTB\b", re.I)
# Evidence that the plant runs: it beats a building permit mentioned in passing («документи: ..., разрешение за строеж»).
OPERATING = re.compile(r"работещ|действащ|експлоатац|въведен|акт 16|разрешени\w* за ползване|пуснат|(?<!ще )произвежда"
                       r"|годишно производство|произведен|в работа|\bработи\b", re.I)


@dataclass
class Listing:
    source: str
    url: str
    title: str
    text: str = ""
    price_text: str = ""
    location: str = ""
    seller: str = ""
    extra: dict = field(default_factory=dict)


def is_station_sale(title: str) -> bool:
    permit = bool(PERMIT.search(title))
    return (bool(PV.search(title)) and not SERVICES.search(title) and (permit or not LAND.search(title))
            and (not NOT_OPERATING.search(title) or permit))


def robots_rules(text: str) -> list[tuple[bool, str]]:
    """Rules of the `User-agent: *` group as (allow, pattern)."""
    rules, in_star, ua_block = [], False, False
    for line in text.lstrip("\ufeff").removeprefix("\xef\xbb\xbf").splitlines():  # a BOM, decoded as utf-8 or latin-1
        key, _, value = line.split("#")[0].partition(":")
        key, value = key.strip().lower(), value.strip()
        if key == "user-agent":
            in_star = (in_star and ua_block) or value == "*"
            ua_block = True
        elif key in ("allow", "disallow"):
            ua_block = False
            if in_star and value:
                rules.append((key == "allow", value))
    return rules


def robots_allows(rules: list[tuple[bool, str]], path: str) -> bool:
    """RFC 9309: the longest matching rule wins, allow on a tie. `*` = anything, `$` = end."""
    best = (-1, True)
    for allow, pattern in rules:
        rx = re.escape(pattern).replace(r"\*", ".*").replace(r"\$", "$")
        if re.match(rx, path) and (len(pattern), allow) > best:
            best = (len(pattern), allow)
    return best[1]


# OLX (CloudFront) rejects the Python/OpenSSL TLS fingerprint: 403 on every request, even with a browser UA, while system curl
# (Schannel) with the same honest UA gets 200. GETs to these hosts go through curl; pacing, robots.txt and UA are the same.
CURL_HOSTS = {"www.olx.bg"}


class CurlResponse:
    """The minimum of requests.Response that Fetcher reads."""

    def __init__(self, status: int, text: str):
        self.status_code, self.text = status, text
        self.ok = status < 400
        self.headers = {"content-type": "text/html; charset=utf-8"}  # OLX serves utf-8; without this Fetcher would guess the encoding

    def raise_for_status(self) -> None:
        if not self.ok:
            raise requests.HTTPError(f"{self.status_code} Error", response=self)


class Fetcher:
    """Polite GET: a normal UA, pauses between requests, robots.txt of every host."""

    def get(self, url: str):
        if urlparse(url).netloc not in CURL_HOSTS:
            return self.session.get(url, timeout=25)
        try:
            p = subprocess.run(["curl", "-sL", "--max-time", "25", "-A", self.session.headers["User-Agent"],
                                "-w", "\n%{http_code}", url], capture_output=True, timeout=40)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise requests.ConnectionError(f"curl: {e}") from e
        body, _, code = p.stdout.decode("utf-8", "replace").rpartition("\n")
        if p.returncode or not code.isdigit() or code == "000":
            raise requests.ConnectionError(f"curl exit {p.returncode}: {p.stderr.decode('utf-8', 'replace')[:100]}")
        return CurlResponse(int(code), body)

    def __init__(self, user_agent: str, delay: tuple[float, float], host_delay: dict[str, float] | None = None):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = user_agent
        self.delay = delay
        self.host_delay = host_delay or {}  # sites with strict rate limits (alo.bg): minimum pause, s
        self.last = 0.0
        self.robots: dict[str, list] = {}

    def allowed(self, url: str) -> bool:
        p = urlparse(url)
        if p.netloc not in self.robots:
            try:
                r = self.get(f"{p.scheme}://{p.netloc}/robots.txt")
                # 4xx = no restrictions; 5xx/network = unavailable, take nothing (RFC 9309)
                self.robots[p.netloc] = robots_rules(r.text) if r.ok else [] if r.status_code < 500 else [(False, "/")]
            except requests.RequestException:
                self.robots[p.netloc] = [(False, "/")]
        path = (p.path or "/") + (f"?{p.query}" if p.query else "")
        # a rule may be written in Cyrillic («/обяви/») or %-encoded: allowed only if both forms are allowed
        return robots_allows(self.robots[p.netloc], unquote(path)) and robots_allows(self.robots[p.netloc], path)

    def __call__(self, url: str) -> str | None:
        """None = no page (404) or robots.txt disallows it.
        429: one pause per Retry-After (or 90 s) and a retry, then an error."""
        if not self.allowed(url):
            return None
        pause = max(random.uniform(*self.delay), self.host_delay.get(urlparse(url).netloc, 0))
        for attempt in range(2):
            time.sleep(max(0.0, self.last + pause - time.monotonic()))
            try:
                r = self.get(url)
            finally:
                self.last = time.monotonic()
            if r.status_code == 429 and attempt == 0:
                retry = r.headers.get("Retry-After", "")
                time.sleep(min(int(retry), 300) if retry.isdigit() else 90)
                continue
            if r.status_code == 404:
                return None
            r.raise_for_status()
            if not re.search(r"charset=[\w-]", r.headers.get("content-type", ""), re.I):
                # no charset or an empty «charset=» (bulgarianproperties.bg): requests would assume latin-1, but the page is windows-1251
                r.encoding = r.apparent_encoding
            return r.text
