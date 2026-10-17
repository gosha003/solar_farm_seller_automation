"""For the owner: Telegram commands, /stats and the morning digest.
Everything is computed from the sheet (the single source of truth). Commands change the run data in Ctx;
approval.main writes it to the sheet (except marking «Ручная очередь» rows in /sent).
"""
from collections import Counter
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from solar_outreach import inbox, llm, sheets

HELP = """Команды:
/stats статистика
/pause, /resume остановить или возобновить отправку продавцам
/auto on|off автоотправка писем без нарушений правил (эскалации всегда тебе)
/queue ручная очередь (WhatsApp/Viber/сайт)
/digest дайджест сейчас
/blacklist <email, телефон или домен> больше не писать
/status <id станции> карточка станции
/sent <id станции> ты отправил сообщение сам (WhatsApp, Viber, сайт, своя почта): ждём ответа, дожимы через 4 и 9 дней
/note <id станции> <ответ продавца> вставь ответ из WhatsApp/Viber: факты в таблицу, эскалация или черновик ответа"""


class Ctx:
    """Data of one run: read once in approval.main; commands only read it and change settings."""
    def __init__(self, api, sid, cfg, stations, by_id, dialogs, settings, state):
        self.api, self.sid, self.cfg = api, sid, cfg
        self.stations, self.by_id, self.dialogs, self.settings, self.state = stations, by_id, dialogs, settings, state
        self.blacklist_add: list[dict] = []
        self.changed_drafts: dict[str, dict] = {}
        self.new_drafts: list[dict] = []
        self.new_dialogs: list[dict] = []
        self.new_refusals: list[dict] = []

    def mode(self) -> str:
        return self.settings.get("approval_mode") or self.cfg["approval_mode"]

    def paused(self) -> bool:
        return self.settings.get("пауза") == "да"


def real(by_id: dict) -> list[dict]:
    return [d for d in by_id.values() if "ТЕСТ" not in d["тип"]]


def received(st: dict) -> int:
    return sum(str(st.get(i, "")).startswith(("получен", "отказ")) for i in sheets.ITEMS)


def stats(c: Ctx, today: date) -> str:
    t = today.isoformat()
    sent = [d for d in real(c.by_id) if d.get("отправлено", "")[:4].isdigit()]
    first_today = sum(d["отправлено"] == t and d["тип"].startswith("первое") for d in sent)
    statuses = Counter(s["статус переписки"] for s in c.stations.values() if s["приоритет"] != "-")
    prio = Counter(s["приоритет"] for s in c.stations.values())
    return "\n".join([
        f"📊 Станций: {len(c.stations)}, подходят: {sum(v for k, v in prio.items() if k != '-')} "
        f"(A {prio['A']}, B {prio['B']}, C {prio['C']})",
        "Статусы подходящих: " + (", ".join(f"{k} {v}" for k, v in statuses.most_common()) or "нет"),
        f"Писем отправлено: {len(sent)}, сегодня {sum(d['отправлено'] == t for d in sent)} "
        f"(первых {first_today} из {c.cfg['limits']['new_per_day']})",
        f"Ответов продавцов: {sum(r['направление'] == 'in' for r in c.dialogs)}",
        f"Ждут твоего решения: {sum(d['статус'] in ('на одобрении', 'ждёт правки') for d in c.by_id.values())} черновиков, "
        f"{statuses['эскалация']} эскалаций",
        f"Режим: {c.mode()}{', ПАУЗА' if c.paused() else ''} · bounce сегодня: {c.state.get(f'bounce {t}') or 0}"])


def status(c: Ctx, station_id: str) -> str:
    st = c.stations.get(station_id.upper())
    if not st:
        return f"Нет станции {station_id}"
    items = "\n".join(f"{i}: {st.get(i) or '-'}" for i in sheets.ITEMS)
    talk = [r for r in c.dialogs if r["id станции"] == st["id"]][-3:]
    return (f"{st['id']} · {st['заголовок'][:90]}\n{st['мощность кВт']} кВт · {st['цена EUR']} EUR · "
            f"{st['EUR/кВт']} €/кВт · {st['регион']} · приоритет {st['приоритет']}\n"
            f"Статус: {st['статус переписки']} · последний контакт {st['последний контакт'] or '-'} · "
            f"далее: {st['следующий шаг'] or '-'}\nКанал: {st['канал']} · {st['ссылка']}\n\n{items}"
            + ("\n\nПоследнее:\n" + "\n".join(f"{r['дата']} {r['направление']}: {r['краткое содержание'][:200]}"
                                              for r in talk) if talk else ""))


def queue(c: Ctx) -> str:
    rows = [r for r in sheets.read(c.api, c.sid, "Ручная очередь") if r["статус"] == "отправить вручную"]
    if not rows:
        return "Ручная очередь пуста."
    return "📱 Отправить вручную (текст и ссылки во вкладке «Ручная очередь»):\n" + "\n".join(
        f"{r['id станции']} · {r['канал']} · {r['ссылка на объявление']}" for r in rows)


def digest(c: Ctx, today: date) -> str:
    since = (today - timedelta(days=1)).isoformat()
    new = [s for s in c.stations.values() if s["дата найдено"] >= since]
    fresh = [s for s in new if s["приоритет"] != "-"]
    sent = [d for d in real(c.by_id) if d.get("отправлено", "")[:4].isdigit() and d["отправлено"] >= since]
    replies = [r for r in c.dialogs if r["направление"] == "in" and r["дата"] >= since]
    waiting = [d["id"] for d in c.by_id.values() if d["статус"] in ("на одобрении", "ждёт правки")]
    escal = [s["id"] for s in c.stations.values() if s["статус переписки"] == "эскалация"]
    top = sorted((s for s in c.stations.values() if s["приоритет"] in ("A", "B")),
                 key=lambda s: (s["приоритет"], -float(s["скоринг"] or 0)))[:5]
    manual = [r for r in sheets.read(c.api, c.sid, "Ручная очередь") if r["статус"] == "отправить вручную"]
    refused = [r for r in sheets.read(c.api, c.sid, "Отказы") if r["дата"] >= since]
    lines = [f"☀️ Дайджест {today:%d.%m}. Режим {c.mode()}{', ПАУЗА' if c.paused() else ''}",
             f"Новые объявления за сутки: {len(new)}, подходят: {len(fresh)}"
             + "".join(f"\n  {s['id']} {s['мощность кВт']} кВт {s['EUR/кВт']} €/кВт {s['регион']}" for s in fresh),
             f"Отправлено за сутки: {len(sent)} ({', '.join(f'{k} {v}' for k, v in Counter(d['тип'].split(',')[0] for d in sent).items()) or '-'}), "
             f"ответов: {len(replies)}, отказов: {len(refused)}",
             f"Ждут твоего решения: черновики {', '.join(waiting) or 'нет'}; эскалации {', '.join(escal) or 'нет'}",
             "Топ-кандидаты:" + ("".join(f"\n  {s['id']} {s['приоритет']} {s['мощность кВт']} кВт, "
                                         f"{s['статус переписки']}, пунктов {received(s)}/{len(sheets.ITEMS)}" for s in top)
                                 or " нет"),
             f"Ручная очередь: {len(manual)}" + (f" ({', '.join(r['id станции'] for r in manual)})" if manual else "")]
    bounce = sum(int(v or 0) for k, v in c.state.items() if k.startswith("bounce ") and k[7:] >= since)
    if bounce:
        lines.append(f"Bounce за сутки: {bounce}")
    if c.state.get("площадки недоступны"):
        lines.append(f"Недоступные площадки: {c.state['площадки недоступны']}")
    if c.state.get("расходы Google Cloud"):
        lines.append(f"Google Cloud: {c.state['расходы Google Cloud']}")
    trial = c.cfg.get("gcp", {}).get("trial_ends")
    if trial and 0 <= (trial - today).days <= 14:
        lines.append(f"⏰ Free Trial Google Cloud кончается {trial:%d.%m.%Y}: Billing -> Activate full account, "
                     "иначе Google остановит VM (потолок $5 остаётся)")
    if c.state.get("последняя ошибка") and c.state["последняя ошибка"][:10] >= since:
        lines.append(f"Последняя ошибка: {c.state['последняя ошибка']}")
    return "\n".join(lines)


def local_now(cfg: dict) -> datetime:
    return datetime.now(ZoneInfo(cfg["owner"]["timezone"]))


def digest_due(c: Ctx, now: datetime | None = None) -> bool:
    """Once a day after digest_hour in the owner's time zone (Toronto)."""
    now = now or local_now(c.cfg)
    return now.hour >= c.cfg["notify"]["digest_hour"] and c.state.get("дайджест") != now.date().isoformat()


def sent(c: Ctx, station_id: str, today: date) -> str:
    """The owner sent an approved message personally: draft sent, station awaits a reply, queue row closed."""
    st = c.stations.get(station_id.upper())
    if not st:
        return f"Нет станции {station_id}"
    t = today.isoformat()
    ds = [d for d in c.by_id.values() if d["id станции"] == st["id"] and d["статус"] == "одобрено"
          and "ТЕСТ" not in d["тип"] and d.get("отправлено", "") in ("", "вручную", "DRY_RUN")]
    if not ds:
        return f"У {st['id']} нет одобренных неотправленных сообщений. Сначала ✅ на карточке."
    for d in ds:
        d.update({"статус": "отправлено", "отправлено": t})
        c.changed_drafts[d["id"]] = d
        c.new_dialogs.append({"id станции": st["id"], "направление": "out", "дата": t, "канал": f"{d['канал']} (вручную)",
                              "язык": d["язык"], "краткое содержание": f"{d['тип']}, отправил владелец", "тред Gmail": ""})
        if not d["тип"].startswith("дожим"):
            sheets.mark_asked(st, d["тип"].startswith("первое"))
    st.update({"статус переписки": "ждём ответа", "последний контакт": t, "следующий шаг": "ждать ответа"})
    rows = [r for r in sheets.read(c.api, c.sid, "Ручная очередь")
            if r["id станции"] == st["id"] and r["статус"] == "отправить вручную"]
    sheets.update(c.api, c.sid, "Ручная очередь", [{**r, "статус": "отправлено вручную"} for r in rows])
    return f"📤 {st['id']}: отмечено отправленным ({', '.join(d['id'] for d in ds)}). Нет ответа: дожим через 4 и 9 дней."


def note(c: Ctx, station_id: str, text: str, today: date) -> str:
    """Seller reply outside email (WhatsApp, Viber, call): same parsing as for an email in inbox.process."""
    st = c.stations.get(station_id.upper())
    if not st:
        return f"Нет станции {station_id}"
    if not text:
        return f"Пришли /note {st['id']} и текст ответа продавца."
    try:
        cl = llm.classify(text, {k: v for k, v in st.items() if k != "_row"})
    except llm.LLMError as e:
        return f"⚠️ Не смог разобрать ({e}). Пришли ещё раз."
    category, t = inbox.decide(cl.category, cl.confidence, text), today.isoformat()
    got = inbox.apply_facts(st, cl)
    st["последний контакт"] = t
    c.new_dialogs.append({"id станции": st["id"], "направление": "in", "дата": t, "канал": "вручную",
                          "язык": cl.language, "краткое содержание": f"[{category}] {cl.summary_ru}", "тред Gmail": ""})
    head = f"{st['id']} · {category}\n{cl.summary_ru}" + (f"\nВ таблицу: {', '.join(got)}" if got else "")
    if category in inbox.ESCALATE:
        st["статус переписки"], st["следующий шаг"] = "эскалация", f"решение владельца: {category}"
        return f"🚨 {head}\nЭто решаешь ты, черновик не делаю. Диалог на паузе."
    if category in inbox.CLOSE:
        c.new_refusals.append({"контакт": st.get("email") or st.get("телефон") or st["ссылка"], "id станции": st["id"],
                               "причина": "продано" if category == "sold" else "отказ", "дата": t,
                               "что написал": cl.summary_ru})
        st["статус переписки"], st["следующий шаг"] = "отказ", "не писать автоматически"
        return f"📕 {head}\nЗаписал в «Отказы»."
    if category == "broker_redirect":
        return f"↪️ {head}\nНовый контакт: {cl.new_contact or '?'}. Добавь его в «Станции» вручную."
    if category == "auto_reply":
        return f"🤖 {head}\nАвтоответ, ничего не делаю."
    if not inbox.missing_items(st):
        st["статус переписки"] = "квалифицирована"
        return f"✅ {head}\nВсе пункты раздела 3 получены: станция квалифицирована."
    try:
        d = inbox.manual_reply_draft(st, cl, f"D{len(c.by_id) + 1:03d}")
    except llm.LLMError as e:
        return f"{head}\n⚠️ Черновик ответа не получился ({e})."
    c.by_id[d["id"]] = d
    c.new_drafts.append(d)
    st["статус переписки"] = "черновик ответа"
    return f"📝 {head}\nНе хватает пунктов: {len(inbox.missing_items(st))}. Черновик ответа следующим сообщением."


def command(text: str, c: Ctx, today: date) -> str:
    cmd, _, arg = text.strip().partition(" ")
    cmd, arg = cmd.split("@")[0].lower(), arg.strip()
    if cmd == "/stats":
        return stats(c, today)
    if cmd in ("/pause", "/resume"):
        c.settings["пауза"] = "да" if cmd == "/pause" else "нет"
        return ("⏸ Пауза: продавцам ничего не уходит, входящие разбираются, эскалации приходят. /resume чтобы продолжить."
                if cmd == "/pause" else "▶️ Отправка возобновлена.")
    if cmd == "/auto" and arg in ("on", "off"):
        c.settings["approval_mode"] = "auto" if arg == "on" else "manual"
        return ("🤖 Автоотправка включена: письма без нарушений правил уходят сами, эскалации и всё спорное приходят тебе. "
                "Черновики, которые уже ждут кнопок, остаются за тобой. /auto off чтобы вернуть ручной режим."
                if arg == "on" else "✋ Ручной режим: каждое письмо только после твоего ✅.")
    if cmd == "/queue":
        return queue(c)
    if cmd == "/digest":
        return digest(c, today)
    if cmd == "/blacklist" and arg:
        c.blacklist_add.append({"контакт или домен": arg.lower(), "причина": "владелец", "дата": today.isoformat()})
        return f"⛔ {arg} в чёрном списке, больше не пишем."
    if cmd == "/status" and arg:
        return status(c, arg)
    if cmd == "/sent" and arg:
        return sent(c, arg.split()[0], today)
    if cmd == "/note" and arg:
        sid_, reply = (arg.split(maxsplit=1) + [""])[:2]  # the reply text may span several lines
        return note(c, sid_, reply.strip(), today)
    hints = {"/auto": f"Сейчас режим {c.mode()}. Пришли /auto on или /auto off.",  # tapped from the menu without an argument
             "/status": "Пришли /status и id станции, например /status S019.",
             "/blacklist": "Пришли /blacklist и email, телефон или домен.",
             "/sent": "Пришли /sent и id станции, например /sent S020.",
             "/note": "Пришли /note, id станции и текст ответа продавца."}
    return hints.get(cmd, HELP)
