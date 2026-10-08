"""Фаза 2: черновики -> Telegram на одобрение -> таблица. Ничего не отправляет продавцам (отправка = Фаза 3).

    python -m solar_outreach.approval            # обработать нажатия/правки владельца, создать черновики новых станций
    python -m solar_outreach.approval --test 9   # плюс 9 тестовых черновиков по мелким станциям (не отправляются)

Состояние между запусками в «_state»: tg_offset, pending_edit. Счётчик одобренных в «Настройки».
"""
import os
import sys
from datetime import date

from solar_outreach import drafts, llm, notify, rules, sheets
from solar_outreach.config import load_config, single_run

WAITING = ("на одобрении", "ждёт правки")


def contact(st: dict) -> str:
    return st["email"] if st["канал"] == "email" else st["телефон"] if st["канал"].startswith("телефон") else st["ссылка"]


def make_draft(st: dict, did: str, test: bool) -> dict:
    lang = drafts.language(st["заголовок"])
    subject, body = drafts.first(st, lang)
    kind = "первое (брокер)" if drafts.is_broker(st) else "первое"
    return {"id": did, "id станции": st["id"], "дата": date.today().isoformat(), "язык": lang,
            "тип": f"{kind}, ТЕСТ" if test else kind, "канал": st["канал"], "кому": contact(st), "тема": subject,
            "текст": body, "статус": "на одобрении",
            "нарушения правил": ", ".join(rules.violations(f"{subject}\n{body}", drafts.template(lang)["signature"]))}


def card(d: dict, st: dict) -> str:
    head = [f"📝 {d['id']} · {d['id станции']} · {d['тип']} ({d['язык']})",
            f"{st['заголовок'][:90]}",
            f"{st['мощность кВт']} кВт · {st['цена EUR']} EUR · {st['EUR/кВт']} €/кВт · {st['регион']}",
            f"Канал: {d['канал']} · кому: {d['кому']}", st["ссылка"]]
    if "ТЕСТ" in d["тип"]:
        head.append("ТЕСТ шаблона: этому продавцу писать не будем")
    if d.get("нарушения правил"):
        head.append(f"⚠️ Нарушения правил раздела 4: {d['нарушения правил']}")
    return "\n".join(head) + f"\n\nТема: {d['тема']}\n\n{d['текст']}"


def queue_row(d: dict, st: dict) -> dict:
    row = {"id станции": d["id станции"], "канал": d["канал"], "ссылка на объявление": st["ссылка"],
           "текст": d["текст"], "статус": "отправить вручную"}
    if d["канал"].startswith("телефон"):
        row["WhatsApp"], row["Viber"] = drafts.manual_links(d["кому"].split(",")[0], d["текст"])
    return row


def handle_updates(state, stations, by_id) -> tuple[dict, list, int]:
    """Возвращает (изменённые черновики, строки ручной очереди, прирост одобренных)."""
    offset = int(state.get("tg_offset") or 0)
    pending = state.get("pending_edit") or ""
    changed, queue, approved = {}, [], 0
    for u in notify.updates(offset):
        offset = u["update_id"] + 1
        if not u["_owner"]:
            continue
        cq, msg = u.get("callback_query"), u.get("message") or {}
        if cq:
            action, _, did = (cq.get("data") or "").partition(":")
            d = by_id.get(did)
            if not d or d["статус"] not in WAITING:
                notify.answer_callback(cq["id"], "Уже обработан")
                continue
            st = stations[d["id станции"]]
            if action == "a":
                d["статус"] = "одобрено"
                approved += 1
                test = "ТЕСТ" in d["тип"]
                if not test and d["канал"] != "email":
                    queue.append(queue_row(d, st))
                if not test:
                    st["статус переписки"] = "одобрено, ждёт отправки"
                notify.answer_callback(cq["id"], "Одобрено")
                notify.send(f"✅ {did} одобрено." + (" Тест: никуда не отправляется." if test else
                            " DRY_RUN: письмо не отправлено (отправка с Фазы 3)." if d["канал"] == "email"
                            else " Текст и ссылки во вкладке «Ручная очередь»."))
            elif action == "s":
                d["статус"] = "пропущено"
                if "ТЕСТ" not in d["тип"]:
                    st["статус переписки"] = "пропущено владельцем"
                notify.answer_callback(cq["id"], "Пропущено")
            elif action == "e":
                d["статус"], pending = "ждёт правки", did
                notify.answer_callback(cq["id"])
                notify.send(f"✏️ {did}: пришли одним сообщением, что поменять.")
            changed[did] = d
        elif msg.get("text") and pending and not msg["text"].startswith("/"):
            d, pending = by_id[pending], ""
            st = stations[d["id станции"]]
            examples = [x["текст"] for x in by_id.values() if x["статус"] == "одобрено" and x.get("правка владельца")]
            try:
                new = llm.rewrite(d["тема"], d["текст"], msg["text"], {k: v for k, v in st.items() if k != "_row"},
                                  d["язык"], examples)
            except llm.LLMError as e:
                d["статус"] = "на одобрении"
                notify.send(f"⚠️ {d['id']}: не смог переписать ({e}). Черновик прежний, кнопки ниже.", notify.draft_buttons(d["id"]))
                changed[d["id"]] = d
                continue
            sig = drafts.template(d["язык"])["signature"]
            d.update({"тема": new.subject, "текст": new.body, "статус": "на одобрении", "правка владельца": msg["text"],
                      "нарушения правил": ", ".join(rules.violations(f"{new.subject}\n{new.body}", sig))})
            d["tg сообщение"] = notify.send(card(d, st), notify.draft_buttons(d["id"]))
            changed[d["id"]] = d
        elif msg.get("text", "").startswith("/"):
            notify.send("Команды (/stats, /pause, /auto ...) появятся в Фазе 4.")
    state["tg_offset"], state["pending_edit"] = offset, pending
    return changed, queue, approved


def main(test_n: int = 0) -> None:
    cfg = load_config()
    api, sid = sheets.service(), os.environ["SPREADSHEET_ID"]
    sheets.ensure_tabs(api, sid)
    state = {k: r["значение"] for k, r in sheets.kv(api, sid, "_state").items()}
    stations = {r["id"]: r for r in sheets.read(api, sid, "Станции")}
    by_id = {d["id"]: d for d in sheets.read(api, sid, "Черновики")}
    before = {sid_: dict(st) for sid_, st in stations.items()}

    changed, queue, approved = handle_updates(state, stations, by_id)

    drafted = {d["id станции"] for d in by_id.values()}
    real = [s for s in stations.values() if s["статус переписки"] == "новая" and s["приоритет"] != "-" and s["id"] not in drafted]
    test = sorted((s for s in stations.values() if s["приоритет"] == "-" and s["id"] not in drafted),
                  key=lambda s: -float(s["скоринг"] or 0))[:test_n]
    new = []
    for st in real + test:
        d = make_draft(st, f"D{len(by_id) + len(new) + 1:03d}", st in test)
        d["tg сообщение"] = notify.send(card(d, st), notify.draft_buttons(d["id"]))
        if st not in test:
            st["статус переписки"] = "черновик"
        new.append(d)

    sheets.update(api, sid, "Черновики", list(changed.values()))
    sheets.append(api, sid, "Черновики", new)
    sheets.append(api, sid, "Ручная очередь", queue)
    sheets.update(api, sid, "Станции", [st for k, st in stations.items() if st != before[k]])
    sheets.set_kv(api, sid, "_state", {"tg_offset": state["tg_offset"], "pending_edit": state["pending_edit"]})
    if approved:
        settings = sheets.kv(api, sid, "Настройки")
        total = int(settings.get("одобрено писем", {}).get("значение") or 0) + approved
        sheets.set_kv(api, sid, "Настройки", {"одобрено писем": total})
        if total >= cfg["limits"]["approvals_before_auto_offer"] > total - approved:
            notify.send(f"{total} писем одобрено. Включить автоотправку? Команда /auto on заработает в Фазе 4.")
    print(f"обновлений черновиков: {len(changed)}, новых черновиков: {len(new)}, в ручную очередь: {len(queue)}")


if __name__ == "__main__":
    n = sys.argv[sys.argv.index("--test") + 1] if "--test" in sys.argv else "0"
    with single_run("sheet"):
        main(int(n))
