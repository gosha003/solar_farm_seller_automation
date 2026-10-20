import pytest

from solar_outreach import drafts, rules

ROW = {"площадка": "alo.bg", "ссылка": "https://www.alo.bg/fotovoltaichna-centrala-1-5mw-10983881",
       "регион": "Плевен", "мощность кВт": "1500", "продавец": "MGroup23 Real Estate"}
PRIVATE = {**ROW, "продавец": "", "регион": "неизвестно", "мощность кВт": "неизвестно"}
PERMIT = {**ROW, "заголовок": "Продавам 2 MW ФЕЦ с разрешително за строеж", "мощность кВт": "2000"}


@pytest.mark.parametrize("lang", ["bg", "en", "ru"])
@pytest.mark.parametrize("row", [ROW, PRIVATE, PERMIT, {**ROW, "регион": "Ботевград"}])
def test_every_rendered_letter_passes_section4_rules(lang, row):
    sig = drafts.template(lang)["signature"]
    subject, body = drafts.first(row, lang)
    for text in (subject + "\n" + body, drafts.second(row, lang), drafts.followup(lang, "08.10.2026")):
        assert rules.violations(text, sig) == [], (lang, text)
        assert "(" not in text, (lang, text)          # owner: no parenthetical clarifications in letters


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
    long_id = {**PRIVATE, "ссылка": "https://www.example.bg/ad?adv=1c171234567890123", "мощность кВт": "1125"}
    s3, _ = drafts.first(long_id, "bg")     # a 9+ digit id would read as a phone: the power is the ref instead
    assert s3 == "Запитване за фотоволтаична централа - 1,125 MW"
    assert rules.violations(f"{s3}\n{drafts.template('bg')['signature']}", drafts.template("bg")["signature"]) == []
    assert "a photovoltaic plant 1.125 MW in the area of" in drafts.first({**ROW, "мощность кВт": "1125"}, "en")[1]
    assert "фотоэлектрическая станция 1,5 МВт в районе Плевен" in drafts.first(ROW, "ru")[1]
    assert drafts.power_text("800", "ru") == "800 кВт" and drafts.power_text("неизвестно", "en") == ""


def test_second_letter_skips_answered_items():
    row = {**ROW, "5 год ввода": "получен: 2024", "8 панели": "получен: LONGi", "8 инверторы": "спрошено"}
    body = drafts.second(row, "bg")
    assert "Година на въвеждане" not in body
    assert "Марка и модел на панелите" in body          # inverters not known yet
    assert body.startswith("Благодаря за информацията") and "\n6. Моля, изпратете копия" in body
    assert "т. 3" not in body and "разрешението за строеж и договора за присъединяване" in body


def test_second_letter_to_permit_project_skips_operating_items():
    body = drafts.second(PERMIT, "bg")
    for gone in ("Година на въвеждане", "Произведена енергия", "Режим на продажба", "Марка и модел"):
        assert gone not in body
    assert "\n1. Земята собствена ли е" in body and "\n3. Моля, изпратете копия на документа за собственост" in body
    assert "Година на въвеждане" in drafts.second(ROW, "bg")


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
    ("Да, аз съм реален човек, не робот.", "человек/за границей"),
    ("Я живой человек, не робот.", "человек/за границей"),
    ("Yes, you are talking to a real person, not a robot.", "человек/за границей"),
    ("I'm not a robot.", "человек/за границей"),
    ("В момента съм в чужбина.", "человек/за границей"),
    ("Сейчас я нахожусь за границей.", "человек/за границей"),
    ("I am currently abroad.", "человек/за границей"),
    ("Предлагаме 1,5 милиона евро за централата.", "сумма/цена покупки/бюджет"),
    ("Готовы предложить 1,2 миллиона евро.", "сумма/цена покупки/бюджет"),
    ("Our bid is 1.1 mln euro.", "сумма/цена покупки/бюджет"),
    ("Our price is EUR 1,200,000.", "сумма/цена покупки/бюджет"),
    ("Цената, която можем да платим, е 1 200 000.", "офер/предложение цены"),
    ("We could pay more.", "офер/предложение цены"),
    ("Мога да Ви се обадя утре.", "встреча/звонок/осмотр/NDA/задаток"),
    ("Let's schedule a call tomorrow.", "встреча/звонок/осмотр/NDA/задаток"),
    ("Я могу позвонить Вам завтра.", "встреча/звонок/осмотр/NDA/задаток"),
    ("We could come and inspect the site.", "встреча/звонок/осмотр/NDA/задаток"),
    ("Ready to sign a non-disclosure agreement.", "встреча/звонок/осмотр/NDA/задаток"),
    ("Готовы подписать соглашение о конфиденциальности.", "встреча/звонок/осмотр/NDA/задаток"),
    ("Можем да подпишем писмо за намерение.", "встреча/звонок/осмотр/NDA/задаток"),
    ("We can send a letter of intent (LOI).", "встреча/звонок/осмотр/NDA/задаток"),
    ("Готовы заключить предварительный договор.", "встреча/звонок/осмотр/NDA/задаток"),
    ("Можем да направим оглед на централата.", "встреча/звонок/осмотр/NDA/задаток"),
    ("Ботът ще Ви отговори.", "слово про бота/систему/ИИ"),
    ("Банковский счёт пришлём позже.", "реквизиты/ЕИК/банк"),
    ("Вижте example.bg за повече.", "ссылка"),
])
def test_rules_catch_violations(bad, reason):
    sig = drafts.template("bg")["signature"]
    assert reason in rules.violations(f"{bad}\n\n{sig}", sig)
    assert "нет подписи" in rules.violations(bad, sig)


@pytest.mark.parametrize("ok", ["С оглед на това ще се радвам да получа документите.",
                                "Вы можете задать любые вопросы по почте.", "Централата е край Ботевград, до Ботево.",
                                "Пишете ми на ivan@abv.bg или seller@my-solar.bg."])
def test_rules_ignore_everyday_words(ok):
    sig = drafts.template("bg")["signature"]
    assert rules.violations(f"{ok}\n\n{sig}", sig) == []


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


def test_permit_project_gets_permit_letter():
    permit = {**PRIVATE, "площадка": "olx.bg", "заголовок": "Продавам 2 MW ФЕЦ с разрешително за строеж"}
    for lang in ("bg", "en", "ru"):
        subject, body = drafts.first(permit, lang)
        assert rules.violations(f"{subject}\n{body}", drafts.template(lang)["signature"]) == [], lang
    subject, body = drafts.first(permit, "bg")
    assert subject.startswith("Запитване за проект") and "проекти с мощност 1-3 MW с издадено разрешение" in body
    assert "\n2. До кога е валидно разрешението за строеж?" in body and "действаща ли е" not in body
    assert "\n5. Каква е исканата цена" in body
    assert "\n5." not in drafts.first({**permit, "цена EUR": "900000"}, "bg")[1]   # known price: last question dropped
    operating = {**permit, "заголовок": "Действаща ФЕЦ 2 MW с разрешение за строеж и акт 16"}
    assert "\n1. Централата действаща ли е" in drafts.first(operating, "bg")[1]
    assert drafts.is_permit_project({"заголовок": "Парцел за ФЕЦ"})          # sources keep plots only with a permit
    note = {"заголовок": "Бизнес имот", "примечание": "крупная; разрешение на строительство в тексте"}
    assert drafts.is_permit_project(note) and not drafts.is_permit_project({**note, "примечание": ""})
    assert not drafts.is_permit_project({**note, "заголовок": "Работеща ФЕЦ"})


def test_more_rule_cases_from_the_review():
    from solar_outreach import rules
    sig = drafts.template("bg")["signature"]
    for bad in ("Готови сме да дадем капаро.", "Бихме дали около милион евро.", "Във връзка с огледа, ще пиша.",
                "Ние сме чуждестранна компания.", "Компанията е регистрирана зад граница.", "Аз съм човекът за това.",
                "We are a foreign investor.", "Ще дойдем да видим централата.", "We can come and see the plant."):
        assert rules.violations(f"{bad}\n\n{sig}", sig), bad
    for ok in ("С оглед на това ще се радвам на документите.", "На първо място, благодаря.", "Когато дойде моментът."):
        assert rules.violations(f"{ok}\n\n{sig}", sig) == [], ok


def test_broker_rows_are_never_permit_projects():
    row = {"площадка": "broker:example-energy.example", "заголовок": "Енерго Проект Консулт - ФЕЦ и енергийни проекти"}
    assert not drafts.is_permit_project(row)
