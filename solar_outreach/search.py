"""Поиск (шаг 6 цикла): площадки -> парсинг -> дедуп -> скоринг -> вкладка «Станции».

    python -m solar_outreach.search            # пишет в Google Sheet (создаёт при первом запуске)
    python -m solar_outreach.search --local    # только печать, без Sheets
    python -m solar_outreach.search --web      # плюс веб-поиск через Claude (платно, раз в сутки)
    python -m solar_outreach.search --only imot.bg   # только imot.bg: с VM он даёт 403, запускается на ПК владельца
    python -m solar_outreach.search --regions  # веб-поиск отдельно по областям Монтана, Видин, Добрич, Шумен, Ямбол, Хасково, Кърджали (платно)
    python -m solar_outreach.search --brokers  # только поиск брокеров (платно, раз в неделю), строки «брокер:сайт»
"""
import os
import re
import sys
from datetime import date
from types import SimpleNamespace

import requests

from solar_outreach import parse, sheets
from solar_outreach.config import load_config, single_run
from solar_outreach.contacts import channel
from solar_outreach.qualify import qualify
from solar_outreach.sources import UNAVAILABLE, Fetcher, Listing, alo, bazar, brokers, imot, is_station_sale, kpd, web

SOURCES = {"bazar.bg": bazar, "alo.bg": alo, "kpd.bg": kpd}
ORDER = {"A": 0, "B": 1, "C": 2, "-": 3}


def to_row(l, cfg) -> dict:
    text = f"{l.title} {l.text}"
    kw = parse.power_kw(l.title, l.text)
    eur = parse.price_eur(l.price_text, l.text, cfg["eur_bgn"])
    yr, rg = parse.year(text), parse.regime(text)
    em, ph = parse.emails(text), parse.phones(text)
    q = qualify(kw, eur, text, yr, rg, cfg["criteria"])
    return {"дата найдено": date.today().isoformat(), "площадка": l.source, "ссылка": l.url, "заголовок": l.title,
            "регион": parse.region(l.location) or "неизвестно", "мощность кВт": kw or "неизвестно",
            "цена EUR": eur or "неизвестно", "EUR/кВт": q["eur_per_kw"] or "неизвестно", "год": yr or "неизвестно",
            "режим": rg, "телефон": ", ".join(ph), "email": ", ".join(em), "продавец": l.seller,
            "канал": channel(em, ph), "отбор": q["verdict"], "скоринг": q["score"], "приоритет": q["priority"],
            **{item: "не спрошено" for item in sheets.ITEMS},
            "статус переписки": "новая" if q["priority"] != "-" else "не писать",
            "примечание": "; ".join(q["notes"])}


def broker_row(l) -> dict:
    """Брокер без объявления: строка «Станции» с неизвестными мощностью и ценой, приоритет B (писать всем найденным)."""
    em, ph = l.extra["emails"], l.extra["phones"]
    return {"дата найдено": date.today().isoformat(), "площадка": l.source.replace(": ", ":"), "ссылка": l.url,
            "заголовок": l.title, "регион": "неизвестно", "мощность кВт": "неизвестно", "цена EUR": "неизвестно",
            "EUR/кВт": "неизвестно", "год": "неизвестно", "режим": "неизвестно", "телефон": ", ".join(ph),
            "email": ", ".join(em), "продавец": l.seller, "канал": channel(em, ph), "отбор": "брокер", "скоринг": 50,
            "приоритет": "B", **{item: "не спрошено" for item in sheets.ITEMS}, "статус переписки": "новая",
            "примечание": "брокер, найден поиском (не по объявлению)"}


def keys(r: dict) -> set:
    """Дедуп по ТЗ: ссылка, телефон, email, заголовок+мощность+регион.
    ponytail: телефон/email сравниваются вместе с мощностью, иначе у агентства все станции схлопнутся в одну."""
    kw = str(r.get("мощность кВт", ""))
    title = re.sub(r"\W+", " ", r["заголовок"].lower()).strip()
    out = {("url", r["ссылка"])}
    if title:
        out.add(("title", title, kw, r["регион"]))
    out |= {("contact", c.strip(), kw) for c in f"{r['телефон']},{r['email']}".split(",") if c.strip()}
    return out


def listings(name, src, fetch, known_urls: set, crit: dict):
    """Страницу объявления открываем только если её нет в таблице и мощность из заголовка не заведомо мимо.
    Заведомо мимо (мелкая/крупная) пишем по одному заголовку: меньше запросов, площадки не блокируют."""
    for url, title in src.links(fetch).items():
        if url in known_urls or not is_station_sale(title):
            continue
        kw = parse.power_kw(title, "")
        if kw is not None and not crit["min_kwp"] <= kw <= crit["max_kwp"]:
            yield Listing(name, url, title, extra={"title_only": True})
            continue
        try:
            html = fetch(url)
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 429:
                raise  # площадка просит остановиться: прекращаем её до следующего запуска
            continue  # одна битая страница (503, 500) не останавливает площадку
        if html:
            yield src.parse_ad(url, html)


def find_brokers(fetch, seen: set) -> list[dict]:
    out = []
    for url, name in brokers.links(fetch).items():
        if ("url", url) in seen or not (html := fetch(url)):
            continue
        row = broker_row(brokers.parse_ad(url, html, name, fetch))
        if not keys(row) & seen:
            seen |= keys(row)
            out.append(row)
    return out


def main(local: bool, use_web: bool = False, only: str = "", find_brokers_too: bool = False, regions: bool = False) -> None:
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

    new, failed = [], dict(UNAVAILABLE)
    by_region = SimpleNamespace(links=web.regional_links, parse_ad=web.parse_ad)
    active = ({} if find_brokers_too else {"web": by_region} if regions else {"imot.bg": imot} if only == "imot.bg"
              else {**SOURCES, **({"web": web} if use_web else {})})
    if find_brokers_too:  # отдельный запуск раз в неделю: только брокеры
        try:
            new += find_brokers(fetch, seen)
        except Exception as e:
            failed["brokers"] = f"{type(e).__name__}: {e}"[:200]
        print(f"brokers: готово, всего новых {len(new)}", flush=True)
    for name, src in active.items():
        try:
            for l in listings(name, src, fetch, {k[1] for k in seen if k[0] == "url"}, cfg["criteria"]):
                row = to_row(l, cfg)
                if l.extra.get("title_only"):
                    row["примечание"] = "; ".join(filter(None, [row["примечание"], "только заголовок"]))
                k = keys(row)
                if k & seen:
                    continue
                seen |= k
                new.append(row)
        except Exception as e:  # площадка легла/заблокировала: отмечаем, не обходим
            failed[name] = f"{type(e).__name__}: {e}"[:200]
        print(f"{name}: готово, всего новых {len(new)}", flush=True)

    new.sort(key=lambda r: (ORDER[r["приоритет"]], -r["скоринг"]))
    if local:
        number(new, existing)
    else:  # блокировка только на запись: бот не ждёт, пока идёт поиск по сайтам
        with single_run("sheet", stale_sec=3600, wait=900):
            existing = sheets.read(api, sid, "Станции")  # пока искали, цикл мог добавить станцию (broker_redirect)
            seen = set().union(*(keys(r) for r in existing))
            new = [r for r in new if not keys(r) & seen]
            number(new, existing)
            sheets.append(api, sid, "Станции", new)
            down = "; ".join(f"{k}: {v}" for k, v in failed.items() if k not in UNAVAILABLE)  # для дайджеста
            sheets.set_kv(api, sid, "_state", {"площадки недоступны": down})

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
    main("--local" in sys.argv, "--web" in sys.argv, sys.argv[sys.argv.index("--only") + 1] if "--only" in sys.argv else "",
         "--brokers" in sys.argv, "--regions" in sys.argv)
