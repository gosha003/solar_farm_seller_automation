from solar_outreach.sources import imot, is_station_sale

CARDS = """<div class="item"><a href="//www.imot.bg/obiava-1t177-prodava-biznes-imot-grad-vratsa-dabnika">x</a>
Продава СОЛАРЕН ПАРК град Враца, Дъбника 989 000 € 23000 кв.м, Соларен парк, с мощност 1.2 Мегавата, тел.: 0888654321</div>
<div class="item"><a href="//www.imot.bg/obiava-1t178-prodava-kashta">x</a>Продава КЪЩА с. Козаревец 50 000 €</div>"""

AD = """<html><body><script>var x=1;</script>Продава СОЛАРЕН ПАРК, град Враца, Дъбника Площ: 23000 кв.м, Агенция
Продава СОЛАРЕН ПАРК град Враца, Дъбника Виж карта 989 000 € (43 €/m2) Не се начислява ДДС
Описание на имота: Соларен парк, с мощност 1.2 Мегавата, напълно завършена. Особености Сглобяема
За контакт: 0888654321 Брокер: Петър Георгиев Агенция: ПРИМЕР ИМОТИ 0888654321 В imot.bg от 2009 г.
ИЗПРАТИ ЗАПИТВАНЕ</body></html>"""


def test_only_solar_park_cards_are_taken():
    n, found = imot.card_links(CARDS)
    assert n == 2 and list(found) == ["https://www.imot.bg/obiava-1t177-prodava-biznes-imot-grad-vratsa-dabnika"]
    assert is_station_sale(next(iter(found.values())))


def test_card_title_is_short_and_keeps_project_marker():
    def title(desc):
        html = f'<div class="item"><a href="//www.imot.bg/obiava-1t9-x">x</a>Продава СОЛАРЕН ПАРК град Враца 989 000 € {desc}</div>'
        return next(iter(imot.card_links(html)[1].values()))
    t = title("Соларен парк 1.2 Мегавата, панели Longi, инвертори Huawei, в работа от 2012, подходящ за инвеститори")
    assert t == "Соларен парк 1200 kW" and is_station_sale(t)          # description words never meet the title filter
    assert title("Соларен парк, проект за изграждане") == "Соларен парк проект"
    assert not is_station_sale(title("Соларен парк 2 MW, проект за изграждане"))
    assert is_station_sale(title("Соларен парк 2 MW, проект с разрешение за строеж"))


def test_parse_ad_extracts_facts():
    l = imot.parse_ad("https://www.imot.bg/obiava-1t177", AD)
    assert l.price_text == "989 000 €" and l.location == "Враца, Дъбника" and l.seller == "ПРИМЕР ИМОТИ"
    assert "1.2 Мегавата" in l.text and "0888654321" in l.text


def test_running_park_card_with_design_capacity_is_not_a_project():
    html = ('<div class="item"><a href="//www.imot.bg/obiava-1">x</a> СОЛАРЕН ПАРК Работещ соларен парк 1.5 MWp, '
            'проектна мощност 1,5 MWp</div>')
    assert list(imot.card_links(html)[1].values()) == ["Соларен парк 1500 kW"]
