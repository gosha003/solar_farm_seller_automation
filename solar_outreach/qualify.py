"""Отбор и скоринг по criteria из config.yaml (раздел 2 ТЗ).

verdict: «кандидат» / «вне диапазона» (писать с пометкой) / «мелкая», «крупная», «дорого» (не писать).
Неизвестная мощность = «кандидат» с приоритетом C: размер выясним в переписке.
"""
from solar_outreach import parse

WRITE = {"кандидат", "вне диапазона"}


def qualify(kw: float | None, eur: float | None, text: str, yr: int | None, regime: str, c: dict) -> dict:
    p = c["price_per_kwp_eur"]
    notes, score = [], 0
    if kw is None:
        verdict = "кандидат"
        notes.append("мощность неизвестна")
    elif c["min_kwp"] <= kw <= c["max_kwp"]:
        verdict, score = "кандидат", 40
    elif c["soft_min_kwp"] <= kw <= c["soft_max_kwp"]:
        verdict, score = "вне диапазона", 25
    else:
        verdict = "мелкая" if kw < c["soft_min_kwp"] else "крупная"

    per_kw = round(eur / kw) if eur and kw else None
    if per_kw is not None:
        if per_kw > p["skip_above"] and verdict in WRITE:
            verdict = "дорого"
        elif per_kw < p["suspicious_below"]:
            notes.append("подозрительно дешево")
            score += 10
        elif p["preferred"][0] <= per_kw <= p["preferred"][1]:
            score += 30
        elif p["acceptable"][0] <= per_kw <= p["acceptable"][1]:
            score += 20
        else:
            score += 5
    if yr and yr >= c["min_year_preferred"]:
        score += 10
    if regime != "неизвестно":
        score += 5
    if parse.DOCS.search(text):
        score += 5
    if parse.SPV.search(text):
        score += 5
    if verdict not in WRITE:
        priority = "-"
    elif kw is None or verdict == "вне диапазона":
        priority = "C" if score < 50 else "B"
    else:
        priority = "A" if score >= 70 else "B" if score >= 45 else "C"
    return {"verdict": verdict, "score": score, "priority": priority, "eur_per_kw": per_kw, "notes": notes}
