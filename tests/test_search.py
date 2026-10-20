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


def test_import_keeps_pv_sales_and_criteria_still_apply(tmp_path):
    import json
    f = tmp_path / "found.json"
    f.write_text(json.dumps([
        {"source": "imoti.net", "url": "https://a/1", "title": "Бизнес имот", "text": "Работеща фотоволтаична централа 1,5 MW, 1 100 000 EUR"},
        {"source": "imoti.net", "url": "https://a/2", "title": "Монтаж на фотоволтаици", "text": "ФЕЦ 1 MW"},
        {"source": "imoti.net", "url": "https://a/3", "title": "Парцел за ФЕЦ", "text": "проект 2 MW"},
        {"source": "imoti.net", "url": "https://a/4", "title": "Парцел за ФЕЦ", "text": "2 MW, разрешение за строеж"},
        {"source": "imoti.net", "url": "https://a/5", "title": "Апартамент", "text": "тристаен"},
        {"source": "homes.bg", "url": "https://a/6", "title": "ФЕЦ 400 kW", "text": "работеща"},
        {"source": "homes.bg", "url": "https://a/known", "title": "ФЕЦ 2 MW", "text": ""},
        {"url": "javascript:alert(1)", "title": "ФЕЦ 2 MW"},
    ], ensure_ascii=False), encoding="utf-8")
    out = list(search.imported(str(f), {"https://a/known"}))
    assert [l.url for l in out] == ["https://a/1", "https://a/4", "https://a/6"]
    assert out[0].source == "браузер: imoti.net"
    rows = [search.to_row(l, CFG) for l in out]
    assert rows[0]["отбор"] == "кандидат" and rows[0]["приоритет"] != "-"   # 1.5 MW, ~733 €/kW
    assert rows[2]["отбор"] == "мелкая" and rows[2]["приоритет"] == "-"     # 400 kW: same criteria as every source
