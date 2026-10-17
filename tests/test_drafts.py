import pytest

from solar_outreach import drafts, rules

ROW = {"площадка": "alo.bg", "ссылка": "https://www.alo.bg/fotovoltaichna-centrala-1-5mw-10983881",
       "регион": "Плевен", "мощность кВт": "1500", "продавец": "MGroup23 Real Estate"}
PRIVATE = {**ROW, "продавец": "", "регион": "неизвестно", "мощность кВт": "неизвестно"}


@pytest.mark.parametrize("lang", ["bg", "en", "ru"])
@pytest.mark.parametrize("row", [ROW, PRIVATE])
def test_every_rendered_letter_passes_section4_rules(lang, row):
    sig = drafts.template(lang)["signature"]
    subject, body = drafts.first(row, lang)
    for text in (subject + "\n" + body, drafts.second(row, lang), drafts.followup(lang, "08.10.2026")):
        assert rules.violations(text, sig) == [], (lang, text)


def test_first_letter_content():
    subject, body = drafts.first(ROW, "bg")
    assert subject == "Запитване за фотоволтаична централа - Плевен"
    assert "Видях Вашата обява за фотоволтаична централа 1,5 MW в района на Плевен и ще се радвам" in body
    assert "\n1. Централата действаща ли е" in body and "\n4. Каква е исканата цена?" in body
    assert "цена?\n\nАко имате други действащи централи" in body          # broker: the portfolio question at the end
    assert "исканата цена" in body
    _, priced = drafts.first({**ROW, "цена EUR": "1160000"}, "bg")
    assert "исканата цена" not in priced and "\n3. Налични" in priced and "\n4." not in priced
    s2, b2 = drafts.first(PRIVATE, "bg")
    assert s2 == "Запитване за фотоволтаична централа - обява 10983881"
    assert "обява за фотоволтаична централа и ще се радвам" in b2 and "други действащи" not in b2


def test_second_letter_skips_answered_items():
    row = {**ROW, "5 год ввода": "получен: 2024", "8 панели": "получен: LONGi", "8 инверторы": "спрошено"}
    body = drafts.second(row, "bg")
    assert "Година на въвеждане" not in body
    assert "Марка и модел на панелите" in body          # inverters not known yet
    assert body.startswith("Благодаря за информацията") and "\n6. Моля, изпратете копия" in body


def test_language_and_broker_detection():
    assert drafts.language("Продавам фотоволтаична централа 1 MW, работеща") == "bg"
    assert drafts.language("Продаётся солнечная электростанция, объект в эксплуатации") == "ru"
    assert drafts.language("Operating solar park 1.2 MWp for sale near Ruse") == "en"
    assert drafts.is_broker(ROW) and not drafts.is_broker(PRIVATE)
    assert drafts.is_broker({"площадка": "web: www.bulgarianproperties.bg"})
    assert not drafts.is_broker({"площадка": "web: bazar.bg"})


def test_manual_links():
    wa, viber = drafts.manual_links("+359888123456", "Здравейте, 1-2 MW?")
    assert wa == "https://wa.me/359888123456?text=%D0%97%D0%B4%D1%80%D0%B0%D0%B2%D0%B5%D0%B9%D1%82%D0%B5%2C%201-2%20MW%3F"
    assert viber == "viber://chat?number=%2B359888123456"


@pytest.mark.parametrize("bad,reason", [
    ("Можем да предложим 750 000 EUR за централата.", "сумма/цена покупки/бюджет"),
    ("Нашият бюджет е ограничен.", "сумма/цена покупки/бюджет"),
    ("Ще купим централата веднага.", "офер/предложение цены"),
    ("Can we arrange a meeting next week?", "встреча/звонок/осмотр/NDA/задаток"),
    ("Готови сме да подпишем NDA и да дадем задатък.", "встреча/звонок/осмотр/NDA/задаток"),
    ("Пиша Ви чрез автоматизирана система.", "слово про бота/систему/ИИ"),
    ("I am an AI assistant helping Ivan.", "слово про бота/систему/ИИ"),
    ("Обадете се на +7 701 123 45 67.", "чужой номер телефона"),
    ("Вижте https://example.com", "ссылка"),
    ("Нашият ЕИК е 123456789.", "реквизиты/ЕИК/банк"),
])
def test_rules_catch_violations(bad, reason):
    sig = drafts.template("bg")["signature"]
    assert reason in rules.violations(f"{bad}\n\n{sig}", sig)
    assert "нет подписи" in rules.violations(bad, sig)


def test_general_broker_letter_has_no_listing_and_always_asks_price():
    row = {"площадка": "broker:agency.bg", "заголовок": "Agency", "ссылка": "https://agency.bg/contacts",
           "мощность кВт": "неизвестно", "цена EUR": "неизвестно", "регион": "неизвестно"}
    assert drafts.is_broker(row) and drafts.is_general(row)
    for lang in ("bg", "en", "ru"):
        subject, body = drafts.first(row, lang)
        sig = drafts.template(lang)["signature"]
        assert rules.violations(f"{subject}\n{body}", sig) == [] and "\n4. " in body
        assert rules.violations(drafts.followup(lang, "08.10.2026", True), sig) == []
    assert "Видях Вашата обява" not in drafts.first(row, "bg")[1] and "поземлени имоти" in drafts.first(row, "bg")[1]
