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
    assert "сумма/цена покупки/бюджет" in out["нарушения правил"]       # владельцу видно предупреждение
    assert "⚠️ Нарушения правил" in tg[-1]


def test_llm_failure_keeps_draft(monkeypatch, tg):
    d = approval.make_draft(ST, "D004", test=False)

    def boom(*a):
        raise llm.LLMError("невалидный JSON после повтора")
    monkeypatch.setattr(approval.llm, "rewrite", boom)
    changed, *_ = run(monkeypatch, [cb(5, "e:D004"), msg(6, "короче")], [d])
    assert changed["D004"]["статус"] == "на одобрении" and changed["D004"]["текст"] == d["текст"]
