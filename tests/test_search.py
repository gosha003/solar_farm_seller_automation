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


def test_alo_ad_dedup_by_number_not_url_form():
    a = search.to_row(Listing("alo.bg", "https://www.alo.bg/prodavame-fec-9mw-11333221", "Продаваме ФЕЦ 9MW"), CFG)
    b = search.to_row(Listing("x", "https://www.alo.bg/11333221", "Друго заглавие"), CFG)
    c = search.to_row(Listing("x", "https://www.alo.bg/11333222", "Трето заглавие"), CFG)
    assert search.keys(a) & search.keys(b) and not search.keys(b) & search.keys(c)
    assert search.url_key("https://bazar.bg/obiava-11333221/fec") == "https://bazar.bg/obiava-11333221/fec"


def test_titles_dedup_across_scripts():
    a = search.to_row(Listing("x", "u1", "Продаваме ФЕЦ 9MW"), CFG)   # Latin M
    b = search.to_row(Listing("x", "u2", "Продаваме ФЕЦ 9МW"), CFG)   # Cyrillic М
    assert a["мощность кВт"] == b["мощность кВт"] == 9000
    assert search.keys(a) & search.keys(b)
    from_sheet = {**a, "ссылка": "u3", "мощность кВт": "9000"}  # sheets.read returns formatted strings
    assert search.keys(from_sheet) & search.keys(b)


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
    assert "разрешение на строительство в тексте" in rows[1]["примечание"]  # drafts: the project letter
    assert not out[0].extra and not out[1].extra                             # the PV keyword is in the title or it runs


def test_permit_note_only_when_the_text_alone_names_it():
    def note(title, text):
        return search.to_row(Listing("x", "u", title, text), CFG)["примечание"]
    assert note("Парцел за ФЕЦ", "2 MW, разрешение за строеж") == "разрешение на строительство в тексте"
    assert note("ФЕЦ 2 MW с разрешение за строеж", "разрешение за строеж") == ""
    assert note("ФЕЦ 2 MW", "работеща, разрешение за строеж и акт 16") == ""


def test_import_survives_bom_nulls_and_bad_elements(tmp_path):
    import json
    f = tmp_path / "found.json"
    f.write_text(json.dumps([None, "x", {"url": "https:x", "title": "ФЕЦ 2 MW"}, {"url": "https://", "title": "ФЕЦ 2 MW"},
                             {"url": "https://a.example/1", "title": None, "text": "ФЕЦ 2 MW", "price": None,
                              "location": None, "seller": None}], ensure_ascii=False), encoding="utf-8-sig")
    out = list(search.imported(str(f), set()))
    assert [(l.url, l.title, l.price_text, l.location, l.seller) for l in out] == [("https://a.example/1", "", "", "", "")]
    assert out[0].source == "браузер: a.example" and search.to_row(out[0], CFG)["регион"] == "неизвестно"
    f.write_text('{"items": [{"url": "https://a.example/2", "title": "ФЕЦ 2 MW"}]}', encoding="utf-8")
    assert list(search.imported(str(f), set())) == []


def test_same_plant_on_two_sites_shares_power_price_key():
    a = search.to_row(Listing("bazar.bg", "https://bazar.example/1", "Продавам ФЕЦ 1,5 MW", "", "1 100 000 €", "гр. Плевен"), CFG)
    b = search.to_row(Listing("alo.bg", "https://alo.example/2", "Фотоволтаична централа", "мощност 1500 kW, цена 1 100 000 EUR"), CFG)
    c = search.to_row(Listing("alo.bg", "https://alo.example/3", "ФЕЦ Ловеч", "мощност 1500 kW, цена 1 200 000 EUR"), CFG)
    assert search.dup(search.keys(b), search.keys(a)) and not search.dup(search.keys(b), search.keys(c))
    for eur in ("1100000", "1 100 000", "1\xa0100\xa0000"):  # read back from the sheet: strings, ru_RU group separators
        back = {**b, "ссылка": "https://web.example/4", "заголовок": "Друго", "мощность кВт": "1500", "цена EUR": eur}
        assert search.dup(search.keys(back), search.keys(a))
    d = search.to_row(Listing("x", "https://x.example/5", "Продавам ФЕЦ 1,5 MW", "", "", "Плевен, област Плевен"), CFG)
    assert search.keys(d) & search.keys(a)  # «гр. Плевен» and «Плевен» are one region
    e = search.to_row(Listing("x", "https://x.example/6", "ФЕЦ 1,5 MW", "", "1 100 000 €", "гр. Видин"), CFG)
    assert not search.dup(search.keys(e), search.keys(a))   # same kW and price, another town: another plant


def test_main_writes_health_per_source_and_flags_unconfirmed_imports(tmp_path, monkeypatch):
    import contextlib
    import json
    import re
    state, written = {}, []
    monkeypatch.setenv("SPREADSHEET_ID", "sheet-id")
    monkeypatch.setattr(search.sheets, "service", lambda: None)
    monkeypatch.setattr(search.sheets, "read", lambda api, sid, tab: [])
    monkeypatch.setattr(search.sheets, "append", lambda api, sid, tab, rows: written.extend(rows))
    monkeypatch.setattr(search.sheets, "set_kv", lambda api, sid, tab, values: state.update(values))
    monkeypatch.setattr(search, "single_run", lambda *a, **kw: contextlib.nullcontext())

    def down(fetch):
        raise RuntimeError("boom")
    ok = SimpleNamespace(links=lambda f: {"https://bazar.example/1": "ФЕЦ 5 MW", "https://bazar.example/2": "Изграждане на ФЕЦ"})
    monkeypatch.setattr(search, "SOURCES", {"bazar.bg": ok, "alo.bg": SimpleNamespace(links=down)})
    search.main(False)
    assert set(state) == {"поиск bazar.bg", "поиск alo.bg"}  # one key per source of the run, UNAVAILABLE stays out
    assert re.fullmatch(r"\d{4}-\d\d-\d\d \d\d:\d\d ссылок 2, новых 1", state["поиск bazar.bg"])  # 5 MW: title only
    assert state["поиск alo.bg"].endswith(" ссылок 0, новых 0, ошибка: RuntimeError: boom")

    f = tmp_path / "found.json"
    f.write_text(json.dumps([
        {"url": "https://a.example/1", "title": "Бизнес имот",
         "text": "Продава се парцел 25 дка, подходящ за изграждане на ФЕЦ 2 MW, без разрешение"},
        {"url": "https://a.example/2", "title": "Индустриален имот",
         "text": "Фотоволтаичен парк 2 MW, въведен в експлоатация 2012, проект на Siemens"},
        None], ensure_ascii=False), encoding="utf-8")
    state.clear()
    written.clear()
    search.main(False, import_path=str(f))
    assert set(state) == {"поиск браузер"} and state["поиск браузер"].endswith(" ссылок 3, новых 2")
    plot, plant = sorted(written, key=lambda r: r["ссылка"])
    assert (plot["отбор"], plot["приоритет"], plot["статус переписки"]) == ("без разрешения?", "-", "не писать")
    assert plot["примечание"] == "участок или проект без разрешения на строительство? проверь вручную"
    assert plant["отбор"] == "кандидат" and plant["приоритет"] != "-"


def test_crash_alerts_the_owner_and_still_fails(monkeypatch):
    import runpy
    import sys
    import warnings

    import pytest

    from solar_outreach import notify
    sent = []

    def no_sheets():
        raise RuntimeError("no token")
    monkeypatch.setattr(search.sheets, "service", no_sheets)
    monkeypatch.setattr(notify, "urgent", sent.append)
    monkeypatch.setattr(sys, "argv", ["search", "--only", "pc"])
    with warnings.catch_warnings(), pytest.raises(RuntimeError):
        warnings.simplefilter("ignore", RuntimeWarning)  # runpy: the module is already imported by this test file
        runpy.run_module("solar_outreach.search", run_name="__main__")
    assert sent == ["⚠️ поиск --only pc упал: RuntimeError: no token"]


def test_site_queries_include_permit_projects_and_alo_stays_small():
    from solar_outreach.sources import PERMIT, alo, bazar, olx
    assert len(alo.QUERIES) <= 7  # alo.bg answers 429 after ~30 requests in a row
    assert all(any(PERMIT.search(q) for q in m.QUERIES) for m in (alo, bazar, olx))


def test_a_running_plant_that_lists_its_building_permit_is_not_a_project():
    text = "ФЕЦ 1,5 MW с преференциална цена до 2032 г. Документи: нотариален акт, разрешение за строеж."
    row = search.to_row(Listing("bazar.bg", "https://bazar.example/9", "Продавам фотоволтаична централа", text,
                                "1 100 000 €", "Пловдив"), CFG)
    assert "разрешение на строительство в тексте" not in row["примечание"]
