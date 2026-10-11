"""imot.bg: раздел «Бизнес имоти», у брокеров там отдельный тип объявления «СОЛАРЕН ПАРК».
Категории под него нет, поэтому листаем весь раздел и берём карточки с этим типом (в карточке уже цена, площадь,
начало описания и телефон). Раздел большой (~200 страниц по 20), поэтому запуск раз в сутки.
С VM (адрес облака) сайт отдаёт 403 и это не обходится, поэтому источник идёт с ПК владельца: search --only imot.bg.
robots.txt: Disallow пуст. Кодировка windows-1251: Fetcher определяет её сам."""
import re

from bs4 import BeautifulSoup

from solar_outreach.sources import Listing

BASE = "https://www.imot.bg/obiavi/prodazhbi/biznes-imot"
MAX_PAGES = 250
TYPE = "СОЛАРЕН ПАРК"


def card_links(html: str) -> tuple[int, dict[str, str]]:
    """(число карточек на странице, {ссылка: текст карточки} только для типа «СОЛАРЕН ПАРК»)."""
    soup = BeautifulSoup(html, "html.parser")
    items, out = soup.select("div.item"), {}
    for it in items:
        text = it.get_text(" ", strip=True)
        a = it.find("a", href=re.compile(r"obiava-"))
        if a and TYPE in text.upper():
            href = a["href"].split("?")[0]
            out[("https:" + href) if href.startswith("//") else href] = text
    return len(items), out


def parse_ad(url: str, html: str) -> Listing:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
    cut = text.find("Виж карта")  # заголовок страницы повторяется дважды, место берём из последнего
    place = text[:cut].upper().rsplit(TYPE, 1)[-1] if cut > 0 else ""
    location = re.sub(r"^(град|гр\.|с\.|село)\s+", "", text[:cut][len(text[:cut]) - len(place):].strip(" ,")) if place else ""
    price = re.search(r"Виж карта\s*([\d ]+€)", text)
    desc = re.search(r"Описание на имота:(.*?)(?:Особености|За контакт|ИЗПРАТИ ЗАПИТВАНЕ|$)", text)
    contact = re.search(r"За контакт:\s*([+\d ]{9,})", text)
    seller = re.search(r"Агенция:\s*(.+?)\s+(?:\d{9,}|В imot\.bg)", text)
    body = " ".join(filter(None, [desc.group(1).strip() if desc else "", f"Тел.: {contact.group(1).strip()}" if contact else ""]))
    return Listing("imot.bg", url, f"Соларен парк, {location}" if location else "Соларен парк", body,
                   price.group(1) if price else "", location, seller.group(1).strip() if seller else "")


def links(fetch) -> dict[str, str]:
    seen: dict[str, str] = {}
    for page in range(1, MAX_PAGES + 1):
        html = fetch(BASE if page == 1 else f"{BASE}/p-{page}")
        n, found = card_links(html) if html else (0, {})
        if not n:
            break
        seen.update(found)
    return seen
