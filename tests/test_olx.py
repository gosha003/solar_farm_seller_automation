from solar_outreach.sources import is_station_sale, olx

CRIT = {"min_kwp": 1000, "max_kwp": 2000}


def card(href: str, title: str, loc: str = "гр. Сунгурларе - Днес в 15:10 ч.") -> str:
    return (f'<div data-cy="l-card"><a href="{href}?search_reason=search%7Corganic">x</a><h4>{title}</h4>'
            f'<span data-testid="location-date">{loc}</span></div>')


CARDS = (card("/d/ad/fec-1-5-mw-CID368-IDa1.html", "Продавам ФЕЦ 1.5 MW") + card("/d/ad/fec-30kw-CID368-IDa2.html", "Фотоволтаична централа 30kw")
         + card("/d/ad/fec-bez-moshtnost-CID368-IDa3.html", "Фотоволтаична централа") + '<div data-cy="l-card"><h4>без ссылки</h4></div>')

AD = """<html><head><title>x - OLX.bg</title></head><body>
<h4 data-testid="offer_title">Фотоволтаична централа 1.5 MW работеща</h4><div data-testid="ad-price-container">1450000 €</div>
<div data-testid="ad-parameters-container">Бизнес Цена на кв.м: 67 Квадратура: 30000 кв.м Регулация: В регулация Описание ...</div>
<div data-testid="ad_description">Продавам действаща ФЕЦ 1.5 MW, лицензирана, тел. 0885123456</div>
<h4 data-testid="user-profile-user-name">Иван Петров</h4></body></html>"""


def test_cards_keep_unknown_power_and_drop_out_of_range():
    n, found = olx.card_links(CARDS, CRIT)
    assert n == 4
    assert list(found) == ["https://www.olx.bg/d/ad/fec-1-5-mw-CID368-IDa1.html", "https://www.olx.bg/d/ad/fec-bez-moshtnost-CID368-IDa3.html"]
    assert all(is_station_sale(t) for t in found.values())
    assert olx.LOCATIONS["https://www.olx.bg/d/ad/fec-1-5-mw-CID368-IDa1.html"] == "Сунгурларе"


def test_olx_noise_titles_are_not_station_sales():
    for t in ("2 MW ФЕЦ с разрешително за строеж. Започване веднага", "Бензиностанция и Газстанция ФОТОВОЛТАИЦИ, Автомивка"):
        assert not is_station_sale(t)
    assert is_station_sale("Продавам действаща фотоволтаична централа 1.5 MW")


def test_parse_ad_extracts_facts():
    url = "https://www.olx.bg/d/ad/fec-1-5-mw-CID368-IDa1.html"
    l = olx.parse_ad(url, AD)
    assert l.source == "olx.bg" and l.title == "Фотоволтаична централа 1.5 MW работеща"
    assert l.price_text == "1450000 €" and l.seller == "Иван Петров" and l.location == "Сунгурларе"
    assert "0885123456" in l.text and "Площ: 30000 кв.м" in l.text


def test_olx_host_goes_through_curl(monkeypatch):
    import subprocess
    from types import SimpleNamespace

    from solar_outreach.sources import Fetcher
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: calls.append(cmd) or SimpleNamespace(
        stdout="User-agent: *\nAllow: /\n\n200".encode() if cmd[-1].endswith("robots.txt") else "<html>ok</html>\n200".encode(),
        stderr=b"", returncode=0))
    f = Fetcher("UA/1", (0, 0))
    assert f("https://www.olx.bg/ads/q-x/") == "<html>ok</html>"
    assert all(c[0] == "curl" and "UA/1" in c for c in calls) and len(calls) == 2  # robots.txt + page
