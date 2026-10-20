"""The PC routine sends «Ручная очередь» rows by itself: the same guards as email, one row per station."""
from solar_outreach import site_queue

ALO = {"id станции": "S001", "канал": "сайт (ручная)", "ссылка на объявление": "https://www.alo.bg/x-1", "статус": "отправить вручную"}
WA = {"id станции": "S002", "канал": "телефон (ручная)", "ссылка на объявление": "https://bazar.example/2",
      "WhatsApp": "https://wa.me/359888000222?text=x", "статус": "отправить вручную"}


def station(sid, status="ждём ответа", **kw):
    return {"id": sid, "статус переписки": status, "email": "", "телефон": "", "ссылка": f"https://x.example/{sid}", **kw}


def test_kind_only_alo_and_whatsapp():
    assert site_queue.kind(ALO) == "alo" and site_queue.kind(WA) == "whatsapp"
    assert site_queue.kind({**WA, "канал": "телефон (ручная)", "WhatsApp": ""}) == ""
    assert site_queue.kind({**ALO, "ссылка на объявление": "https://bazar.example/1"}) == ""
    assert site_queue.kind({**WA, "канал": "email: a@example.org"}) == ""


def test_rows_skip_paused_refused_and_older_rows(sheet):
    sheet.add("Станции", station("S001"), station("S002", телефон="+359888000222"), station("S003", "эскалация"),
              station("S004"))
    sheet.add("Отказы", {"контакт": "0888 000 222"})
    sheet.add("Ручная очередь", {**ALO, "текст": "дожим"}, WA, {**ALO, "id станции": "S003"},
              {**ALO, "id станции": "S004", "текст": "старое"}, {**ALO, "id станции": "S004", "текст": "ответ"},
              {**ALO, "id станции": "S005"})                      # no such station
    rows = site_queue.rows(None, None)
    assert [(r["id станции"], r["текст"]) for r in rows] == [("S001", "дожим"), ("S004", "ответ")]
    sheet.add("Настройки", {"параметр": "пауза", "значение": "да"})
    assert site_queue.rows(None, None) == []                       # /pause stops the routine too


def test_sent_marks_only_the_message_that_was_handed_out(sheet, monkeypatch):
    sheet.add("Станции", station("S004", "одобрено, ждёт отправки"))
    sheet.add("Черновики", {"id": "D001", "id станции": "S004", "тип": "дожим 1", "статус": "одобрено", "текст": "старое"},
              {"id": "D002", "id станции": "S004", "тип": "ответ", "статус": "одобрено", "текст": "ответ"})
    sheet.add("Ручная очередь", {**ALO, "id станции": "S004", "текст": "старое"}, {**ALO, "id станции": "S004", "текст": "ответ"})
    assert "D002" in site_queue.sent(None, None, "S004")
    assert [d["статус"] for d in sheet.tabs["Черновики"]] == ["отменено: заменено новым", "отправлено"]  # never sent later
    assert [r["статус"] for r in sheet.tabs["Ручная очередь"]] == ["отменено: заменено новым", "отправлено вручную"]
    assert "Нет такого сообщения" in site_queue.sent(None, None, "S999")


def test_sent_with_the_text_records_it_even_after_a_pause_and_keeps_the_pause(sheet):
    sheet.add("Станции", station("S004"))
    sheet.add("Черновики", {"id": "D002", "id станции": "S004", "тип": "ответ", "статус": "одобрено", "текст": "ответ"})
    sheet.add("Ручная очередь", {**ALO, "id станции": "S004", "текст": "ответ"})
    sheet.tabs["Станции"][0]["статус переписки"] = "ответ на alo"   # the seller wrote again while the routine was sending
    sheet.add("Настройки", {"параметр": "пауза", "значение": "да"})
    assert "D002" in site_queue.sent(None, None, "S004", "ответ\n")   # the file the routine wrote, a newline added
    assert sheet.tabs["Черновики"][0]["статус"] == "отправлено" and sheet.tabs["Ручная очередь"][0]["статус"] == "отправлено вручную"
    assert sheet.tabs["Станции"][0]["статус переписки"] == "ответ на alo"   # the new reply still gets read


def test_failed_takes_the_station_off_the_routine_list(sheet):
    sheet.add("Станции", station("S002", телефон="+359888000222"), station("S004"))
    sheet.add("Ручная очередь", WA, {**ALO, "id станции": "S004"})
    assert "оповещён" in site_queue.failed(None, None, "S002", "номера нет в WhatsApp")
    assert [r["id станции"] for r in site_queue.rows(None, None)] == ["S004"]
    assert sheet.tabs["Ручная очередь"][0]["статус"] == "не отправлено с ПК: номера нет в WhatsApp"
