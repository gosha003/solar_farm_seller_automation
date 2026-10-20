from datetime import date

from solar_outreach import approval, followup

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


def test_late_first_followup_is_not_chased_by_the_second_at_once():
    up1 = {**FIRST, "id": "D002", "тип": "дожим 1", "отправлено": "2026-10-10"}   # sent late, after a /pause
    assert due([FIRST, up1], "2026-10-10") == []
    assert due([FIRST, up1], "2026-10-14") == []                                # 5 days apart, as day 4 -> day 9
    assert due([FIRST, up1], "2026-10-15")[0]["тип"] == "дожим 2"


def test_auto_reply_postpones_followups_but_the_letter_keeps_our_date():
    away = {"id станции": "S001", "направление": "in", "дата": "2026-10-03", "краткое содержание": "[auto_reply] отпуск"}
    assert due([FIRST], "2026-10-05", [away]) == []
    [d] = due([FIRST], "2026-10-07", [away])
    assert "01.10.2026" in d["текст"]


# the seller answered on 10-03, our reply with the remaining questions went out the same day
IN = {"id станции": "S001", "направление": "in", "дата": "2026-10-03", "краткое содержание": "[info_provided] работи"}
REPLY = {**FIRST, "id": "D002", "тип": "ответ", "кому": "owner@firm.bg", "тема": "Re: Запитване - Плевен",
         "отправлено": "2026-10-03", "тред Gmail": "t2"}


def test_quiet_after_our_reply_gets_followups_counted_from_it():
    assert due([FIRST, REPLY], "2026-10-06", [IN]) == []
    [d] = due([FIRST, REPLY], "2026-10-07", [IN])
    assert d["тип"] == "дожим 1" and d["тред Gmail"] == "t2" and d["кому"] == "owner@firm.bg"
    assert d["тема"] == "Re: Запитване - Плевен" and "03.10.2026" in d["текст"] and "съобщението" in d["текст"]
    assert d["нарушения правил"] == ""
    up1 = {**d, "статус": "отправлено", "отправлено": "2026-10-07"}
    assert due([FIRST, REPLY, up1], "2026-10-11", [IN]) == []
    assert due([FIRST, REPLY, up1], "2026-10-12", [IN])[0]["тип"] == "дожим 2"
    up2 = {**up1, "id": "D004", "тип": "дожим 2"}
    assert due([FIRST, REPLY, up1, up2], "2026-12-01", [IN]) == []


def test_two_followups_per_contact_across_both_stages():
    up1 = {**FIRST, "id": "D003", "тип": "дожим 1", "отправлено": "2026-10-05"}
    reply = {**REPLY, "отправлено": "2026-10-06"}
    later = {**IN, "дата": "2026-10-06"}
    [d] = due([FIRST, up1, reply], "2026-10-10", [later])
    assert d["тип"] == "дожим 2"                                  # the stage-1 follow-up counts, its days don't
    assert due([FIRST, up1, reply, {**d, "статус": "отправлено", "отправлено": "2026-10-10"}], "2026-12-01", [later]) == []
    cancelled = {**up1, "статус": "отменено: продавец ответил", "отправлено": ""}
    assert due([FIRST, cancelled, reply], "2026-10-10", [later])[0]["тип"] == "дожим 1"   # never went out: not counted


def test_no_followup_when_the_last_word_is_the_sellers():
    assert due([FIRST], "2026-10-20", [IN]) == []                                 # answered, our reply not sent yet
    assert due([FIRST, {**REPLY, "отправлено": "DRY_RUN"}], "2026-10-20", [IN]) == []
    assert due([FIRST, REPLY], "2026-10-20", [IN, {**IN, "дата": "2026-10-05"}]) == []   # he wrote after our reply
    assert due([FIRST, REPLY], "2026-10-20", [{**IN, "дата": ""}]) == []          # no date: assume it is new
    away = {**IN, "дата": "2026-10-05", "краткое содержание": "[auto_reply] отпуск"}
    assert due([FIRST, REPLY], "2026-10-08", [IN, away]) == []                    # an auto-reply only postpones
    assert len(due([FIRST, REPLY], "2026-10-09", [IN, away])) == 1


def test_manual_reply_is_followed_up_on_its_channel():
    manual = {**REPLY, "канал": "телефон (ручная)", "кому": "+359888000111", "тема": "", "тред Gmail": ""}
    [d] = due([FIRST, manual], "2026-10-07", [IN])
    assert d["канал"] == "телефон (ручная)" and d["тема"] == "" and d["кому"] == "+359888000111"


def test_a_followup_is_cancelled_only_by_an_answer_after_it(monkeypatch):
    monkeypatch.setattr(approval.sheets, "read", lambda *a: [])
    monkeypatch.setattr(approval.sheets, "update", lambda *a: None)
    up = {**FIRST, "id": "D003", "тип": "дожим 1", "дата": "2026-10-07", "статус": "на одобрении", "отправлено": "",
          "текст": "Връщам се"}
    assert approval.cancel_followups(None, "", {"D003": up}, [IN]) == {} and up["статус"] == "на одобрении"
    approval.cancel_followups(None, "", {"D003": up}, [IN, {**IN, "дата": "2026-10-07"}])
    assert up["статус"] == "отменено: продавец ответил"
