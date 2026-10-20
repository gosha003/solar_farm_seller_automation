"""approval.main end to end on an in-memory sheet: a letter goes out exactly once, whatever fails after it."""
import pytest

from solar_outreach import approval, sheets

STATION = {"id": "S001", "дата найдено": "2026-10-01", "площадка": "bazar.bg", "ссылка": "https://bazar.example/obiava-1",
           "заголовок": "Фотоволтаична централа 1.5 MW", "регион": "Плевен", "мощность кВт": "1500", "цена EUR": "неизвестно",
           "EUR/кВт": "неизвестно", "год": "неизвестно", "режим": "неизвестно", "телефон": "", "email": "seller@example.org",
           "продавец": "", "канал": "email", "отбор": "кандидат", "скоринг": "50", "приоритет": "B",
           **{i: "не спрошено" for i in sheets.ITEMS}, "статус переписки": "новая", "примечание": ""}


@pytest.fixture
def bot(monkeypatch, sheet):
    """Telegram and Gmail fakes; returns (sheet, sent emails, Telegram texts)."""
    monkeypatch.setenv("DRY_RUN", "false")
    sent, tg = [], []
    monkeypatch.setattr(approval.notify, "updates", lambda offset: [])
    monkeypatch.setattr(approval.notify, "send", lambda text, buttons=None: tg.append(text) or len(tg))
    monkeypatch.setattr(approval.notify, "try_send", tg.append)
    monkeypatch.setattr(approval.notify, "urgent", tg.append)
    monkeypatch.setattr(approval.gmail, "service", lambda: None)
    monkeypatch.setattr(approval.gmail, "send", lambda svc, to, *a: sent.append(to) or {"id": "m", "threadId": f"t{len(sent)}"})
    monkeypatch.setattr(approval.owner, "digest_due", lambda c: False)
    monkeypatch.setattr(approval.time, "sleep", lambda s: None)   # the 60-180 s pause between letters
    sheet.add("Настройки", {"параметр": "approval_mode", "значение": "auto"})
    return sheet, sent, tg


def test_auto_letter_goes_out_once_even_if_the_pass_dies_after_sending(bot):
    sheet, sent, _ = bot
    sheet.add("Станции", STATION)
    approval.main()                                   # drafts and auto-approves; a draft is sent once it is a sheet row
    assert sent == [] and sheet.tabs["Черновики"][0]["статус"] == "одобрено"
    sheet.fail[("_state", "update")] = RuntimeError("Sheets 503")   # the end-of-pass write dies after the send
    with pytest.raises(RuntimeError):
        approval.main()
    assert sent == ["seller@example.org"] and sheet.tabs["Черновики"][0]["статус"] == "отправлено"  # saved at once
    approval.main()
    approval.main()
    assert sent == ["seller@example.org"]             # never a second letter
    assert sheet.tabs["Станции"][0]["статус переписки"] == "ждём ответа" and len(sheet.tabs["Диалоги"]) == 1


def test_failed_station_write_after_a_send_is_retried_at_the_end_of_the_pass(bot):
    sheet, sent, _ = bot
    sheet.add("Станции", STATION)
    approval.main()
    sheet.fail[("Диалоги", "append")] = RuntimeError("Sheets 429")    # the per-letter write fails once
    approval.main()
    approval.main()
    assert sent == ["seller@example.org"] and len(sheet.tabs["Диалоги"]) == 1   # the thread row inbox matches replies by
    assert sheet.tabs["Станции"][0]["статус переписки"] == "ждём ответа"


def test_one_draft_gmail_rejects_does_not_resend_the_others(bot, monkeypatch):
    sheet, sent, tg = bot
    reply = {"id": "D001", "id станции": "S001", "тип": "ответ", "канал": "email", "кому": "seller@example.org",
             "тема": "Re: x", "текст": "t", "статус": "одобрено", "язык": "bg"}
    sheet.add("Станции", {**STATION, "статус переписки": "черновик ответа"},
              {**STATION, "id": "S002", "email": "bad@example.org.тел", "статус переписки": "черновик ответа"})
    sheet.add("Черновики", reply, {**reply, "id": "D002", "id станции": "S002", "кому": "bad@example.org.тел"})

    def send(svc, to, *a):
        if to.startswith("bad"):
            raise ValueError("400 Invalid To header")
        sent.append(to)
        return {"id": "m", "threadId": "t1"}
    monkeypatch.setattr(approval.gmail, "send", send)
    approval.main()
    approval.main()
    assert sent == ["seller@example.org"]
    assert [d["статус"] for d in sheet.tabs["Черновики"]] == ["отправлено", "ошибка отправки"]
    assert any("Gmail не принял" in t for t in tg)


def test_refused_or_blacklisted_contact_gets_nothing_on_any_channel(bot):
    sheet, sent, tg = bot
    phone = {**STATION, "id": "S002", "ссылка": "https://alo.example/2", "email": "", "телефон": "+359888000111",
             "канал": "телефон (ручная)"}
    sheet.add("Станции", {**STATION, "email": "Seller@Example.org"}, phone)
    sheet.add("Отказы", {"контакт": "seller@example.org", "id станции": "S900", "причина": "отказ"})
    sheet.add("Чёрный список", {"контакт или домен": "0888 000 111", "причина": "владелец"})  # /blacklist in local format
    approval.main()
    assert sheet.tabs["Черновики"] == [] and sheet.tabs["Ручная очередь"] == [] and sent == []
    assert [s["статус переписки"] for s in sheet.tabs["Станции"]] == ["не писать", "не писать"]
    assert "в «Отказах»" in sheet.tabs["Станции"][0]["примечание"]


def test_queue_gate_marks_rows_of_blocked_contacts(sheet, monkeypatch):
    sent = []
    monkeypatch.setattr(approval.notify, "try_send", sent.append)
    sheet.add("Отказы", {"контакт": "+359888000111, +359888000222"})
    queue = [{"id станции": "S002", "статус": "отправить вручную"}, {"id станции": "S003", "статус": "отправить вручную"}]
    stations = {"S002": {"телефон": "0888000222", "email": "", "ссылка": "u2"}, "S003": {"телефон": "+359888000333"}}
    approval.gate(None, None, queue, stations)
    assert queue[0]["статус"].startswith("не отправлять") and queue[1]["статус"] == "отправить вручную"


def test_followup_queued_before_the_reply_is_cancelled(bot):
    sheet, _, _ = bot
    sheet.add("Станции", {**STATION, "канал": "телефон (ручная)", "статус переписки": "ждём ответа"})
    sheet.add("Черновики", {"id": "D001", "id станции": "S001", "тип": "первое", "канал": "телефон (ручная)",
                            "статус": "отправлено", "отправлено": "2026-10-01", "текст": "Здравейте"},
              {"id": "D002", "id станции": "S001", "тип": "дожим 1", "канал": "телефон (ручная)", "статус": "одобрено",
               "текст": "Връщам се по повод запитването ми"})
    sheet.add("Ручная очередь", {"id станции": "S001", "текст": "Връщам се по повод запитването ми", "статус": "отправить вручную"})
    sheet.add("Диалоги", {"id станции": "S001", "направление": "in", "краткое содержание": "[info_provided] работи"})
    approval.main()
    assert sheet.tabs["Черновики"][1]["статус"] == "отменено: продавец ответил"
    assert sheet.tabs["Ручная очередь"][0]["статус"] == "отменено: продавец ответил"


def test_old_card_tapped_after_escalation_keeps_the_pause():
    st, queue = {**STATION, "статус переписки": "эскалация"}, []
    approval.approve(approval.make_draft(STATION, "D001", test=False), st, queue)
    assert st["статус переписки"] == "эскалация"


def test_a_failing_update_does_not_block_the_next_ones(monkeypatch):
    out = []
    monkeypatch.setattr(approval.notify, "send", lambda text, buttons=None: out.append(text))
    monkeypatch.setattr(approval.notify, "try_send", out.append)
    monkeypatch.setattr(approval.notify, "answer_callback", lambda *a: out.append(a))

    def command(text, c, today):
        if text == "/boom":
            raise KeyError("S077")
        return "ok " + text
    monkeypatch.setattr(approval.owner, "command", command)
    monkeypatch.setattr(approval.notify, "updates", lambda offset: [
        {"update_id": 7, "_owner": True, "callback_query": {"id": "c7", "data": "a:D001"}},   # its station row is gone
        {"update_id": 8, "_owner": True, "message": {"text": "/boom"}},
        {"update_id": 9, "_owner": True, "message": {"text": "/pause"}}])
    state = {"tg_offset": "7"}
    approval.handle_updates(state, {}, {"D001": {"id": "D001", "id станции": "S077", "статус": "на одобрении"}}, ctx=object())
    assert state["tg_offset"] == 10 and "ok /pause" in out and any("Не смог выполнить" in str(x) for x in out)


def test_only_a_pass_with_the_inbox_resets_the_failure_counter(bot):
    sheet, _, _ = bot
    sheet.add("_state", {"ключ": "ошибок подряд", "значение": "2"})
    approval.main(full=False)                          # the daemon's approval-only pass between failing inbox passes
    assert sheet.kv("_state")["ошибок подряд"] == "2"
    approval.main()
    assert sheet.kv("_state")["ошибок подряд"] == "0"


def test_a_failed_write_right_after_the_send_never_resends(bot, monkeypatch):
    sheet, sent, _ = bot
    sheet.add("Станции", STATION)
    approval.main()
    keep = sheets.update

    def down_after_send(api, sid, tab, rows):
        if tab == "Черновики" and rows and rows[0]["статус"] == "отправлено":
            raise RuntimeError("Sheets 503")
        keep(api, sid, tab, rows)
    monkeypatch.setattr(sheets, "update", down_after_send)
    with pytest.raises(RuntimeError):
        approval.main()
    monkeypatch.setattr(sheets, "update", keep)
    approval.main()
    assert sent == ["seller@example.org"] and sheet.tabs["Черновики"][0]["статус"] == "отправляется"   # claimed before the send
    assert any("отправляется" in x for x in approval.owner.stuck(
        approval.owner.Ctx(None, None, {}, {}, {d["id"]: d for d in sheet.read(None, None, "Черновики")}, [], {}, {}),
        approval.date.today()))                                                   # and the digest asks the owner


def test_gmail_failing_before_the_send_leaves_the_draft_for_the_next_pass(bot, monkeypatch):
    sheet, sent, _ = bot
    sheet.add("Станции", STATION)
    approval.main()
    ok = approval.gmail.send

    def down(*a):
        raise approval.gmail.NotSent("RefreshError")
    monkeypatch.setattr(approval.gmail, "send", down)
    approval.main()
    assert sheet.tabs["Черновики"][0]["статус"] == "одобрено"
    monkeypatch.setattr(approval.gmail, "send", ok)
    approval.main()
    assert sent == ["seller@example.org"]


def test_no_second_first_letter_to_any_address_already_written_to(bot):
    sheet, sent, tg = bot
    sheet.add("Станции", {**STATION, "email": "sales@agency.example, office@agency.example", "статус переписки": "квалифицирована"},
              {**STATION, "id": "S002", "ссылка": "https://bazar.example/obiava-2", "мощность кВт": "2000",
               "email": "office@agency.example"},
              {**STATION, "id": "S003", "ссылка": "https://bazar.example/obiava-3", "email": "seller@example.org"})
    sheet.add("Черновики", {"id": "D001", "id станции": "S001", "тип": "первое", "канал": "email", "статус": "отправлено",
                            "кому": "sales@agency.example, office@agency.example", "отправлено": "2026-10-01", "тред Gmail": "t1"},
              {"id": "D002", "id станции": "S900", "тип": "первое", "канал": "email", "статус": "отправлено",
               "кому": "seller@example.org", "отправлено": "2026-10-02"})              # sent by the owner's hand (/sent)
    approval.main()
    approval.main()
    approval.main()
    assert sent == [] and sum("уже уходило" in t for t in tg) == 2


def test_only_the_newest_reply_goes_out(bot):
    sheet, sent, _ = bot
    reply = {"id станции": "S001", "тип": "ответ", "канал": "email", "кому": "seller@example.org", "тема": "Re: x",
             "статус": "одобрено", "язык": "bg"}
    sheet.add("Станции", {**STATION, "статус переписки": "черновик ответа"})
    sheet.add("Черновики", {**reply, "id": "D001", "текст": "старый"}, {**reply, "id": "D002", "текст": "новый"})
    approval.main()
    approval.main()
    assert sent == ["seller@example.org"]
    assert [d["статус"] for d in sheet.tabs["Черновики"]] == ["заменено новым ответом", "отправлено"]


def test_owner_is_not_told_to_send_what_the_routine_sends(monkeypatch):
    out = []
    monkeypatch.setattr(approval.notify, "send", lambda text, buttons=None: out.append(text))
    alo = {**STATION, "канал": "сайт (ручная)", "ссылка": "https://www.alo.bg/x-10983881", "email": ""}
    viber_only = {**STATION, "канал": "сайт (ручная)", "ссылка": "https://bazar.example/obiava-1", "email": ""}
    for st in (alo, viber_only):
        approval.present(approval.make_draft(st, "D001", test=False), dict(st), True, [])
    assert "сам не отправляй" in out[0] and len(out) == 4 and "/sent S001" in out[1]   # alo: one line; other site: + hint
