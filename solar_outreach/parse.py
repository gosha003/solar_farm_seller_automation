"""Extract facts from a listing. Ambiguous = None («неизвестно»): no guessing."""
import re

NUM = r"(\d{1,3}(?:[ ., ]\d{3})+(?:[.,]\d{1,2}(?!\d))?(?!\d)|\d+(?:[.,]\d+)?)"  # «1 160 000», «220,000», «1.5», «1,5», «1 496,88»
# Units in either script, mixed too: sellers write «9МW», «250кW», «1,5 MВт» with Cyrillic/Latin lookalikes; «кВтп» = kWp.
# Colloquial «кв» = kW («ФЕЦ 30кв», «500кВ»), but «кв.м»/«кв. м» = area and a grid voltage («20 кВ», «0,4 кВ») is not power.
# Energy is not power: «kWh», «кВт.ч», «кВт/ч», «MW/h», «киловатчаса», «мегавата часа».
UNIT = r"([mм](?:w|вт)[pрп]?|(?:мегават|киловат)(?!\w*час)\w*|[kк](?:w|вт)[pрп]?|кв(?!\.?\s*м))"
POWER = re.compile(NUM + r"\s*-?\s*" + UNIT + r"(?![a-zа-я])(?!\s*[-/.]?\s*(?:ч|час\w*|h)(?![a-zа-я]))", re.I)
VOLTS = {0.4, 6, 10, 20, 35, 110}  # kV of Bulgarian distribution lines
MONEY = re.compile(NUM + r"\s*(€|eur\w*|евро|лв\.?|лева|bgn)|(€|eur)\s*" + NUM, re.I)
PER_UNIT = re.compile(r"\s*(?:/|(?:на|за|per)\s)\s*(?:[kк][wв]|[mм][wв]|мегават|киловат|[mм][2²])", re.I)  # «€/MW», «лв за кВт»
YEAR = re.compile(r"(?:експлоатация|изградена|построена|пусната|въведена|от|година|г\.?)\D{0,25}?\b(20[01]\d|202[0-6])\b"
                  r"|\b(20[01]\d|202[0-6])\s*(?:г\.|год)", re.I)
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
PHONE = re.compile(r"(?:\+359|00359|\b0)\s*(8[789](?:[\s-]*\d){7})")
SITE_EMAILS = re.compile(r"@(bazar|alo|olx|imot|imoti|homes)\.(bg|net)$", re.I)
TARIFF_WORDS = re.compile(r"преференциал|фиксиран\w* цен|договор за изкупуване|изкупуване на (ел|електр)|\bНЕК\b", re.I)
DSO = re.compile(r"\bЕСО\b|Електрохолд|ЕВН|EVN|Енерго-?Про|ЧЕЗ", re.I)  # grid companies: a project names them too
TARIFF = re.compile(f"{TARIFF_WORDS.pattern}|{DSO.pattern}", re.I)
MARKET = re.compile(r"свободен пазар|\bIBEX\b|\bБНЕБ\b|борса|търговец|балансираща група|пазарн\w* цен", re.I)
DOCS = re.compile(r"нотариален акт|документ|разрешение за (строеж|ползване)|акт 1[456]|удостоверение за въвеждане", re.I)
SPV = re.compile(r"\bSPV\b|фирм|дружество|\bЕ?ООД\b|\bАД\b|дялове|лиценз", re.I)


def num(s: str) -> float:
    s = s.replace(" ", " ")
    if m := re.fullmatch(r"(\d{1,3}(?:[ .,]\d{3})+)(?:[.,](\d{1,2}))?", s):  # thousands groups, maybe a decimal tail
        return float(re.sub(r"\D", "", m.group(1)) + "." + (m.group(2) or "0"))
    return float(s.replace(",", "."))


def powers_kw(text: str) -> list[float]:
    out = []
    for m in POWER.finditer(text):
        before = text[max(0, m.start() - 2):m.start()]
        if re.search(r"\d\s*[xх×]\s*$", before):  # «15x100kw», «1х700kw»: a breakdown, not the station's power
            continue
        raw, unit = m.group(1), m.group(2).lower()
        if unit == "кв" and num(raw) in VOLTS:
            continue
        if unit.startswith(("m", "м")):
            # «1,498 MWp» is 1.498 MW (a 1498 MW plant doesn't exist here), not a thousands group
            out.append(1000 * (float(raw.replace(",", ".")) if re.fullmatch(r"\d{1,2}[.,]\d{3}", raw) else num(raw)))
        else:
            out.append(num(raw))
    return out


def power_kw(title: str, text: str) -> float | None:
    """The title wins over the text. In the text: a single value, or the value after the word «мощност»."""
    for src in (title, text):
        vals = set(powers_kw(src))
        if len(vals) == 1:
            return vals.pop()
        if src is text and vals:
            near = {v for m in re.finditer(r"мощност\D{0,30}", src, re.I) for v in powers_kw(src[m.end():m.end() + 25])}
            if len(near) == 1:
                return near.pop()
        if len(vals) > 1 and src is title:
            return None
    return None


def price_eur(price_text: str, text: str, eur_bgn: float) -> float | None:
    """Price from the listing's price field, else a single amount in the text. «Договаряне» / several different amounts = None."""
    for src in (price_text, text):
        found = set()
        for m in MONEY.finditer(src):
            amount, cur = (m.group(1), m.group(2)) if m.group(1) else (m.group(4), m.group(3))
            if PER_UNIT.match(src, m.end()):  # «750 000 €/MW» is a rate, not the total: the letter asks the price
                continue
            value = num(amount)
            if value < 1000:  # prices per m², per kW, per kWh
                continue
            found.add(round(value / eur_bgn if cur.lower().startswith(("л", "b")) else value))
        if found:  # one amount, or EUR plus a rounded BGN conversion of it
            return float(min(found)) if max(found) <= min(found) * 1.005 else None
    return None


def year(text: str) -> int | None:
    years = {int(a or b) for a, b in YEAR.findall(text)}
    return years.pop() if len(years) == 1 else None


def regime(text: str) -> str:
    t, m = bool(TARIFF.search(text)), bool(MARKET.search(text))
    return "тариф" if t and not m else "рынок" if m and not t else "неизвестно"


def emails(text: str) -> list[str]:
    return sorted({e.lower().rstrip(".") for e in EMAIL.findall(text) if not SITE_EMAILS.search(e.rstrip("."))})


def phones(text: str) -> list[str]:
    return sorted({"+359" + re.sub(r"\D", "", m) for m in PHONE.findall(text)})


def region(location: str) -> str:
    m = re.search(r"област\s+([\w\s-]+?)(?:,|\(|$)", location, re.I)
    return (m.group(1).strip() if m else location.split(",")[0]).strip()
