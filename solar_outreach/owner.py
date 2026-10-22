"""For the owner: Telegram commands, /stats and the morning digest.
Everything is computed from the sheet (the single source of truth). Commands change the run data in Ctx;
approval.main writes it to the sheet (except marking «Ручная очередь» rows in /sent).
Every reply is Telegram HTML (notify's layout); a CLI that prints one (site_queue, alo_reply) prints notify.plain of it.
"""
from collections import Counter
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from solar_outreach import brief, inbox, llm, sheets
from solar_outreach.notify import b, code, esc, head, i, join, link, num, section

ID = i("id станции")
HELP = join(head("📋", "Команды"), "\n".join([
    f"{code('/stats')} — статистика",
    f"{code('/pause')}, {code('/resume')} — остановить или возобновить отправку продавцам",
    f"{code('/auto on')}, {code('/auto off')} — автоотправка писем без нарушений правил (эскалации всегда тебе)",
    f"{code('/queue')} — ручная очередь (WhatsApp/Viber/сайт)",
    f"{code('/digest')} — дайджест сейчас",
    f"{code('/blacklist')} {i('email, телефон или домен')} — больше не писать",
    f"{code('/status')} {ID} — карточка станции",
    f"{code('/sent')} {ID} — ты отправил сообщение сам (WhatsApp, Viber, сайт, своя почта): ждём ответа, "
    "дожимы через 4 и 9 дней",
    f"{code('/note')} {ID} {i('ответ продавца')} — вставь ответ из WhatsApp/Viber: факты в таблицу, эскалация "
    "или черновик ответа",
    f"{code('/brief')} {ID} — готова ли сводка директору, чего не хватает и что запрошено",
    f"📎 файл, фото или ссылка на Drive с подписью {code('S020')} — документ в папку станции (например, переслать из WhatsApp)"]))


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
    return sum(str(st.get(x, "")).startswith(("получен", "отказ")) for x in sheets.ITEMS)


def no_station(station_id: str) -> str:
    return head("🔍", "Нет станции", station_id)


def para(title: str, text: str = "", lines=()) -> str:
    """«<b>Title</b> text» and bullet lines under it (text and lines are markup: escape their values)."""
    return f"{b(title)} {text}".rstrip() + "".join(f"\n• {x}" for x in lines)


def stats(c: Ctx, today: date) -> str:
    t = today.isoformat()
    sent = [d for d in real(c.by_id) if d.get("отправлено", "")[:4].isdigit()]
    first_today = sum(d["отправлено"] == t and d["тип"].startswith("первое") for d in sent)
    statuses = Counter(s["статус переписки"] for s in c.stations.values() if s["приоритет"] != "-")
    prio = Counter(s["приоритет"] for s in c.stations.values())
    waiting = sum(d["статус"] in ("на одобрении", "ждёт правки") for d in c.by_id.values())
    return join(
        head("📊", "Статистика"),
        f"Станций: {len(c.stations)} · подходят {sum(v for k, v in prio.items() if k != '-')} "
        f"(A {prio['A']}, B {prio['B']}, C {prio['C']})\n"
        f"Писем отправлено: {len(sent)} · сегодня {sum(d['отправлено'] == t for d in sent)} "
        f"(первых {first_today} из {c.cfg['limits']['new_per_day']})\n"
        f"Ответов продавцов: {sum(r['направление'] == 'in' for r in c.dialogs)}",
        section("Статусы подходящих", [f"{esc(k)}: {v}" for k, v in statuses.most_common()]) or "Статусы подходящих: нет",
        para("Ждут твоего решения:", f"{waiting} черновиков, {statuses['эскалация']} эскалаций"),
        f"Режим: {esc(c.mode())}{', ' + b('ПАУЗА') if c.paused() else ''} · "
        f"bounce сегодня: {esc(c.state.get(f'bounce {t}') or 0)}")


def status(c: Ctx, station_id: str) -> str:
    st = c.stations.get(station_id.upper())
    if not st:
        return no_station(station_id)
    items = []
    for x in sheets.ITEMS:  # ✅ received, ❔ asked, ▫️ not asked; 🚫 the seller refused to answer
        v = str(st.get(x) or "")
        icon = "✅" if v.startswith("получен") else "🚫" if v.startswith("отказ") else "❔" if v == "спрошено" else "▫️"
        val = v.removeprefix("получен").lstrip(": ") if icon == "✅" else "" if v in ("", "спрошено", "не спрошено") else v
        items.append(f"{icon} {esc(x)}" + (f": {esc(val)}" if val else ""))
    talk = [r for r in c.dialogs if r["id станции"] == st["id"]][-3:]
    arrow = {"in": "📥", "out": "📤"}
    return join(
        head("🏭", st["id"], st["заголовок"][:90]) + "\n" + " · ".join(esc(x) for x in (
            f"{num(st['мощность кВт'])} кВт", f"{num(st['цена EUR'])} €", f"{num(st['EUR/кВт'])} €/кВт",
            st["регион"],
            f"приоритет {st['приоритет']}")),
        f"{b('Статус:')} {esc(st['статус переписки'])} · последний контакт {esc(st['последний контакт'] or '-')} · "
        f"далее: {esc(st['следующий шаг'] or '-')}\nКанал: {esc(st['канал'])} · {link(st['ссылка'], 'объявление')}"
        + (f"\n⚑ {esc(st['примечание'])}" if st.get("примечание") else ""),
        f"{b('Пункты')} {received(st)}/{len(sheets.ITEMS)}\n" + "\n".join(items)
        + "\n" + i("✅ получен · ❔ спрошено · ▫️ не спрошено"),
        section("Последнее", [f"{esc(r['дата'])} {arrow.get(r['направление'], esc(r['направление']))} "
                              f"{esc(r['краткое содержание'][:200])}" for r in talk]))


def queue(c: Ctx) -> str:
    rows = [r for r in sheets.read(c.api, c.sid, "Ручная очередь") if r["статус"] == "отправить вручную"]
    if not rows:
        return head("📱", "Ручная очередь пуста")
    return join(head("📱", "Отправить вручную", len(rows)), "Текст и ссылки во вкладке «Ручная очередь».", "\n".join(
        f"• {esc(r['id станции'])} · {esc(r['канал'])} · {link(r['ссылка на объявление'], 'объявление')}" for r in rows))


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
    held = sorted({r["id станции"] for r in sheets.read(c.api, c.sid, "Сводки") if r["статус"] == "на одобрении"})
    bounce = sum(int(v or 0) for k, v in c.state.items() if k.startswith("bounce ") and k[7:] >= since)
    kinds = ", ".join(f"{k} {v}" for k, v in Counter(d["тип"].split(",")[0] for d in sent).items()) or "-"
    warn = stuck(c, today) + [f"⚠️ Поиск {x}" for x in search_health(c.state, local_now(c.cfg).replace(tzinfo=None))]
    trial = c.cfg.get("gcp", {}).get("trial_ends")
    if trial and 0 <= (trial - today).days <= 14:
        warn.append(f"⏰ Free Trial Google Cloud кончается {trial:%d.%m.%Y}: Billing → Activate full account, "
                    "иначе Google остановит VM (потолок $5 остаётся)")
    if c.state.get("последняя ошибка") and c.state["последняя ошибка"][:10] >= since:
        warn.append(f"⚠️ Последняя ошибка: {esc(c.state['последняя ошибка'])}")
    return join(
        head("☀️", "Дайджест", f"{today:%d.%m}", f"режим {c.mode()}", "ПАУЗА" if c.paused() else ""),
        para("Новые за сутки:", f"{len(new)}, подходят {len(fresh)}",
             [esc(f"{s['id']} · {s['мощность кВт']} кВт · {s['EUR/кВт']} €/кВт · {s['регион']}") for s in fresh]),
        para("Отправлено за сутки:", f"{len(sent)} ({esc(kinds)}) · ответов {len(replies)} · отказов {len(refused)}"
             + (f" · bounce {bounce}" if bounce else "")),
        para("Ждут твоего решения", "", [f"черновики: {esc(', '.join(waiting) or 'нет')}",
                                        f"эскалации: {esc(', '.join(escal) or 'нет')}"]),
        para("Топ-кандидаты", "" if top else "нет", [
            esc(f"{s['id']} · {s['приоритет']} · {s['мощность кВт']} кВт · {s['статус переписки']} · "
                f"пунктов {received(s)}/{len(sheets.ITEMS)}") for s in top]),
        para("Ручная очередь:", f"{len(manual)}"
             + (f" ({esc(', '.join(r['id станции'] for r in manual))})" if manual else "")),
        para("Сводки директору ждут ✅:", esc(", ".join(held))) if held else "",
        f"Google Cloud: {esc(c.state['расходы Google Cloud'])}" if c.state.get("расходы Google Cloud") else "",
        f"{b('Внимание')}\n" + "\n".join(warn) if warn else "")


LISTING_SITES = ("bazar.bg", "alo.bg", "kpd.bg", "imot.bg", "olx.bg")  # 0 links there = the site changed its markup
STALE_DAYS = {"брокеры": 8, "регионы": 8}  # weekly runs; every other search runs at least daily


def search_health(state: dict, now: datetime) -> list[str]:
    """Problems from the «поиск <источник>» keys every search run writes: an error, 0 links on a listing site,
    or a source that hasn't run for days (a dead timer, the PC routine not running, a failed browser import). HTML lines."""
    out = []
    for k, v in sorted(state.items()):
        if not k.startswith("поиск ") or not v:
            continue
        name = k.removeprefix("поиск ")
        try:
            age = (now - datetime.strptime(v[:16], "%Y-%m-%d %H:%M")).days
        except ValueError:
            continue
        if "ошибка" in v:
            out.append(f"{esc(name)}: {esc(v[17:])}")
        elif name in LISTING_SITES and "ссылок 0," in v:
            out.append(f"{esc(name)}: 0 ссылок, похоже, сайт поменял вёрстку")
        if age >= STALE_DAYS.get(name, 2):
            out.append(f"{esc(name)}: не запускался {age} дн.")
    return out


def stuck(c: Ctx, today: date) -> list[str]:
    """What waits on someone and for how long: an unread alo.bg reply is the warmest lead and nothing else reminds of it.
    HTML lines for the digest."""
    last_in: dict[str, str] = {}
    for r in c.dialogs:
        if r["направление"] == "in" and r["дата"] > last_in.get(r["id станции"], ""):
            last_in[r["id станции"]] = r["дата"]

    def age(day: str) -> int:
        try:
            return (today - date.fromisoformat(day)).days
        except ValueError:
            return 0
    alo = [f"{s['id']} ({age(last_in.get(s['id'], ''))} дн.)" for s in c.stations.values()
           if s["статус переписки"] == "ответ на alo" and age(last_in.get(s["id"], "")) >= 1]
    escal = [f"{s['id']} ({age(last_in.get(s['id'], ''))} дн.)" for s in c.stations.values()
             if s["статус переписки"] == "эскалация" and age(last_in.get(s["id"], "")) >= 2]
    old = [f"{d['id']} ({age(d.get('дата', ''))} дн.)" for d in c.by_id.values()
           if d["статус"] in ("на одобрении", "ждёт правки") and age(d.get("дата", "")) >= 2]
    sending = [d["id"] for d in c.by_id.values() if d["статус"] == "отправляется"]  # the bot died between claim and send
    return ([f"⏳ Не прочитан ответ на alo.bg: {esc(', '.join(alo))}"] if alo else []) + \
           ([f"⏳ Эскалации ждут тебя: {esc(', '.join(escal))}"] if escal else []) + \
           ([f"⏳ Черновики без решения: {esc(', '.join(old))}"] if old else []) + \
           ([f"⚠️ Письма зависли в «отправляется»: {esc(', '.join(sending))}. Проверь «Отправленные» в Gmail: ушло — "
             "поставь «отправлено», нет — «одобрено»."] if sending else [])


def local_now(cfg: dict) -> datetime:
    return datetime.now(ZoneInfo(cfg["owner"]["timezone"]))


def digest_due(c: Ctx, now: datetime | None = None) -> bool:
    """Once a day after digest_hour in the owner's time zone (Toronto)."""
    now = now or local_now(c.cfg)
    return now.hour >= c.cfg["notify"]["digest_hour"] and c.state.get("дайджест") != now.date().isoformat()


def sent(c: Ctx, station_id: str, today: date, text: str = "") -> str:
    """The owner sent an approved message personally: draft sent, station awaits a reply, queue rows closed.
    text (site_queue sent, the routine): exactly that message went out. Its draft and row are closed, the station's
    older unsent messages are superseded by it, and a paused dialog stays paused. The reply is HTML: site_queue prints
    notify.plain of it."""
    st = c.stations.get(station_id.upper())
    if not st:
        return no_station(station_id)
    t = today.isoformat()
    unsent = [d for d in c.by_id.values() if d["id станции"] == st["id"] and d["статус"] == "одобрено"
              and "ТЕСТ" not in d["тип"] and d.get("отправлено", "") in ("", "вручную", "DRY_RUN")]
    rows = [r for r in sheets.read(c.api, c.sid, "Ручная очередь")
            if r["id станции"] == st["id"] and r["статус"] == "отправить вручную"]
    if text:
        # its draft; the text edited in the sheet: the newest unsent alo/WhatsApp draft, the one the newest row belongs to
        ds = ([d for d in unsent if d["текст"] and d["текст"] in text]
              or [d for d in unsent if d["канал"] != "email"][-1:])
        last_row = max([r["_row"] for r in rows if r["текст"] == text], default=0)
        last_draft = max([d.get("_row", 0) for d in ds], default=0)
        newer = [d for d in unsent if d.get("_row", 0) > last_draft]  # their rows stay: they go out next
        rows = [{**r, "статус": "отправлено вручную" if r["текст"] == text else "отменено: заменено новым"}
                for r in rows if r["текст"] == text or r["_row"] < last_row
                and not any(x["текст"] and x["текст"] in r["текст"] for x in newer)]
        older = [d for d in unsent if d not in ds and d.get("_row", 0) < last_draft]
    else:
        ds, older, rows = unsent, [], [{**r, "статус": "отправлено вручную"} for r in rows]
    if not ds and not rows:
        return join(head("⚠️", "Нечего отмечать", st["id"]),
                    "У станции нет одобренных неотправленных сообщений: сначала ✅ на карточке.")
    sheets.update(c.api, c.sid, "Ручная очередь", rows)  # first: if Sheets fails here, nothing is half-done
    for d in older:
        d["статус"] = "отменено: заменено новым"
        c.changed_drafts[d["id"]] = d
    if not ds:  # the row went out but no draft matches (its text was edited in the sheet)
        c.new_dialogs.append({"id станции": st["id"], "направление": "out", "дата": t, "канал": f"{st['канал']} (вручную)",
                              "язык": "", "краткое содержание": "сообщение из «Ручной очереди»", "тред Gmail": ""})
    for d in ds:
        d.update({"статус": "отправлено", "отправлено": t})
        c.changed_drafts[d["id"]] = d
        c.new_dialogs.append({"id станции": st["id"], "направление": "out", "дата": t, "канал": f"{d['канал']} (вручную)",
                              "язык": d["язык"], "краткое содержание": f"{d['тип']}, отправил владелец", "тред Gmail": ""})
        if not d["тип"].startswith(("дожим", brief.DOC_REQUEST)):
            sheets.mark_asked(st, d["тип"].startswith("первое"))
    if (text or st["статус переписки"] == inbox.OWNER_LED) and st["статус переписки"] in inbox.PAUSED:
        st["последний контакт"] = t  # the seller wrote again while the routine was sending, or it is the owner's dialog
    else:
        st.update({"статус переписки": "ждём ответа", "последний контакт": t, "следующий шаг": "ждать ответа"})
    return join(head("📤", "Отмечено отправленным", st["id"], ", ".join(d["id"] for d in ds) or "строка очереди"),
                "Нет ответа: дожим через 4 и 9 дней.")


def note(c: Ctx, station_id: str, text: str, today: date) -> str:
    """Seller reply outside email (WhatsApp, Viber, call): same parsing as for an email in inbox.process.
    The reply is HTML: alo_reply prints notify.plain of it."""
    st = c.stations.get(station_id.upper())
    if not st:
        return no_station(station_id)
    if not text:
        return f"ℹ️ Пришли {code('/note ' + st['id'])} и текст ответа продавца."
    try:
        cl = llm.classify(text, {k: v for k, v in st.items() if k != "_row"})
    except llm.LLMError as e:
        return join(head("⚠️", "Не смог разобрать ответ", st["id"]), f"{esc(e)}. Пришли ещё раз.")
    category, t = inbox.decide(cl.category, cl.confidence, text), today.isoformat()
    got = inbox.apply_facts(st, cl) if category != "suspicious" else []  # as in inbox: no facts from a manipulative text
    st["последний контакт"] = t
    c.new_dialogs.append({"id станции": st["id"], "направление": "in", "дата": t, "канал": "вручную",
                          "язык": cl.language, "краткое содержание": f"[{category}] {cl.summary_ru}", "тред Gmail": ""})

    def reply(emoji: str, title: str, *tail: str) -> str:
        return join(head(emoji, title, st["id"], category), esc(cl.summary_ru),
                    para("В таблицу:", esc(", ".join(got))) if got else "", *tail)
    status = st["статус переписки"]
    if status in inbox.HOLD and category != "auto_reply" and (category not in inbox.CLOSE or status == "отказ"):
        return reply("⏸", "Диалог на паузе", f"Статус «{esc(status)}»: ответ записал, черновик не делаю. Решение за тобой.")
    if category in inbox.ESCALATE:
        st["статус переписки"], st["следующий шаг"] = "эскалация", f"решение владельца: {category}"
        return reply("🚨", "Это решаешь ты", "Черновик не делаю. Диалог на паузе.")
    if category in inbox.CLOSE:
        c.new_refusals.append({"контакт": st.get("email") or st.get("телефон") or st["ссылка"], "id станции": st["id"],
                               "причина": "продано" if category == "sold" else "отказ", "дата": t,
                               "что написал": cl.summary_ru})
        st["статус переписки"], st["следующий шаг"] = "отказ", "не писать автоматически"
        return reply("📕", "Записал в «Отказы»")
    if category == "broker_redirect":
        return reply("↪️", "Новый контакт",
                     f"{code(cl.new_contact) if cl.new_contact else '?'}: добавь его в «Станции» вручную.")
    if category == "auto_reply":
        return reply("🤖", "Автоответ", "Ничего не делаю.")
    if not inbox.missing_items(st):
        st["статус переписки"] = "квалифицирована"
        return reply("✅", "Станция квалифицирована", "Все пункты раздела 3 получены.")
    try:
        d = inbox.manual_reply_draft(st, cl, sheets.next_id(c.by_id))
    except llm.LLMUnavailable as e:  # not recorded: alo_reply keeps «ответ на alo», the next run reads it again
        c.new_dialogs.pop()
        return join(head("⚠️", "Не смог составить ответ", st["id"]), f"{esc(e)}. Пришли ещё раз.")
    except llm.LLMError as e:
        return reply("⚠️", "Черновик ответа не получился", esc(e))
    c.by_id[d["id"]] = d
    c.new_drafts.append(d)
    st["статус переписки"] = "черновик ответа"
    return reply("📝", "Ответ записан",
                 f"Не хватает пунктов: {len(inbox.missing_items(st))}. Черновик ответа следующим сообщением.")


def command(text: str, c: Ctx, today: date) -> str:
    cmd, _, arg = text.strip().partition(" ")
    cmd, arg = cmd.split("@")[0].lower(), arg.strip()
    if cmd == "/stats":
        return stats(c, today)
    if cmd in ("/pause", "/resume"):
        c.settings["пауза"] = "да" if cmd == "/pause" else "нет"
        return (join(head("⏸", "Пауза"), "Продавцам ничего не уходит, входящие разбираются, эскалации приходят.\n"
                     f"{code('/resume')} чтобы продолжить.") if cmd == "/pause" else head("▶️", "Отправка возобновлена"))
    if cmd == "/auto" and arg in ("on", "off"):
        c.settings["approval_mode"] = "auto" if arg == "on" else "manual"
        return (join(head("🤖", "Автоотправка включена"), "Письма без нарушений правил уходят сами, эскалации и всё "
                     "спорное приходят тебе. Черновики, которые уже ждут кнопок, остаются за тобой.\n"
                     f"{code('/auto off')} чтобы вернуть ручной режим.")
                if arg == "on" else join(head("✋", "Ручной режим"), "Каждое письмо только после твоего ✅."))
    if cmd == "/queue":
        return queue(c)
    if cmd == "/digest":
        return digest(c, today)
    if cmd == "/blacklist" and arg:
        c.blacklist_add.append({"контакт или домен": arg.lower(), "причина": "владелец", "дата": today.isoformat()})
        return join(head("⛔", "В чёрном списке", arg), "Больше не пишем.")
    if cmd == "/status" and arg:
        return status(c, arg)
    if cmd == "/brief" and arg:
        return brief.status(c.api, c.sid, arg.split()[0])
    if cmd == "/sent" and arg:
        return sent(c, arg.split()[0], today)
    if cmd == "/note" and arg:
        sid_, reply = (arg.split(maxsplit=1) + [""])[:2]  # the reply text may span several lines
        return note(c, sid_, reply.strip(), today)
    # tapped from the menu without an argument
    hints = {"/auto": f"Сейчас режим {esc(c.mode())}. Пришли {code('/auto on')} или {code('/auto off')}.",
             "/status": f"Пришли {code('/status')} и id станции, например {code('/status S019')}.",
             "/blacklist": f"Пришли {code('/blacklist')} и email, телефон или домен.",
             "/sent": f"Пришли {code('/sent')} и id станции, например {code('/sent S020')}.",
             "/note": f"Пришли {code('/note')}, id станции и текст ответа продавца, например {code('/note S020 текст')}.",
             "/brief": f"Пришли {code('/brief')} и id станции, например {code('/brief S020')}."}
    return f"ℹ️ {hints[cmd]}" if cmd in hints else HELP
