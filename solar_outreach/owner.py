"""Владельцу: команды Telegram (раздел 15), /stats и утренний дайджест (раздел 10).
Всё считается из таблицы (единственный источник правды), здесь только чтение и тексты; запись делает approval.main.
"""
from collections import Counter
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from solar_outreach import sheets

HELP = """Команды:
/stats статистика
/pause, /resume остановить или возобновить отправку продавцам
/auto on|off автоотправка писем без нарушений правил (эскалации всегда тебе)
/queue ручная очередь (WhatsApp/Viber/сайт)
/digest дайджест сейчас
/blacklist <email, телефон или домен> больше не писать
/status <id станции> карточка станции"""


class Ctx:
    """Данные одного запуска: читаются один раз в approval.main, команды их только смотрят и меняют settings."""
    def __init__(self, api, sid, cfg, stations, by_id, dialogs, settings, state):
        self.api, self.sid, self.cfg = api, sid, cfg
        self.stations, self.by_id, self.dialogs, self.settings, self.state = stations, by_id, dialogs, settings, state
        self.blacklist_add: list[dict] = []

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
    if c.state.get("последняя ошибка") and c.state["последняя ошибка"][:10] >= since:
        lines.append(f"Последняя ошибка: {c.state['последняя ошибка']}")
    return "\n".join(lines)


def local_now(cfg: dict) -> datetime:
    return datetime.now(ZoneInfo(cfg["owner"]["timezone"]))


def digest_due(c: Ctx, now: datetime | None = None) -> bool:
    """Раз в сутки после digest_hour по времени владельца (Торонто)."""
    now = now or local_now(c.cfg)
    return now.hour >= c.cfg["notify"]["digest_hour"] and c.state.get("дайджест") != now.date().isoformat()


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
    return HELP
