"""Follow-ups: no reply to the first email -> a short reminder on day 4 and day 9 after it, in the same thread.
Any seller reply (except an auto-reply) cancels the rest; an auto-reply (out of office) postpones them.
A follow-up is an ordinary draft: approval, hard rules, dialog pause, blacklist, «Отказы».
A follow-up the owner skipped stops follow-ups for that station.
A first message the owner sent by hand (/sent: WhatsApp, Viber, website, own mailbox) is followed up too:
the follow-up draft uses the same channel and lands in «Ручная очередь» after ✅.
"""
from datetime import date

from solar_outreach import drafts, rules


def sent_on(d: dict) -> date | None:
    try:
        return date.fromisoformat(d.get("отправлено", ""))
    except ValueError:
        return None


def replied(dialogs: list[dict]) -> set[str]:
    """Stations whose seller answered (an auto-reply doesn't count)."""
    return {r["id станции"] for r in dialogs
            if r["направление"] == "in" and not r["краткое содержание"].startswith("[auto_reply]")}


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
    answered = replied(dialogs)
    out = []
    for first in list(by_id.values()):
        sid, start = first["id станции"], sent_on(first)
        st = stations.get(sid)
        if (not first["тип"].startswith("первое") or "ТЕСТ" in first["тип"] or not start
                or not st or sid in answered or st["статус переписки"] != "ждём ответа"):
            continue
        ups = [d for d in by_id.values() if d["id станции"] == sid and d["тип"].startswith("дожим")]
        k = len(ups)
        if k >= len(days) or not all(sent_on(u) for u in ups):
            continue
        anchor = max([start, *auto_replies(dialogs, sid)])  # out of office: count the days from the auto-reply
        prev = max([sent_on(u) for u in ups], default=start)  # a late follow-up 1 must not be chased by follow-up 2 at once
        if (today - anchor).days < days[k] or ups and (today - prev).days < days[k] - days[k - 1]:
            continue
        lang = first["язык"]
        body = drafts.followup(lang, start.strftime("%d.%m.%Y"), drafts.is_general(st))
        subject = first["тема"] if first["тема"].lower().startswith("re:") else f"Re: {first['тема']}"
        thread = first.get("тред Gmail", "")  # no thread = the owner sent the first message by hand
        channel = "email" if thread else ("email (вручную)" if first["канал"] == "email" else first["канал"])
        d = {"id": f"D{len(by_id) + len(out) + 1:03d}", "id станции": sid, "дата": today.isoformat(), "язык": lang,
             "тип": f"дожим {k + 1}", "канал": channel, "кому": first["кому"], "тема": subject, "текст": body,
             "статус": "на одобрении", "тред Gmail": thread,
             "нарушения правил": ", ".join(rules.violations(f"{subject}\n{body}", drafts.template(lang)["signature"]))}
        out.append(d)
    return out
