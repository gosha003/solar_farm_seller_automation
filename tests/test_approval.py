import pytest

from solar_outreach import approval, llm

ST = {"id": "S001", "заголовок": "Фотоволтаична централа 1.5MW", "ссылка": "https://www.alo.bg/x-10983881",
      "регион": "Плевен", "мощность кВт": "1500", "цена EUR": "1160000", "EUR/кВт": "773", "продавец": "",
      "канал": "телефон (ручная)", "телефон": "+359888123456", "email": "", "статус переписки": "черновик",
      "приоритет": "A", "скоринг": "85", "площадка": "alo.bg"}


def cb(uid, data, owner=True):
    return {"update_id": uid, "_owner": owner, "callback_query": {"id": f"c{uid}", "data": data}}


def msg(uid, text):
    return {"update_id": uid, "_owner": True, "message": {"text": text}}


@pytest.fixture
def tg(monkeypatch):
    sent = []
    monkeypatch.setattr(approval.notify, "send", lambda text, buttons=None: sent.append(text) or len(sent))
    monkeypatch.setattr(approval.notify, "answer_callback", lambda *a: None)
    return sent


def run(monkeypatch, updates, drafts):
    monkeypatch.setattr(approval.notify, "updates", lambda offset: updates)
    state, stations = {"tg_offset": "5"}, {"S001": dict(ST)}
    by_id = {d["id"]: d for d in drafts}
    return (*approval.handle_updates(state, stations, by_id), state, stations)


def test_approve_goes_to_manual_queue_and_counts(monkeypatch, tg):
    d = approval.make_draft(ST, "D001", test=False)
    changed, queue, approved, state, stations = run(monkeypatch, [cb(5, "a:D001")], [d])
    assert changed["D001"]["статус"] == "одобрено" and approved == 1 and state["tg_offset"] == 6
    assert queue[0]["WhatsApp"].startswith("https://wa.me/359888123456?text=")
    assert stations["S001"]["статус переписки"] == "одобрено, ждёт отправки"


def test_test_draft_never_queued_and_foreign_updates_ignored(monkeypatch, tg):
    d = approval.make_draft(ST, "D002", test=True)
    changed, queue, approved, _, stations = run(monkeypatch, [cb(5, "s:D002", owner=False), cb(6, "a:D002")], [d])
    assert changed["D002"]["статус"] == "одобрено" and queue == [] and stations["S001"]["статус переписки"] == "черновик"


def test_edit_flow_rewrites_and_checks_rules(monkeypatch, tg):
    d = approval.make_draft(ST, "D003", test=False)
    calls = []

    def fake_rewrite(subject, body, instruction, listing, lang, examples):
        calls.append(instruction)
        return llm.Draft(subject=subject, body=body.replace("Здравейте", "Добър ден") + "\nПредлагам 700 000 EUR.")
    monkeypatch.setattr(approval.llm, "rewrite", fake_rewrite)
    changed, _, _, state, _ = run(monkeypatch, [cb(5, "e:D003"), msg(6, "поздоровайся иначе")], [d])
    assert calls == ["поздоровайся иначе"] and state["pending_edit"] == ""
    out = changed["D003"]
    assert out["статус"] == "на одобрении" and out["текст"].startswith("Добър ден")
    assert "сумма/цена покупки/бюджет" in out["нарушения правил"]       # the owner sees a warning
    assert "⚠️ Нарушения правил" in tg[-1]


def test_paused_dialog_is_not_sent(monkeypatch, tg):
    d = {**approval.make_draft({**ST, "канал": "email", "email": "s@abv.bg"}, "D005", test=False), "статус": "одобрено"}
    stations = {"S001": {**ST, "статус переписки": "эскалация"}}
    monkeypatch.setattr(approval.gmail, "service", lambda: None)
    monkeypatch.setattr(approval.gmail, "send", lambda *a, **k: pytest.fail("отправка при эскалации"))
    monkeypatch.setattr(approval.sheets, "read", lambda api, sid, tab: [])
    assert approval.send_approved(None, None, [d], {"D005": d}, stations, {"limits": {"send_pause_sec": [0, 0]}}) == []
    assert d["статус"] == "на паузе" and "на паузе" in tg[-1]


def test_llm_failure_keeps_draft(monkeypatch, tg):
    d = approval.make_draft(ST, "D004", test=False)

    def boom(*a):
        raise llm.LLMError("невалидный JSON после повтора")
    monkeypatch.setattr(approval.llm, "rewrite", boom)
    changed, *_ = run(monkeypatch, [cb(5, "e:D004"), msg(6, "короче")], [d])
    assert changed["D004"]["статус"] == "на одобрении" and changed["D004"]["текст"] == d["текст"]


EMAIL_ST = {**ST, "канал": "email", "email": "s@abv.bg"}


def test_auto_mode_approves_clean_drafts_only(tg):
    clean = approval.make_draft(EMAIL_ST, "D010", test=False)
    st, queue = dict(EMAIL_ST), []
    approval.present(clean, st, True, queue)
    assert clean["статус"] == "одобрено" and tg == [] and st["статус переписки"] == "одобрено, ждёт отправки"
    dirty = {**approval.make_draft(EMAIL_ST, "D011", test=False), "нарушения правил": "ссылка"}
    approval.present(dirty, dict(EMAIL_ST), True, queue)
    assert dirty["статус"] == "на одобрении" and len(tg) == 1        # violation -> to the owner with buttons
    test = approval.make_draft(EMAIL_ST, "D012", test=True)
    approval.present(test, dict(EMAIL_ST), True, queue)
    manual = approval.make_draft(EMAIL_ST, "D013", test=False)
    approval.present(manual, dict(EMAIL_ST), False, queue)
    assert test["статус"] == manual["статус"] == "на одобрении"


def test_limits_per_run_and_per_day_but_replies_unlimited(monkeypatch, tg):
    monkeypatch.setenv("DRY_RUN", "false")
    sent = []
    monkeypatch.setattr(approval.gmail, "service", lambda: None)
    monkeypatch.setattr(approval.gmail, "send", lambda svc, to, *a: sent.append(to) or {"id": "m", "threadId": "t"})
    monkeypatch.setattr(approval.sheets, "read", lambda api, sid, tab: [])

    def mk(i, typ):
        return {**approval.make_draft(EMAIL_ST, f"D{i}", test=False), "статус": "одобрено", "тип": typ, "кому": f"s{i}@abv.bg"}
    ds = [mk(1, "первое"), mk(2, "первое"), mk(3, "ответ"), mk(4, "дожим 1"), mk(5, "ответ")]
    by_id, stations, cfg = {d["id"]: d for d in ds}, {"S001": dict(EMAIL_ST)}, {"limits": {"send_pause_sec": [0, 0]}}
    approval.send_approved(None, None, ds, by_id, stations, cfg, {"run": 1, "first": 5})
    assert sent == ["s1@abv.bg", "s3@abv.bg", "s5@abv.bg"]          # one first letter per run, all replies
    assert ds[1]["статус"] == ds[3]["статус"] == "одобрено"            # wait for the next run
    sent.clear()
    approval.send_approved(None, None, ds[1:2], by_id, stations, cfg, {"run": 1, "first": 0})
    assert sent == []                                                   # daily cap


def test_bounce_stop_switches_to_manual(monkeypatch, tg):
    urgent = []
    monkeypatch.setattr(approval.notify, "urgent", urgent.append)
    c = approval.owner.Ctx(None, None, {"approval_mode": "manual", "limits": {"bounce_rate_stop": 0.1}}, {}, {}, [],
                           {"approval_mode": "auto"}, {"bounce 2026-10-08": "2"})
    by_id = {str(i): {"отправлено": "2026-10-08"} for i in range(10)}
    approval.bounce_stop(c, by_id, "2026-10-08")
    assert c.mode() == "manual" and "Автоотправка выключена" in urgent[0]
    c.settings["approval_mode"], c.state["bounce 2026-10-08"] = "auto", "1"
    approval.bounce_stop(c, by_id, "2026-10-08")
    assert c.mode() == "auto"                                           # exactly 10%: not above the threshold


def test_dry_run_email_goes_to_manual_queue_and_never_auto_sent(monkeypatch, tg):
    monkeypatch.setenv("DRY_RUN", "true")
    d, st, queue = approval.make_draft(EMAIL_ST, "D020", test=False), dict(EMAIL_ST), []
    approval.approve(d, st, queue)
    assert queue[0]["канал"] == "email: s@abv.bg" and queue[0]["текст"].startswith("Тема: ")
    monkeypatch.setenv("DRY_RUN", "false")
    assert approval.to_send({"D020": d}) == []                       # the system won't send it later
