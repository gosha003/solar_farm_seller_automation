from contextlib import nullcontext

from solar_outreach import engine


def test_success_clears_the_offline_failure_counter(monkeypatch, tmp_path):
    monkeypatch.setattr(engine, "FAIL_FILE", tmp_path / "failures")
    monkeypatch.setattr(engine, "single_run", lambda *a, **k: nullcontext())
    engine.FAIL_FILE.write_text("5")                   # left over from an earlier Sheets outage
    monkeypatch.setattr(engine, "run", lambda test_n=0, with_inbox=True: 7)
    assert engine.once() == 7 and not engine.FAIL_FILE.exists()


def test_sigterm_during_a_pass_waits_for_it(monkeypatch):
    monkeypatch.setattr(engine, "STOP", {"asked": False, "busy": True})
    engine.stop()                                      # no SystemExit: the pass in progress finishes first
    assert engine.STOP["asked"]


def test_llm_outage_counts_as_a_failed_pass(monkeypatch, sheet):
    calls, fails = [], []
    monkeypatch.setattr(engine.gmail_client, "service", lambda: None)
    monkeypatch.setattr(engine.inbox, "process", lambda *a, **k: {"входящих": 1, "LLM недоступен": "API: 529"})
    monkeypatch.setattr(engine.approval, "main", lambda test_n=0, full=True: calls.append(full) or 3)
    monkeypatch.setattr(engine, "failure", fails.append)
    assert engine.run() == 3 and calls == [False] and fails == ["LLM недоступен: API: 529"]
