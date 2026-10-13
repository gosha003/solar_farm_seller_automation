"""Поиск брокеров и агентств, которые продают готовые ФЕЦ 1-2 МВт и участки под ФЕЦ (не по объявлению).
Claude только находит сайты; контакты берём со страниц сами (Fetcher: robots.txt, пауза), выдуманные ссылки
и адреса отсекаются. Раз в неделю: python -m solar_outreach.search --brokers
"""
import re
from urllib.parse import urljoin, urlparse

import anthropic
from bs4 import BeautifulSoup

from solar_outreach import parse
from solar_outreach.config import load_config
from solar_outreach.sources import Listing, web

PROMPT = """Find brokers, real-estate or business-sale agencies and energy-asset consultants in Bulgaria that \
mediate the sale of (a) operating photovoltaic power plants (ФЕЦ / фотоволтаична централа / соларен парк) of about \
1-2 MW, or (b) land plots suitable for building a solar plant, with a building permit and grid connection capacity. \
Search in Bulgarian and English, e.g. "брокер фотоволтаични централи продажба", "продажба на ФЕЦ посредник", \
"парцели за фотоволтаичен парк с разрешение за строеж", "solar park Bulgaria broker M&A", "агенция за енергийни проекти". \
Run several searches. Then list each company once (its contact page or homepage; not listing pages, classifieds portals \
such as bazar.bg, alo.bg, olx.bg, imot.bg, news, directories, installers or equipment sellers). Include a company when its site plausibly brokers energy assets or solar land, even if you cannot confirm everything from the snippet: its contacts are checked separately afterwards. Do not stop after one search. One per line:
<url> | <company name>
If none, answer NONE."""
SKIP_HOSTS = ("bazar.bg", "alo.bg", "olx.bg", "imot.bg", "kpd.bg", "facebook.com", "linkedin.com", "youtube.com",
              "instagram.com", "wikipedia.org")
# версия 20260209 фильтрует результаты кодом и на этом запросе отказывалась перечислять компании (NONE)
TOOL = "web_search_20250305"
CONTACT = re.compile(r"контакт|contact|за нас|about", re.I)


def links(fetch) -> dict[str, str]:
    found = web.search_results(anthropic.Anthropic(), load_config(), PROMPT, TOOL)
    return {u: t for u, t in found.items() if not urlparse(u).netloc.endswith(SKIP_HOSTS)}


def contacts(html: str) -> tuple[list[str], list[str], str]:
    """(emails, phones, ссылка на страницу контактов) из видимого текста и mailto:/tel: ссылок."""
    soup = BeautifulSoup(html, "html.parser")
    hrefs = [a["href"] for a in soup.find_all("a", href=True)]
    text = soup.get_text(" ") + " " + " ".join(h.removeprefix("mailto:") for h in hrefs if h.startswith("mailto:"))
    phones = parse.phones(text + " " + " ".join(h.removeprefix("tel:") for h in hrefs if h.startswith("tel:")))
    page = next((h for h, a in ((a["href"], a.get_text(" ", strip=True)) for a in soup.find_all("a", href=True))
                 if CONTACT.search(a) or CONTACT.search(h)), "")
    return parse.emails(text), phones, page


def parse_ad(url: str, html: str, name: str, fetch) -> Listing:
    """Контакты с указанной страницы; если email нет, одна попытка на странице контактов того же сайта."""
    emails, phones, page = contacts(html)
    host = urlparse(url).netloc
    if not emails and page and urlparse(urljoin(url, page)).netloc == host and (more := fetch(urljoin(url, page))):
        e2, p2, _ = contacts(more)
        emails, phones = emails or e2, sorted(set(phones) | set(p2))
    return Listing(f"broker: {host}", url, name, seller=name, extra={"emails": emails, "phones": phones})
