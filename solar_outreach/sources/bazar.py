"""bazar.bg: search /obiavi?q=..., the listing page carries JSON-LD (name, description, areaServed).
The phone behind the «покажи» button is not taken: only what the seller wrote in the text."""
import re
from urllib.parse import quote

from bs4 import BeautifulSoup

from solar_outreach.sources import Listing

QUERIES = ["фотоволтаична централа", "фотоволтаичен парк", "ФЕЦ продава", "соларен парк", "фотоволтаична MW",
           "фец разрешение за строеж"]
PAGES = 3


def ad_links(html: str) -> dict[str, str]:
    soup = BeautifulSoup(html, "html.parser")
    out = {}
    for a in soup.find_all("a", href=re.compile(r"^https://bazar\.bg/obiava-\d+/")):
        title = a.get("title") or a.get_text(" ", strip=True)
        if title:
            out.setdefault(a["href"].split("?")[0], title)
    return out


def parse_ad(url: str, html: str) -> Listing:
    """HTML, not JSON-LD: bazar's JSON-LD comes in different shapes (Service / @graph); the page markup is more stable."""
    soup = BeautifulSoup(html, "html.parser")
    h1 = soup.select_one("h1.classifiedTitle") or soup.find("h1")
    for s in h1.find_all("span", string=re.compile(r"Обява\s*\d+")) if h1 else []:
        s.decompose()  # «→ Обява 54043039»; the title itself may sit in a span too (itemprop="name")
    desc = soup.find(itemprop="description")
    price = soup.select_one(".price-value") or soup.select_one(".price")
    loc = soup.select_one(".location")
    return Listing("bazar.bg", url, h1.get_text(" ", strip=True).rstrip(" →") if h1 else "",
                   desc.get_text(" ", strip=True) if desc else "",
                   price.get_text(" ", strip=True) if price else "", loc.get_text(" ", strip=True) if loc else "")


def links(fetch) -> dict[str, str]:
    seen = {}
    for q in QUERIES:
        for page in range(1, PAGES + 1):
            html = fetch(f"https://bazar.bg/obiavi?q={quote(q)}&page={page}")
            new = {u: t for u, t in ad_links(html).items() if u not in seen} if html else {}
            seen.update(new)
            if not new:
                break
    return seen
