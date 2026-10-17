from types import SimpleNamespace

from solar_outreach import search
from solar_outreach.config import load_config
from solar_outreach.sources import Listing

CFG = load_config()


def test_listings_fetches_only_unknown_or_in_range():
    fake = SimpleNamespace(
        links=lambda fetch: {"u/known": "ФЕЦ 1.5 MW", "u/small": "Фотоволтаична централа 30kw",
                             "u/big": "ФЕЦ 1.5 MW продава", "u/nosize": "Фотоволтаична централа",
                             "u/service": "Изграждане на ФЕЦ 1 MW"},
        parse_ad=lambda url, html: Listing("x", url, html))
    fetched = []
    out = list(search.listings("x", fake, lambda u: fetched.append(u) or u, {"u/known"}, CFG["criteria"]))
    assert fetched == ["u/big", "u/nosize"]
    assert [l.url for l in out] == ["u/small", "u/big", "u/nosize"]
    assert out[0].extra == {"title_only": True}


def http_error(code):
    import requests
    return requests.HTTPError(response=SimpleNamespace(status_code=code))


def test_broken_page_skipped_but_429_stops_source():
    import pytest
    fake = SimpleNamespace(links=lambda f: {"u/503": "ФЕЦ 1 MW", "u/ok": "ФЕЦ 1.2 MW"},
                           parse_ad=lambda url, html: Listing("x", url, html))

    def fetch(url):
        if url == "u/503":
            raise http_error(503)
        return "page"
    assert [l.url for l in search.listings("x", fake, fetch, set(), CFG["criteria"])] == ["u/ok"]

    def limited(url):
        raise http_error(429)
    with pytest.raises(Exception):
        list(search.listings("x", fake, limited, set(), CFG["criteria"]))


def test_rows_dedup_by_contact_and_title_but_not_empty_title():
    a = search.to_row(Listing("x", "u1", "ФЕЦ 1.5 MW", "тел 0888123456"), CFG)
    b = search.to_row(Listing("y", "u2", "Друга обява ФЕЦ 1.5 MW", "0888 123 456"), CFG)
    c = search.to_row(Listing("x", "u3", ""), CFG)
    d = search.to_row(Listing("x", "u4", ""), CFG)
    assert search.keys(a) & search.keys(b)          # same phone and power
    assert not search.keys(c) & search.keys(d)      # empty titles don't merge


def test_ids_continue_after_max_existing():
    from solar_outreach import search
    new = [{"id": ""}, {"id": ""}]
    search.number(new, [{"id": "S001"}, {"id": "S021"}, {"id": "S003"}])
    assert [r["id"] for r in new] == ["S022", "S023"]
