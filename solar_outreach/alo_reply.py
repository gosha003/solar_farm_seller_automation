"""Ответ продавца с alo.bg: уведомление приходит без текста, текст читается в кабинете alo (в Chrome владельца).

    python -m solar_outreach.alo_reply pending                  # станции со статусом «ответ на alo»
    python -m solar_outreach.alo_reply note S020 "<текст>"      # разобрать текст как /note, снять пометку

Читает кабинет не бот на VM (вход в alo только через Google/reCAPTCHA), а Claude в Chrome по расписанию на ПК владельца.
"""
import os
import sys
from datetime import date

from solar_outreach import approval, notify, owner, sheets
from solar_outreach.config import load_config, single_run

PENDING = "ответ на alo"


def pending(api, sid) -> list[dict]:
    return [s for s in sheets.read(api, sid, "Станции") if s["статус переписки"] == PENDING]


def note(api, sid, station_id: str, text: str) -> str:
    cfg = load_config()
    state = {k: r["значение"] for k, r in sheets.kv(api, sid, "_state").items()}
    settings = {k: r["значение"] for k, r in sheets.kv(api, sid, "Настройки").items()}
    stations = {r["id"]: r for r in sheets.read(api, sid, "Станции")}
    by_id = {d["id"]: d for d in sheets.read(api, sid, "Черновики")}
    before = {k: dict(v) for k, v in stations.items()}
    c = owner.Ctx(api, sid, cfg, stations, by_id, sheets.read(api, sid, "Диалоги"), settings, state)
    msg = owner.note(c, station_id, text, date.today())
    st = stations.get(station_id.upper())
    if st and st["статус переписки"] == PENDING:  # автоответ и т.п.: пометка снимается, ждём дальше
        st["статус переписки"], st["следующий шаг"] = "ждём ответа", "ждать ответа"
    for d in c.new_drafts:
        approval.present(d, stations[d["id станции"]], False, [])
    sheets.append(api, sid, "Диалоги", c.new_dialogs)
    sheets.append(api, sid, "Отказы", c.new_refusals)
    sheets.append(api, sid, "Черновики", c.new_drafts)
    sheets.update(api, sid, "Станции", [s for k, s in stations.items() if s != before[k]])
    notify.send(msg)
    return msg


if __name__ == "__main__":
    from solar_outreach.config import load_env
    load_env()
    api, sid = sheets.service(), os.environ["SPREADSHEET_ID"]
    if sys.argv[1:2] == ["pending"]:
        for s in pending(api, sid):
            print(s["id"], s["ссылка"], "|", s["продавец"])
    elif sys.argv[1:2] == ["note"] and len(sys.argv) == 4:
        with single_run("sheet", stale_sec=3600, wait=300):
            print(note(api, sid, sys.argv[2], sys.argv[3]))
    else:
        sys.exit(__doc__)
