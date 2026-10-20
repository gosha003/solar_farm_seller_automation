from solar_outreach.alo_reply import same_title


def test_dialog_title_must_match_station_title():
    assert same_title("Запитване по Фотоволтаична централа 1.5MW)", "Фотоволтаична централа 1.5MW")
    assert same_title("Inquiry on Photovoltaic plant", "photovoltaic  plant")
    assert not same_title("Запитване по Фотоволтаична централа 1.5MW)", "Продава фотоволтаична централа")  # S020 != S021
    assert not same_title("", "Фотоволтаична централа")


def test_unparsed_reply_keeps_the_station_on_the_routine_list(sheet, monkeypatch):
    from solar_outreach import alo_reply, llm

    def down(text, st):
        raise llm.LLMUnavailable("API: OverloadedError")
    monkeypatch.setattr(alo_reply.owner.llm, "classify", down)
    monkeypatch.setattr(alo_reply.notify, "send", lambda text, buttons=None: 1)
    sheet.add("Станции", {"id": "S020", "заголовок": "Фотоволтаична централа 1.5MW", "статус переписки": "ответ на alo",
                          "ссылка": "https://www.alo.bg/x-1"})
    msg = alo_reply.note(None, None, "S020", "Здравейте, работи.", "Запитване по Фотоволтаична централа 1.5MW")
    assert "S020" in msg and [s["id"] for s in alo_reply.pending(None, None)] == ["S020"]   # read again next run
