"""Площадки объявлений. Каждый модуль: links(fetch) -> {url: заголовок} и parse_ad(url, html) -> Listing.
Обход страниц объявлений и отбор по заголовку общий, в search.py.

robots.txt соблюдает Fetcher (свой разбор: стандартный urllib.robotparser не понимает `*` и `$`).
Проверено вручную 2026-10-08:
  bazar.bg  /obiavi?q=... и /obiava-* разрешены
  alo.bg    /searchq/?q=... разрешён, пагинация (/searchq/*page=) запрещена, AJAX показа телефона (?ajax=) запрещён
Недоступны (причина в UNAVAILABLE) и не обходятся.
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

# Объявление о продаже станции: ключевое слово ФЕЦ в заголовке, без услуг/оборудования/покупки.
PV = re.compile(r"фотовол|фотоволт|фец|фвец|соларе?н\w* (парк|централ)|pv парк|\d\s*(mw|mwp|мвт|мегават)", re.I)
# Услуги/оборудование/покупка, а также не действующие станции: парцел «за ФЕЦ», проект, разрешение.
NOT_SALE = re.compile(r"изгражд|проектир|монтаж|инвертор|панел|търс|\bкупува|ремарке|^батери|бойлер|компенсир"
                      r"|под наем|сглобен|оборудван|подд?ръжк|профилакт|почиств|кабел|конструкц|система за"
                      r"|за фотовол|за фец|подходящ|проект|уведомление|разрешени|разрешител|за строеж"
                      r"|бензиностанц|газстанц|автомивк|автосервиз"
                      r"|електротехник|работа|служител|оператор|техник|косене|трева|земеделска земя|декара", re.I)


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
    return bool(PV.search(title)) and not NOT_SALE.search(title)


def robots_rules(text: str) -> list[tuple[bool, str]]:
    """Правила группы `User-agent: *` как (allow, pattern)."""
    rules, in_star, ua_block = [], False, False
    for line in text.splitlines():
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
    """RFC 9309: самое длинное совпавшее правило, при равенстве allow. `*` = что угодно, `$` = конец."""
    best = (-1, True)
    for allow, pattern in rules:
        rx = re.escape(pattern).replace(r"\*", ".*").replace(r"\$", "$")
        if re.match(rx, path) and (len(pattern), allow) > best:
            best = (len(pattern), allow)
    return best[1]


# OLX (CloudFront) режет TLS-отпечаток Python/OpenSSL: 403 на любой запрос, даже с браузерным UA, а системный curl
# (Schannel) с тем же честным UA получает 200. Для этих хостов GET идёт через curl; pace, robots.txt и UA те же.
CURL_HOSTS = {"www.olx.bg"}


class CurlResponse:
    """Минимум от requests.Response, который читает Fetcher."""

    def __init__(self, status: int, text: str):
        self.status_code, self.text = status, text
        self.ok = status < 400
        self.headers = {"content-type": "text/html; charset=utf-8"}  # OLX отдаёт utf-8; без этого Fetcher гадал бы кодировку

    def raise_for_status(self) -> None:
        if not self.ok:
            raise requests.HTTPError(f"{self.status_code} Error", response=self)


class Fetcher:
    """Вежливый GET: нормальный UA, пауза между запросами, robots.txt каждого хоста."""

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
        self.host_delay = host_delay or {}  # сайты с жёстким лимитом (alo.bg): минимальная пауза, с
        self.last = 0.0
        self.robots: dict[str, list] = {}

    def allowed(self, url: str) -> bool:
        p = urlparse(url)
        if p.netloc not in self.robots:
            try:
                r = self.get(f"{p.scheme}://{p.netloc}/robots.txt")
                # 4xx = ограничений нет; 5xx/сеть = недоступен, ничего не берём (RFC 9309)
                self.robots[p.netloc] = robots_rules(r.text) if r.ok else [] if r.status_code < 500 else [(False, "/")]
            except requests.RequestException:
                self.robots[p.netloc] = [(False, "/")]
        path = (p.path or "/") + (f"?{p.query}" if p.query else "")
        # правило может быть записано и кириллицей («/обяви/»), и %-кодом: разрешено, только если обе формы разрешены
        return robots_allows(self.robots[p.netloc], unquote(path)) and robots_allows(self.robots[p.netloc], path)

    def __call__(self, url: str) -> str | None:
        """None = страницы нет (404) или её запрещает robots.txt.
        429: одна пауза по Retry-After (или 90 с) и повтор, дальше ошибка."""
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
                # нет кодировки или пустой «charset=» (bulgarianproperties.bg): requests взял бы latin-1, а там windows-1251
                r.encoding = r.apparent_encoding
            return r.text
