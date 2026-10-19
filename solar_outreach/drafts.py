"""Letter drafts: language, private seller vs broker, facts from the listing, manual-queue links.

ponytail: the station description is built deterministically from sheet fields (power, place), not by the LLM:
zero invented facts and zero cost. The LLM is used only when the owner taps «Править» (llm.rewrite).
"""
import re
from functools import cache
from pathlib import Path
from urllib.parse import quote

import yaml

from solar_outreach.config import load_config
from solar_outreach.sources import PERMIT

TEMPLATES = Path(__file__).parent / "templates"
BROKER = re.compile(r"real estate|estate|propert|имот|агенц|брокер|broker|consult|group|груп|\bЕ?ООД\b|\bАД\b|\bLtd", re.I)
CLASSIFIEDS = ("bazar.bg", "alo.bg", "kpd.bg", "olx.bg")
OPERATING = re.compile(r"действащ|работещ|действующ|работающ|operating|in operation", re.I)


@cache
def template(lang: str) -> dict:
    o = load_config()["owner"]
    text = (TEMPLATES / f"{lang}.yaml").read_text(encoding="utf-8")
    for key, value in {"name": o["name"][lang], "company": o["company"][lang], "phone": o["phone"], "email": o["email"]}.items():
        text = text.replace(f"{{{key}}}", value)
    return yaml.safe_load(text)


def language(text: str) -> str:
    """bg by default; en if there is more Latin than Cyrillic; ru if ы/э/ё appear (Bulgarian has none of them)."""
    lat, cyr = len(re.findall(r"[A-Za-z]", text)), len(re.findall(r"[А-Яа-яЁё]", text))
    if lat > cyr:
        return "en"
    return "ru" if re.search(r"[ыэёЫЭЁ]", text) else "bg"


def is_general(row: dict) -> bool:
    """A broker found by search rather than through a listing: the letter skips «Видях Вашата обява» and always asks the price."""
    return row.get("площадка", "").startswith("broker:")


def is_broker(row: dict) -> bool:
    src = row.get("площадка", "")
    if is_general(row):
        return True
    web_agency = src.startswith("web:") and not src.endswith(CLASSIFIEDS)
    return web_agency or bool(BROKER.search(row.get("продавец", "")))


def is_permit_project(row: dict) -> bool:
    """The title names a building permit and not an operating plant: the letter asks about the project, not the plant."""
    title = row.get("заголовок", "")
    return bool(PERMIT.search(title)) and not OPERATING.search(title)


def power_text(kw) -> str:
    try:
        kw = float(kw)
    except (TypeError, ValueError):
        return ""
    return f"{kw / 1000:g} MW".replace(".", ",") if kw >= 1000 else f"{kw:g} kW"


def description(row: dict, lang: str) -> tuple[str, str]:
    """(description for «Видях Вашата обява за ...», ref for the subject). Sheet facts only."""
    noun = {"bg": "фотоволтаична централа", "en": "a photovoltaic plant", "ru": "фотоэлектрическая станция"}[lang]
    where = {"bg": "в района на", "en": "in the area of", "ru": "в районе"}[lang]
    power = power_text(row.get("мощность кВт"))
    place = row.get("регион", "")
    place = "" if place in ("", "неизвестно") else place
    desc = " ".join(filter(None, [noun, power, f"{where} {place}" if place else ""]))
    ad_no = re.findall(r"\d{5,}", row.get("ссылка", ""))
    ref = place or (f"{'обява' if lang == 'bg' else 'listing' if lang == 'en' else 'объявление'} {ad_no[-1]}" if ad_no else power)
    return desc, ref or noun


def numbered(items: list[str]) -> str:
    return "\n".join(f"{i}. {q}" for i, q in enumerate(items, 1))


def first(row: dict, lang: str) -> tuple[str, str]:
    t = template(lang)
    if is_general(row):
        g = t["first"]["general"]
        return g["subject"], f"{g['intro']}\n{numbered(g['questions'])}\n\n{g['terms']}\n\n{t['first']['outro']}\n\n{t['signature']}"
    tf = {**t["first"], **t["first"]["permit"]} if is_permit_project(row) else t["first"]
    desc, ref = description(row, lang)
    intro = tf["intro_private"].format(description=desc)
    extra = f"{tf['broker_extra']}\n\n" if is_broker(row) else ""
    questions = tf["questions"]
    if str(row.get("цена EUR", "")).replace(".", "").isdigit():
        questions = questions[:-1]  # the price is already in the listing; in the templates the price question is always last
    body = f"{intro}\n{numbered(questions)}\n\n{extra}{tf['outro']}\n\n{t['signature']}"
    return tf["subject"].format(ref=ref), body


def second(row: dict, lang: str) -> str:
    """Only questions with no answer yet (the cell status doesn't start with «получен»/«отказ»)."""
    t = template(lang)["second"]
    answered = lambda key: str(row.get(key, "")).startswith(("получен", "отказ"))
    qs = [q for key, q in t["questions"].items()
          if key == "docs" or not answered(key) or (key == "8 панели" and not answered("8 инверторы"))]
    return f"{t['intro']}\n{numbered(qs)}\n\n{t['outro']}\n\n{template(lang)['signature']}"


def followup(lang: str, date: str, general: bool = False) -> str:
    t = template(lang)
    return f"{t['followup']['body_general' if general else 'body'].format(date=date)}\n\n{t['signature']}"


def manual_links(phone: str, text: str) -> tuple[str, str]:
    """WhatsApp gets the prefilled text; Viber can't prefill, so the text is given next to it for copying."""
    digits = re.sub(r"\D", "", phone)
    return f"https://wa.me/{digits}?text={quote(text)}", f"viber://chat?number=%2B{digits}"
