from solar_outreach.config import load_config
from solar_outreach.qualify import qualify

C = load_config()["criteria"]


def q(kw, eur, text="", yr=None, regime="неизвестно"):
    return qualify(kw, eur, text, yr, regime, C)


def test_alo_15mw_is_top_candidate():
    r = q(1500, 1_160_000, "", 2024, "рынок")
    assert r["verdict"] == "кандидат" and r["eur_per_kw"] == 773 and r["priority"] == "A"


def test_size_and_price_bands():
    assert q(60, 54_300)["verdict"] == "мелкая"
    assert q(950, None)["verdict"] == "вне диапазона"
    assert q(2100, None)["verdict"] == "вне диапазона"
    assert q(5000, None)["verdict"] == "крупная"
    assert q(1000, 1_200_000)["verdict"] == "дорого"           # 1200 €/кВт > 1100
    assert q(1000, 1_100_000)["verdict"] == "кандидат"         # граница включительно
    cheap = q(1000, 500_000)
    assert cheap["verdict"] == "кандидат" and "подозрительно дешево" in cheap["notes"]


def test_unknowns_still_candidates():
    r = q(None, None)
    assert r["verdict"] == "кандидат" and r["priority"] == "C" and r["eur_per_kw"] is None
    assert q(1500, None)["verdict"] == "кандидат"


def test_bonuses_raise_score():
    base = q(1500, None)["score"]
    rich = q(1500, None, "нотариален акт, продава се фирмата ЕООД", 2020, "тариф")["score"]
    assert rich == base + 10 + 5 + 5 + 5
