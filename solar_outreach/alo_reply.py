"""Ответ продавца с alo.bg: уведомление приходит без текста, текст читается в кабинете alo (в Chrome владельца).

    python -m solar_outreach.alo_reply pending                  # станции со статусом «ответ на alo»
    python -m solar_outreach.alo_reply note S020 @файл.txt --title "Запитване по <заголовок диалога>"
        # разобрать текст как /note, снять пометку; текст можно и строкой вместо @файл (UTF-8)
        # --title обязателен: заголовок диалога в кабинете должен совпасть с заголовком станции, иначе отказ

Читает кабинет не бот на VM (вход в alo только через Google/reCAPTCHA), а Claude в Chrome по расписанию на ПК владельца.
"""
import os
import re
import sys
from datetime import date

from solar_outreach import approval, notify, owner, sheets
from solar_outreach.config import load_config, single_run

PENDING = "ответ на alo"


def norm(title: str) -> str:
    t = title.lower().removeprefix("запитване по").removeprefix("inquiry on").removeprefix("inquiry about")
    return " ".join(re.sub(r"[^\w]+", " ", t).split())


def same_title(dialog_title: str, station_title: str) -> bool:
    """Защита от чужого диалога: ответ записывается станции, только если заголовки объявления совпали."""
    return bool(norm(dialog_title)) and norm(dialog_title) == norm(station_title)


def pending(api, sid) -> list[dict]:
    return [s for s in sheets.read(api, sid, "Станции") if s["статус переписки"] == PENDING]


def note(api, sid, station_id: str, text: str, title: str) -> str:
    cfg = load_config()
    state = {k: r["значение"] for k, r in sheets.kv(api, sid, "_state").items()}
    settings = {k: r["значение"] for k, r in sheets.kv(api, sid, "Настройки").items()}
    stations = {r["id"]: r for r in sheets.read(api, sid, "Станции")}
    by_id = {d["id"]: d for d in sheets.read(api, sid, "Черновики")}
    st = stations.get(station_id.upper())
    if not st or not same_title(title, st["заголовок"]):
        return f"Отказ: диалог «{title}» не от {station_id} («{st['заголовок'] if st else 'нет такой станции'}»). Ничего не записано."
    before = {k: dict(v) for k, v in stations.items()}
    c = owner.Ctx(api, sid, cfg, stations, by_id, sheets.read(api, sid, "Диалоги"), settings, state)
    msg = owner.note(c, station_id, text, date.today())
    if st and st["статус переписки"] == PENDING:  # автоответ и т.п.: пометка снимается, ждём дальше
        st["статус переписки"], st["следующий шаг"] = "ждём ответа", "ждать ответа"
    queue = []
    auto = (settings.get("approval_mode") or cfg["approval_mode"]) == "auto"
    for d in c.new_drafts:
        approval.present(d, stations[d["id станции"]], auto, queue)
    sheets.append(api, sid, "Ручная очередь", queue)
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
    args = sys.argv[1:]
    if args[:1] == ["pending"]:
        for s in pending(api, sid):
            print(s["id"], s["ссылка"], "| заголовок объявления:", s["заголовок"])
    elif args[:1] == ["note"] and len(args) == 5 and args[3] == "--title":
        text = args[2]
        if text.startswith("@"):
            text = open(text[1:], encoding="utf-8").read().strip()
        with single_run("sheet", stale_sec=3600, wait=300):
            print(note(api, sid, args[1], text, args[4]))
    else:
        sys.exit(__doc__)
