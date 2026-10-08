from types import SimpleNamespace

from solar_outreach.sources import web

CFG = {"llm": {"model_strong": "m"}}


def block(type_, **kw):
    return SimpleNamespace(type=type_, **kw)


def fake_client(responses):
    it = iter(responses)
    return SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: next(it))))


def test_only_urls_seen_in_search_results_are_accepted():
    results = [SimpleNamespace(url="https://bp.bg/imot-1/", title="ФЕЦ 1 MW за продажба"),
               SimpleNamespace(url="https://news.bg/a", title="Новина")]
    answer = ("1. https://bp.bg/imot-1 | 1 MW 790 000 EUR Разград\n"
              "- <https://invented.bg/x> | 2 MW\n"                       # нет в результатах поиска
              "NONE")
    r = SimpleNamespace(stop_reason="end_turn", content=[
        block("web_search_tool_result", content=results),
        block("web_search_tool_result", content=SimpleNamespace(error_code="max_uses_exceeded")),
        block("text", text=answer)])
    assert web.search_results(fake_client([r]), CFG) == {"https://bp.bg/imot-1": "ФЕЦ 1 MW за продажба 1 MW 790 000 EUR Разград"}


def test_pause_turn_continues():
    first = SimpleNamespace(stop_reason="pause_turn", content=[
        block("web_search_tool_result", content=[SimpleNamespace(url="https://a.bg/1", title="t")])])
    second = SimpleNamespace(stop_reason="end_turn", content=[block("text", text="https://a.bg/1 | 1.2 MW")])
    assert web.search_results(fake_client([first, second]), CFG) == {"https://a.bg/1": "t 1.2 MW"}
