import pytest

from solar_outreach.config import dry_run, load_config, single_run, update_env


def test_single_run_blocks_parallel_and_releases():
    with single_run("pytest_lock"):
        with pytest.raises(SystemExit):
            with single_run("pytest_lock"):
                pass
    with single_run("pytest_lock"):  # освобождена после выхода
        pass


def test_owner_facts_are_plain_strings():
    o = load_config()["owner"]  # «Связь: ...» без кавычек YAML читает как словарь
    assert all(isinstance(f, str) for f in o["facts"] + o["internal_notes"])


def test_criteria_ranges_are_consistent():
    c = load_config()["criteria"]
    assert c["min_kwp"] < c["max_kwp"]
    p = c["price_per_kwp_eur"]
    assert (p["suspicious_below"] <= p["acceptable"][0] <= p["preferred"][0]
            <= p["preferred"][1] <= p["acceptable"][1] < p["skip_above"])


def test_dry_run_is_default_and_fail_safe(monkeypatch):
    monkeypatch.delenv("DRY_RUN", raising=False)
    assert dry_run()
    for value in ("true", "1", "", "no", "flase", "0"):
        monkeypatch.setenv("DRY_RUN", value)
        assert dry_run(), value
    monkeypatch.setenv("DRY_RUN", " False ")
    assert not dry_run()


def test_update_env_replaces_keys_and_keeps_rest(tmp_path):
    env = tmp_path / ".env"
    env.write_text("ANTHROPIC_API_KEY=a\nGOOGLE_REFRESH_TOKEN=old\n# comment\n", encoding="utf-8")
    update_env({"GOOGLE_REFRESH_TOKEN": "new", "GOOGLE_CLIENT_ID": "id"}, env)
    lines = env.read_text(encoding="utf-8").splitlines()
    assert lines == ["ANTHROPIC_API_KEY=a", "# comment", "GOOGLE_REFRESH_TOKEN=new", "GOOGLE_CLIENT_ID=id"]


def test_single_run_waits_then_gives_up():
    import time
    from solar_outreach.config import Busy
    with single_run("pytest_wait"):
        t = time.time()
        with pytest.raises(Busy):
            with single_run("pytest_wait", wait=0.1):
                pass
        assert time.time() - t < 5
