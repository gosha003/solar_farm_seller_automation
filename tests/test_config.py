import pytest

from solar_outreach.config import dry_run, load_config, single_run, update_env


def test_single_run_blocks_parallel_and_releases():
    with single_run("pytest_lock"):
        with pytest.raises(SystemExit):
            with single_run("pytest_lock"):
                pass
    with single_run("pytest_lock"):  # освобождена после выхода
        pass


def test_criteria_ranges_are_consistent():
    c = load_config()["criteria"]
    assert c["soft_min_kwp"] <= c["min_kwp"] < c["max_kwp"] <= c["soft_max_kwp"]
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
