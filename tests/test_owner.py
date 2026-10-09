from datetime import date, datetime

from solar_outreach import owner, sheets

CFG = {"approval_mode": "manual", "limits": {"new_per_day": 30}, "notify": {"digest_hour": 8},
       "owner": {"timezone": "America/Toronto"}}
ST = {"id": "S019", "заголовок": "ФЕЦ 1.5 MW", "мощность кВт": "1500", "цена EUR": "1160000", "EUR/кВт": "773",
      "регион": "Плевен", "приоритет": "A", "скоринг": "85", "статус переписки": "ждём ответа", "последний контакт": "",
      "следующий шаг": "", "канал": "email", "ссылка": "https://x", "дата найдено": "2026-10-08",
      **{i: "" for i in sheets.ITEMS}, "1 действующая": "получен: да"}
DAY = date(2026, 10, 8)


def ctx(monkeypatch=None):
    if monkeypatch:
        monkeypatch.setattr(owner.sheets, "read", lambda api, sid, tab: [])
    d = {"id": "D001", "id станции": "S019", "тип": "первое", "статус": "отправлено", "отправлено": "2026-10-08"}
    return owner.Ctx(None, None, CFG, {"S019": dict(ST)}, {"D001": d}, [], {}, {})


def test_pause_auto_blacklist_commands():
    c = ctx()
    assert "Пауза" in owner.command("/pause", c, DAY) and c.paused()
    owner.command("/resume", c, DAY)
    assert not c.paused()
    owner.command("/auto on", c, DAY)
    assert c.mode() == "auto"
    owner.command("/auto@SolarBGbot off", c, DAY)
    assert c.mode() == "manual"
    owner.command("/blacklist Spam@X.bg", c, DAY)
    assert c.blacklist_add[0]["контакт или домен"] == "spam@x.bg"
    assert owner.command("/whatever", c, DAY) == owner.HELP


def test_stats_status_digest(monkeypatch):
    c = ctx(monkeypatch)
    s = owner.command("/stats", c, DAY)
    assert "первых 1 из 30" in s and "Режим: manual" in s
    st = owner.command("/status s019", c, DAY)
    assert "1 действующая: получен: да" in st and "773 €/кВт" in st
    dg = owner.digest(c, date(2026, 10, 9))
    assert "S019" in dg and "пунктов 1/13" in dg and "Отправлено за сутки: 1" in dg


def test_digest_once_a_day_after_hour():
    c = ctx()
    assert not owner.digest_due(c, datetime(2026, 10, 9, 7, 59))
    assert owner.digest_due(c, datetime(2026, 10, 9, 8, 0))
    c.state["дайджест"] = "2026-10-09"
    assert not owner.digest_due(c, datetime(2026, 10, 9, 20, 0))


def test_menu_commands_without_argument_give_hint():
    c = ctx()
    assert "Сейчас режим manual" in owner.command("/auto", c, DAY) and c.mode() == "manual"
    assert "S019" in owner.command("/status", c, DAY)


def test_digest_reminds_about_trial_end(monkeypatch):
    c = ctx(monkeypatch)
    c.cfg = {**CFG, "gcp": {"trial_ends": date(2027, 1, 7)}}
    assert "Free Trial" not in owner.digest(c, date(2026, 12, 20))
    assert "Activate full account" in owner.digest(c, date(2026, 12, 25))


def test_sent_marks_manual_message_and_queue(monkeypatch):
    c = ctx(monkeypatch)
    updated = []
    monkeypatch.setattr(owner.sheets, "read", lambda api, sid, tab: [{"id станции": "S019", "статус": "отправить вручную", "_row": 2}])
    monkeypatch.setattr(owner.sheets, "update", lambda api, sid, tab, rows: updated.extend(rows))
    c.stations["S019"].update({i: "не спрошено" for i in sheets.ITEMS}, **{"статус переписки": "одобрено, ждёт отправки"})
    c.by_id["D002"] = {"id": "D002", "id станции": "S019", "тип": "первое", "статус": "одобрено", "отправлено": "",
                       "канал": "сайт (ручная)", "язык": "bg", "_row": 3}
    assert "отмечено отправленным (D002)" in owner.command("/sent s019", c, DAY)
    st = c.stations["S019"]
    assert st["статус переписки"] == "ждём ответа" and st["1 действующая"] == "спрошено" and st["5 год ввода"] == "не спрошено"
    assert c.changed_drafts["D002"]["отправлено"] == "2026-10-08" and updated[0]["статус"] == "отправлено вручную"
    assert "нет одобренных" in owner.command("/sent S019", c, DAY)


def test_note_extracts_facts_and_escalates(monkeypatch):
    c = ctx(monkeypatch)
    cl = owner.llm.Classification(category="info_provided", language="bg", summary_ru="работает с 2020", seller_questions=[],
                                  new_contact="", confidence="high",
                                  facts=[owner.llm.Fact(item="5 год ввода", answer="2020", refused=False)])
    monkeypatch.setattr(owner.llm, "classify", lambda text, st: cl)
    monkeypatch.setattr(owner.inbox, "reply_body", lambda st, c: "Благодаря")
    out = owner.command("/note S019 Работи от 2020\nцена по договаряне", c, DAY)
    assert "5 год ввода" in out and c.stations["S019"]["5 год ввода"] == "получен: 2020"
    assert c.new_dialogs[0]["направление"] == "in" and c.new_drafts[0]["канал"] == "email (вручную)"
    cl.category = "price_discussion"
    assert "🚨" in owner.command("/note S019 Може и по-евтино", c, DAY)
    assert c.stations["S019"]["статус переписки"] == "эскалация"
