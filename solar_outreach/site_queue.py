"""«Ручная очередь» messages for alo.bg and WhatsApp, sent by a scheduled Claude-in-Chrome routine in the
owner's browser (Viber and other sites stay with people). The routine drives this CLI (usage: USAGE).
"""
import os
import sys
from datetime import date

from solar_outreach import inbox, notify, owner, sheets
from solar_outreach.config import load_config, load_env, single_run

USAGE = """Сообщения «Ручной очереди» для alo.bg и WhatsApp: отправляет Claude в Chrome владельца по расписанию (Viber и другие сайты остаются людям).

    python -m solar_outreach.site_queue pending                 # «S0xx alo <ссылка>» или «S0xx whatsapp <wa.me-ссылка>», по строке на сообщение
    python -m solar_outreach.site_queue text S0xx @файл.txt     # записать текст сообщения в UTF-8 файл (не вышло: файл пустой, код возврата 1)
    python -m solar_outreach.site_queue sent S0xx @файл.txt     # это сообщение отправлено: станция ждёт ответа, дожимы 4/9 дней
    python -m solar_outreach.site_queue failed S0xx <причина>   # отправить нельзя (номера нет в WhatsApp, объявление снято): владельцу
"""  # printed to the owner, so it stays in Russian


def kind(r: dict) -> str:
    if r["канал"].startswith("сайт") and "alo.bg/" in r["ссылка на объявление"]:
        return "alo"
    return "whatsapp" if r["канал"].startswith("телефон") and r.get("WhatsApp", "").startswith("https://wa.me/") else ""


def rows(api, sid) -> list[dict]:
    """What the routine may send now, with the same guards the email path has: nothing during /pause, nothing to a
    paused dialog (escalation, refusal, alo reply not read yet) or to a contact in «Отказы»/«Чёрный список».
    One row per station, the newest: an older follow-up never goes out ahead of a reply drafted after it."""
    if sheets.kv(api, sid, "Настройки").get("пауза", {}).get("значение") == "да":
        return []
    stations = {s["id"]: s for s in sheets.read(api, sid, "Станции")}
    black, refused = sheets.read(api, sid, "Чёрный список"), sheets.read(api, sid, "Отказы")
    ok = [r for r in sheets.read(api, sid, "Ручная очередь") if r["статус"] == "отправить вручную" and kind(r)
          and (st := stations.get(r["id станции"])) and st["статус переписки"] not in inbox.PAUSED
          and not inbox.blocked(st, black, refused)]
    return list({r["id станции"]: r for r in ok}.values())


def handed_out(api, sid, station_id: str, text: str | None) -> dict | None:
    """The row the routine sent. With the text it sent, found without the send guards: a /pause or a new seller
    message arriving between `text` and `sent` must not leave a sent message unrecorded (it would go out again)."""
    if text is None:
        return next((r for r in rows(api, sid) if r["id станции"] == station_id.upper()), None)
    return next((r for r in reversed(sheets.read(api, sid, "Ручная очередь")) if r["id станции"] == station_id.upper()
                 and r["статус"] == "отправить вручную" and kind(r) and r["текст"].strip() == text.strip()), None)


def sent(api, sid, station_id: str, text: str | None = None) -> str:
    """Only the message the routine sent is marked sent; the station's older unsent ones are superseded by it."""
    r = handed_out(api, sid, station_id, text)
    if not r:
        msg = f"Нет такого сообщения для {station_id} в очереди: ничего не отмечено."
        notify.try_send(f"⚠️ ПК: {msg} Проверь «Ручную очередь».")
        return msg
    cfg = load_config()
    state = {k: x["значение"] for k, x in sheets.kv(api, sid, "_state").items()}
    settings = {k: x["значение"] for k, x in sheets.kv(api, sid, "Настройки").items()}
    stations = {x["id"]: x for x in sheets.read(api, sid, "Станции")}
    by_id = {d["id"]: d for d in sheets.read(api, sid, "Черновики")}
    before = {k: dict(v) for k, v in stations.items()}
    c = owner.Ctx(api, sid, cfg, stations, by_id, sheets.read(api, sid, "Диалоги"), settings, state)
    msg = owner.sent(c, station_id, date.today(), r["текст"])
    sheets.update(api, sid, "Черновики", list(c.changed_drafts.values()))
    sheets.append(api, sid, "Диалоги", c.new_dialogs)
    sheets.update(api, sid, "Станции", [s for k, s in stations.items() if s != before[k]])
    notify.try_send(f"{msg} (отправлено с ПК, {kind(r)})")  # the owner sees every message the routine sent
    return msg


def failed(api, sid, station_id: str, why: str) -> str:
    """The routine can't send it (no WhatsApp on the number, listing removed): the station's rows leave the routine's
    list, so they don't take its few slots per run forever, and the owner gets them to send by hand."""
    rs = [r for r in sheets.read(api, sid, "Ручная очередь") if r["id станции"] == station_id.upper()
          and r["статус"] == "отправить вручную" and kind(r)]
    if not rs:
        return f"Нет сообщения для {station_id} в очереди: ничего не отмечено."
    sheets.update(api, sid, "Ручная очередь", [{**r, "статус": f"не отправлено с ПК: {why}"} for r in rs])
    r = rs[-1]
    notify.try_send(f"📱 {r['id станции']}: с ПК не отправилось ({why}). Отправь сам: {r.get('Viber') or r['ссылка на объявление']}"
                    f", текст во вкладке «Ручная очередь», потом /sent {r['id станции']}.")
    return f"{r['id станции']}: отмечено «не отправлено с ПК», владелец оповещён."


def read_arg(arg: str) -> str:
    return open(arg[1:], encoding="utf-8").read() if arg.startswith("@") else arg


if __name__ == "__main__":
    load_env()
    api, sid = sheets.service(), os.environ["SPREADSHEET_ID"]
    a = sys.argv[1:]
    if a[:1] == ["pending"]:
        for r in rows(api, sid):
            print(r["id станции"], kind(r), r["WhatsApp"] if kind(r) == "whatsapp" else r["ссылка на объявление"])
    elif a[:1] == ["text"] and len(a) == 3 and a[2].startswith("@"):
        open(a[2][1:], "w", encoding="utf-8").close()  # a failed call never leaves the previous station's text in the file
        r = next((r for r in rows(api, sid) if r["id станции"] == a[1].upper()), None)
        if not r:
            sys.exit(f"Нет сообщения для {a[1]} в очереди alo.bg/WhatsApp")
        open(a[2][1:], "w", encoding="utf-8").write(r["текст"])
    elif a[:1] == ["sent"] and len(a) in (2, 3):
        with single_run("sheet", stale_sec=3600, wait=300):
            print(sent(api, sid, a[1], read_arg(a[2]) if len(a) == 3 else None))
    elif a[:1] == ["failed"] and len(a) >= 3:
        with single_run("sheet", stale_sec=3600, wait=300):
            print(failed(api, sid, a[1], " ".join(a[2:])))
    else:
        sys.exit(USAGE)
