"""«Ручная очередь» messages for alo.bg and WhatsApp, sent by a scheduled Claude-in-Chrome routine in the
owner's browser (Viber and other sites stay with people). The routine drives this CLI (usage: USAGE).
"""
import os
import sys
from datetime import date

from solar_outreach import owner, sheets
from solar_outreach.config import load_config, load_env, single_run

USAGE = """Сообщения «Ручной очереди» для alo.bg и WhatsApp: отправляет Claude в Chrome владельца по расписанию (Viber и другие сайты остаются людям).

    python -m solar_outreach.site_queue pending                 # «S0xx alo <ссылка>» или «S0xx whatsapp <wa.me-ссылка>», по строке на сообщение
    python -m solar_outreach.site_queue text S0xx @файл.txt     # записать текст сообщения в UTF-8 файл
    python -m solar_outreach.site_queue sent S0xx               # сообщение отправлено в alo.bg: станция ждёт ответа, дожимы 4/9 дней
"""  # printed to the owner, so it stays in Russian


def kind(r: dict) -> str:
    if r["канал"].startswith("сайт") and "alo.bg/" in r["ссылка на объявление"]:
        return "alo"
    return "whatsapp" if r["канал"].startswith("телефон") and r.get("WhatsApp", "").startswith("https://wa.me/") else ""


def rows(api, sid) -> list[dict]:
    return [r for r in sheets.read(api, sid, "Ручная очередь") if r["статус"] == "отправить вручную" and kind(r)]


def sent(api, sid, station_id: str) -> str:
    cfg = load_config()
    state = {k: r["значение"] for k, r in sheets.kv(api, sid, "_state").items()}
    settings = {k: r["значение"] for k, r in sheets.kv(api, sid, "Настройки").items()}
    stations = {r["id"]: r for r in sheets.read(api, sid, "Станции")}
    by_id = {d["id"]: d for d in sheets.read(api, sid, "Черновики")}
    before = {k: dict(v) for k, v in stations.items()}
    c = owner.Ctx(api, sid, cfg, stations, by_id, sheets.read(api, sid, "Диалоги"), settings, state)
    msg = owner.sent(c, station_id, date.today())
    sheets.update(api, sid, "Черновики", list(c.changed_drafts.values()))
    sheets.append(api, sid, "Диалоги", c.new_dialogs)
    sheets.update(api, sid, "Станции", [s for k, s in stations.items() if s != before[k]])
    return msg


if __name__ == "__main__":
    load_env()
    api, sid = sheets.service(), os.environ["SPREADSHEET_ID"]
    a = sys.argv[1:]
    if a[:1] == ["pending"]:
        for r in rows(api, sid):
            print(r["id станции"], kind(r), r["WhatsApp"] if kind(r) == "whatsapp" else r["ссылка на объявление"])
    elif a[:1] == ["text"] and len(a) == 3 and a[2].startswith("@"):
        r = next((r for r in rows(api, sid) if r["id станции"] == a[1].upper()), None)
        if not r:
            sys.exit(f"Нет сообщения для {a[1]} в очереди alo.bg")
        open(a[2][1:], "w", encoding="utf-8").write(r["текст"])
    elif a[:1] == ["sent"] and len(a) == 2:
        with single_run("sheet", stale_sec=3600, wait=300):
            print(sent(api, sid, a[1]))
    else:
        sys.exit(USAGE)
