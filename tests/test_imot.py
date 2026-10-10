from solar_outreach.sources import imot, is_station_sale

CARDS = """<div class="item"><a href="//www.imot.bg/obiava-1t123-prodava-biznes-imot-grad-gabrovo-etara">x</a>
Продава СОЛАРЕН ПАРК град Габрово, Етъра 870 000 € 19000 кв.м, Соларен парк, с мощност 1.1 Мегавата, тел.: 0888654321</div>
<div class="item"><a href="//www.imot.bg/obiava-1t178-prodava-kashta">x</a>Продава КЪЩА с. Козаревец 50 000 €</div>"""

AD = """<html><body><script>var x=1;</script>Продава СОЛАРЕН ПАРК, град Габрово, Етъра Площ: 19000 кв.м, Агенция
Продава СОЛАРЕН ПАРК град Габрово, Етъра Виж карта 870 000 € (46 €/m2) Не се начислява ДДС
Описание на имота: Соларен парк, с мощност 1.1 Мегавата, в експлоатация. Особености Нова
За контакт: 0888654321 Брокер: Петър Георгиев Агенция: ПРИМЕР ИМОТИ 0888654321 В imot.bg от 2015 г.
ИЗПРАТИ ЗАПИТВАНЕ</body></html>"""


def test_only_solar_park_cards_are_taken():
    n, found = imot.card_links(CARDS)
    assert n == 2 and list(found) == ["https://www.imot.bg/obiava-1t123-prodava-biznes-imot-grad-gabrovo-etara"]
    assert is_station_sale(next(iter(found.values())))


def test_parse_ad_extracts_facts():
    l = imot.parse_ad("https://www.imot.bg/obiava-1t123", AD)
    assert l.price_text == "870 000 €" and l.location == "Габрово, Етъра" and l.seller == "ПРИМЕР ИМОТИ"
    assert "1.1 Мегавата" in l.text and "0888654321" in l.text
