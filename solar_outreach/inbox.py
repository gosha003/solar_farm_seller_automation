"""Шаг 3 цикла (раздел 9): входящие в тредах SolarBG -> классификация -> факты в таблицу -> действие.

Действия:
  info_provided / question_we_can_answer -> черновик ответа (недостающие пункты) на одобрение
  price_discussion ... suspicious, other, низкая уверенность -> СРОЧНО владельцу, диалог на паузу
  not_interested_or_stop / sold -> вкладка «Отказы» (автоматически больше не пишем), диалог закрыт
  auto_reply -> ничего; broker_redirect -> новый контакт как новый продавец; bounce -> чёрный список + счётчик
"""
import re
from datetime import date
from email.utils import parseaddr

from solar_outreach import drafts, drive, gmail_client as gmail, llm, notify, rules, sheets

ESCALATE = {"price_discussion", "counteroffer", "wants_call_or_meeting", "asks_who_are_you", "asks_for_docs_or_nda",
            "asks_money", "suspicious", "other"}
CLOSE = {"not_interested_or_stop": "отказ", "sold": "отказ"}
# Независимо от LLM: явные попытки prompt injection / фишинга -> suspicious, даже если модель «согласилась».
INJECTION = re.compile(
    r"ignore (all |any |the )?(previous|prior|above) (instructions|rules|prompts?)|disregard (all |the )?(previous|above)"
    r"|system prompt|you are (an? )?(ai|bot|language model|assistant)|as an ai\b|send (me )?(your|the) (password|login|"
    r"bank|card|passport|data)|игнорир\w* (все |всички )?(предыдущ|предишн|правил|инструкц)|забудь\w* (все )?(правил|инструкц)"
    r"|системн\w* (промпт|инструкц)|изпратете (ни )?(парола|данни за карта)|перейд\w* по ссылк|кликн\w* (на|по) (линк|ссылк)",
    re.I)
ALO = re.compile(r"@([\w-]+\.)*alo\.bg$")  # уведомления alo.bg об ответах продавцов (ответить можно только на сайте)
ALO_NOTICE = re.compile(r"ново съобщение|new message", re.I)  # в письме alo только ссылка, текст ответа лежит в кабинете
GREETING = {"bg": "Здравейте,", "en": "Hello,", "ru": "Здравствуйте,"}


def blacklisted(contact: str, blacklist: list[dict]) -> bool:
    c = contact.lower().strip()
    domain = c.split("@")[-1] if "@" in c else ""
    entries = {b["контакт или домен"].lower().strip() for b in blacklist}
    return c in entries or bool(domain) and domain in entries


def station_by_ad(stations: dict, text: str) -> dict | None:
    """Станция по номеру объявления из текста письма: ссылки alo/bazar оканчиваются на номер."""
    ids = set(re.findall(r"\d{7,9}", text))
    hits = [s for s in stations.values() if ids & set(re.findall(r"\d{7,9}$", s["ссылка"].rstrip("/").split(".html")[0]))]
    if len(hits) != 1:  # номера нет в письме: заголовок объявления (alo ставит его в тему диалога), потом имя продавца
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
    """Итоговая категория: regex-инъекция побеждает LLM; низкая уверенность = other (эскалация)."""
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
    """Ответ продавцу: ответы на его вопросы (только факты о покупателе) + недостающие пункты раздела 3."""
    lang = c.language
    para = llm.answer_questions(c.seller_questions, lang).paragraph.strip() if c.seller_questions else ""
    body = drafts.second(st, lang)
    return f"{GREETING[lang]}\n\n{para}\n\n{body}" if para else body


def manual_reply_draft(st: dict, c: llm.Classification, did: str) -> dict:
    """/note: продавец ответил в WhatsApp/Viber/на почту владельца; отвечает владелец сам (Ручная очередь)."""
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
    """Вложения в Drive (папка станции). Сбой Drive не роняет разбор письма: тогда только имена."""
    names = gmail.attachments(msg)
    if not names or not st:
        return names
    try:
        return drive.save(drive.service(), svc, msg, st["id"])
    except Exception as e:
        return [f"{n} (Drive: {type(e).__name__})" for n in names]


def process(api, sid, svc, auto: bool = False) -> dict:
    """Обработать все новые входящие. auto (/auto on): черновик ответа без нарушений правил одобряется сам
    (раздел 10), эскалации от этого не меняются. Возвращает счётчики для лога/дайджеста."""
    stations = {r["id"]: r for r in sheets.read(api, sid, "Станции")}
    dialogs = sheets.read(api, sid, "Диалоги")
    blacklist = sheets.read(api, sid, "Чёрный список")
    n_drafts = len(sheets.read(api, sid, "Черновики"))
    by_thread = {d["тред Gmail"]: d["id станции"] for d in dialogs if d["тред Gmail"]}
    by_email = {e.strip().lower(): s["id"] for s in stations.values() for e in s["email"].split(",") if e.strip()}
    before = {k: dict(v) for k, v in stations.items()}
    new_dialogs, new_drafts, new_black, new_stations, new_refusals = [], [], [], [], []
    stats = {"входящих": 0, "эскалаций": 0, "черновиков": 0, "закрыто": 0, "bounce": 0}

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
            gmail.mark_done(svc, msg)  # остальное от alo (регистрация, вход через Google) не диалог
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
                manual = bool(ALO.search(frm))  # на noreply@alo.bg не ответить: текст владельцу, отправляет сам на сайте
                try:
                    did = f"D{n_drafts + len(new_drafts) + 1:03d}"
                    d = manual_reply_draft(st, c, did) if manual else reply_draft(st, msg, c, did)
                except llm.LLMError as e:
                    notify.urgent(f"⚠️ {sid_}: не смог составить ответ ({e}). Тред: {thread_url(msg['threadId'])}")
                else:
                    if auto and not d["нарушения правил"] and not manual:
                        d["статус"] = "одобрено"  # режим включает только владелец (/auto on); уйдёт в approval.main
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
