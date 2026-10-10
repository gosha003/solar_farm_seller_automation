from solar_outreach.alo_reply import same_title


def test_dialog_title_must_match_station_title():
    assert same_title("Запитване по Фотоволтаична централа 1.5MW)", "Фотоволтаична централа 1.5MW")
    assert same_title("Inquiry on Photovoltaic plant", "photovoltaic  plant")
    assert not same_title("Запитване по Фотоволтаична централа 1.5MW)", "Продава фотоволтаична централа")  # S020 != S021
    assert not same_title("", "Фотоволтаична централа")
