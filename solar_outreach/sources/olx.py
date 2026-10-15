"""olx.bg: поиск /ads/q-<запрос>/?page=N, в карточке заголовок, цена, «Город - дата»; страница объявления: HTML.
Категории под ФЕЦ нет, выдача шумная (оборудование, мелкие крыши по 30 кВт), поэтому карточки с мощностью в заголовке
вне критериев отбрасываем сразу. Телефон за кнопкой «Покажи» (AJAX /ad/contact/ закрыт в robots.txt) не берём:
только то, что продавец написал в тексте, иначе канал «сайт (ручная)» и владелец пишет через чат OLX.
С VM (адрес облака) сайт отдаёт 403 и это не обходится, поэтому источник идёт с ПК владельца: search --only olx.bg.
robots.txt проверено 2026-10-15: поиск и /d/ad/ разрешены (закрыты /api/, */ajax/, /ad/contact/, */i2/*)."""
import re
from urllib.parse import quote

from bs4 import BeautifulSoup

from solar_outreach import parse
from solar_outreach.config import load_config
from solar_outreach.sources import Listing

QUERIES = ["фотоволтаична централа", "фотоволтаичен парк", "соларен парк", "ФЕЦ продава", "фотоволтаична MW"]
PAGES = 3
LOCATIONS: dict[str, str] = {}  # {ссылка: «Город»} из карточки: на странице объявления место рисуется картой


def card_links(html: str, crit: dict | None = None) -> tuple[int, dict[str, str]]:
    """(число карточек, {ссылка: заголовок}); с crit отбрасывает карточки, где мощность из заголовка вне диапазона."""
    soup = BeautifulSoup(html, "html.parser")
    cards, out = soup.select('div[data-cy="l-card"]'), {}
    for c in cards:
        a, h = c.find("a", href=re.compile(r"^/d/ad/")), c.select_one("h4, h6")
        if not (a and h):
            continue
        title = h.get_text(" ", strip=True)
        kw = parse.power_kw(title, "")
        if crit and kw is not None and not crit["min_kwp"] <= kw <= crit["max_kwp"]:
            continue
        url = "https://www.olx.bg" + a["href"].split("?")[0]
        out.setdefault(url, title)
        where = c.select_one('[data-testid="location-date"]')
        if where:
            LOCATIONS[url] = re.sub(r"^(гр\.|с\.)\s*", "", where.get_text(" ", strip=True).split(" - ")[0])
    return len(cards), out


def parse_ad(url: str, html: str) -> Listing:
    soup = BeautifulSoup(html, "html.parser")

    def text(testid: str) -> str:
        e = soup.find(attrs={"data-testid": testid})
        return e.get_text(" ", strip=True) if e else ""

    params = text("ad-parameters-container")
    area = re.search(r"Квадратура:\s*([\d .,]+кв\.м)", params)
    body = " ".join(filter(None, [text("ad_description"), f"Площ: {area.group(1)}" if area else ""]))
    return Listing("olx.bg", url, text("offer_title") or (soup.title.get_text(strip=True) if soup.title else ""), body,
                   text("ad-price-container"), LOCATIONS.get(url, ""), text("user-profile-user-name"))


def links(fetch) -> dict[str, str]:
    crit, seen = load_config()["criteria"], {}
    for q in QUERIES:
        for page in range(1, PAGES + 1):
            html = fetch(f"https://www.olx.bg/ads/q-{quote(q.replace(' ', '-'))}/" + (f"?page={page}" if page > 1 else ""))
            n, found = card_links(html, crit) if html else (0, {})
            seen.update(found)
            if not n:
                break
    return seen
