"""Площадки объявлений. Каждый модуль: links(fetch) -> {url: заголовок} и parse_ad(url, html) -> Listing.
Обход страниц объявлений и отбор по заголовку общий, в search.py.

robots.txt проверен вручную 2026-10-08 (Python-парсер не понимает `*` в правилах):
  bazar.bg  /obiavi?q=... и /obiava-* разрешены
  alo.bg    /searchq/?q=... разрешён, пагинация (/searchq/*page=) запрещена, AJAX показа телефона (?ajax=) запрещён
Недоступны (причина в UNAVAILABLE) и не обходятся.
"""
import random
import re
import time
from dataclasses import dataclass, field

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


class Fetcher:
    """Вежливый GET: нормальный UA, пауза 3-5 с между запросами к сайтам."""

    def __init__(self, user_agent: str, delay: tuple[float, float]):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = user_agent
        self.delay = delay
        self.last = 0.0

    def __call__(self, url: str) -> str | None:
        """None = страницы нет (404). 429: одна пауза по Retry-After (или 90 с) и повтор, дальше ошибка."""
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
