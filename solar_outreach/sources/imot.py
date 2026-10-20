"""imot.bg: the «Бизнес имоти» section, where brokers have a separate listing type «СОЛАРЕН ПАРК».
There is no category for it, so we page through the whole section and take cards of that type (a card already has
price, area, the start of the description and a phone). The section is large (~200 pages of 20), so it runs once a day.
The site returns 403 to the VM (a cloud IP) and that isn't worked around, so this source runs from the owner's PC:
search --only imot.bg. robots.txt: Disallow is empty. Encoding windows-1251: Fetcher detects it."""
import re

from bs4 import BeautifulSoup

from solar_outreach import parse
from solar_outreach.sources import PERMIT, Listing

BASE = "https://www.imot.bg/obiavi/prodazhbi/biznes-imot"
MAX_PAGES = 250
TYPE = "СОЛАРЕН ПАРК"
# a running plant, present tense only: project cards say «въвеждане в експлоатация 2027», «ще произвежда», «в работа»
RUNNING = re.compile(r"работещ|действащ|(?<!бъде )въведен|акт 16|за ползване|(?:в експлоатация|в работа) от", re.I)


def card_links(html: str) -> tuple[int, dict[str, str]]:
    """(number of cards on the page, {link: short title} only for the «СОЛАРЕН ПАРК» type).
    The card text is a description snippet («панели», «инвертори», «в работа»), not a title, so it never meets the title
    filter: the title is the power from the card plus «проект» when the card names a project without a building permit."""
    soup = BeautifulSoup(html, "html.parser")
    items, out = soup.select("div.item"), {}
    for it in items:
        text = it.get_text(" ", strip=True)
        a = it.find("a", href=re.compile(r"obiava-"))
        if a and TYPE in text.upper():
            href = a["href"].split("?")[0]
            kw = parse.power_kw(text, "")
            project = (" проект" if re.search(r"проект(?!ант)", text, re.I) and not PERMIT.search(text)
                       and not RUNNING.search(text) else "")  # «проектна мощност» of a running plant is no project
            out[("https:" + href) if href.startswith("//") else href] = f"Соларен парк{f' {kw:.0f} kW' if kw else ''}{project}"
    return len(items), out


def parse_ad(url: str, html: str) -> Listing:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
    cut = text.find("Виж карта")  # the page title repeats twice; the place is taken from the last one
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
