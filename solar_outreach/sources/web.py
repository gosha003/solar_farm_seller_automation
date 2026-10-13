"""Веб-поиск через Claude (server tool web_search): находит объявления о ФЕЦ 1-2 МВт на любых сайтах.
Claude только ищет; страницы открывает наш Fetcher (robots.txt, пауза). Раз в сутки: python -m solar_outreach.search --web
"""
import re
from urllib.parse import urlparse

import anthropic
from bs4 import BeautifulSoup

from solar_outreach.config import load_config
from solar_outreach.sources import Listing, alo, bazar, kpd

PROMPT = """Find current listings (classified ads, real-estate or business-for-sale sites, broker pages) offering an \
OPERATING photovoltaic power plant (ФЕЦ / фотоволтаична централа / соларен парк) for sale in Bulgaria with capacity \
roughly 0.9-2.2 MW (900-2200 kWp). Search in Bulgarian and English, e.g. "продава работеща фотоволтаична централа 1 MW", \
"ФЕЦ 1,5 MW продажба", "фотоволтаичен парк 2 MW продава", "operating solar park Bulgaria for sale 1 MW". \
Run several searches. Then list every individual listing page from the results (one ad per URL; not search-result \
pages, news, encyclopedias or company homepages) that may offer a built plant for sale. Include it even when the \
capacity or status cannot be confirmed from the snippet: each page is checked separately afterwards. One per line:
<url> | <capacity if stated> <price if stated> <place>
Skip only what is clearly a project without a built plant, a land plot, a service or equipment. If none, answer NONE."""
OWN = {"bazar.bg": bazar, "www.alo.bg": alo, "alo.bg": alo, "www.kpd.bg": kpd}
SKIP_HOSTS = ("olx.bg", "facebook.com", "youtube.com", "linkedin.com", "instagram.com", "tiktok.com")


def search_results(client, cfg, prompt: str = PROMPT, tool: str = "web_search_20260209") -> dict[str, str]:
    """{url: описание модели} для страниц, которые модель отобрала. URL принимаем только если он был
    в реальных результатах web_search (защита от выдуманных ссылок). pause_turn: продолжаем ход (до 3 раз)."""
    messages = [{"role": "user", "content": prompt}]
    found, answer = {}, ""
    for _ in range(3):
        r = client.beta.messages.create(
            model=cfg["llm"]["model_strong"], max_tokens=4000,
            betas=["server-side-fallback-2026-07-01"], fallbacks="default",  # отказ модели -> сервер повторит на другой
            output_config={"effort": "medium"},
            # user_location с country=BG API не принимает (400); болгарские запросы и так находят .bg
            tools=[{"type": tool, "name": "web_search", "max_uses": 10}],
            messages=messages)
        for block in r.content:
            if block.type == "web_search_tool_result" and isinstance(block.content, list):  # объект = ошибка поиска
                for res in block.content:
                    found.setdefault(res.url.rstrip("/"), res.title or "")
            elif block.type == "text":
                answer += block.text + "\n"
        if r.stop_reason != "pause_turn":
            break
        messages = [messages[0], {"role": "assistant", "content": r.content}]
    picked = {}
    for line in answer.splitlines():
        m = re.match(r"\s*[-*\d.)]*\s*<?(https?://\S+?)>?\s*\|\s*(.*)", line)
        if m and m.group(1).rstrip("/") in found:
            picked[m.group(1).rstrip("/")] = f"{found[m.group(1).rstrip('/')]} {m.group(2)}".strip()
    return picked


def links(fetch) -> dict[str, str]:
    cfg = load_config()
    found = search_results(anthropic.Anthropic(), cfg)
    return {u: t for u, t in found.items() if not urlparse(u).netloc.endswith(SKIP_HOSTS)}


def parse_ad(url: str, html: str) -> Listing:
    """Наши площадки разбираем их парсером; чужой сайт: заголовок и видимый текст страницы."""
    host = urlparse(url).netloc
    if host in OWN:
        listing = OWN[host].parse_ad(url, html)
    else:
        soup = BeautifulSoup(html, "html.parser")
        h1 = soup.find("h1") or soup.title
        for t in soup(["script", "style", "noscript", "nav", "footer", "header"]):
            t.decompose()
        text = re.sub(r"\s+", " ", soup.get_text(" "))[:6000]
        listing = Listing(host, url, h1.get_text(" ", strip=True) if h1 else "", text)
    listing.source = f"web: {host}"
    return listing
