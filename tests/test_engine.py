from contextlib import nullcontext

from solar_outreach import engine


def test_success_clears_the_offline_failure_counter(monkeypatch, tmp_path):
    monkeypatch.setattr(engine, "FAIL_FILE", tmp_path / "failures")
    monkeypatch.setattr(engine, "single_run", lambda *a, **k: nullcontext())
    engine.FAIL_FILE.write_text("5")                   # left over from an earlier Sheets outage
    monkeypatch.setattr(engine, "run", lambda test_n=0, with_inbox=True, mail=None: 7)
    assert engine.once() == 7 and not engine.FAIL_FILE.exists()


def test_sigterm_during_a_pass_waits_for_it(monkeypatch):
    monkeypatch.setattr(engine, "STOP", {"asked": False, "busy": True})
    engine.stop()                                      # no SystemExit: the pass in progress finishes first
    assert engine.STOP["asked"]


def test_llm_outage_counts_as_a_failed_pass(monkeypatch, sheet):
    calls, fails = [], []
    monkeypatch.setattr(engine.gmail_client, "service", lambda: None)
    monkeypatch.setattr(engine.inbox, "process", lambda *a, **k: {"входящих": 1, "LLM недоступен": "API: 529"})
    monkeypatch.setattr(engine.approval, "main", lambda test_n=0, full=True, fresh=True: calls.append((full, fresh)) or 3)
    monkeypatch.setattr(engine, "failure", fails.append)
    assert engine.run() == 3 and calls == [(False, False)] and fails == ["LLM недоступен: API: 529"]


def test_mail_is_due_twice_a_day():
    from datetime import datetime
    slots = ["08:30", "20:30"]
    at = lambda s: datetime.fromisoformat(f"2026-10-23T{s}")
    assert not engine.mail_due(at("08:29"), "2026-10-22T20:30", slots)
    assert engine.mail_due(at("08:31"), "2026-10-22T20:30", slots)
    assert not engine.mail_due(at("15:00"), "2026-10-23T08:31", slots)
    assert engine.mail_due(at("20:30"), "2026-10-23T08:31", slots)
    assert engine.mail_due(at("03:00"), "2026-10-22T08:31", slots)        # the bot was down at 20:30: caught up
    assert engine.mail_due(at("03:00"), "", slots)                         # never checked
    assert not engine.mail_due(at("09:15"), "2026-10-23 8:30", slots)      # how the sheet may show 08:30


def test_between_checks_the_inbox_is_left_alone_and_mail_asks_for_it(monkeypatch, sheet):
    from datetime import datetime
    reads, sent, fresh_calls = [], [], []
    monkeypatch.setattr(engine.owner, "local_now", lambda cfg: datetime(2026, 10, 23, 15, 0))
    monkeypatch.setattr(engine.gmail_client, "service", lambda: None)
    monkeypatch.setattr(engine.inbox, "process", lambda *a, **k: reads.append(1) or {"входящих": 0})
    monkeypatch.setattr(engine.director, "replies", lambda *a, **k: 0)
    monkeypatch.setattr(engine.approval, "main", lambda test_n=0, full=True, fresh=True: fresh_calls.append(fresh) or 5)
    monkeypatch.setattr(engine.notify, "try_send", sent.append)
    sheet.add("_state", {"ключ": engine.MAIL_KEY, "значение": "2026-10-23T08:30"})
    assert engine.run() == 5 and reads == [] and fresh_calls == [False]   # 15:00: no mail at all, bounces neither
    sheet.add("Настройки", {"параметр": engine.owner.MAIL_NOW, "значение": "да"})   # /mail
    engine.run()
    assert reads == [1] and fresh_calls[-1] and "Почта проверена" in sent[0]
    assert sheet.kv("Настройки")[engine.owner.MAIL_NOW] == "нет" and sheet.kv("_state")[engine.MAIL_KEY] == "2026-10-23T15:00"
    engine.run()
    assert reads == [1]
    engine.run(mail=True)                                                  # a manual single pass
    assert reads == [1, 1]


def test_a_failed_director_check_stays_due_until_the_alert(monkeypatch, sheet):
    from datetime import datetime
    reads, fails = [], []
    monkeypatch.setattr(engine.owner, "local_now", lambda cfg: datetime(2026, 10, 23, 8, 31))
    monkeypatch.setattr(engine.gmail_client, "service", lambda: None)
    monkeypatch.setattr(engine.inbox, "process", lambda *a, **k: reads.append(1) or {"входящих": 0})
    monkeypatch.setattr(engine.approval, "main", lambda test_n=0, full=True, fresh=True: 5)
    def boom(*a, **k):
        raise engine.llm.LLMUnavailable("API: 529")
    monkeypatch.setattr(engine.director, "replies", boom)
    monkeypatch.setattr(engine, "failure", lambda err: fails.append(err) or len(fails))
    sheet.add("_state", {"ключ": engine.MAIL_KEY, "значение": "2026-10-22T20:30"})
    for _ in range(engine.ALERT_AFTER):
        engine.run()
    assert reads == [1] * engine.ALERT_AFTER and len(fails) == engine.ALERT_AFTER
    assert sheet.kv("_state")[engine.MAIL_KEY] == "2026-10-23T08:31"    # after the alert: the next slot, not every pass
    engine.run()
    assert len(reads) == engine.ALERT_AFTER
