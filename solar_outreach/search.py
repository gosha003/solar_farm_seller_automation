"""Search step: sites -> parsing -> dedup -> scoring -> «Станции» tab.

    python -m solar_outreach.search            # writes to the Google Sheet (creates it on first run)
    python -m solar_outreach.search --local    # print only, no Sheets
    python -m solar_outreach.search --web      # plus web search via Claude (paid, once a day)
    python -m solar_outreach.search --only pc        # imot.bg + olx.bg: they return 403 to the VM, so they run on the owner's PC
                                                     # (or --only imot.bg / --only olx.bg separately)
    python -m solar_outreach.search --regions  # web search per province: Montana, Vidin, Dobrich, Shumen, Yambol, Haskovo, Kardzhali (paid)
    python -m solar_outreach.search --brokers  # broker search only (paid, weekly), rows `broker:<site>`
    python -m solar_outreach.search --import found.json  # listings the PC routine collected in the owner's browser
"""
import json
import os
import re
import sys
from datetime import date, datetime
from types import SimpleNamespace

import requests

from solar_outreach import parse, sheets
from solar_outreach.config import load_config, single_run
from solar_outreach.contacts import channel
from solar_outreach.qualify import qualify
from solar_outreach.sources import (LAND, NOT_OPERATING, OPERATING, PERMIT, PV, SERVICES, UNAVAILABLE, Fetcher, Listing, alo,
                                    bazar, brokers,
                                    imot, is_station_sale, kpd, olx, web)

SOURCES = {"bazar.bg": bazar, "alo.bg": alo, "kpd.bg": kpd}
PC_SOURCES = {"imot.bg": imot, "olx.bg": olx}  # they return 403 to the VM: run only from the owner's PC (--only pc = both)
ORDER = {"A": 0, "B": 1, "C": 2, "-": 3}
LOOKALIKE = str.maketrans("асеорхукмтвн", "aceopxykmtbh")  # «9МW» and «9MW» are the same title (lowercased Cyrillic -> Latin)
UNCONFIRMED = "участок или проект без разрешения на строительство? проверь вручную"
LINKS: dict[str, int] = {}  # links/items each source returned in this run, for the «_state» health line


def to_row(l, cfg) -> dict:
    text = f"{l.title} {l.text}"
    kw = parse.power_kw(l.title, l.text)
    eur = parse.price_eur(l.price_text, l.text, cfg["eur_bgn"])
    yr, rg = parse.year(text), parse.regime(text)
    em, ph = parse.emails(text), parse.phones(text)
    q = qualify(kw, eur, text, yr, rg, cfg["criteria"])
    # drafts send the project letter; selling under a tariff or on the market means the plant runs (a grid company
    # named in the text does not: a project's connection contract names it too)
    if (PERMIT.search(l.text) and not PERMIT.search(l.title) and not OPERATING.search(text)
            and not parse.TARIFF_WORDS.search(text) and not parse.MARKET.search(text)):
        q["notes"].append("разрешение на строительство в тексте")
    return {"дата найдено": date.today().isoformat(), "площадка": l.source, "ссылка": l.url, "заголовок": l.title,
            "регион": parse.region(l.location) or "неизвестно", "мощность кВт": kw or "неизвестно",
            "цена EUR": eur or "неизвестно", "EUR/кВт": q["eur_per_kw"] or "неизвестно", "год": yr or "неизвестно",
            "режим": rg, "телефон": ", ".join(ph), "email": ", ".join(em), "продавец": l.seller,
            "канал": channel(em, ph), "отбор": q["verdict"], "скоринг": q["score"], "приоритет": q["priority"],
            **{item: "не спрошено" for item in sheets.ITEMS},
            "статус переписки": "новая" if q["priority"] != "-" else "не писать",
            "примечание": "; ".join(q["notes"])}


def broker_row(l) -> dict:
    """A broker without a listing: a «Станции» row with unknown power and price, priority B (write to everyone found)."""
    em, ph = l.extra["emails"], l.extra["phones"]
    return {"дата найдено": date.today().isoformat(), "площадка": l.source.replace(": ", ":"), "ссылка": l.url,
            "заголовок": l.title, "регион": "неизвестно", "мощность кВт": "неизвестно", "цена EUR": "неизвестно",
            "EUR/кВт": "неизвестно", "год": "неизвестно", "режим": "неизвестно", "телефон": ", ".join(ph),
            "email": ", ".join(em), "продавец": l.seller, "канал": channel(em, ph), "отбор": "брокер", "скоринг": 50,
            "приоритет": "B", **{item: "не спрошено" for item in sheets.ITEMS}, "статус переписки": "новая",
            "примечание": "брокер, найден поиском (не по объявлению)"}


def url_key(url: str) -> str:
    """alo.bg serves one ad under several URLs («/prodavame-fec-9mw-11333221», «/11333221», and realestates.bg links to the
    same numbers): compare them by the ad number."""
    m = re.match(r"https?://(?:www\.)?alo\.bg/(?:[^/?#]*-)?(\d{6,})(?:[/?#]|$)", url)
    return f"alo.bg/{m.group(1)}" if m else url


def keys(r: dict) -> set:
    """Dedup keys: link, phone, email, title+power+region, power+total price+region (the same plant on another site;
    see dup() for how the region of that key is compared).
    ponytail: phone/email are compared together with power, otherwise all of an agency's stations would collapse into one."""
    out = {("url", url_key(r["ссылка"]))}
    region = re.sub(r"^(?:(?:гр\.|град\b|с\.|село\b|обл\.|област\b)\s*)+", "", r["регион"].strip().lower())  # «гр. Плевен»
    kw, eur = (re.sub(r"\s", "", str(r.get(c, ""))).replace(",", ".") for c in ("мощность кВт", "цена EUR"))
    try:  # a fresh row has 1500.0 / 1100000.0, the same row read back from the sheet has «1500», «999,6», «1 100 000»
        kw = f"{float(kw):g}"
        out.add(("price", kw, str(round(float(eur))), region))
    except ValueError:
        pass
    title = re.sub(r"\W+", " ", r["заголовок"].lower().translate(LOOKALIKE)).strip()
    if title:
        out.add(("title", title, kw, region))
    out |= {("contact", c.strip(), kw) for c in f"{r['телефон']},{r['email']}".split(",") if c.strip()}
    return out


def dup(k: set, seen: set) -> bool:
    """A shared key, or the same kW and total price where the regions don't contradict: one site often gives no place
    («неизвестно» merges), but 1 MW for 1 000 000 € in Видин and in Стара Загора are two plants."""
    if {x for x in k if x[0] != "price"} & seen:
        return True
    return any(y[0] == "price" and y[1:3] == x[1:3] and (x[3] == y[3] or "неизвестно" in (x[3], y[3]))
               for x in k if x[0] == "price" for y in seen)


def listings(name, src, fetch, known_urls: set, crit: dict):
    """Open a listing page only if it isn't in the sheet yet and the title's power isn't clearly out of range.
    Clearly out of range (too small/too large) is recorded from the title alone: fewer requests, no blocks from the sites."""
    links = src.links(fetch)
    LINKS[name] = len(links)
    for url, title in links.items():
        if url_key(url) in known_urls or not is_station_sale(title):
            continue
        kw = parse.power_kw(title, "")
        if kw is not None and not crit["min_kwp"] <= kw <= crit["max_kwp"]:
            yield Listing(name, url, title, extra={"title_only": True})
            continue
        try:
            html = fetch(url)
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 429:
                raise  # the site asks us to stop: skip it until the next run
            continue  # one broken page (503, 500) doesn't stop the site
        if html:
            yield src.parse_ad(url, html)


def imported(path: str, known_urls: set):
    """Listings the browser routine saved as a JSON list of {source, url, title, text, price, location, seller}.
    Page text is untrusted data: it only goes through the same parser and the same criteria as every other source.
    Site titles are often generic («Бизнес имот»), so the PV keyword may be in the text; service/project titles are still dropped.
    A generic title whose text reads like a plot or a project without a permit is kept as «unconfirmed» for the owner to check.
    The agent may write a BOM, nulls or a bad element: they are skipped, never abort the batch."""
    with open(path, encoding="utf-8-sig") as f:
        items = json.load(f)
    items = items if isinstance(items, list) else []
    LINKS["браузер"] = len(items)
    for d in items:
        if not isinstance(d, dict):
            continue
        url, title, text = (str(d.get(k) or "") for k in ("url", "title", "text"))
        text = text[:6000]
        if (not re.match(r"https?://[^/]", url) or url_key(url) in known_urls or SERVICES.search(title)
                or LAND.search(title) and not PERMIT.search(f"{title} {text}")
                or not PV.search(f"{title} {text}") or (NOT_OPERATING.search(title) and not PERMIT.search(f"{title} {text}"))):
            continue
        unconfirmed = (not PV.search(title) and NOT_OPERATING.search(text[:300]) and not PERMIT.search(text)
                       and not OPERATING.search(text))  # never SERVICES on the text: plant descriptions name panels, cables
        yield Listing(f"браузер: {d.get('source') or url.split('/')[2]}", url, title, text, str(d.get("price") or ""),
                      str(d.get("location") or ""), str(d.get("seller") or ""), {"unconfirmed": True} if unconfirmed else {})


def find_brokers(fetch, seen: set) -> list[dict]:
    out, links = [], brokers.links(fetch)
    LINKS["брокеры"] = len(links)
    for url, name in links.items():
        if ("url", url_key(url)) in seen or not (html := fetch(url)):
            continue
        row = broker_row(brokers.parse_ad(url, html, name, fetch))
        if not keys(row) & seen:
            seen |= keys(row)
            out.append(row)
    return out


def main(local: bool, use_web: bool = False, only: str = "", find_brokers_too: bool = False, regions: bool = False,
         import_path: str = "") -> None:
    cfg = load_config()
    s = cfg["scraping"]
    fetch = Fetcher(s["user_agent"], tuple(s["delay_sec"]), s.get("host_delay_sec"))
    api = sid = None
    seen: set = set()
    existing = []
    if not local:
        api = sheets.service()
        sid = os.environ.get("SPREADSHEET_ID") or sheets.create(api, cfg["sheet_title"], settings(cfg))
        existing = sheets.read(api, sid, "Станции")
        for r in existing:
            seen |= keys(r)

    new, failed, fresh = [], dict(UNAVAILABLE), {}
    LINKS.clear()
    by_region = SimpleNamespace(links=web.regional_links, parse_ad=web.parse_ad)
    active = ({"браузер": None} if import_path else {} if find_brokers_too else {"регионы": by_region} if regions else
              {k: v for k, v in PC_SOURCES.items() if only in (k, "pc")} if only else
              {**SOURCES, **({"web": web} if use_web else {})})
    if find_brokers_too:  # separate weekly run: brokers only
        try:
            new += find_brokers(fetch, seen)
        except Exception as e:
            failed["брокеры"] = f"{type(e).__name__}: {e}"[:200]
        fresh["брокеры"] = len(new)
        print(f"brokers: готово, всего новых {len(new)}", flush=True)
    for name, src in active.items():
        before = len(new)
        try:
            known = {k[1] for k in seen if k[0] == "url"}
            for l in imported(import_path, known) if src is None else listings(name, src, fetch, known, cfg["criteria"]):
                row = to_row(l, cfg)
                if l.extra.get("unconfirmed"):  # generic title, the text reads like a plot or a project: the owner decides
                    row |= {"отбор": "без разрешения?", "приоритет": "-", "статус переписки": "не писать"}
                note = "только заголовок" if l.extra.get("title_only") else UNCONFIRMED if l.extra.get("unconfirmed") else ""
                row["примечание"] = "; ".join(filter(None, [row["примечание"], note]))
                k = keys(row)
                if dup(k, seen):
                    continue
                seen |= k
                new.append(row)
        except Exception as e:  # site down or blocking us: record it, never work around it
            failed[name] = f"{type(e).__name__}: {e}"[:200]
        fresh[name] = len(new) - before
        print(f"{name}: готово, всего новых {len(new)}", flush=True)

    new.sort(key=lambda r: (ORDER[r["приоритет"]], -r["скоринг"]))
    if local:
        number(new, existing)
    else:  # lock only for writing: the bot doesn't wait while the site search runs
        with single_run("sheet", stale_sec=3600, wait=900):
            existing = sheets.read(api, sid, "Станции")  # while we were searching, the loop may have added a station (broker_redirect)
            seen = set().union(*(keys(r) for r in existing))
            new = [r for r in new if not dup(keys(r), seen)]
            number(new, existing)
            sheets.append(api, sid, "Станции", new)
            now = datetime.now()  # per-source health for the digest: one key per source of this run
            sheets.set_kv(api, sid, "_state", {
                f"поиск {name}": f"{now:%Y-%m-%d %H:%M} ссылок {LINKS.get(name, 0)}, новых {n}"
                                 + (f", ошибка: {failed[name]}" if name in failed else "") for name, n in fresh.items()})

    write = [r for r in new if r["приоритет"] != "-"]
    print(f"новых объявлений: {len(new)}, писать: {len(write)}")
    for r in new:
        print(f"  {r['id']} {r['приоритет']} {r['отбор']:13} {str(r['мощность кВт']):>8} кВт "
              f"{str(r['EUR/кВт']):>10} €/кВт  {r['канал']:16} {r['заголовок'][:60]}")
    print("недоступные площадки:", *[f"  {k}: {v}" for k, v in failed.items()], sep="\n")
    if sid:
        print(f"таблица: https://docs.google.com/spreadsheets/d/{sid}")


def number(new: list[dict], existing: list[dict]) -> None:
    last = max([int(re.sub(r"\D", "", r["id"]) or 0) for r in existing] + [0])
    for i, r in enumerate(new, last + 1):
        r["id"] = f"S{i:03d}"


def settings(cfg: dict) -> list[list]:
    c = cfg["criteria"]
    return [["approval_mode", cfg["approval_mode"]], ["одобрено писем", 0],
            ["мощность кВт", f"{c['min_kwp']}-{c['max_kwp']}"],
            ["EUR/кВт предпочт.", "-".join(map(str, c["price_per_kwp_eur"]["preferred"]))],
            ["EUR/кВт не писать выше", c["price_per_kwp_eur"]["skip_above"]],
            ["новых писем в сутки", cfg["limits"]["new_per_day"]]]


if __name__ == "__main__":
    try:
        main("--local" in sys.argv, "--web" in sys.argv, sys.argv[sys.argv.index("--only") + 1] if "--only" in sys.argv else "",
             "--brokers" in sys.argv, "--regions" in sys.argv,
             sys.argv[sys.argv.index("--import") + 1] if "--import" in sys.argv else "")
    except Exception as e:  # a crash outside the per-source try (Sheets, config) leaves no trace in the sheet: tell the owner
        from solar_outreach import notify
        notify.urgent(f"⚠️ поиск {' '.join(sys.argv[1:]) or 'сайты'} упал: {type(e).__name__}: {str(e)[:200]}")
        raise
