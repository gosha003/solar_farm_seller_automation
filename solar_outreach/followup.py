"""Дожимы (раздел 7): нет ответа на первое email-письмо -> на 4-й и 9-й день после него короткое напоминание
в том же треде. Любой ответ продавца (кроме автоответа) отменяет оставшиеся.
Дожим = обычный черновик: одобрение, правила раздела 4, пауза диалога, чёрный список, «Отказы».
Пропущенный владельцем дожим останавливает дожимы этой станции.
Первое сообщение, отправленное владельцем вручную (/sent: WhatsApp, Viber, сайт, своя почта), тоже дожимается:
черновик дожима в том же канале, после ✅ он в «Ручной очереди».
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
        body = drafts.followup(lang, start.strftime("%d.%m.%Y"))
        subject = first["тема"] if first["тема"].lower().startswith("re:") else f"Re: {first['тема']}"
        thread = first.get("тред Gmail", "")  # нет треда = первое отправлял владелец сам
        channel = "email" if thread else ("email (вручную)" if first["канал"] == "email" else first["канал"])
        d = {"id": f"D{len(by_id) + len(out) + 1:03d}", "id станции": sid, "дата": today.isoformat(), "язык": lang,
             "тип": f"дожим {k + 1}", "канал": channel, "кому": first["кому"], "тема": subject, "текст": body,
             "статус": "на одобрении", "тред Gmail": thread,
             "нарушения правил": ", ".join(rules.violations(f"{subject}\n{body}", drafts.template(lang)["signature"]))}
        out.append(d)
    return out
