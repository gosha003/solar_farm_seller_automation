"""Inbox step of the loop: incoming mail in SolarBG threads -> classification -> facts into the sheet -> action.

Actions:
  info_provided / question_we_can_answer -> reply draft (missing checklist items) for approval
  price_discussion ... suspicious, other, low confidence -> URGENT to the owner, dialog paused
  not_interested_or_stop / sold -> «Отказы» tab (never written to automatically again), dialog closed
  auto_reply -> nothing; broker_redirect -> the new contact becomes a new seller; bounce -> blacklist + counter
"""
import re
from datetime import date
from email.utils import parseaddr

from solar_outreach import drafts, drive, gmail_client as gmail, llm, notify, rules, sheets

ESCALATE = {"price_discussion", "counteroffer", "wants_call_or_meeting", "asks_who_are_you", "asks_for_docs_or_nda",
            "asks_money", "suspicious", "other"}
CLOSE = {"not_interested_or_stop": "отказ", "sold": "отказ"}
# Independent of the LLM: obvious prompt-injection / phishing attempts -> suspicious, even if the model "agreed".
INJECTION = re.compile(
    r"ignore (all |any |the )?(previous|prior|above) (instructions|rules|prompts?)|disregard (all |the )?(previous|above)"
    r"|system prompt|you are (an? )?(ai|bot|language model|assistant)|as an ai\b|send (me )?(your|the) (password|login|"
    r"bank|card|passport|data)|игнорир\w* (все |всички )?(предыдущ|предишн|правил|инструкц)|забудь\w* (все )?(правил|инструкц)"
    r"|системн\w* (промпт|инструкц)|изпратете (ни )?(парола|данни за карта)|перейд\w* по ссылк|кликн\w* (на|по) (линк|ссылк)",
    re.I)
OWNER_LED = "ведёт владелец"  # the owner runs this conversation: the bot records the reply and notifies, but never answers
ALO = re.compile(r"@([\w-]+\.)*alo\.bg$")  # alo.bg notifications about seller replies (they can only be answered on the site)
ALO_NOTICE = re.compile(r"ново съобщение|new message", re.I)  # the alo email has only a link; the reply text is in the alo.bg account
GREETING = {"bg": "Здравейте,", "en": "Hello,", "ru": "Здравствуйте,"}


def blacklisted(contact: str, blacklist: list[dict]) -> bool:
    c = contact.lower().strip()
    domain = c.split("@")[-1] if "@" in c else ""
    entries = {b["контакт или домен"].lower().strip() for b in blacklist}
    return c in entries or bool(domain) and domain in entries


def station_by_ad(stations: dict, text: str) -> dict | None:
    """Station by the listing number in the email text: alo/bazar links end with the number."""
    ids = set(re.findall(r"\d{7,9}", text))
    hits = [s for s in stations.values() if ids & set(re.findall(r"\d{7,9}$", s["ссылка"].rstrip("/").split(".html")[0]))]
    if len(hits) != 1:  # no number in the email: the listing title (alo puts it in the dialog subject), then the seller name
        low = text.lower()
        waiting = [s for s in stations.values() if s.get("статус переписки") == "ждём ответа"]
        for field in ("заголовок", "продавец"):
            hits = [s for s in waiting if len(s.get(field, "")) > 8 and s[field].lower() in low]
            if len(hits) == 1:
                break
    return hits[0] if len(hits) == 1 else None


def thread_url(thread_id: str) -> str:
    return f"https://mail.google.com/mail/u/0/#all/{thread_id}"


def decide(category: str, confidence: str, body: str) -> str:
    """Final category: a regex injection hit beats the LLM; low confidence = other (escalation)."""
    if INJECTION.search(body):
        return "suspicious"
    if confidence == "low" and category not in ESCALATE | set(CLOSE) | {"auto_reply"}:
        return "other"
    return category


def apply_facts(st: dict, c: llm.Classification) -> list[str]:
    changed = []
    for f in c.facts:
        st[f.item] = "отказ ответить" if f.refused else f"получен: {f.answer}"
        changed.append(f.item)
    return changed


def missing_items(st: dict) -> list[str]:
    return [i for i in sheets.ITEMS if not str(st.get(i, "")).startswith(("получен", "отказ"))]


def reply_body(st: dict, c: llm.Classification) -> str:
    """Reply to the seller: answers to their questions (buyer facts only) + the missing checklist items."""
    lang = c.language
    para = llm.answer_questions(c.seller_questions, lang).paragraph.strip() if c.seller_questions else ""
    body = drafts.second(st, lang)
    return f"{GREETING[lang]}\n\n{para}\n\n{body}" if para else body


def manual_reply_draft(st: dict, c: llm.Classification, did: str) -> dict:
    """/note: the seller answered on WhatsApp/Viber/the owner's mailbox; the owner replies personally («Ручная очередь»)."""
    lang, body = c.language, reply_body(st, c)
    channel = "email (вручную)" if st["канал"] == "email" else st["канал"]
    to = st.get("телефон") if channel.startswith("телефон") else st.get("email") if "email" in channel else st["ссылка"]
    return {"id": did, "id станции": st["id"], "дата": date.today().isoformat(), "язык": lang, "тип": "ответ",
            "канал": channel, "кому": to, "тема": "", "текст": body, "статус": "на одобрении",
            "нарушения правил": ", ".join(rules.violations(body, drafts.template(lang)["signature"]))}


def reply_draft(st: dict, msg: dict, c: llm.Classification, did: str) -> dict:
    lang = c.language
    body = reply_body(st, c)
    subject = gmail.header(msg, "Subject")
    subject = subject if subject.lower().startswith("re:") else f"Re: {subject}"
    return {"id": did, "id станции": st["id"], "дата": date.today().isoformat(), "язык": lang, "тип": "ответ",
            "канал": "email", "кому": parseaddr(gmail.header(msg, "From"))[1], "тема": subject, "текст": body,
            "статус": "на одобрении", "тред Gmail": msg["threadId"], "in-reply-to": gmail.header(msg, "Message-ID"),
            "нарушения правил": ", ".join(rules.violations(body, drafts.template(lang)["signature"]))}


def save_files(st: dict | None, msg: dict, svc) -> list[str]:
    """Attachments to Drive (station folder). A Drive failure doesn't break parsing the email: then names only."""
    names = gmail.attachments(msg)
    if not names or not st:
        return names
    try:
        return drive.save(drive.service(), svc, msg, st["id"])
    except Exception as e:
        return [f"{n} (Drive: {type(e).__name__})" for n in names]


def process(api, sid, svc, auto: bool = False) -> dict:
    """Process all new incoming mail. auto (/auto on): a reply draft without rule violations approves itself;
    escalations are unaffected. Returns counters for the log/digest."""
    stations = {r["id"]: r for r in sheets.read(api, sid, "Станции")}
    dialogs = sheets.read(api, sid, "Диалоги")
    blacklist = sheets.read(api, sid, "Чёрный список")
    n_drafts = len(sheets.read(api, sid, "Черновики"))
    by_thread = {d["тред Gmail"]: d["id станции"] for d in dialogs if d["тред Gmail"]}
    by_email = {e.strip().lower(): s["id"] for s in stations.values() for e in s["email"].split(",") if e.strip()}
    before = {k: dict(v) for k, v in stations.items()}
    new_dialogs, new_drafts, new_black, new_stations, new_refusals = [], [], [], [], []
    stats = {"входящих": 0, "эскалаций": 0, "черновиков": 0, "закрыто": 0, "bounce": 0}

    gmail.adopt(svc, list(by_email))
    for msg in gmail.incoming(svc):
        stats["входящих"] += 1
        frm = parseaddr(gmail.header(msg, "From"))[1].lower()
        st = stations.get(by_thread.get(msg["threadId"], "")) or stations.get(by_email.get(frm, ""))
        if gmail.is_bounce(msg):
            stats["bounce"] += 1
            if st:
                for e in filter(None, (x.strip() for x in st["email"].split(","))):
                    new_black.append({"контакт или домен": e, "причина": "bounce", "дата": date.today().isoformat()})
                st["статус переписки"] = "отказ"
                st["примечание"] = (st["примечание"] + "; bounce").strip("; ")
            gmail.mark_done(svc, msg)
            continue
        if blacklisted(frm, blacklist + new_black):
            gmail.mark_done(svc, msg)
            continue

        body = gmail.text(msg)
        if not st and ALO.search(frm):
            st = station_by_ad(stations, f"{gmail.header(msg, 'Subject')} {body}")
        if ALO.search(frm) and (st is None or ALO_NOTICE.search(body)):
            if ALO_NOTICE.search(body):
                where = st["id"] if st else "станцию не определил"
                notify.send(f"📨 Ответ на alo.bg ({where}), в письме текста нет. Читаю в кабинете alo: нужен включённый ПК "
                            "с открытым Chrome. Если не прочитается, открой https://www.alo.bg/user_messages.php сам.")
                if st:
                    st["статус переписки"], st["следующий шаг"] = "ответ на alo", "прочитать в кабинете alo (alo_reply.py)"
                    new_dialogs.append({"id станции": st["id"], "направление": "in", "дата": date.today().isoformat(),
                                        "канал": "alo.bg", "язык": "", "краткое содержание": "[ответ на alo] уведомление без текста",
                                        "тред Gmail": msg["threadId"]})
            gmail.mark_done(svc, msg)  # anything else from alo (sign-up, Google sign-in) is not a dialog
            continue
        files = save_files(st, msg, svc)
        try:
            c = llm.classify(body, st or {})
            category = decide(c.category, c.confidence, body)
        except llm.LLMError as e:
            c, category = None, "other"
        sid_ = st["id"] if st else "?"
        summary = c.summary_ru if c else body[:200]
        new_dialogs.append({"id станции": sid_, "направление": "in", "дата": date.today().isoformat(), "канал": "email",
                            "язык": c.language if c else "", "краткое содержание": f"[{category}] {summary}"
                            + (f" Вложения: {', '.join(files)}" if files else ""), "тред Gmail": msg["threadId"]})
        if st and c:
            apply_facts(st, c)
            st["последний контакт"] = date.today().isoformat()
        if st and st["статус переписки"] == OWNER_LED:
            notify.urgent(f"📩 {sid_} · {category} (переписку ведёшь ты, бот не отвечает)\nОт: {frm}\n{summary}\n"
                          f"Тред: {thread_url(msg['threadId'])}")
            gmail.mark_done(svc, msg)
            continue

        if category in ESCALATE or not st:
            stats["эскалаций"] += 1
            if st:
                st["статус переписки"], st["следующий шаг"] = "эскалация", f"решение владельца: {category}"
            notify.urgent(f"🚨 {sid_} · {category}\nОт: {frm}\n{summary}\n"
                          + (f"Вложения (не открывались):\n" + "\n".join(files) + "\n" if files else "")
                          + f"Тред: {thread_url(msg['threadId'])}\nДиалог на паузе до твоего решения.")
        elif category in CLOSE:
            stats["закрыто"] += 1
            new_refusals.append({"контакт": frm, "id станции": sid_, "причина": "продано" if category == "sold" else "отказ",
                                 "дата": date.today().isoformat(), "что написал": summary})
            st["статус переписки"], st["следующий шаг"] = CLOSE[category], "не писать автоматически"
        elif category == "broker_redirect" and c.new_contact:
            new_stations.append({**{k: v for k, v in st.items() if k != "_row"}, "id": "", "email": c.new_contact
                                 if "@" in c.new_contact else "", "телефон": "" if "@" in c.new_contact else c.new_contact,
                                 "статус переписки": "новая", "примечание": f"по рекомендации от {sid_}"})
            st["статус переписки"] = "перенаправлено"
        elif category in ("info_provided", "question_we_can_answer"):
            if not missing_items(st):
                st["статус переписки"] = "квалифицирована"
                notify.urgent(f"✅ {sid_} полностью квалифицирована: все пункты раздела 3 получены.\n{st['ссылка']}")
            else:
                manual = bool(ALO.search(frm))  # noreply@alo.bg can't be answered: the text goes to the owner, who replies on the site
                try:
                    did = f"D{n_drafts + len(new_drafts) + 1:03d}"
                    d = manual_reply_draft(st, c, did) if manual else reply_draft(st, msg, c, did)
                except llm.LLMError as e:
                    notify.urgent(f"⚠️ {sid_}: не смог составить ответ ({e}). Тред: {thread_url(msg['threadId'])}")
                else:
                    if auto and not d["нарушения правил"] and not manual:
                        d["статус"] = "одобрено"  # only the owner switches the mode (/auto on); handled in approval.main
                    else:
                        d["tg сообщение"] = notify.send(_card(d, st, summary), notify.draft_buttons(d["id"]))
                    new_drafts.append(d)
                    stats["черновиков"] += 1
                    st["статус переписки"] = "черновик ответа"
        gmail.mark_done(svc, msg)

    if new_stations:
        last = max([int(re.sub(r"\D", "", s) or 0) for s in stations] + [0])
        for i, s in enumerate(new_stations, last + 1):
            s["id"] = f"S{i:03d}"
    sheets.update(api, sid, "Станции", [s for k, s in stations.items() if s != before[k]])
    sheets.append(api, sid, "Станции", new_stations)
    sheets.append(api, sid, "Диалоги", new_dialogs)
    sheets.append(api, sid, "Черновики", new_drafts)
    sheets.append(api, sid, "Чёрный список", new_black)
    sheets.append(api, sid, "Отказы", new_refusals)
    if stats["bounce"]:
        state = sheets.kv(api, sid, "_state")
        key = f"bounce {date.today().isoformat()}"
        sheets.set_kv(api, sid, "_state", {key: int(state.get(key, {}).get("значение") or 0) + stats["bounce"]})
    return stats


def _card(d: dict, st: dict, summary: str) -> str:
    warn = f"\n⚠️ Нарушения правил: {d['нарушения правил']}" if d["нарушения правил"] else ""
    return (f"↩️ {d['id']} · {st['id']} · ответ ({d['язык']})\nПродавец написал: {summary}\nКому: {d['кому']}{warn}"
            f"\n\nТема: {d['тема']}\n\n{d['текст']}")
