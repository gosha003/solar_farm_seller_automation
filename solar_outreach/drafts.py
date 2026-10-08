"""Черновики писем (раздел 7): язык, частник/брокер, подстановка фактов из объявления, ссылки ручной очереди.

ponytail: описание станции собирается детерминированно из полей таблицы (мощность, место), а не LLM:
ноль выдуманных фактов и ноль затрат. LLM подключается только на «Править» владельца (llm.rewrite).
"""
import re
from functools import cache
from pathlib import Path
from urllib.parse import quote

import yaml

TEMPLATES = Path(__file__).parent / "templates"
BROKER = re.compile(r"real estate|estate|propert|имот|агенц|брокер|broker|consult|group|груп|\bЕ?ООД\b|\bАД\b|\bLtd", re.I)
CLASSIFIEDS = ("bazar.bg", "alo.bg", "kpd.bg", "olx.bg")


@cache
def template(lang: str) -> dict:
    return yaml.safe_load((TEMPLATES / f"{lang}.yaml").read_text(encoding="utf-8"))


def language(text: str) -> str:
    """bg по умолчанию; en, если латиницы больше кириллицы; ru, если есть ы/э/ё (в болгарском их нет)."""
    lat, cyr = len(re.findall(r"[A-Za-z]", text)), len(re.findall(r"[А-Яа-яЁё]", text))
    if lat > cyr:
        return "en"
    return "ru" if re.search(r"[ыэёЫЭЁ]", text) else "bg"


def is_broker(row: dict) -> bool:
    src = row.get("площадка", "")
    web_agency = src.startswith("web:") and not src.endswith(CLASSIFIEDS)
    return web_agency or bool(BROKER.search(row.get("продавец", "")))


def power_text(kw) -> str:
    try:
        kw = float(kw)
    except (TypeError, ValueError):
        return ""
    return f"{kw / 1000:g} MW".replace(".", ",") if kw >= 1000 else f"{kw:g} kW"


def description(row: dict, lang: str) -> tuple[str, str]:
    """(описание для «Видях Вашата обява за ...», ref для темы). Только факты из таблицы."""
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
    desc, ref = description(row, lang)
    intro = t["first"]["intro_broker" if is_broker(row) else "intro_private"].format(description=desc)
    body = f"{intro}\n{numbered(t['first']['questions'])}\n\n{t['first']['outro']}\n\n{t['signature']}"
    return t["first"]["subject"].format(ref=ref), body


def second(row: dict, lang: str) -> str:
    """Только вопросы, на которые ещё нет ответа (статус в ячейке не начинается с «получен»/«отказ»)."""
    t = template(lang)["second"]
    answered = lambda key: str(row.get(key, "")).startswith(("получен", "отказ"))
    qs = [q for key, q in t["questions"].items()
          if key == "docs" or not answered(key) or (key == "8 панели" and not answered("8 инверторы"))]
    return f"{t['intro']}\n{numbered(qs)}\n\n{t['outro']}\n\n{template(lang)['signature']}"


def followup(lang: str, date: str) -> str:
    t = template(lang)
    return f"{t['followup']['body'].format(date=date)}\n\n{t['signature']}"


def manual_links(phone: str, text: str) -> tuple[str, str]:
    """WhatsApp с готовым текстом; Viber текст не подставляет, его даём рядом для копирования."""
    digits = re.sub(r"\D", "", phone)
    return f"https://wa.me/{digits}?text={quote(text)}", f"viber://chat?number=%2B{digits}"
