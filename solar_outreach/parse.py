"""Extract facts from a listing. Ambiguous = None («неизвестно»): no guessing."""
import re

NUM = r"(\d{1,3}(?:[ ., ]\d{3})+(?!\d)|\d+(?:[.,]\d+)?)"  # «1 160 000», «220,000», «1.5», «1,5»
# colloquial «кв» = kW («ФЕЦ 30кв»), but «кв.м»/«кв. м» = area.
POWER = re.compile(NUM + r"\s*(mwp|mw|мвт|мегават\w*|kwp|kw|квт|кв(?!\.?\s*м)|kвт|кw)(?![a-zа-я])", re.I)
MONEY = re.compile(NUM + r"\s*(€|eur\w*|евро|лв\.?|лева|bgn)|(€|eur)\s*" + NUM, re.I)
YEAR = re.compile(r"(?:експлоатация|изградена|построена|пусната|въведена|от|година|г\.?)\D{0,25}?\b(20[01]\d|202[0-6])\b"
                  r"|\b(20[01]\d|202[0-6])\s*(?:г\.|год)", re.I)
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
PHONE = re.compile(r"(?:\+359|00359|\b0)\s*(8[789](?:[\s-]*\d){7})")
SITE_EMAILS = re.compile(r"@(bazar|alo|olx|imot|imoti|homes)\.(bg|net)$", re.I)
TARIFF = re.compile(r"преференциал|фиксиран\w* цен|договор за изкупуване|изкупуване на (ел|електр)|\bНЕК\b|\bЕСО\b"
                    r"|Електрохолд|ЕВН|EVN|Енерго-?Про|ЧЕЗ", re.I)
MARKET = re.compile(r"свободен пазар|\bIBEX\b|\bБНЕБ\b|борса|търговец|балансираща група|пазарн\w* цен", re.I)
DOCS = re.compile(r"нотариален акт|документ|разрешение за (строеж|ползване)|акт 1[456]|удостоверение за въвеждане", re.I)
SPV = re.compile(r"\bSPV\b|фирм|дружество|\bЕ?ООД\b|\bАД\b|дялове|лиценз", re.I)


def num(s: str) -> float:
    s = s.replace(" ", " ")
    if re.fullmatch(r"\d{1,3}(?:[ .,]\d{3})+", s):
        return float(re.sub(r"[ .,]", "", s))
    return float(s.replace(",", "."))


def powers_kw(text: str) -> list[float]:
    out = []
    for m in POWER.finditer(text):
        before = text[max(0, m.start() - 2):m.start()]
        if re.search(r"\d\s*[xх×]\s*$", before):  # «15x100kw», «1х700kw»: a breakdown, not the station's power
            continue
        value, unit = num(m.group(1)), m.group(2).lower()
        out.append(value * 1000 if unit.startswith(("m", "м")) else value)
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
            value = num(amount)
            if value < 1000:  # prices per m², per kW, per kWh
                continue
            found.add(round(value / eur_bgn if cur.lower().startswith(("л", "b")) else value))
        if len(found) == 1:
            return float(found.pop())
        if found:
            return None
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
