"""alo.bg: только первая страница поиска (пагинацию запрещает robots.txt), поэтому много запросов.
Телефон скрыт за AJAX (?ajax= запрещён robots.txt): контакт берём из текста или канал «сайт»."""
import re
from urllib.parse import quote

from bs4 import BeautifulSoup

from solar_outreach.sources import Listing

# alo.bg отвечает 429 уже после ~30 запросов подряд: держим запросы к поиску короткими.
QUERIES = ["фотоволтаична централа", "фец продажба", "фотоволтаичен парк", "фотоволтаична централа 1 mw",
           "фотоволтаична централа работеща", "соларен парк"]


def ad_links(html: str) -> dict[str, str]:
    soup = BeautifulSoup(html, "html.parser")
    out = {}
    for a in soup.find_all("a", href=re.compile(r"^/[\w-]+-\d{6,}(#|$)")):
        title = a.get_text(" ", strip=True)
        if title:
            out.setdefault("https://www.alo.bg" + a["href"].split("#")[0], title)
    return out


def parse_ad(url: str, html: str) -> Listing:
    soup = BeautifulSoup(html, "html.parser")
    h1 = soup.find("h1")
    params = {}
    for row in soup.select(".ads-params-row"):
        cells = [c.get_text(" ", strip=True) for c in row.select(".ads-params-cell, .ads-param-title")]
        if len(cells) >= 2:
            params[cells[0]] = cells[1]
    single = soup.select_one(".ads-params-single")
    for t in soup(["script", "style", "noscript"]):
        t.decompose()
    text = re.sub(r"\s+", " ", soup.get_text(" "))
    m = re.search(r"Описание (.*?)(Напиши първи коментар|Коментари|Подобни обяви)", text)
    seller = re.search(r"Контакт с подателя на обявата (.*?) \S+\.alo\.bg", text)
    return Listing("alo.bg", url, h1.get_text(" ", strip=True) if h1 else "",
                   (m.group(1) if m else "") + (" " + single.get_text(" ", strip=True) if single else ""),
                   params.get("Цена", ""), params.get("Местоположение", ""), seller.group(1) if seller else "")


def links(fetch) -> dict[str, str]:
    seen = {}
    for q in QUERIES:
        if html := fetch(f"https://www.alo.bg/searchq/?q={quote(q)}"):
            for u, t in ad_links(html).items():
                seen.setdefault(u, t)
    return seen
