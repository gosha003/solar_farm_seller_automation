"""Follow-ups: no reply to the first email -> a short reminder on day 4 and day 9 after it, in the same thread.
Any seller reply (except an auto-reply) cancels the rest.
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


def due(stations: dict, by_id: dict, dialogs: list[dict], days: list[int], today: date) -> list[dict]:
    replied = {r["id станции"] for r in dialogs
               if r["направление"] == "in" and not r["краткое содержание"].startswith("[auto_reply]")}
    out = []
    for first in list(by_id.values()):
        sid, start = first["id станции"], sent_on(first)
        st = stations.get(sid)
        if (not first["тип"].startswith("первое") or "ТЕСТ" in first["тип"] or not start
                or not st or sid in replied or st["статус переписки"] != "ждём ответа"):
            continue
        ups = [d for d in by_id.values() if d["id станции"] == sid and d["тип"].startswith("дожим")]
        k = len(ups)
        if k >= len(days) or not all(sent_on(u) for u in ups) or (today - start).days < days[k]:
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
