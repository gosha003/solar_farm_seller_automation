"""Telegram (buttons and commands) -> drafts (first letters, follow-ups) -> sending within limits -> digest.

    python -m solar_outreach.approval            # process the owner's taps/edits, create drafts for new stations
    python -m solar_outreach.approval --test 9   # plus 9 test drafts for small stations (never sent)

State between runs lives in «_state»: tg_offset, pending_edit, digest, errors.
«Настройки»: approval_mode (manual|auto, changed by /auto), pause (/pause), approved counter.
"""
import os
import random
import sys
import time
from datetime import date, timedelta

from solar_outreach import (brief, drafts, followup, gmail_client as gmail, inbox, llm, notify, owner, rules, sheets,
                            site_queue)
from solar_outreach.config import dry_run, load_config, single_run
from solar_outreach.notify import b, code, esc, head, i, join, link, quote

WAITING = ("на одобрении", "ждёт правки")
PAUSED = inbox.PAUSED  # hard rules: nothing goes out until the owner decides
UNSENT = ("", "вручную", "DRY_RUN")
INTERNAL = ("_row", "примечание", "отбор", "скоринг", "приоритет", "следующий шаг")  # buyer-side columns, never in a prompt


def contact(st: dict) -> str:
    return st["email"] if st["канал"] == "email" else st["телефон"] if st["канал"].startswith("телефон") else st["ссылка"]


def make_draft(st: dict, did: str, test: bool) -> dict:
    lang = "bg" if drafts.is_general(st) else drafts.language(st["заголовок"])  # an agency's language can't be told from its name
    subject, body = drafts.first(st, lang)
    kind = "первое (брокер)" if drafts.is_broker(st) else "первое"
    return {"id": did, "id станции": st["id"], "дата": date.today().isoformat(), "язык": lang,
            "тип": f"{kind}, ТЕСТ" if test else kind, "канал": st["канал"], "кому": contact(st), "тема": subject,
            "текст": body, "статус": "на одобрении",
            "нарушения правил": ", ".join(rules.violations(f"{subject}\n{body}", drafts.template(lang)["signature"]))}


def card(d: dict, st: dict) -> str:
    facts = (f"{notify.num(st['мощность кВт'])} кВт · {notify.num(st['цена EUR'])} € · {notify.num(st['EUR/кВт'])} €/кВт · "
             f"{st['регион']}")
    return join(head("📝", "Черновик", d["id станции"], d["id"], f"{d['тип']} ({d['язык']})"),
                "\n".join([b(st["заголовок"][:90]), esc(facts), f"Канал: {esc(d['канал'])} · кому: {esc(d['кому'])}",
                           link(st["ссылка"], "объявление")]),
                # price band flags («только при хороших условиях», «подозрительно дешево»)
                f"⚑ {esc(st['примечание'])}" if st.get("примечание") else "",
                f"{b('ТЕСТ шаблона:')} этому продавцу писать не будем" if "ТЕСТ" in d["тип"] else "",
                f"⚠️ {b('Нарушения правил раздела 4:')} {esc(d['нарушения правил'])}" if d.get("нарушения правил") else "",
                (f"<b>Тема:</b> {esc(d['тема'])}\n" if d.get("тема") else "") + quote(d["текст"]))


def queue_row(d: dict, st: dict) -> dict:
    email = "email" in d["канал"]
    row = {"id станции": d["id станции"], "канал": f"{d['канал']}: {d['кому']}" if email else d["канал"],
           "ссылка на объявление": st["ссылка"], "статус": "отправить вручную",
           "текст": f"Тема: {d['тема']}\n\n{d['текст']}" if email and d.get("тема") else d["текст"]}
    if d["канал"].startswith("телефон"):
        row["WhatsApp"], row["Viber"] = drafts.manual_links(d["кому"].split(",")[0], d["текст"])
    return row


def approve(d: dict, st: dict, queue: list) -> None:
    """Approval (button or auto mode): email goes out on the next send, everything else goes to «Ручная очередь».
    DRY_RUN: email goes to the queue too and the owner sends it by hand; the «вручную» mark stops the system sending it later.
    An old card tapped after an escalation or refusal doesn't lift the pause: send_approved and the routine still skip it."""
    d["статус"] = "одобрено"
    if "ТЕСТ" not in d["тип"]:
        if d["канал"] != "email" or dry_run():
            queue.append(queue_row(d, st))
            if d["канал"] == "email":
                d["отправлено"] = "вручную"
        if st["статус переписки"] not in PAUSED:
            st["статус переписки"] = "одобрено, ждёт отправки"


def block_lists(api, sid) -> tuple[list, list]:
    return sheets.read(api, sid, "Чёрный список"), sheets.read(api, sid, "Отказы")


def gate(api, sid, queue: list, stations: dict) -> None:
    """«Ручная очередь» rows are sent by a routine without the owner, so a station whose contact is in «Отказы» or
    «Чёрный список» never gets the «отправить вручную» status; the row stays visible for the owner."""
    if not queue:
        return
    black, refused = block_lists(api, sid)
    for r in queue:
        why = inbox.blocked(stations.get(r["id станции"], {}), black, refused)
        if why:
            r["статус"] = f"не отправлять: контакт {why}"
            notify.try_send(join(head("⛔", "Не ставлю в очередь", r["id станции"]),
                                 f"Контакт {esc(why)}, в ручную очередь на отправку не ставлю."))


def cancel_followups(api, sid, by_id: dict, dialogs: list[dict]) -> dict:
    """A follow-up drafted before the seller answered never goes out: the draft and its «Ручная очередь» row are cancelled.
    Returns the cancelled drafts that are sheet rows."""
    answered = followup.last_reply(dialogs)  # an answer the follow-up was drafted after doesn't cancel it
    # a seller who refused, sold or wrote something suspicious gets no document request either, even in a conversation
    # the owner leads (inbox records such a reply there but leaves the status to the owner)
    ins = {}
    for r in dialogs:
        if r["направление"] == "in":
            ins[r["id станции"]] = r["краткое содержание"]
    closing = {k for k, text in ins.items() if text.startswith(brief.CLOSING)}
    unsent = lambda d: d["статус"] in WAITING or d["статус"] == "одобрено" and d.get("отправлено", "") in UNSENT
    stale = {d["id"]: d for d in by_id.values() if unsent(d) and (
        d["тип"].startswith("дожим") and d["id станции"] in answered and answered[d["id станции"]] >= d.get("дата", "")
        or d["тип"].startswith(brief.DOC_REQUEST) and d["id станции"] in closing)}
    for d in stale.values():
        d["статус"] = followup.CANCELLED
    if stale:
        texts = {(d["id станции"], d["текст"]) for d in stale.values()}
        rows = [r for r in sheets.read(api, sid, "Ручная очередь") if r["статус"] == "отправить вручную"
                and any(s == r["id станции"] and t in r["текст"] for s, t in texts)]
        sheets.update(api, sid, "Ручная очередь", [{**r, "статус": "отменено: продавец ответил"} for r in rows])
    return {k: d for k, d in stale.items() if "_row" in d}


def by_routine(d: dict, st: dict) -> bool:
    """alo.bg and WhatsApp rows are sent by the routine on the owner's PC: telling the owner to send them too doubles them."""
    return d["канал"] != "email" and bool(site_queue.kind(queue_row(d, st)))


ROUTINE = "уйдёт с ПК по расписанию (alo.bg/WhatsApp), сам не отправляй"


def site_hint(d: dict, st: dict) -> None:
    """«сайт» channel (alo.bg): link and plain text as two messages, so the text copies with one tap.
    The text is only escaped, no markup: what the owner copies is exactly the letter."""
    if d["канал"].startswith("сайт") and "ТЕСТ" not in d["тип"]:
        notify.send(join(head("📋", "Отправь на сайте", d["id станции"], d["id"]),
                         f"Открой {link(st['ссылка'], 'объявление')}, вставь текст из следующего сообщения."))
        notify.send(esc(d["текст"]))


def present(d: dict, st: dict, auto: bool, queue: list) -> None:
    """New draft: in auto mode it approves itself if it breaks no rules, otherwise the owner gets a card with buttons."""
    if auto and not d["нарушения правил"] and "ТЕСТ" not in d["тип"]:
        approve(d, st, queue)
        if d["канал"] != "email" and by_routine(d, st):
            notify.send(join(head("📱", "Одобрено автоматически", d["id станции"], d["id"]), f"Сообщение {ROUTINE}."))
        elif d["канал"] != "email":
            notify.send(join(head("📱", "Отправь вручную", d["id станции"], d["id"]),
                             "Текст и ссылки во вкладке «Ручная очередь».",
                             f"Отправишь сам: {code('/sent ' + d['id станции'])}"))
            site_hint(d, st)
    else:
        d["tg сообщение"] = notify.send(card(d, st), notify.draft_buttons(d["id"]))


def handle_updates(state, stations, by_id, ctx: "owner.Ctx | None" = None) -> tuple[dict, list, int]:
    """Returns (changed drafts, manual-queue rows, number of new approvals).
    An update that raises is reported and skipped: the offset has already moved past it, so it can't block later
    taps and commands (/pause included) on every pass."""
    offset = int(state.get("tg_offset") or 0)
    pending = state.get("pending_edit") or ""
    changed, queue, approved = {}, [], 0
    for u in notify.updates(offset):
        offset = u["update_id"] + 1
        if not u["_owner"]:
            continue
        cq, msg = u.get("callback_query"), u.get("message") or {}
        try:
            if cq:
                action, _, did = (cq.get("data") or "").partition(":")
                if action in ("b", "bx") and ctx:  # a director brief (brief.py), not a draft: «b:<row>:<station>»
                    notify.answer_callback(cq["id"])
                    mid = (cq.get("message") or {}).get("message_id")
                    notify.send(brief.approve(ctx.api, ctx.sid, did, date.today(), mid) if action == "b"
                                else brief.skip(ctx.api, ctx.sid, did, mid))
                    continue
                d = by_id.get(did)
                st = stations.get(d["id станции"]) if d else None
                if not d or not st or d["статус"] not in WAITING:
                    notify.answer_callback(cq["id"], "Уже обработан")
                    continue
                changed[did] = d
                newer = d["тип"] == "ответ" and any(  # a newer reply already answers more: the old card can't overtake it
                    x["id станции"] == d["id станции"] and x["тип"] == "ответ" and x["статус"] in ("одобрено", "отправлено")
                    and x.get("_row", 10 ** 9) > d.get("_row", 0) for x in by_id.values())
                if action == "a" and newer:
                    d["статус"] = "заменено новым ответом"
                    notify.answer_callback(cq["id"], "Уже есть более новый ответ")
                    notify.send(join(head("↩️", "Есть ответ новее", d["id станции"], did),
                                     f"У {esc(d['id станции'])} уже есть более новый ответ, этот не отправляю."))
                elif action == "a":
                    approve(d, st, queue)
                    approved += 1
                    notify.answer_callback(cq["id"], "Одобрено")
                    routine = "ТЕСТ" not in d["тип"] and by_routine(d, st)
                    notify.send(join(head("✅", "Одобрено", d["id станции"], did),
                                     "Тест: никуда не отправляется." if "ТЕСТ" in d["тип"] else
                                     "Письмо уйдёт в ближайшем запуске." if d["канал"] == "email" and not dry_run()
                                     else f"Сообщение {ROUTINE}." if routine
                                     else "Текст во вкладке «Ручная очередь».\n"
                                          f"Отправишь сам: {code('/sent ' + d['id станции'])}"))
                    if not routine:
                        site_hint(d, st)
                elif action == "s":
                    d["статус"] = "пропущено"
                    if "ТЕСТ" not in d["тип"] and not d["тип"].startswith(brief.DOC_REQUEST):  # a skipped request only drops itself
                        st["статус переписки"] = "пропущено владельцем"
                    notify.answer_callback(cq["id"], "Пропущено")
                elif action == "e":
                    d["статус"], pending = "ждёт правки", did
                    notify.answer_callback(cq["id"])
                    notify.send(join(head("✏️", "Правка", d["id станции"], did), "Пришли одним сообщением, что поменять."))
            elif msg.get("text") and pending and not msg["text"].startswith("/"):
                d, pending = by_id.get(pending), ""
                st = stations.get(d["id станции"]) if d else None
                if not d or not st:
                    notify.send(join(head("⚠️", "Правка не применена"),
                                     "Черновик для правки не нашёл в таблице, правку не применяю."))
                    continue
                changed[d["id"]] = d
                examples = [x["текст"] for x in by_id.values() if x["статус"] in ("одобрено", "отправлено")
                            and x.get("правка владельца") and "ТЕСТ" not in x["тип"]]
                try:
                    new = llm.rewrite(d["тема"], d["текст"], msg["text"], {k: v for k, v in st.items() if k not in INTERNAL},
                                      d["язык"], examples)
                except llm.LLMError as e:
                    d["статус"] = "на одобрении"
                    notify.send(join(head("⚠️", "Не смог переписать", d["id станции"], d["id"]),
                                     f"Ошибка: {i(e)}", "Черновик прежний, кнопки ниже."), notify.draft_buttons(d["id"]))
                    continue
                sig = drafts.template(d["язык"])["signature"]
                d.update({"тема": new.subject, "текст": new.body, "статус": "на одобрении", "правка владельца": msg["text"],
                          "нарушения правил": ", ".join(rules.violations(f"{new.subject}\n{new.body}", sig))})
                d["tg сообщение"] = notify.send(card(d, st), notify.draft_buttons(d["id"]))
            elif msg.get("text", "").startswith("/"):
                notify.send(owner.command(msg["text"], ctx, date.today()) if ctx else owner.HELP)
        except Exception as e:
            print(f"telegram: обновление {u['update_id']} упало: {type(e).__name__}: {str(e)[:200]}", flush=True)
            notify.try_send(join(head("⚠️", "Не смог выполнить"),
                                 f"Ошибка: {i(f'{type(e).__name__}: {str(e)[:100]}')}", "Пропускаю."))
    state["tg_offset"], state["pending_edit"] = offset, pending
    return changed, queue, approved


def superseded(by_id: dict) -> dict:
    """Approved, unsent email replies that a newer reply to the same station replaces (e.g. piled up during /pause):
    only the newest goes out, it already answers everything the older ones asked."""
    newest = {d["id станции"]: k for k, d in by_id.items()
              if d["тип"] == "ответ" and d["канал"] == "email" and d["статус"] in ("одобрено", "отправлено")}
    return {k: d for k, d in by_id.items() if d["тип"] == "ответ" and d["канал"] == "email" and d["статус"] == "одобрено"
            and not d.get("отправлено") and newest.get(d["id станции"]) != k}


def to_send(by_id: dict) -> list[dict]:
    """Approved email drafts not sent yet. Those marked DRY_RUN wait until DRY_RUN is turned off."""
    pending = ("",) if dry_run() else ("", "DRY_RUN")
    return [d for d in by_id.values() if d["статус"] == "одобрено" and d["канал"] == "email"
            and "ТЕСТ" not in d["тип"] and d.get("отправлено", "") in pending]


def send_approved(api, sid, ds: list[dict], by_id: dict, stations: dict, cfg: dict,
                  left: dict | None = None, quiet: bool = False) -> list[dict]:
    """Send through Gmail with a 60-180 s pause. Before each send the draft is saved as «отправляется»: whatever fails
    after that (a Sheets error, a restart, an OOM kill), no pass picks it up again, so a letter never goes out twice
    (a draft stuck in «отправляется» shows in the digest). After the send: draft, station and «Диалоги» row.
    Returns the letters' «Диалоги» rows («_saved»: already in the sheet).
    left = {"run": first letters and follow-ups for this run, "first": first letters left under the daily cap};
    whatever doesn't fit stays «одобрено» and goes out in later runs. Replies inside dialogs are unlimited."""
    left = left if left is not None else {"run": len(ds), "first": len(ds)}
    if not ds:
        return []
    svc = gmail.service()
    blacklist, refused = block_lists(api, sid)
    def addrs(x: dict) -> set[str]:
        return {a.strip().lower() for a in x["кому"].split(",") if "@" in a}
    # every address of a first letter that went out, by Gmail or by the owner's hand (/sent), not the whole «кому» cell
    sent_first = set().union(*(addrs(x) for x in by_id.values() if x["тип"].startswith("первое")
                               and (x.get("тред Gmail") or x.get("отправлено", "")[:4].isdigit())))
    out = []
    for d in ds:
        to = d["кому"].split(",")[0].strip()
        st = stations.get(d["id станции"])
        if not st or "@" not in to:
            d["статус"] = "нет адреса"
            notify.try_send(join(head("⛔", "Нет адреса", d["id станции"], d["id"]),
                                 f"Нет адреса получателя («{esc(to)}»), не отправлено.",
                                 "Исправь «кому» и верни «одобрено»."))
            continue
        why = "в чёрном списке" if inbox.blacklisted(to, blacklist) else inbox.blocked(st, blacklist, refused)
        if why:
            d["статус"] = "в отказах" if "Отказ" in why else "чёрный список"
            notify.try_send(join(head("⛔", "Не отправляю", d["id станции"], d["id"]),
                                 f"{esc(to)} {esc(why)} (просил не писать, продано или bounce), "
                                 "автоматически не отправляю."))
            continue
        # into a conversation the owner leads only a document request he approved, or one the director asked for
        owner_led = st["статус переписки"] == inbox.OWNER_LED and brief.cleared(d)
        if st["статус переписки"] in PAUSED and not owner_led:
            d["статус"] = "на паузе"
            notify.try_send(join(head("⏸", "Диалог на паузе", d["id станции"], d["id"]),
                                 f"Статус: {esc(st['статус переписки'])}, не отправляю.",
                                 "Реши эскалацию, потом пришли правку или одобри заново."))
            continue
        if d["тип"].startswith("первое") and addrs(d) & sent_first:
            d["статус"] = "дубль адреса"
            notify.try_send(join(head("⛔", "Дубль адреса", d["id станции"], d["id"]),
                                 f"На {esc(to)} уже уходило первое письмо, второе не отправляю."))
            continue
        first, outreach = d["тип"].startswith("первое"), d["тип"].startswith(("первое", "дожим"))
        if outreach and (left["run"] <= 0 or first and left["first"] <= 0):
            continue
        if out and not dry_run():
            time.sleep(random.uniform(*cfg["limits"]["send_pause_sec"]))
        claim = "_row" in d and not dry_run()
        if claim:  # saved before the send: if anything dies after it, no pass picks this draft up again
            d["статус"] = "отправляется"
            sheets.update(api, sid, "Черновики", [d])
        try:
            res = gmail.send(svc, to, d["тема"], d["текст"], d.get("тред Gmail", ""), d.get("in-reply-to", ""))
        except gmail.NotSent as e:  # Gmail never got it (labels, token): back to «одобрено», the next pass retries
            d["статус"] = "одобрено"
            if claim:
                sheets.update(api, sid, "Черновики", [d])
            print(f"{d['id']}: не отправлено, повторю в следующий раз ({e})", flush=True)
            break
        except Exception as e:  # a bad address, Gmail quota: this draft stops here, the others still go out
            d["статус"] = "ошибка отправки"  # not «одобрено»: after a timeout the letter may have gone out, never resend it
            if "_row" in d:
                sheets.update(api, sid, "Черновики", [d])
            notify.try_send(join(head("⚠️", "Gmail не принял письмо", d["id станции"], d["id"]),
                                 f"Кому: {esc(to)}\nОшибка: {i(f'{type(e).__name__}: {str(e)[:120]}')}",
                                 "Проверь «кому» в «Черновиках» и верни «одобрено»."))
            continue
        if res.get("dry_run"):
            d["отправлено"] = "DRY_RUN"
            notify.try_send(join(head("🧪", "DRY_RUN", d["id станции"], d["id"]), f"Письмо на {esc(to)} не отправлено."))
            continue
        left["run"] -= outreach
        left["first"] -= first
        today = date.today().isoformat()
        d.update({"статус": "отправлено", "отправлено": today, "тред Gmail": res["threadId"]})
        if first:
            sent_first |= addrs(d)
        st["последний контакт"] = today
        if not owner_led:  # the owner's conversation stays his
            st.update({"статус переписки": "ждём ответа", "следующий шаг": "ждать ответа"})
        if not d["тип"].startswith(("дожим", brief.DOC_REQUEST)):
            sheets.mark_asked(st, first)
        row = {"id станции": d["id станции"], "направление": "out", "дата": today, "канал": "email",
               "язык": d["язык"], "краткое содержание": f"{d['тип']}: {d['тема']}", "тред Gmail": res["threadId"]}
        out.append(row)
        try:  # if these fail, main's end-of-pass writes retry them («_saved» marks what is already in the sheet)
            if "_row" in d:
                sheets.update(api, sid, "Черновики", [d])
            sheets.append(api, sid, "Диалоги", [row])
            row["_saved"] = True
            if "_row" in st:
                sheets.update(api, sid, "Станции", [st])
        except Exception as e:
            print(f"{d['id']}: письмо ушло, запись в таблицу отложена до конца прохода ({type(e).__name__})", flush=True)
        if not quiet:
            notify.try_send(join(head("📤", "Отправлено", d["id станции"], d["id"]), f"Кому: {esc(to)}"))
    return out


def bounce_stop(c: "owner.Ctx", by_id: dict, today: date) -> None:
    """Bounce share over the last two days above the threshold -> auto-sending turns off (back to manual), urgent alert.
    Only email can bounce, so only emails count, and two days so a bounce just after midnight isn't n/0. Letters auto mode
    had already approved go back to the owner as cards instead of trickling out one per pass."""
    days = {today.isoformat(), (today - timedelta(days=1)).isoformat()}
    bounces = sum(int(c.state.get(f"bounce {t}") or 0) for t in days)
    sent = sum(d.get("отправлено") in days and d.get("канал") == "email" for d in by_id.values())
    if c.mode() != "auto" or bounces / max(sent, 1) <= c.cfg["limits"]["bounce_rate_stop"]:
        return
    c.settings["approval_mode"] = "manual"
    held = [d for d in by_id.values() if d["статус"] == "одобрено" and d.get("канал") == "email" and not d.get("отправлено")
            and not d.get("tg сообщение") and "ТЕСТ" not in d["тип"] and d["id станции"] in c.stations]
    for d in held:
        d["статус"] = "на одобрении"
        d["tg сообщение"] = notify.send(card(d, c.stations[d["id станции"]]), notify.draft_buttons(d["id"]))
        c.changed_drafts[d["id"]] = d
    notify.urgent(join(head("🛑", "Автоотправка выключена", "режим manual"),
                       f"Bounce за двое суток: {bounces} на {sent} отправленных писем. Адреса в «Чёрном списке».",
                       f"{len(held)} одобренных автоматически писем вернул тебе на кнопки." if held else "",
                       f"{code('/auto on')} вернёт автоотправку."))


def main(test_n: int = 0, full: bool = True) -> int:
    """One pass. Returns the Telegram offset for the daemon's long polling.
    full=False: the daemon's pass without the inbox; it doesn't reset the failure counter, so a broken inbox still alerts."""
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
    bounce_stop(ctx, by_id, today)
    changed.update({k: d for k, d in ctx.changed_drafts.items() if "_row" in d})  # /sent, drafts bounce_stop took back
    changed.update(cancel_followups(api, sid, by_id, dialogs + ctx.new_dialogs))
    auto, paused = ctx.mode() == "auto", ctx.paused()

    def next_id() -> str:
        return sheets.next_id([*by_id, *(d["id"] for d in new)])

    new = []
    for d in ctx.new_drafts:  # /note: draft a reply after the seller answered on WhatsApp/Viber
        present(d, stations[d["id станции"]], auto, queue)
        new.append(d)
    if not paused:  # while paused, don't prepare new letters so buttons don't pile up
        drafted = {d["id станции"] for d in by_id.values()}
        real = [s for s in stations.values()
                if s["статус переписки"] == "новая" and s["приоритет"] != "-" and s["id"] not in drafted]
        test = sorted((s for s in stations.values() if s["приоритет"] == "-" and s["id"] not in drafted),
                      key=lambda s: -float(s["скоринг"] or 0))[:test_n]
        black, refused = block_lists(api, sid) if real else ([], [])
        for st in real + test:
            why = inbox.blocked(st, black, refused) if st in real else ""
            if why:  # the same seller refused or is blacklisted under another listing: no letter, the owner decides
                st["статус переписки"] = "не писать"
                st["примечание"] = "; ".join(filter(None, [st.get("примечание", ""), f"контакт {why}"]))
                notify.try_send(join(head("⛔", "Черновик не делаю", st["id"]), f"Контакт {esc(why)}."))
                continue
            d = make_draft(st, next_id(), st in test)
            if st not in test:
                st["статус переписки"] = "черновик"
            present(d, st, auto, queue)
            new.append(d)
        for d in followup.due(stations, by_id, dialogs + ctx.new_dialogs, cfg["limits"]["followup_days"], today):
            d["id"] = next_id()
            present(d, stations[d["id станции"]], auto, queue)
            new.append(d)
    by_id.update({d["id"]: d for d in new})
    for k, d in superseded(by_id).items():
        d["статус"] = "заменено новым ответом"
        if "_row" in d:
            changed[k] = d

    # a draft is sent only once it is a sheet row: send_approved then saves each letter the moment it goes out
    sending = [] if paused else [d for d in to_send(by_id) if "_row" in d]
    t = today.isoformat()
    first_today = sum(d.get("отправлено") == t and d["тип"].startswith("первое") for d in by_id.values())
    left = {"run": cfg["limits"]["outreach_per_run"], "first": cfg["limits"]["new_per_day"] - first_today}
    out_dialogs = send_approved(api, sid, sending, by_id, stations, cfg, left, quiet=auto)
    changed.update({d["id"]: d for d in sending if "_row" in d})  # new rows go out via append

    if owner.digest_due(ctx):
        notify.urgent(owner.digest(ctx, today))  # Telegram, falling back to a [SOLAR] email
        state["дайджест"] = owner.local_now(cfg).date().isoformat()

    gate(api, sid, queue, stations)
    sheets.append(api, sid, "Диалоги", ctx.new_dialogs + [r for r in out_dialogs if not r.get("_saved")])
    sheets.append(api, sid, "Отказы", ctx.new_refusals)
    sheets.update(api, sid, "Черновики", list(changed.values()))
    sheets.append(api, sid, "Черновики", new)
    sheets.append(api, sid, "Ручная очередь", queue)
    sheets.append(api, sid, "Чёрный список", ctx.blacklist_add)
    sheets.update(api, sid, "Станции", [st for k, st in stations.items() if st != before[k]])
    done = {"tg_offset": state["tg_offset"], "pending_edit": state["pending_edit"], "дайджест": state.get("дайджест", ""),
            "последний запуск": f"{owner.local_now(cfg):%Y-%m-%d %H:%M}"}
    if full:
        done["ошибок подряд"] = 0
    sheets.set_kv(api, sid, "_state", done)
    if approved:
        settings["одобрено писем"] = int(settings.get("одобрено писем") or 0) + approved
        total = settings["одобрено писем"]
        if total >= cfg["limits"]["approvals_before_auto_offer"] > total - approved and not auto:
            notify.send(join(head("🤖", "Автоотправка?"), f"{total} писем одобрено. Включить автоотправку?",
                             f"Пришли {code('/auto on')} (выключить: {code('/auto off')})."))
    sheets.set_kv(api, sid, "Настройки", {k: v for k, v in settings.items() if settings_before.get(k) != v})
    if changed or new or queue:
        print(f"обновлений черновиков: {len(changed)}, новых черновиков: {len(new)}, в ручную очередь: {len(queue)}")
    return int(state["tg_offset"] or 0)


if __name__ == "__main__":
    n = sys.argv[sys.argv.index("--test") + 1] if "--test" in sys.argv else "0"
    with single_run("sheet"):
        main(int(n))
