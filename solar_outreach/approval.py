"""Telegram (кнопки и команды) -> черновики (первые письма, дожимы) -> отправка в рамках лимитов -> дайджест.

    python -m solar_outreach.approval            # обработать нажатия/правки владельца, создать черновики новых станций
    python -m solar_outreach.approval --test 9   # плюс 9 тестовых черновиков по мелким станциям (не отправляются)

Состояние между запусками в «_state»: tg_offset, pending_edit, дайджест, ошибки.
«Настройки»: approval_mode (manual|auto, меняет /auto), пауза (/pause), счётчик одобренных.
"""
import os
import random
import sys
import time
from datetime import date

from solar_outreach import drafts, followup, gmail_client as gmail, inbox, llm, notify, owner, rules, sheets
from solar_outreach.config import dry_run, load_config, single_run

WAITING = ("на одобрении", "ждёт правки")
PAUSED = ("эскалация", "отказ", "пропущено владельцем", "ответ на alo")  # раздел 4: пока владелец не решил, письма не уходят


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
    email = "email" in d["канал"]
    row = {"id станции": d["id станции"], "канал": f"{d['канал']}: {d['кому']}" if email else d["канал"],
           "ссылка на объявление": st["ссылка"], "статус": "отправить вручную",
           "текст": f"Тема: {d['тема']}\n\n{d['текст']}" if email and d.get("тема") else d["текст"]}
    if d["канал"].startswith("телефон"):
        row["WhatsApp"], row["Viber"] = drafts.manual_links(d["кому"].split(",")[0], d["текст"])
    return row


def approve(d: dict, st: dict, queue: list) -> None:
    """Одобрение (кнопкой или в режиме auto): email уйдёт при отправке, остальное в «Ручную очередь».
    DRY_RUN: email тоже в очередь, отправляет владелец сам; пометка «вручную» не даст системе отправить его позже."""
    d["статус"] = "одобрено"
    if "ТЕСТ" not in d["тип"]:
        if d["канал"] != "email" or dry_run():
            queue.append(queue_row(d, st))
            if d["канал"] == "email":
                d["отправлено"] = "вручную"
        st["статус переписки"] = "одобрено, ждёт отправки"


def site_hint(d: dict, st: dict) -> None:
    """Канал «сайт» (alo.bg): ссылка и чистый текст двумя сообщениями, текст копируется одним тапом."""
    if d["канал"].startswith("сайт") and "ТЕСТ" not in d["тип"]:
        notify.send(f"{d['id станции']}: открой объявление, вставь текст из следующего сообщения\n{st['ссылка']}")
        notify.send(d["текст"])


def present(d: dict, st: dict, auto: bool, queue: list) -> None:
    """Новый черновик: в режиме auto без нарушений правил одобряется сам, иначе карточка с кнопками владельцу."""
    if auto and not d["нарушения правил"] and "ТЕСТ" not in d["тип"]:
        approve(d, st, queue)
        if d["канал"] != "email":
            notify.send(f"📱 {d['id']} · {d['id станции']}: отправь вручную, текст и ссылки во вкладке «Ручная очередь».")
            site_hint(d, st)
    else:
        d["tg сообщение"] = notify.send(card(d, st), notify.draft_buttons(d["id"]))


def handle_updates(state, stations, by_id, ctx: "owner.Ctx | None" = None) -> tuple[dict, list, int]:
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
                approve(d, st, queue)
                approved += 1
                notify.answer_callback(cq["id"], "Одобрено")
                notify.send(f"✅ {did} одобрено." + (" Тест: никуда не отправляется." if "ТЕСТ" in d["тип"] else
                            " Письмо уйдёт в ближайшем запуске." if d["канал"] == "email" and not dry_run()
                            else f" Текст во вкладке «Ручная очередь». Отправишь сам: /sent {d['id станции']}"))
                site_hint(d, st)
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
            notify.send(owner.command(msg["text"], ctx, date.today()) if ctx else owner.HELP)
    state["tg_offset"], state["pending_edit"] = offset, pending
    return changed, queue, approved


def to_send(by_id: dict) -> list[dict]:
    """Одобренные email-черновики, ещё не отправленные. Помеченные DRY_RUN ждут, пока DRY_RUN не выключат."""
    pending = ("",) if dry_run() else ("", "DRY_RUN")
    return [d for d in by_id.values() if d["статус"] == "одобрено" and d["канал"] == "email"
            and "ТЕСТ" not in d["тип"] and d.get("отправлено", "") in pending]


def send_approved(api, sid, ds: list[dict], by_id: dict, stations: dict, cfg: dict,
                  left: dict | None = None, quiet: bool = False) -> list[dict]:
    """Отправка через Gmail с паузой 60-180 с. Возвращает строки «Диалоги» (out).
    left = {"run": первых писем и дожимов за этот запуск, "first": первых писем до суточного лимита};
    не влезшее остаётся «одобрено» и уходит в следующих запусках. Ответы в диалогах без лимита (раздел 10)."""
    left = left if left is not None else {"run": len(ds), "first": len(ds)}
    if not ds:
        return []
    svc = gmail.service()
    blacklist = sheets.read(api, sid, "Чёрный список")
    refused = {r["контакт"].lower().strip() for r in sheets.read(api, sid, "Отказы")}
    sent_first = {x["кому"].lower() for x in by_id.values() if x["тип"].startswith("первое") and x.get("тред Gmail")}
    out = []
    for d in ds:
        to = d["кому"].split(",")[0].strip()
        if inbox.blacklisted(to, blacklist):
            d["статус"] = "чёрный список"
            notify.send(f"⛔ {d['id']}: {to} в чёрном списке, не отправлено.")
            continue
        if to.lower() in refused:
            d["статус"] = "в отказах"
            notify.send(f"⛔ {d['id']}: {to} в «Отказах» (просил не писать или продано), автоматически не отправляю.")
            continue
        if stations[d["id станции"]]["статус переписки"] in PAUSED:
            d["статус"] = "на паузе"
            notify.send(f"⏸ {d['id']}: диалог {d['id станции']} на паузе ({stations[d['id станции']]['статус переписки']}), "
                        "не отправляю. Реши эскалацию, потом пришли правку или одобри заново.")
            continue
        if d["тип"].startswith("первое") and to.lower() in sent_first:
            d["статус"] = "дубль адреса"
            notify.send(f"⛔ {d['id']}: на {to} уже уходило первое письмо, второе не отправляю.")
            continue
        first, outreach = d["тип"].startswith("первое"), d["тип"].startswith(("первое", "дожим"))
        if outreach and (left["run"] <= 0 or first and left["first"] <= 0):
            continue
        if out and not dry_run():
            time.sleep(random.uniform(*cfg["limits"]["send_pause_sec"]))
        res = gmail.send(svc, to, d["тема"], d["текст"], d.get("тред Gmail", ""), d.get("in-reply-to", ""))
        if res.get("dry_run"):
            d["отправлено"] = "DRY_RUN"
            notify.send(f"🧪 {d['id']}: DRY_RUN, письмо на {to} не отправлено.")
            continue
        left["run"] -= outreach
        left["first"] -= first
        today = date.today().isoformat()
        d.update({"статус": "отправлено", "отправлено": today, "тред Gmail": res["threadId"]})
        if first:
            sent_first.add(to.lower())
        st = stations[d["id станции"]]
        st.update({"статус переписки": "ждём ответа", "последний контакт": today, "следующий шаг": "ждать ответа"})
        if not d["тип"].startswith("дожим"):
            sheets.mark_asked(st, first)
        out.append({"id станции": d["id станции"], "направление": "out", "дата": today, "канал": "email",
                    "язык": d["язык"], "краткое содержание": f"{d['тип']}: {d['тема']}", "тред Gmail": res["threadId"]})
        if not quiet:
            notify.send(f"📤 {d['id']}: отправлено на {to}.")
    return out


def bounce_stop(c: owner.Ctx, by_id: dict, today: str) -> None:
    """Раздел 8: доля bounce за сутки выше порога -> автоотправка выключается (назад в manual), владельцу срочно."""
    bounces = int(c.state.get(f"bounce {today}") or 0)
    sent = sum(d.get("отправлено") == today for d in by_id.values())
    if c.mode() == "auto" and bounces / max(sent, 1) > c.cfg["limits"]["bounce_rate_stop"]:
        c.settings["approval_mode"] = "manual"
        notify.urgent(f"🛑 Bounce сегодня {bounces} на {sent} отправленных. Автоотправка выключена, режим manual. "
                      "Адреса в «Чёрном списке». /auto on вернёт автоотправку.")


def main(test_n: int = 0) -> int:
    """Один проход. Возвращает offset Telegram для long polling демона."""
    cfg = load_config()
    api, sid = sheets.service(), os.environ["SPREADSHEET_ID"]
    sheets.ensure_tabs(api, sid)
    state = {k: r["значение"] for k, r in sheets.kv(api, sid, "_state").items()}
    settings = {k: r["значение"] for k, r in sheets.kv(api, sid, "Настройки").items()}
    stations = {r["id"]: r for r in sheets.read(api, sid, "Станции")}
    by_id = {d["id"]: d for d in sheets.read(api, sid, "Черновики")}
    dialogs = sheets.read(api, sid, "Диалоги")
    before = {sid_: dict(st) for sid_, st in stations.items()}
    settings_before = dict(settings)
    today = date.today()
    ctx = owner.Ctx(api, sid, cfg, stations, by_id, dialogs, settings, state)

    changed, queue, approved = handle_updates(state, stations, by_id, ctx)
    changed.update({k: d for k, d in ctx.changed_drafts.items() if "_row" in d})  # /sent
    bounce_stop(ctx, by_id, today.isoformat())
    auto, paused = ctx.mode() == "auto", ctx.paused()

    new = []
    for d in ctx.new_drafts:  # /note: черновик ответа после ответа продавца в WhatsApp/Viber
        present(d, stations[d["id станции"]], auto, queue)
        new.append(d)
    if not paused:  # на паузе новые письма не готовим, чтобы не копить кнопки
        drafted = {d["id станции"] for d in by_id.values()}
        real = [s for s in stations.values()
                if s["статус переписки"] == "новая" and s["приоритет"] != "-" and s["id"] not in drafted]
        test = sorted((s for s in stations.values() if s["приоритет"] == "-" and s["id"] not in drafted),
                      key=lambda s: -float(s["скоринг"] or 0))[:test_n]
        for st in real + test:
            d = make_draft(st, f"D{len(by_id) + len(new) + 1:03d}", st in test)
            if st not in test:
                st["статус переписки"] = "черновик"
            present(d, st, auto, queue)
            new.append(d)
        for d in followup.due(stations, by_id, dialogs + ctx.new_dialogs, cfg["limits"]["followup_days"], today):
            d["id"] = f"D{len(by_id) + len(new) + 1:03d}"
            present(d, stations[d["id станции"]], auto, queue)
            new.append(d)
    by_id.update({d["id"]: d for d in new})

    sending = [] if paused else to_send(by_id)
    t = today.isoformat()
    first_today = sum(d.get("отправлено") == t and d["тип"].startswith("первое") for d in by_id.values())
    left = {"run": cfg["limits"]["outreach_per_run"], "first": cfg["limits"]["new_per_day"] - first_today}
    out_dialogs = send_approved(api, sid, sending, by_id, stations, cfg, left, quiet=auto)
    changed.update({d["id"]: d for d in sending if "_row" in d})  # новые строки уйдут через append

    if owner.digest_due(ctx):
        notify.urgent(owner.digest(ctx, today))  # Telegram, при сбое письмом [SOLAR]
        state["дайджест"] = owner.local_now(cfg).date().isoformat()

    sheets.append(api, sid, "Диалоги", ctx.new_dialogs + out_dialogs)
    sheets.append(api, sid, "Отказы", ctx.new_refusals)
    sheets.update(api, sid, "Черновики", list(changed.values()))
    sheets.append(api, sid, "Черновики", new)
    sheets.append(api, sid, "Ручная очередь", queue)
    sheets.append(api, sid, "Чёрный список", ctx.blacklist_add)
    sheets.update(api, sid, "Станции", [st for k, st in stations.items() if st != before[k]])
    sheets.set_kv(api, sid, "_state", {"tg_offset": state["tg_offset"], "pending_edit": state["pending_edit"],
                                       "дайджест": state.get("дайджест", ""), "ошибок подряд": 0,
                                       "последний запуск": f"{owner.local_now(cfg):%Y-%m-%d %H:%M}"})
    if approved:
        settings["одобрено писем"] = int(settings.get("одобрено писем") or 0) + approved
        total = settings["одобрено писем"]
        if total >= cfg["limits"]["approvals_before_auto_offer"] > total - approved and not auto:
            notify.send(f"{total} писем одобрено. Включить автоотправку? Пришли /auto on (выключить: /auto off).")
    sheets.set_kv(api, sid, "Настройки", {k: v for k, v in settings.items() if settings_before.get(k) != v})
    if changed or new or queue:
        print(f"обновлений черновиков: {len(changed)}, новых черновиков: {len(new)}, в ручную очередь: {len(queue)}")
    return int(state["tg_offset"] or 0)


if __name__ == "__main__":
    n = sys.argv[sys.argv.index("--test") + 1] if "--test" in sys.argv else "0"
    with single_run("sheet"):
        main(int(n))
