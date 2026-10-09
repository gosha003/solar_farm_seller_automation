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
