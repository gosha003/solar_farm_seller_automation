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
    assert q(950, None)["verdict"] == "мелкая"      # no soft bounds: strictly min_kwp..max_kwp
    assert q(3100, None)["verdict"] == "крупная"
    assert q(1000, None)["verdict"] == q(2500, None)["verdict"] == q(3000, None)["verdict"] == "кандидат"
    assert q(5000, None)["verdict"] == "крупная"
    assert q(1000, 1_200_000)["verdict"] == "дорого"           # 1200 €/kW > skip_above
    cap = C["price_per_kwp_eur"]["skip_above"]
    assert q(1000, (cap + 1) * 1000)["verdict"] == "дорого"
    assert q(1000, cap * 1000)["verdict"] == "кандидат"         # boundary inclusive
    cheap = q(1000, (C["price_per_kwp_eur"]["suspicious_below"] - 1) * 1000)
    assert cheap["verdict"] == "кандидат" and "подозрительно дешево" in cheap["notes"]


def test_above_preferred_cap_is_written_but_flagged():
    top = C["price_per_kwp_eur"]["preferred"][1]
    r = q(1000, (top + 1) * 1000)
    assert r["verdict"] == "кандидат" and any("только при хороших условиях" in n for n in r["notes"])
    assert not q(1000, top * 1000)["notes"]


def test_unknown_power_is_not_written():
    r = q(None, None)
    assert r["verdict"] == "мощность неизвестна" and r["priority"] == "-" and r["eur_per_kw"] is None
    assert q(1500, None)["verdict"] == "кандидат"


def test_bonuses_raise_score():
    base = q(1500, None)["score"]
    rich = q(1500, None, "нотариален акт, продава се фирмата ЕООД", 2020, "тариф")["score"]
    assert rich == base + 10 + 5 + 5 + 5
