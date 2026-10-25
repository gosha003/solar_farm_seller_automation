"""Web search via Claude (server tool web_search): finds 1-3 MW PV listings on any site.
Claude only searches; our Fetcher opens the pages (robots.txt, pauses). Once a day: python -m solar_outreach.search --web
"""
import re
from urllib.parse import urlparse

import anthropic
from bs4 import BeautifulSoup

from solar_outreach import llm
from solar_outreach.config import load_config
from solar_outreach.sources import Listing, alo, bazar, kpd

PROMPT = """Find current listings (classified ads, real-estate or business-for-sale sites, broker pages) offering an \
OPERATING photovoltaic power plant (ФЕЦ / фотоволтаична централа / соларен парк), or a PV project with an issued \
building permit (разрешение за строеж), for sale in Bulgaria with capacity roughly 1-3 MW (1000-3000 kWp). Search in \
Bulgarian and English, e.g. "продава работеща фотоволтаична централа 1 MW", "ФЕЦ 1,5 MW продажба", \
"фотоволтаичен парк 2 MW продава", "ФЕЦ 3 MW с разрешение за строеж", "operating solar park Bulgaria for sale 1 MW". \
Run several searches. Then list every individual listing page from the results (one ad per URL; not search-result \
pages, news, encyclopedias or company homepages) that may offer a built plant or a permitted project for sale. Include \
it even when the capacity or status cannot be confirmed from the snippet: each page is checked separately afterwards. \
One per line:
<url> | <capacity if stated> <price if stated> <place>
Skip only what is clearly a project or land plot without a building permit, a service or equipment. If none, answer NONE."""
OWN = {"bazar.bg": bazar, "www.alo.bg": alo, "alo.bg": alo, "www.kpd.bg": kpd}
SKIP_HOSTS = ("olx.bg", "facebook.com", "youtube.com", "linkedin.com", "instagram.com", "tiktok.com")


def search_results(client, cfg, prompt: str = PROMPT, tool: str = "web_search_20260209") -> dict[str, str]:
    """{url: model's description} for pages the model picked. A URL is accepted only if it appeared
    in the actual web_search results (guards against invented links). pause_turn: continue the turn (up to 3 times)."""
    messages = [{"role": "user", "content": prompt}]
    found, answer = {}, ""
    for _ in range(3):
        r = client.beta.messages.create(
            model=cfg["llm"]["model_strong"], max_tokens=4000,
            betas=["server-side-fallback-2026-07-01"], fallbacks="default",  # the model refused -> the server retries on another one
            output_config={"effort": "medium"},
            # the API rejects user_location with country=BG (400); Bulgarian queries find .bg sites anyway
            tools=[{"type": tool, "name": "web_search", "max_uses": 10}],
            messages=messages)
        llm.log_usage(cfg, r, llm.caller())  # the caller tells web / regions / brokers apart
        for block in r.content:
            if block.type == "web_search_tool_result" and isinstance(block.content, list):  # an object = a search error
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


REGIONS = ("Монтана", "Видин", "Добрич", "Шумен", "Ямбол", "Хасково", "Кърджали")  # the general query doesn't find them


def regional_links(fetch) -> dict[str, str]:
    """Same search, but one query per province: python -m solar_outreach.search --regions."""
    cfg, client, found = load_config(), anthropic.Anthropic(), {}
    for region in REGIONS:
        prompt = PROMPT + (f'\nSearch ONLY listings located in {region} province (област {region}). Put "{region}" in '
                           f'every query, e.g. "продава ФЕЦ {region}", "фотоволтаична централа {region} 1 MW".')
        found.update(search_results(client, cfg, prompt))
    return {u: t for u, t in found.items() if not urlparse(u).netloc.endswith(SKIP_HOSTS)}


def parse_ad(url: str, html: str) -> Listing:
    """Our own sites are parsed by their parser; any other site: title and visible page text."""
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
