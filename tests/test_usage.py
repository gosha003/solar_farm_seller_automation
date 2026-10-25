import json
from types import SimpleNamespace

from solar_outreach import llm, usage
from solar_outreach.sources import brokers

PRICES = {"claude-sonnet-5-5": [2.0, 10.0, 0.2, 2.5]}


def response(model="claude-sonnet-5-5", **kw):
    u = dict(input_tokens=1000, output_tokens=200, cache_creation_input_tokens=0, cache_read_input_tokens=500,
             server_tool_use=None)
    return SimpleNamespace(model=model, usage=SimpleNamespace(**{**u, **kw}), stop_reason="end_turn",
                           parsed_output=llm.Answer(paragraph="ok"), content=[])


def logged(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "USAGE_LOG", tmp_path / "usage.jsonl")
    return lambda: [json.loads(x) for x in (tmp_path / "usage.jsonl").read_text(encoding="utf-8").splitlines()]


def test_call_json_logs_tokens_and_the_calling_function(tmp_path, monkeypatch):
    read = logged(tmp_path, monkeypatch)
    monkeypatch.setattr(llm, "anthropic_client", lambda: SimpleNamespace(beta=SimpleNamespace(
        messages=SimpleNamespace(parse=lambda **kw: response()))))
    llm.answer_questions(["?"], "en")
    (rec,) = read()
    assert rec | {"ts": ""} == {"ts": "", "model": "claude-sonnet-5-5", "where": "llm.answer_questions", "in": 1000,
                                "out": 200, "cache_write": 0, "cache_read": 500, "searches": 0}


def test_web_search_is_logged_under_its_caller_with_search_count(tmp_path, monkeypatch):
    read = logged(tmp_path, monkeypatch)
    r = response(server_tool_use=SimpleNamespace(web_search_requests=7))
    r.content = [SimpleNamespace(type="text", text="NONE")]
    client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: r)))
    monkeypatch.setattr(brokers.web.anthropic, "Anthropic", lambda: client)
    brokers.links(None)
    (rec,) = read()
    assert rec["where"] == "sources.brokers.links" and rec["searches"] == 7


def test_logging_failure_never_breaks_the_call(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "USAGE_LOG", tmp_path / "no" / "such" / "dir" / "usage.jsonl")
    llm.log_usage({"owner": {"timezone": "UTC"}}, response(), "x")  # must not raise


def test_summary_prices_and_averages():
    base = {"model": "claude-sonnet-5-5", "where": "brief.write", "in": 1_000_000, "out": 100_000, "cache_write": 0,
            "cache_read": 0, "searches": 0}
    recs = [{**base, "ts": "2026-10-19T10:00:00-04:00"},                                     # Monday, week 43
            {**base, "ts": "2026-10-25T10:00:00-04:00", "searches": 10},                     # Sunday, still week 43
            {**base, "ts": "2026-10-26T10:00:00-04:00", "model": "gone-model"}]              # Monday, week 44, no price
    s = usage.summarize(recs, PRICES, 10.0)
    assert round(s["day"]["2026-10-19"][4], 2) == 3.0            # 1M in at $2 + 0.1M out at $10
    assert round(s["day"]["2026-10-25"][4], 2) == 3.1            # plus 10 searches at $10 per 1000
    assert round(s["week"]["2026-W43"][4], 2) == 6.1 and s["week"]["2026-W44"][4] == 0
    assert s["unpriced"] == ["gone-model"] and s["days"] == 8
