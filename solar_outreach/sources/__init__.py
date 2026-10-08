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
import time
from dataclasses import dataclass, field
from urllib.parse import unquote, urlparse

import requests

UNAVAILABLE = {
    "olx.bg": "403 на любой запрос, включая robots.txt (CloudFront блок)",
    "bulgarianproperties.bg": "robots.txt запрещает страницы поиска (/*search)",
    "businessesforsale.com": "robots.txt запрещает /search?",
    "sales.bcpea.org": "нет поиска по ключевым словам, ФЕЦ не выделены в типах имуществ",
}

# Объявление о продаже станции: ключевое слово ФЕЦ в заголовке, без услуг/оборудования/покупки.
PV = re.compile(r"фотовол|фотоволт|фец|фвец|соларн\w* (парк|централ)|pv парк|\d\s*(mw|mwp|мвт|мегават)", re.I)
# Услуги/оборудование/покупка, а также не действующие станции: парцел «за ФЕЦ», проект, разрешение.
NOT_SALE = re.compile(r"изгражд|проектир|монтаж|инвертор|панел|търс|\bкупува|ремарке|^батери|бойлер|компенсир"
                      r"|под наем|сглобен|оборудван|подд?ръжк|профилакт|почиств|кабел|конструкц|система за"
                      r"|за фотовол|за фец|подходящ|проект|уведомление|разрешени"
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


class Fetcher:
    """Вежливый GET: нормальный UA, пауза между запросами, robots.txt каждого хоста."""

    def __init__(self, user_agent: str, delay: tuple[float, float]):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = user_agent
        self.delay = delay
        self.last = 0.0
        self.robots: dict[str, list] = {}

    def allowed(self, url: str) -> bool:
        p = urlparse(url)
        if p.netloc not in self.robots:
            try:
                r = self.session.get(f"{p.scheme}://{p.netloc}/robots.txt", timeout=15)
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
        for attempt in range(2):
            time.sleep(max(0.0, self.last + random.uniform(*self.delay) - time.monotonic()))
            try:
                r = self.session.get(url, timeout=25)
            finally:
                self.last = time.monotonic()
            if r.status_code == 429 and attempt == 0:
                retry = r.headers.get("Retry-After", "")
                time.sleep(min(int(retry), 300) if retry.isdigit() else 90)
                continue
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.text
