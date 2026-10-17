from datetime import date

from solar_outreach import followup

ST = {"S001": {"id": "S001", "статус переписки": "ждём ответа"}}
FIRST = {"id": "D001", "id станции": "S001", "тип": "первое", "канал": "email", "кому": "s@abv.bg", "язык": "bg",
         "тема": "Запитване - Плевен", "статус": "отправлено", "отправлено": "2026-10-01", "тред Gmail": "t1"}


def due(drafts, today, dialogs=(), stations=ST):
    return followup.due(stations, {d["id"]: d for d in drafts}, list(dialogs), [4, 9], date.fromisoformat(today))


def test_schedule_4_then_9_days_in_same_thread():
    assert due([FIRST], "2026-10-04") == []
    [d] = due([FIRST], "2026-10-05")
    assert d["тип"] == "дожим 1" and d["тред Gmail"] == "t1" and d["тема"] == "Re: Запитване - Плевен"
    assert "01.10.2026" in d["текст"] and d["нарушения правил"] == "" and d["кому"] == "s@abv.bg"
    up1 = {**d, "статус": "отправлено", "отправлено": "2026-10-05"}
    assert due([FIRST, up1], "2026-10-09") == []
    assert due([FIRST, up1], "2026-10-10")[0]["тип"] == "дожим 2"
    up2 = {**up1, "id": "D003", "тип": "дожим 2"}
    assert due([FIRST, up1, up2], "2026-12-01") == []           # at most 2 follow-ups


def test_reply_skip_or_pending_cancels():
    reply = {"id станции": "S001", "направление": "in", "краткое содержание": "[info_provided] да"}
    assert due([FIRST], "2026-10-05", [reply]) == []
    auto = {**reply, "краткое содержание": "[auto_reply] отпуск"}
    assert len(due([FIRST], "2026-10-05", [auto])) == 1          # an auto-reply doesn't cancel
    skipped = {**FIRST, "id": "D002", "тип": "дожим 1", "статус": "пропущено", "отправлено": ""}
    assert due([FIRST, skipped], "2026-10-20") == []            # the owner skipped it: no more follow-ups
    assert due([FIRST], "2026-10-05", stations={"S001": {"id": "S001", "статус переписки": "эскалация"}}) == []
    assert due([{**FIRST, "отправлено": "DRY_RUN"}], "2026-10-20") == []
    assert due([{**FIRST, "тип": "первое, ТЕСТ"}], "2026-10-20") == []


def test_manual_first_message_gets_manual_followup():
    manual = {**FIRST, "канал": "сайт (ручная)", "тред Gmail": "", "кому": "https://www.alo.bg/x"}
    [d] = due([manual], "2026-10-05")
    assert d["канал"] == "сайт (ручная)" and d["тред Gmail"] == ""
    [e] = due([{**FIRST, "тред Gmail": ""}], "2026-10-05")
    assert e["канал"] == "email (вручную)"                       # the owner's own mailbox: the system won't send
