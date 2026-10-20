"""Follow-ups: no reply to the first email -> a short reminder on day 4 and day 9 after it, in the same thread.
A seller who answered and then went quiet after our reply (the remaining checklist questions) gets the same reminders,
counted from that reply. At most 2 follow-ups per contact in total, both stages together (SPEC section 8).
A seller reply (except an auto-reply) cancels the follow-ups drafted before it; an auto-reply (out of office) postpones them.
A follow-up is an ordinary draft: approval, hard rules, dialog pause, blacklist, «Отказы».
A follow-up the owner skipped stops follow-ups for that station.
A message the owner sent by hand (/sent: WhatsApp, Viber, website, own mailbox) is followed up too:
the follow-up draft uses the same channel and lands in «Ручная очередь» after ✅.
"""
from datetime import date

from solar_outreach import drafts, rules

CANCELLED = "отменено: продавец ответил"  # the seller answered first: it never went out and doesn't count


def sent_on(d: dict) -> date | None:
    try:
        return date.fromisoformat(d.get("отправлено", ""))
    except ValueError:
        return None


def last_reply(dialogs: list[dict]) -> dict[str, str]:
    """Station -> ISO date of the seller's latest answer (an auto-reply doesn't count).
    A row without a date counts as the newest: when unsure, the system stays quiet."""
    out = {}
    for r in dialogs:
        if r["направление"] == "in" and not r["краткое содержание"].startswith("[auto_reply]"):
            out[r["id станции"]] = max(out.get(r["id станции"], ""), r.get("дата") or "9999")
    return out


def auto_replies(dialogs: list[dict], sid: str) -> list[date]:
    out = []
    for r in dialogs:
        if r["id станции"] == sid and r["направление"] == "in" and r["краткое содержание"].startswith("[auto_reply]"):
            try:
                out.append(date.fromisoformat(r.get("дата", "")))
            except ValueError:
                pass
    return out


def due(stations: dict, by_id: dict, dialogs: list[dict], days: list[int], today: date) -> list[dict]:
    answered = last_reply(dialogs)
    out = []
    for sid, st in stations.items():
        if st["статус переписки"] != "ждём ответа":
            continue
        mine = [d for d in by_id.values() if d["id станции"] == sid]
        if sid in answered:  # chase our reply to the seller's latest message, never a letter he has answered since;
            # a reply the owner skipped is not ours even with a date: he wrote his own, the questions may not be in it
            letters = [d for d in mine if d["тип"] == "ответ" and d["статус"] == "отправлено" and sent_on(d)
                       and sent_on(d).isoformat() >= answered[sid]]
        else:
            letters = [d for d in mine if d["тип"].startswith("первое") and "ТЕСТ" not in d["тип"] and sent_on(d)]
        if not letters:
            continue
        letter = max(letters, key=sent_on)
        start = sent_on(letter)
        ups = [d for d in mine if d["тип"].startswith("дожим") and d["статус"] != CANCELLED]
        n = len(ups)
        if n >= len(days) or not all(sent_on(u) for u in ups):  # the cap per contact; pending or skipped: no more
            continue
        ups = [u for u in ups if sent_on(u) >= start]  # this letter's follow-ups
        k = len(ups)
        anchor = max([start, *auto_replies(dialogs, sid)])  # out of office: count the days from the auto-reply
        prev = max([sent_on(u) for u in ups], default=start)  # a late follow-up 1 must not be chased by follow-up 2 at once
        if (today - anchor).days < days[k] or ups and (today - prev).days < days[k] - days[k - 1]:
            continue
        lang = letter["язык"]
        body = drafts.followup(lang, start.strftime("%d.%m.%Y"), drafts.is_general(st), reply=sid in answered)
        topic = letter["тема"]  # a reply the owner sent by hand on WhatsApp/alo has no subject
        subject = topic if not topic or topic.lower().startswith("re:") else f"Re: {topic}"
        thread = letter.get("тред Gmail", "")  # no thread = the owner sent the letter by hand
        channel = "email" if thread else ("email (вручную)" if letter["канал"] == "email" else letter["канал"])
        d = {"id": f"D{len(by_id) + len(out) + 1:03d}", "id станции": sid, "дата": today.isoformat(), "язык": lang,
             "тип": f"дожим {n + 1}", "канал": channel, "кому": letter["кому"], "тема": subject, "текст": body,
             "статус": "на одобрении", "тред Gmail": thread,
             "нарушения правил": ", ".join(rules.violations(f"{subject}\n{body}", drafts.template(lang)["signature"]))}
        out.append(d)
    return out
