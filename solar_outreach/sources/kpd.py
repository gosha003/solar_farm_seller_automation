"""kpd.bg (продажа бизнеса): первые страницы категорий; поиск по словам там через PRADO-postback, не трогаем.
robots.txt нет (сайт отдаёт HTML) = ограничений нет. Email продавца в HTML защищён Cloudflare (data-cfemail)."""
import re

from bs4 import BeautifulSoup

from solar_outreach.sources import Listing

CATEGORIES = [26, 9, 13]  # Бизнес Имоти, Индустрия, Други


def cf_decode(hexstr: str) -> str:
    key = int(hexstr[:2], 16)
    return "".join(chr(int(hexstr[i:i + 2], 16) ^ key) for i in range(2, len(hexstr), 2))


def ad_links(html: str) -> dict[str, str]:
    soup = BeautifulSoup(html, "html.parser")
    return {"https://www.kpd.bg" + a["href"]: a.get_text(" ", strip=True)
            for a in soup.find_all("a", href=re.compile(r"^/bg/offers/offer-\d+\.html$")) if a.get_text(strip=True)}


def parse_ad(url: str, html: str) -> Listing:
    soup = BeautifulSoup(html, "html.parser")
    emails = [cf_decode(e["data-cfemail"]) for e in soup.select("[data-cfemail]")]
    h1 = soup.find("h1")
    for t in soup(["script", "style", "noscript"]):
        t.decompose()
    text = re.sub(r"\s+", " ", soup.get_text(" "))
    title = h1.get_text(" ", strip=True) if h1 else ""
    body = re.search(r"Цена (.*?)(Подобни бизнеси|Действия с обявата)", text)
    price = re.search(r"Цена ([\d,. ]+\s*(?:EUR|BGN|лв))", text)
    return Listing("kpd.bg", url, title, (body.group(1) if body else "") + " " + " ".join(emails),
                   price.group(1) if price else "")


def links(fetch) -> dict[str, str]:
    seen = {}
    for bid in CATEGORIES:
        if html := fetch(f"https://www.kpd.bg/bg/offers/search.html?bid={bid}"):
            seen.update(ad_links(html))
    return seen
