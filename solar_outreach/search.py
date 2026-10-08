"""Поиск (шаг 6 цикла): площадки -> парсинг -> дедуп -> скоринг -> вкладка «Станции».

    python -m solar_outreach.search            # пишет в Google Sheet (создаёт при первом запуске)
    python -m solar_outreach.search --local    # только печать, без Sheets
    python -m solar_outreach.search --web      # плюс веб-поиск через Claude (платно, раз в сутки)
"""
import os
import re
import sys
from datetime import date

from solar_outreach import parse, sheets
from solar_outreach.config import load_config
from solar_outreach.contacts import channel
from solar_outreach.qualify import qualify
from solar_outreach.sources import UNAVAILABLE, Fetcher, Listing, alo, bazar, is_station_sale, kpd, web

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
        if kw is not None and not crit["soft_min_kwp"] <= kw <= crit["soft_max_kwp"]:
            yield Listing(name, url, title, extra={"title_only": True})
        elif html := fetch(url):
            yield src.parse_ad(url, html)


def main(local: bool, use_web: bool = False) -> None:
    cfg = load_config()
    fetch = Fetcher(cfg["scraping"]["user_agent"], tuple(cfg["scraping"]["delay_sec"]))
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
    for name, src in {**SOURCES, **({"web": web} if use_web else {})}.items():
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
    for i, r in enumerate(new, len(existing) + 1):
        r["id"] = f"S{i:03d}"
    if not local:
        sheets.append(api, sid, "Станции", new)

    write = [r for r in new if r["приоритет"] != "-"]
    print(f"новых объявлений: {len(new)}, писать: {len(write)}")
    for r in new:
        print(f"  {r['id']} {r['приоритет']} {r['отбор']:13} {str(r['мощность кВт']):>8} кВт "
              f"{str(r['EUR/кВт']):>10} €/кВт  {r['канал']:16} {r['заголовок'][:60]}")
    print("недоступные площадки:", *[f"  {k}: {v}" for k, v in failed.items()], sep="\n")
    if sid:
        print(f"таблица: https://docs.google.com/spreadsheets/d/{sid}")


def settings(cfg: dict) -> list[list]:
    c = cfg["criteria"]
    return [["approval_mode", cfg["approval_mode"]], ["одобрено писем", 0],
            ["мощность кВт", f"{c['min_kwp']}-{c['max_kwp']} (мягко {c['soft_min_kwp']}-{c['soft_max_kwp']})"],
            ["EUR/кВт предпочт.", "-".join(map(str, c["price_per_kwp_eur"]["preferred"]))],
            ["EUR/кВт не писать выше", c["price_per_kwp_eur"]["skip_above"]],
            ["новых писем в сутки", cfg["limits"]["new_per_day"]]]


if __name__ == "__main__":
    main("--local" in sys.argv, "--web" in sys.argv)
