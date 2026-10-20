"""Inbox step of the loop: incoming mail in SolarBG threads -> classification -> facts into the sheet -> action.

Actions:
  info_provided / question_we_can_answer -> reply draft (missing checklist items) for approval
  price_discussion ... suspicious, other, low confidence -> URGENT to the owner, dialog paused
  not_interested_or_stop / sold -> «Отказы» tab (never written to automatically again), dialog closed
  auto_reply -> nothing; broker_redirect -> the new contact becomes a new seller; bounce -> blacklist + counter
A message to a paused dialog (escalation, refusal, skipped) is recorded and shown to the owner; the pause stays.
"""
import re
from datetime import date
from email.utils import parseaddr

from solar_outreach import drafts, drive, gmail_client as gmail, llm, notify, parse, rules, sheets
from solar_outreach.config import dry_run
from solar_outreach.contacts import channel

ESCALATE = {"price_discussion", "counteroffer", "wants_call_or_meeting", "asks_who_are_you", "asks_for_docs_or_nda",
            "asks_money", "suspicious", "other"}
CLOSE = {"not_interested_or_stop": "отказ", "sold": "отказ"}
# Independent of the LLM: obvious prompt-injection / phishing attempts -> suspicious, even if the model "agreed".
INJECTION = re.compile(
    r"ignore (all |any |the )?(previous|prior|above) (instructions|rules|prompts?)|disregard (all |the )?(previous|above)"
    r"|system prompt|you are (an? )?(ai|bot|language model|assistant)|as an ai\b|send (me |us )?(your|the) (password|login|"
    r"bank|card|passport)\b|игнорир\w* (все |всички )?(предыдущ|предишн|правил|инструкц)|забудь\w* (все )?(правил|инструкц)"
    r"|системн\w* (промпт|инструкц)|изпратете (ни )?(парола|данни за карта)|перейд\w* по ссылк|кликн\w* (на|по) (линк|ссылк)",
    re.I)
# "Are you a bot / a real person?" escalates even when the model files it as a question we can answer.
# Question-shaped on purpose: plain «automated reply» / «SCADA system» / «Ботевград» must not escalate.
BOT = re.compile(r"\b(are|r) (you|u) (an? )?(real )?(bot|robot|human|person|ai|machine)\b"
                 r"|\b(bot|robot|ai|chat ?gpt)\b[^.?!\n]{0,40}\?|\bбот(?!ев)\w*[^.?!\n]{0,40}\?|\bробот\w*[^.?!\n]{0,40}\?"
                 r"|\b(жив\w*|истински|реален|реальн\w*) (човек|человек)|\b(човек|человек) ли\b|\b(бот|робот) ли\b"
                 r"|\b(вы|вие|ти|ты) (сте |си |есть )?(бот|робот)", re.I)
OWNER_LED = "ведёт владелец"  # the owner runs this conversation: the bot records the reply and notifies, but never answers
HOLD = ("эскалация", "отказ", "пропущено владельцем")  # waits for the owner's decision; a new message doesn't lift it
PAUSED = (*HOLD, "ответ на alo", OWNER_LED)  # hard rules: nothing goes out to these stations until the owner decides
ALO = re.compile(r"@([\w-]+\.)*alo\.bg$")  # alo.bg notifications about seller replies (they can only be answered on the site)
ALO_NOTICE = re.compile(r"ново съобщение|new message", re.I)  # the alo email has only a link; the reply text is in the alo.bg account
GREETING = {"bg": "Здравейте,", "en": "Hello,", "ru": "Здравствуйте,"}


def blacklisted(contact: str, blacklist: list[dict]) -> bool:
    c = contact.lower().strip()
    domain = c.split("@")[-1] if "@" in c else ""
    entries = {b["контакт или домен"].lower().strip() for b in blacklist}
    return c in entries or bool(domain) and domain in entries


def contact_keys(text: str) -> set[str]:
    """Comparable contacts from a cell («a@x.bg, +359888...»): emails/links lowercased, phones as their last 9 digits,
    so «0888 123 456», «+359888123456» and «359 88 812 3456» are one number."""
    out = set()
    for c in (x.strip().lower() for x in text.split(",")):
        d = re.sub(r"\D", "", c)
        out.add(d[-9:] if "@" not in c and "/" not in c and len(d) >= 9 else c)
    return out - {""}


def blocked(st: dict, blacklist: list[dict], refusals: list[dict]) -> str:
    """Why nothing may go to this station automatically: one of its contacts is in «Отказы» or «Чёрный список» ("" = ok).
    Checked for every channel (email, WhatsApp, alo.bg), not only email."""
    mine = contact_keys(f"{st.get('email', '')},{st.get('телефон', '')},{st.get('ссылка', '')}")
    if mine & set().union(*(contact_keys(r.get("контакт", "")) for r in refusals)):
        return "в «Отказах»"
    emails = [e for e in mine if "@" in e]
    if mine & set().union(*(contact_keys(b.get("контакт или домен", "")) for b in blacklist)) or any(
            blacklisted(e, blacklist) for e in emails):
        return "в чёрном списке"
    return ""


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
    """Final category: regex hits (injection, "are you a bot") beat the LLM; low confidence = other (escalation)."""
    if INJECTION.search(body):
        return "suspicious"
    if BOT.search(body) and category not in ESCALATE | set(CLOSE) | {"auto_reply"}:
        return "asks_who_are_you"
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
    skip = drafts.PERMIT_SKIP if drafts.is_permit_project(st) else set()  # a project has no production or panels yet
    return [i for i in sheets.ITEMS if i not in skip and not str(st.get(i, "")).startswith(("получен", "отказ"))]


def reply_body(st: dict, c: llm.Classification) -> str:
    """Reply to the seller: answers to their questions (buyer facts only) + the missing checklist items."""
    lang = c.language
    para = llm.answer_questions(c.seller_questions, lang).paragraph.strip() if c.seller_questions else ""
    body = drafts.second(st, lang)
    return f"{GREETING[lang]}\n\n{para}\n\n{body}" if para else body


def manual_reply_draft(st: dict, c: llm.Classification, did: str) -> dict:
    """/note: the seller answered on WhatsApp/Viber/the owner's mailbox; the owner replies personally («Ручная очередь»)."""
    lang, body = c.language, reply_body(st, c)
    channel_ = "email (вручную)" if st["канал"] == "email" else st["канал"]
    to = st.get("телефон") if channel_.startswith("телефон") else st.get("email") if "email" in channel_ else st["ссылка"]
    return {"id": did, "id станции": st["id"], "дата": date.today().isoformat(), "язык": lang, "тип": "ответ",
            "канал": channel_, "кому": to, "тема": "", "текст": body, "статус": "на одобрении",
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


SAVED: dict[str, list[str]] = {}  # message id -> Drive links; ponytail: per process, a restart mid-retry uploads once more


def save_files(st: dict | None, msg: dict, svc) -> list[str]:
    """Attachments to Drive (station folder). A Drive failure doesn't break parsing the email: then names only.
    A message read again (LLM down, a failed write) doesn't upload its files a second time."""
    names = gmail.attachments(msg)
    if not names or not st:
        return names
    if msg["id"] in SAVED:
        return SAVED[msg["id"]]
    try:
        SAVED[msg["id"]] = drive.save(drive.service(), svc, msg, st["id"])
        return SAVED[msg["id"]]
    except Exception as e:  # not cached: retried next pass
        return [f"{n} (Drive: {type(e).__name__})" for n in names]


def process(api, sid, svc, auto: bool = False) -> dict:
    """Process all new incoming mail. auto (/auto on): a reply draft without rule violations approves itself;
    escalations are unaffected. Returns counters for the log/digest.

    A message is marked «обработано» in Gmail only after its results are in the sheet. If one raises (Telegram,
    Gmail, the LLM API down), its partial results are dropped, the messages before it are saved and marked,
    and it is read again next pass: at least once, never lost."""
    stations = {r["id"]: r for r in sheets.read(api, sid, "Станции")}
    dialogs = sheets.read(api, sid, "Диалоги")
    blacklist = sheets.read(api, sid, "Чёрный список")
    draft_rows = sheets.read(api, sid, "Черновики")
    draft_ids = [d["id"] for d in draft_rows]
    # read again after a failed mark_done: a merged reply holds every Message-ID it answers
    answered = {i for d in draft_rows for i in d.get("in-reply-to", "").split()}
    by_thread = {d["тред Gmail"]: d["id станции"] for d in dialogs if d["тред Gmail"]}
    by_email = {e.strip().lower(): s["id"] for s in stations.values() for e in s["email"].split(",") if e.strip()}
    by_domain: dict[str, set] = {}  # a colleague answers from the agency's domain in a new thread (contact@ -> ivan@)
    for e, s_id in by_email.items():
        if not gmail.is_public(e.split("@")[-1]):
            by_domain.setdefault(e.split("@")[-1], set()).add(s_id)
    before = {k: dict(v) for k, v in stations.items()}
    new_dialogs, new_drafts, new_black, new_stations, new_refusals = [], [], [], [], []
    stats = {"входящих": 0, "эскалаций": 0, "черновиков": 0, "закрыто": 0, "bounce": 0}
    drafted: dict[str, int] = {}  # station id -> index in new_drafts: one reply per station per pass
    today = date.today().isoformat()

    def find(msg, frm: str, body: str) -> dict | None:
        st = stations.get(by_thread.get(msg["threadId"], "")) or stations.get(by_email.get(frm, ""))
        same_domain = by_domain.get(frm.split("@")[-1], set())
        if not st and len(same_domain) == 1:
            st = stations[next(iter(same_domain))]
        if not st and ALO.search(frm):
            st = station_by_ad(stations, f"{gmail.header(msg, 'Subject')} {body}")
        return st

    def handle(msg, frm: str, body: str, st: dict | None) -> None:
        stats["входящих"] += 1
        if gmail.is_bounce(msg):
            if gmail.is_delay(msg):
                return  # «Delay» DSN: Gmail is still retrying and the letter is often delivered; not a bounce
            stats["bounce"] += 1
            if st:  # blacklist the address that failed (the one approval sends to), not every address of the station
                emails = [x.strip().lower() for x in st["email"].split(",") if x.strip()]
                failed = gmail.failed_recipient(msg)
                for e in [failed] if failed in emails else emails[:1]:
                    new_black.append({"контакт или домен": e, "причина": "bounce", "дата": today})
                st["статус переписки"] = "отказ"
                st["примечание"] = (st["примечание"] + "; bounce").strip("; ")
            return
        if blacklisted(frm, blacklist + new_black):
            return
        status = st["статус переписки"] if st else ""
        if ALO.search(frm) and (st is None or ALO_NOTICE.search(body)):
            if ALO_NOTICE.search(body) and not st:  # nothing marks it «ответ на alo»: the routine won't read it
                notify.urgent("📨 Ответ на alo.bg, станцию не определил: бот его не прочитает. "
                              "Открой https://www.alo.bg/user_messages.php сам.")
            elif ALO_NOTICE.search(body):
                where = st["id"]
                if status in HOLD or status == OWNER_LED:  # the routine must not answer a paused dialog
                    notify.send(f"📨 Ответ на alo.bg ({where}), диалог на паузе ({status}): бот не читает и не отвечает. "
                                "Прочитай сам: https://www.alo.bg/user_messages.php")
                else:
                    notify.send(f"📨 Ответ на alo.bg ({where}), в письме текста нет. Читаю в кабинете alo: нужен включённый ПК "
                                "с открытым Chrome. Если не прочитается, открой https://www.alo.bg/user_messages.php сам.")
                    if st:
                        st["статус переписки"], st["следующий шаг"] = "ответ на alo", "прочитать в кабинете alo (alo_reply.py)"
                if st:
                    new_dialogs.append({"id станции": st["id"], "направление": "in", "дата": today, "канал": "alo.bg",
                                        "язык": "", "краткое содержание": "[ответ на alo] уведомление без текста",
                                        "тред Gmail": msg["threadId"]})
            return  # anything else from alo (sign-up, Google sign-in) is not a dialog
        files = save_files(st, msg, svc)
        why = ""
        try:
            c = llm.classify(body, st or {})
            category = decide(c.category, c.confidence, body)
        except llm.LLMUnavailable:
            raise  # the API is down: read this message again next pass instead of escalating it
        except llm.LLMError as e:
            c, category, why = None, "other", f"\n(разбор не удался: {e})"
        sid_ = st["id"] if st else "?"
        summary = c.summary_ru if c else body[:200]
        new_dialogs.append({"id станции": sid_, "направление": "in", "дата": today, "канал": "email",
                            "язык": c.language if c else "", "краткое содержание": f"[{category}] {summary}"
                            + (f" Вложения: {', '.join(files)}" if files else ""), "тред Gmail": msg["threadId"]})
        if st and c:
            if category != "suspicious":  # facts from a manipulative message don't go into the sheet and later prompts
                apply_facts(st, c)
            st["последний контакт"] = today
        hold = status in HOLD and category != "auto_reply" and (category not in CLOSE or status == "отказ")
        if st and (status == OWNER_LED or hold):
            who = "переписку ведёшь ты" if status == OWNER_LED else f"диалог на паузе: {status}"
            notify.urgent(f"📩 {sid_} · {category} ({who}, бот не отвечает)\nОт: {frm}\n{summary}{why}\n"
                          f"Тред: {thread_url(msg['threadId'])}")
            return
        em = ph = []
        if c and category == "broker_redirect":
            em, ph = parse.emails(c.new_contact), parse.phones(c.new_contact)

        if category in ESCALATE or not st or category == "broker_redirect" and not (em or ph):
            stats["эскалаций"] += 1
            if st:
                st["статус переписки"], st["следующий шаг"] = "эскалация", f"решение владельца: {category}"
            contact = f"\nНовый контакт: {c.new_contact or 'не найден'}" if category == "broker_redirect" else ""
            notify.urgent(f"🚨 {sid_} · {category}\nОт: {frm}\n{summary}{why}{contact}\n"
                          + (f"Вложения (не открывались):\n" + "\n".join(files) + "\n" if files else "")
                          + f"Тред: {thread_url(msg['threadId'])}\nДиалог на паузе до твоего решения.")
        elif category in CLOSE:
            stats["закрыто"] += 1
            new_refusals.append({"контакт": frm, "id станции": sid_, "причина": "продано" if category == "sold" else "отказ",
                                 "дата": today, "что написал": summary})
            st["статус переписки"], st["следующий шаг"] = CLOSE[category], "не писать автоматически"
        elif category == "broker_redirect":  # the same plant, another contact: the channel follows the new contact
            keys = contact_keys(", ".join(em + ph))
            if not any(s.get("примечание", "").startswith(f"по рекомендации от {sid_}")  # read again or repeated: once
                       and contact_keys(f"{s['email']},{s['телефон']}") & keys for s in [*stations.values(), *new_stations]):
                new_stations.append({**{k: v for k, v in st.items() if k != "_row"}, "id": "", "email": ", ".join(em),
                                     "телефон": ", ".join(ph), "канал": channel(em, ph), "статус переписки": "новая",
                                     "последний контакт": "", "следующий шаг": "", "примечание": f"по рекомендации от {sid_}"})
            st["статус переписки"] = "перенаправлено"
        elif category in ("info_provided", "question_we_can_answer"):
            if not missing_items(st):
                if status != "квалифицирована":
                    notify.urgent(f"✅ {sid_} полностью квалифицирована: все пункты раздела 3 получены.\n{st['ссылка']}")
                st["статус переписки"] = "квалифицирована"
            else:
                manual = bool(ALO.search(frm))  # noreply@alo.bg can't be answered: the text goes to the owner, who replies on the site
                again = drafted.get(st["id"])  # a second message this pass: rebuild the reply with all facts, keep its id
                try:
                    did = new_drafts[again]["id"] if again is not None else sheets.next_id(
                        draft_ids + [d["id"] for d in new_drafts])
                    d = manual_reply_draft(st, c, did) if manual else reply_draft(st, msg, c, did)
                except llm.LLMUnavailable:
                    raise
                except llm.LLMError as e:
                    notify.urgent(f"⚠️ {sid_}: не смог составить ответ ({e}). Тред: {thread_url(msg['threadId'])}")
                else:
                    if auto and not d["нарушения правил"] and not manual and not dry_run():
                        d["статус"] = "одобрено"  # only the owner switches the mode (/auto on); handled in approval.main
                    else:  # DRY_RUN: the owner's ✅ goes through approval.approve, which queues it for manual sending
                        d["tg сообщение"] = notify.send(_card(d, st, summary), notify.draft_buttons(d["id"]))
                    if again is None:
                        drafted[st["id"]] = len(new_drafts)
                        new_drafts.append(d)
                        stats["черновиков"] += 1
                    else:
                        if not manual:
                            d["in-reply-to"] = f"{new_drafts[again].get('in-reply-to', '')} {d['in-reply-to']}".strip()
                        new_drafts[again] = d
                    st["статус переписки"] = "черновик ответа"

    lists = (new_dialogs, new_drafts, new_black, new_stations, new_refusals)
    done, err = [], None
    contacted = [e for e, s_id in by_email.items() if stations[s_id]["статус переписки"] not in ("новая", "не писать", "черновик")]
    gmail.adopt(svc, contacted)  # only sellers we wrote to: a listed-only address must not pull in unrelated mail
    for msg in gmail.incoming(svc):
        if gmail.header(msg, "Message-ID") in answered:  # its reply draft is already in the sheet: never a second one
            done.append(msg)
            continue
        st = snap = None
        sizes, stats_snap, drafted_snap = [len(x) for x in lists], dict(stats), dict(drafted)
        try:
            frm = parseaddr(gmail.header(msg, "From"))[1].lower()
            body = gmail.text(msg)
            st = find(msg, frm, body)
            snap = dict(st) if st else None
            handle(msg, frm, body, st)
        except Exception as e:  # undo this message's partial results; it stays unread for the next pass
            for x, n in zip(lists, sizes):
                del x[n:]
            if st and snap is not None:
                st.clear()
                st.update(snap)
            stats.clear()
            stats.update(stats_snap)
            drafted.clear()
            drafted.update(drafted_snap)
            if isinstance(e, llm.LLMUnavailable):  # counted by engine as a failure: a long outage still alerts
                print(f"inbox: LLM недоступен ({e}), письмо разберу в следующий раз", flush=True)
                stats["LLM недоступен"] = str(e)
            else:
                err = e
            break
        done.append(msg)

    if new_stations:
        last = max([int(re.sub(r"\D", "", s) or 0) for s in stations] + [0])
        for i, s in enumerate(new_stations, last + 1):
            s["id"] = f"S{i:03d}"
    # guard rows first and drafts last: a write failing halfway never leaves a station «отказ» without its «Отказы» row
    # (a retry would then only see the pause), and a retried message never gets a second reply draft
    sheets.append(api, sid, "Отказы", new_refusals)
    sheets.append(api, sid, "Чёрный список", new_black)
    sheets.update(api, sid, "Станции", [s for k, s in stations.items() if s != before[k]])
    sheets.append(api, sid, "Станции", new_stations)
    sheets.append(api, sid, "Диалоги", new_dialogs)
    sheets.append(api, sid, "Черновики", new_drafts)
    if stats["bounce"]:
        state = sheets.kv(api, sid, "_state")
        key = f"bounce {today}"
        sheets.set_kv(api, sid, "_state", {key: int(state.get(key, {}).get("значение") or 0) + stats["bounce"]})
    for m in done:
        gmail.mark_done(svc, m)
    if err:
        raise err
    return stats


def _card(d: dict, st: dict, summary: str) -> str:
    warn = f"\n⚠️ Нарушения правил: {d['нарушения правил']}" if d["нарушения правил"] else ""
    return (f"↩️ {d['id']} · {st['id']} · ответ ({d['язык']})\nПродавец написал: {summary}\nКому: {d['кому']}{warn}"
            f"\n\nТема: {d['тема']}\n\n{d['текст']}")
