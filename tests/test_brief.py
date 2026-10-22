"""brief.py, documents.py, director.py: the strict document gate, the second reading, director briefs on the owner's ✅,
document requests inside the conversation, the director's replies. Made-up stations and documents only."""
import base64
import io
import json
import zipfile
from datetime import date

import pytest

from solar_outreach import approval, brief, director, documents, drive, gmail_client, llm, owner, sheets, site_queue
from solar_outreach.config import load_config

TODAY = date(2026, 10, 22)
SAID = {}  # what the director's reply reads as, per test
CFG = load_config()
FIELDS = set(documents.Doc.model_fields)


def doc(kind, title, number="", day="", **kw):
    d = {"type": kind, "title": title, "number": number, "date": day, "issuer": "", "holder": "Пример Солар ЕООД",
         "plots": [], "kwp": None, "kw_ac": None, "in_force": "", "recertified": "", "refers_to": "", "deadlines": [],
         "paid_confirmed": False, "months": 0, "executed": True, "key_terms": [], "file": f"{title}.pdf",
         "file_id": f"f-{title}", "link": ""}
    return {**d, **kw}


def due(day, kind="ввод объекта"):
    return {"kind": kind, "what": kind, "date": day}


PERMIT = doc(documents.PERMIT, "Разрешение №7", "7", "2025-02-01", in_force="2025-03-01", kwp=1500.0)
GRID = doc(documents.GRID, "Договор №111", "111", "2025-05-01", deadlines=[due("2027-12-31")])
PAID = doc(documents.PAYMENT, "Платёжное поручение", paid_confirmed=True)
RIGHT = doc(documents.BUILD_RIGHT, "Нотариальный акт №3", "3")
PROJECT = [PERMIT, GRID, PAID, RIGHT]
ST = {"id": "S500", "отбор": "кандидат", "заголовок": "ФЕЦ 1,5 MW с разрешение за строеж", "мощность кВт": "1500",
      "статус переписки": "ведёт владелец", "1 действующая": "получен: Не построена", "email": "seller@example.org",
      "телефон": "", "канал": "email", "ссылка": "https://bazar.example/1", "регион": "Плевен", "продавец": "",
      "площадка": "bazar.bg", "примечание": "целевая цена 100 €/kWp, брокерам не говорить друг о друге",
      "цена EUR": "300000", "EUR/кВт": "200"}


def problems(docs, st=ST, today=TODAY):
    return brief.gate(st, docs, CFG, today)[0]


# --- the gate ---------------------------------------------------------------------------------------------------------

def test_complete_project_passes_and_each_missing_piece_blocks_with_a_request():
    assert problems(PROJECT) == []
    for missing in PROJECT:
        found = problems([d for d in PROJECT if d is not missing])
        assert len(found) == 1 and found[0][1]          # one problem, and something the seller can send


def test_grid_deadline_counts_a_later_annex_but_not_an_older_one():
    expired = {**GRID, "deadlines": [due("2026-05-03")]}
    [(text, ask)] = problems([PERMIT, expired, PAID, RIGHT])
    assert "истёк" in text and "анекс" in ask
    annex = doc(documents.ANNEX, "Анекс 1", "111-1", "2026-04-01", refers_to="Договор 111", deadlines=[due("2027-12-31")])
    assert problems([PERMIT, expired, annex, PAID, RIGHT]) == []
    older = {**annex, "date": "2024-01-01"}       # an annex to the preliminary contract, before this one was signed
    assert problems([PERMIT, expired, older, PAID, RIGHT])


def test_an_annex_that_moves_another_deadline_never_hides_an_expired_commissioning():
    contract = {**GRID, "deadlines": [due("2026-05-03"), due("2025-12-01", "процедура ЕСО")]}
    eso_only = doc(documents.ANNEX, "Анекс 2", "111-2", "2026-06-01", refers_to="111", deadlines=[due("2027-06-01", "процедура ЕСО")])
    assert "срок ввода 03.05.2026 истёк" in problems([PERMIT, contract, eso_only, PAID, RIGHT])[0][0]


def test_a_near_deadline_reads_the_same_every_day():
    soon = {**GRID, "deadlines": [due("2026-11-30")]}
    today = problems([PERMIT, soon, PAID, RIGHT])
    assert today == problems([PERMIT, soon, PAID, RIGHT], today=date(2026, 10, 23)) and "меньше 60 дн." in today[0][0]


def test_permit_needs_its_in_force_date_and_a_site_protocol_after_three_years():
    assert "не заполнена" in problems([{**PERMIT, "in_force": ""}, GRID, PAID, RIGHT])[0][0]
    old = {**PERMIT, "in_force": "2023-08-07"}       # 3 years ran out on 07.08.2026
    [(text, ask)] = problems([old, GRID, PAID, RIGHT])
    assert "истекли 07.08.2026" in text and "протокол" in ask
    protocol = doc(documents.PROTOCOL, "Протокол обр. 2", "", "2026-04-14", refers_to="")
    assert problems([old, protocol, GRID, PAID, RIGHT]) == []
    assert problems([old, {**protocol, "date": "2023-01-10"}, GRID, PAID, RIGHT])   # before the permit: not a start
    near = {**PERMIT, "in_force": "2023-11-23"}      # loses force 23.11.2026: a month left
    assert "теряет силу 23.11.2026" in problems([near, GRID, PAID, RIGHT])[0][0]
    assert problems([{**near, "recertified": "2026-09-01"}, GRID, PAID, RIGHT]) == []
    started = {**PERMIT, "in_force": "2021-06-01"}
    rough = doc(documents.PROTOCOL, "Протокол", "", "2021-11-30", refers_to="7")  # 5 years for the rough build end 30.11.2026
    assert "грубый строеж" in problems([started, rough, GRID, PAID, RIGHT])[0][0]


def test_references_ignore_the_digits_of_dates():
    assert brief.same_ref("12", "РС № 12/15.03.2024") and not brief.same_ref("15", "РС № 12/15.03.2024")
    assert brief.same_ref("1234567", "към договор от 11.01.2024 № 1234567")


def test_invoices_drafts_and_capacity():
    invoice = doc(documents.INVOICE, "Фактура")
    [(text, _)] = problems([PERMIT, GRID, invoice, RIGHT])
    assert "есть только счета" in text
    small = [{**PERMIT, "kwp": 990.0}, GRID, PAID, RIGHT]
    assert "вне критериев" in problems(small)[0][0] and problems(small)[0][1] == ""
    assert problems(small, {**ST, "отбор": "проект (исключение владельца)"}) == []
    two = [PERMIT, {**PERMIT, "number": "8", "title": "Разрешение №8"}, GRID, PAID, RIGHT]   # parts add up
    assert brief.gate(ST, two, CFG, TODAY)[1]["kwp"] == 3000.0
    again = [PERMIT, {**PERMIT, "number": "№ 7/01.02.2025", "file_id": "f-bundle"}, GRID, PAID, RIGHT]  # sent twice
    assert brief.gate(ST, again, CFG, TODAY)[1]["kwp"] == 1500.0
    assert brief.capacity({**ST, "мощность кВт": "1 499,6"}, [], False) == 1499.6


def test_an_unsigned_copy_is_asked_for_and_its_deadline_still_counts():
    copy = {**GRID, "executed": False, "deadlines": [due("2026-05-03")]}
    found = problems([PERMIT, copy, PAID, RIGHT])
    assert [p for p, _ in found] == ["Договор №111: в присланной копии нет подписей и печатей",
                                     "Договор №111: срок ввода 03.05.2026 истёк, анекса о продлении нет"]
    assert found[0][1] == "подписанный экземпляр: Договор №111"
    assert "подписанный экземпляр" in problems([PERMIT, GRID, PAID, {**RIGHT, "executed": False}])[0][1]


def test_operating_only_when_the_documents_or_the_seller_say_so():
    st = {**ST, "заголовок": "Фотоволтаична централа 1,2 MW", "отбор": "кандидат"}
    for answer in ("получен: проект с разрешением", "получен: в процессе строительства", "не спрошено"):
        assert not brief.is_operating({**st, "1 действующая": answer}, PROJECT)       # unknown means a project
    assert brief.is_operating({**st, "1 действующая": "получен: да, действующая"}, [])
    full = [doc(documents.USE_PERMIT, "Акт 16", "16", kwp=1200.0), GRID, RIGHT,
            doc(documents.PRODUCTION, "Выработка", months=12), doc(documents.POWER_SALE, "Договор с трейдером")]
    assert problems(full, st) == []
    short = [d if d["type"] != documents.PRODUCTION else {**d, "months": 7} for d in full]
    assert "12 месяцев" in problems(short, st)[0][0]


def test_dates_as_models_and_sellers_write_them():
    assert brief._d("2023-11-23") == brief._d("23.11.2023") == brief._d("23.11.2023 г.") == date(2023, 11, 23)
    assert brief._d("4.12.23") == date(2023, 12, 4) and brief._d("36 месеца от 01.03.2024") is None
    assert problems([{**PERMIT, "in_force": "01.03.2025"}, GRID, PAID, RIGHT]) == []


def test_fingerprint_follows_what_the_verdict_depends_on():
    fp = brief.fingerprint(ST, PROJECT, [], CFG)
    assert fp.startswith("fp") and fp == brief.fingerprint(ST, PROJECT, [], CFG)
    assert fp != brief.fingerprint({**ST, "отбор": "проект (исключение владельца)"}, PROJECT, [], CFG)
    assert fp != brief.fingerprint(ST, [{**PERMIT, "in_force": "2025-03-02"}, GRID, PAID, RIGHT], [], CFG)


# --- text -------------------------------------------------------------------------------------------------------------

def test_hedging_never_reaches_the_director():
    text = "Цена низкая. На мой взгляд, объект интересный. Я не юрист, но риск есть.\nРиск — срок."
    assert brief.unhedge(text) == "Цена низкая.\nРиск — срок."
    assert not brief.HEDGE.search("Мнение: объект подходит. Мощность 1 МВт.")


def test_render_lays_out_sections_once_without_a_gmail_link():
    b = brief.Brief(title="S500 — S500 — ФЭС 1,5 MW, Плевен", overview="Проект.", price_eur=300000,
                    confirmed=["Разрешение №7"], seller_only=["Нет долгов"], risks=[brief.Risk(level="средний", text="Срок")],
                    opinion="Подходит.", next_steps=["Выписка из ТР"], attach=["f-1"], blocking=[])
    subject, body = brief.render({**ST, "ссылка": "https://mail.google.com/x"}, b, {"f-1": "Разрешение.pdf"}, True)
    assert subject == "Обновление: S500 — ФЭС 1,5 MW, Плевен: сводка для переговоров" and "Объявление" not in body
    for part in ("Подтверждено документами:\n— Разрешение №7", "Риски:\n— Срок (средний)", "Общее мнение. Подходит.",
                 "До подписания:\n1. Выписка из ТР", "Вложения:\nРазрешение.pdf"):
        assert part in body


def test_route_answers_in_the_thread_without_marking_the_message_answered():
    msg = {"threadId": "t", "payload": {"headers": [{"name": "Subject", "value": "Re: RE: Отговор"},
                                                    {"name": "From", "value": "Иван <s@example.org>"},
                                                    {"name": "Message-ID", "value": "<m@x>"}]}}
    where = brief.route(ST, msg, "bg")
    assert where == {"канал": "email", "кому": "s@example.org", "тред Gmail": "t", "тема": "Re: Отговор"}


def test_docx_text_and_unsupported_files():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", "<w:p><w:t>Договор &amp; анекс</w:t></w:p>")
    [block], _ = documents.blocks("x.docx", buf.getvalue())
    assert "Договор & анекс" in block["text"]
    assert documents.blocks("x.heic", b"1") == (None, "формат .heic не читается")
    assert documents.blocks("x.jpg", b"0" * (documents.MAX_IMAGE + 1))[0] is None   # base64 would pass 5 MB


# --- a pass on an in-memory sheet -------------------------------------------------------------------------------------

@pytest.fixture
def world(monkeypatch, sheet):
    """Drive, Gmail, Telegram and Claude fakes around one owner-led station."""
    monkeypatch.setenv("DRY_RUN", "false")
    tg, sent, calls = [], [], []
    said = SAID
    said.clear()
    monkeypatch.setattr(brief.notify, "send", lambda text, buttons=None: tg.append((text, buttons)) or len(tg))
    monkeypatch.setattr(brief.notify, "try_send", lambda text: tg.append((text, None)))
    monkeypatch.setattr(drive, "service", lambda: None)
    monkeypatch.setattr(drive, "station_files", lambda svc: {"S500": [{"id": d["file_id"], "name": d["file"], "size": "10"}
                                                                      for d in PROJECT]})
    monkeypatch.setattr(drive, "download", lambda svc, fid: b"%PDF-1.4 " + fid.encode())
    monkeypatch.setattr(gmail_client, "service", lambda: None)
    monkeypatch.setattr(gmail_client, "last_in_threads", lambda svc, threads: said.setdefault("threads", threads) and None)
    monkeypatch.setattr(gmail_client, "send_files", lambda svc, to, subject, body, files, *a, **kw: sent.append(
        (to, subject, [f[0] for f in files])) or {"id": "m", "threadId": "t-dir"})
    by_name = {d["file"]: d for d in PROJECT}

    def call_json(model_key, user, schema, **kw):
        calls.append(schema.__name__)
        if schema is director.DirectorReply:
            return said["reply"]
        if schema is brief.Request:
            said["prompt"] = user
            if "during" in said:  # something happens in the sheet while Claude is writing
                said.pop("during")()
            return brief.Request(appropriate=True, reason_ru="", body="Здравейте,\n\nМоля, изпратете анекса.")
        if schema is brief.Review:
            return brief.Review(issues=[])
        if schema is documents.FileDocs:  # the second reading agrees with the first
            name = next(n for n in by_name if f"«{n}»" in user[-1]["text"])
            return documents.FileDocs(readable=True, docs=[documents.Doc(**{k: by_name[name][k] for k in FIELDS})])
        if "fail" in said:
            raise llm.LLMError(said["fail"])
        return brief.Brief(title="ФЭС 1,5 MW, Плевен", overview="Проект.", price_eur=300000, confirmed=["Разрешение №7"],
                           seller_only=[], risks=[], opinion="Подходит для переговоров.", next_steps=[],
                           attach=[PERMIT["file_id"], GRID["file_id"], "f-unknown"], blocking=[])
    monkeypatch.setattr(llm, "call_json", call_json)
    sheet.add("Станции", ST)
    return sheet, tg, sent, calls


def register(sheet, docs, confirmed=False):
    for d in docs:
        sheet.add("Документы", {"id станции": "S500", "id файла": d["file_id"], "файл": d["file"], "тип": d["type"],
                                "документы": d["title"], "данные": json.dumps([{k: d[k] for k in FIELDS}], ensure_ascii=False),
                                "проверено": f"2026-10-22 {documents.CONFIRMED}" if confirmed else "2026-10-22"})


def test_not_ready_requests_documents_once_and_owner_led_gets_a_card(world):
    sheet, tg, _, calls = world
    register(sheet, [PERMIT, {**GRID, "deadlines": [due("2026-05-03")]}, PAID, RIGHT])
    sheet.add("Диалоги", {"id станции": "S500", "направление": "in", "дата": "2026-10-20", "канал": "email",
                          "краткое содержание": "[info_provided] прислал документы", "тред Gmail": "t-own"})
    brief.run(today=TODAY)
    [row] = sheet.tabs["Сводки"]
    [d] = sheet.tabs["Черновики"]
    assert row["итог"] == "не готово" and "истёк" in row["проблемы"] and row["запрос"] == d["id"]
    assert d["тип"] == brief.DOC_REQUEST and d["статус"] == "на одобрении" and d["кому"] == "seller@example.org"
    assert d["in-reply-to"] == "" and any(b for _, b in tg)   # owner-led: a card, even in auto mode
    assert SAID["threads"] == ["t-own"]                      # only this station's own thread is ever searched
    assert "целевая цена" not in SAID["prompt"] and "брокерам" not in SAID["prompt"]   # internal notes stay out
    assert calls == ["Request"]                              # no second reading and no brief for a station not ready
    brief.run(today=TODAY)                                   # nothing changed: no row, no letter, no LLM call
    assert len(sheet.tabs["Сводки"]) == 1 and len(sheet.tabs["Черновики"]) == 1 and calls == ["Request"]


def test_a_request_written_while_the_seller_wrote_again_is_dropped(world):
    sheet, tg, _, calls = world
    register(sheet, [PERMIT, GRID, {**PAID, "paid_confirmed": False}, RIGHT])   # the payment is not proven
    SAID["during"] = lambda: sheet.add("Диалоги", {"id станции": "S500", "направление": "in", "дата": "2026-10-22",
                                                   "канал": "email", "краткое содержание": "[info_provided] новое"})
    brief.run(today=TODAY)
    assert sheet.tabs["Черновики"] == [] and sheet.tabs["Сводки"][0]["запрос"] == ""
    assert any("переписка изменилась" in t for t, _ in tg)


def test_request_waits_for_the_owner_and_the_seller():
    st = {**ST, "статус переписки": "ждём ответа"}
    def block(history=(), mine=(), last=None, status=None, paused=False, today=TODAY):
        return brief.request_block({**st, **({"статус переписки": status} if status else {})}, list(mine), list(history),
                                   last, "fp1", CFG, today, paused)
    row = lambda way, day, text="[info_provided] x": {"направление": way, "дата": day, "краткое содержание": text}
    assert block([row("out", "2026-10-20")]) == "ждём ответа на наше последнее сообщение"
    assert block([row("out", "2026-10-20")], today=date(2026, 10, 28)) == ""
    assert block([row("in", "2026-10-22"), row("out", "2026-10-22")]) != ""     # the bot answered today: it asked already
    assert block([row("out", "2026-10-22"), row("in", "2026-10-22")]) == ""     # the seller wrote after us
    assert "отказался" in block([row("in", "2026-10-22", "[sold] продано")])
    assert "пропустил" in block(mine=[{"тип": brief.DOC_REQUEST, "статус": "пропущено", "дата": "2026-10-21"}])
    assert "черновик" in block(mine=[{"тип": "ответ", "статус": "на одобрении", "дата": "2026-10-21"}])
    assert "решения" in block(status="эскалация") and "решения" in block(status="перенаправлено")
    assert "пауз" in block(paused=True)
    cached = {"запрос": "неуместно: только после NDA", "отпечаток": "fp1", "дата": "2026-01-01"}
    assert block(last=cached) == "неуместно: только после NDA"                  # kept until something changes
    assert block(last={**cached, "отпечаток": "fp2"}) == ""


def test_ready_brief_is_read_twice_and_goes_to_the_director_only_on_the_owners_tap(world):
    sheet, tg, sent, calls = world
    register(sheet, PROJECT)
    brief.run(today=TODAY)
    assert calls.count("FileDocs") == 4 and all(documents.CONFIRMED in r["проверено"] for r in sheet.tabs["Документы"])
    [row] = sheet.tabs["Сводки"]
    assert row["итог"] == "готово" and row["статус"] == "на одобрении" and sent == []
    assert row["вложения"] == f"{PERMIT['file_id']},{GRID['file_id']}"        # unknown file ids dropped
    _, buttons = tg[-1]
    assert buttons[0][0][1] == "b:2:S500"
    assert "не от неё" in brief.approve(None, "test", "2:S501", TODAY)        # another station's card
    assert "не от неё" in brief.approve(None, "test", "2:S500", TODAY, message_id=999)   # an older card of this row
    assert "📨" in brief.approve(None, "test", "2:S500", TODAY, message_id=row["tg сообщение"])
    assert sent == [("director@example.com", row["тема"], [PERMIT["file"], GRID["file"]])]
    assert sheet.tabs["Сводки"][0]["статус"] == "отправлено"
    assert "уже обработана" in brief.approve(None, "test", "2:S500", TODAY)   # a second tap sends nothing
    n = len(calls)
    brief.run(today=TODAY)
    assert len(sheet.tabs["Сводки"]) == 1 and len(calls) == n                 # same documents: no new brief


def test_tap_after_a_deadline_passed_or_the_seller_refused_sends_nothing(world):
    sheet, tg, sent, _ = world
    register(sheet, PROJECT, confirmed=True)
    brief.run(today=TODAY)
    assert "не отправил" in brief.approve(None, "test", "2:S500", date(2028, 1, 5))   # the grid deadline passed
    assert sent == [] and sheet.tabs["Сводки"][0]["статус"] == "устарело"
    sheet.tabs["Сводки"][0]["статус"] = "на одобрении"
    sheet.tabs["Станции"][0]["статус переписки"] = "отказ"
    assert "отказ" in brief.approve(None, "test", "2:S500", TODAY) and sent == []


def test_a_station_that_left_the_deal_turns_its_waiting_brief_stale(world, monkeypatch):
    sheet, tg, sent, _ = world
    register(sheet, PROJECT, confirmed=True)
    brief.run(today=TODAY)
    sheet.tabs["Станции"][0]["статус переписки"] = "отказ"
    brief.run(today=TODAY)
    assert sheet.tabs["Сводки"][0]["статус"] == "устарело"


def test_a_broken_answer_is_recorded_once_not_retried_every_hour(world):
    sheet, tg, _, calls = world
    register(sheet, PROJECT, confirmed=True)
    SAID["fail"] = "модель отказалась"
    brief.run(today=TODAY)
    brief.run(today=TODAY)
    assert [r["итог"] for r in sheet.tabs["Сводки"]] == [brief.ERROR] and calls.count("Brief") == 1
    assert sum("Документы не проверены" in t for t, _ in tg) == 1


def test_one_broken_file_never_stops_the_pass(monkeypatch, sheet):
    files = {"S500": [{"id": "a", "name": "a.pdf", "size": "10"}, {"id": "b", "name": "b.pdf", "size": str(10 ** 9)},
                      {"id": "c", "name": "c.pdf", "size": "10"}]}
    def download(svc, fid):
        if fid == "a":
            raise OSError("Drive 500")
        assert fid != "b"                                   # too big: never downloaded
        return b"%PDF"
    monkeypatch.setattr(drive, "download", download)
    monkeypatch.setattr(documents, "extract", lambda name, data: (documents.FileDocs(readable=True, docs=[]), ""))
    assert documents.refresh(None, "test", files, {"S500"}, None, TODAY) == 3
    assert [r["тип"] for r in sheet.tabs["Документы"]] == [documents.UNREAD] * 3
    assert "OSError" in sheet.tabs["Документы"][0]["документы"] and "МБ" in sheet.tabs["Документы"][1]["документы"]


# --- sending into the owner's conversation ---------------------------------------------------------------------------

def test_owner_led_document_request_is_sent_on_his_tap_and_the_conversation_stays_his(monkeypatch, sheet):
    monkeypatch.setenv("DRY_RUN", "false")
    out = []
    monkeypatch.setattr(approval.gmail, "service", lambda: None)
    monkeypatch.setattr(approval.gmail, "send", lambda svc, to, *a: out.append(to) or {"id": "m", "threadId": "t"})
    monkeypatch.setattr(approval.notify, "try_send", lambda text: None)
    sheet.add("Станции", ST)
    st = sheet.read(None, None, "Станции")[0]
    d = {"id": "D001", "id станции": "S500", "тип": brief.DOC_REQUEST, "канал": "email", "кому": "seller@example.org",
         "тема": "Re: x", "текст": "t", "статус": "одобрено", "язык": "bg", "tg сообщение": "5"}
    approval.send_approved(None, "test", [dict(d)], {}, {"S500": st}, CFG)
    assert out == ["seller@example.org"] and st["статус переписки"] == "ведёт владелец"
    held = {**d, "tg сообщение": ""}                       # never shown to the owner: an owner-led dialog stays paused
    approval.send_approved(None, "test", [held], {}, {"S500": st}, CFG)
    assert out == ["seller@example.org"] and held["статус"] == "на паузе"
    assert approval.send_approved(None, "test", [{**d, "тип": brief.DIRECTOR_REQUEST, "tg сообщение": ""}], {},
                                  {"S500": st}, CFG) and out[-1] == "seller@example.org"   # the director asked


def test_sent_by_hand_keeps_the_owners_conversation(sheet):
    sheet.add("Станции", {**ST, "канал": "сайт (ручная)"})
    sheet.add("Черновики", {"id": "D001", "id станции": "S500", "тип": brief.DOC_REQUEST, "статус": "одобрено",
                            "канал": "сайт (ручная)", "текст": "t", "tg сообщение": "5", "язык": "bg"})
    stations = {s["id"]: s for s in sheet.read(None, None, "Станции")}
    by_id = {d["id"]: d for d in sheet.read(None, None, "Черновики")}
    c = owner.Ctx(None, "test", CFG, stations, by_id, [], {}, {})
    assert "📤" in owner.sent(c, "S500", TODAY)
    assert stations["S500"]["статус переписки"] == "ведёт владелец" and by_id["D001"]["статус"] == "отправлено"


def test_a_seller_who_refused_gets_no_waiting_document_request(sheet):
    d = {"id": "D001", "id станции": "S500", "тип": brief.DOC_REQUEST, "статус": "одобрено", "текст": "t", "дата": "2026-10-20"}
    dialogs = [{"id станции": "S500", "направление": "in", "дата": "2026-10-21", "краткое содержание": "[sold] продано"}]
    approval.cancel_followups(None, "test", {"D001": d}, dialogs)
    assert d["статус"] == "отменено: продавец ответил"


def test_skipping_a_document_request_does_not_pause_the_dialog(monkeypatch):
    st = {"S500": {**ST, "статус переписки": "ждём ответа"}}
    d = {"id": "D001", "id станции": "S500", "тип": brief.DOC_REQUEST, "статус": "на одобрении", "канал": "email"}
    monkeypatch.setattr(approval.notify, "updates", lambda offset: [
        {"update_id": 1, "_owner": True, "callback_query": {"id": "c", "data": "s:D001"}}])
    monkeypatch.setattr(approval.notify, "answer_callback", lambda *a: None)
    approval.handle_updates({}, st, {"D001": d})
    assert d["статус"] == "пропущено" and st["S500"]["статус переписки"] == "ждём ответа"


def test_routine_sends_an_owner_led_request_only_if_he_approved_it(sheet):
    st = {**ST, "канал": "телефон (ручная)", "телефон": "+359888000111", "email": ""}
    sheet.add("Станции", st)
    row = {"id станции": "S500", "канал": "телефон (ручная)", "ссылка на объявление": st["ссылка"], "текст": "Моля, анекса",
           "WhatsApp": "https://wa.me/359888000111", "статус": "отправить вручную"}
    sheet.add("Ручная очередь", row)
    assert site_queue.rows(None, "test") == []
    sheet.add("Черновики", {"id": "D001", "id станции": "S500", "тип": brief.DOC_REQUEST, "статус": "одобрено",
                            "текст": "Моля, анекса", "tg сообщение": "7"})
    assert [r["id станции"] for r in site_queue.rows(None, "test")] == ["S500"]


# --- the director's replies -------------------------------------------------------------------------------------------

def letter(text, msg_id="m1"):
    """A director's reply as Gmail returns it."""
    return {"id": msg_id, "threadId": "t-dir", "labelIds": [],
            "payload": {"mimeType": "text/plain", "headers": [{"name": "From", "value": "director@example.com"},
                                                               {"name": "Message-ID", "value": f"<{msg_id}@x>"}],
                        "body": {"data": base64.urlsafe_b64encode(text.encode()).decode()}}}


@pytest.fixture
def answered(world, monkeypatch):
    """A brief that went to the director; his reply waits in the thread."""
    sheet, tg, sent, calls = world
    register(sheet, PROJECT, confirmed=True)
    sheet.add("Сводки", {"id станции": "S500", "итог": "готово", "статус": "отправлено", "тема": "S500 — ФЭС: сводка",
                         "текст": "сводка", "тред Gmail": "t-dir"})
    sheet.add("Настройки", {"параметр": "approval_mode", "значение": "auto"})
    inbox_, fresh, marked = [letter("Не хватает анекса к договору, запросите.")], [], []
    monkeypatch.setattr(gmail_client, "replies_from", lambda svc, sender, label: [m for m in inbox_ if m["id"] not in marked])
    monkeypatch.setattr(gmail_client, "fresh_from", lambda svc, sender, label: [m for m in fresh if m["id"] not in marked])
    monkeypatch.setattr(gmail_client, "mark_internal", lambda svc, m, label, own_thread=True: marked.append(
        m["id"] if own_thread else f"seen:{m['id']}"))
    SAID["fresh"] = fresh
    monkeypatch.setattr(gmail_client, "send_files", lambda svc, to, subject, body, files, label="", thread_id="",
                        in_reply_to="": sent.append((to, subject, body, [f[0] for f in files], thread_id, in_reply_to))
                        or {"id": "m", "threadId": thread_id})
    return sheet, tg, sent, calls, marked


def reply(verdict, documents=(), for_owner=(), station=""):
    return director.DirectorReply(station=station, summary_ru="Директор ответил.", verdict=verdict,
                                  documents=list(documents), questions=[], for_owner=list(for_owner))


def test_director_asks_for_documents_and_the_system_requests_them_itself(answered):
    sheet, tg, sent, calls, marked = answered
    SAID["reply"] = reply("нужно больше данных", ["анекс к договору №111"], ["цена"])
    assert director.replies(None, "test", None, TODAY) == 1
    [d] = sheet.tabs["Черновики"]
    assert d["тип"] == brief.DIRECTOR_REQUEST and d["статус"] == "одобрено"     # auto mode, the owner's dialog too
    assert sheet.tabs["Станции"][0]["статус переписки"] == "ведёт владелец" and brief.cleared(d)
    [(to, subject, body, files, thread, reply_to)] = sent
    assert to == "director@example.com" and subject.startswith("Re: ") and thread == "t-dir" and reply_to == "<m1@x>"
    assert "Запросил у продавца:\n1. анекс к договору №111" in body and "Передано" in body   # price stays with the owner
    assert any("Сделал сам" in t and "<blockquote>" in t for t, _ in tg)        # his letter quoted in the notice
    row = sheet.tabs["Сводки"][0]
    assert "письмо m1" in row["ответ директора"] and json.loads(row["ждёт директор"])["reply_to"] == "<m1@x>"
    assert "директор ←" in sheet.tabs["Станции"][0]["примечание"] and marked == ["m1"]
    assert any("За тобой" in t for t, _ in tg)
    assert director.replies(None, "test", None, TODAY) == 0 and len(sheet.tabs["Черновики"]) == 1   # handled once


def test_the_director_cannot_push_a_request_to_a_seller_who_refused(answered):
    sheet, tg, sent, calls, marked = answered
    sheet.add("Диалоги", {"id станции": "S500", "направление": "in", "дата": "2026-10-21", "канал": "email",
                          "краткое содержание": "[not_interested_or_stop] не пишите"})
    SAID["reply"] = reply("нужно больше данных", ["анекс"])
    director.replies(None, "test", None, TODAY)
    assert sheet.tabs["Черновики"] == [] and "нельзя" in sent[0][2]


def test_a_reply_written_but_not_marked_is_not_handled_twice(answered):
    sheet, tg, sent, calls, marked = answered
    SAID["reply"] = reply("нужно больше данных", ["анекс"])
    sheet.tabs["Сводки"][0]["ответ директора"] = "22.10 нужно больше данных · письмо m1"
    director.replies(None, "test", None, TODAY)
    assert marked == ["m1"] and sheet.tabs["Черновики"] == [] and sent == [] and "DirectorReply" not in calls


def test_rejection_closes_only_this_listing(answered):
    sheet, tg, sent, calls, marked = answered
    other = {**ST, "id": "S501", "ссылка": "https://bazar.example/2", "статус переписки": "ждём ответа"}
    sheet.add("Станции", other)
    SAID["reply"] = reply("отклонил объект")
    director.replies(None, "test", None, TODAY)
    st = sheet.tabs["Станции"][0]
    [refusal] = sheet.tabs["Отказы"]
    assert st["статус переписки"] == "отказ" and refusal["контакт"] == ST["ссылка"]
    assert brief.inbox.blocked(st, [], sheet.tabs["Отказы"]) and not brief.inbox.blocked(other, [], sheet.tabs["Отказы"])
    assert "закрыт" in sent[0][2] and any("🛑" in t for t, _ in tg)


def test_documents_that_arrive_later_go_to_the_director_in_his_thread(answered):
    sheet, tg, sent, calls, marked = answered
    SAID["reply"] = reply("нужно больше данных", ["анекс"])
    director.replies(None, "test", None, TODAY)
    assert director.deliver(None, "test", TODAY) == "директору досылать нечего"   # nothing new yet: no letter
    register(sheet, [doc(documents.ANNEX, "Анекс 1", "111-1", refers_to="111")])
    sheet.add("Диалоги", {"id станции": "S500", "направление": "in", "дата": "2026-10-24", "канал": "email",
                          "краткое содержание": "[info_provided] Прислал анекс"})
    assert director.deliver(None, "test", TODAY) == "S500: дослано"
    _, _, body, files, thread, reply_to = sent[-1]
    assert files == ["Анекс 1.pdf"] and thread == "t-dir" and reply_to == "<m1@x>" and "24.10: Прислал анекс" in body
    n = len(sent)
    assert director.deliver(None, "test", TODAY) == "директору досылать нечего" and len(sent) == n
    assert director.deliver(None, "test", date(2026, 12, 1)) == "директору досылать нечего"   # closed after 30 days
    assert sheet.tabs["Сводки"][0]["ждёт директор"] == ""


def letter_in(thread, text, msg_id, subject="Вопрос"):
    m = letter(text, msg_id)
    m["threadId"] = thread
    m["payload"]["headers"].append({"name": "Subject", "value": subject})
    return m


def test_a_new_letter_about_a_briefed_object_is_handled_in_its_own_thread(answered):
    sheet, tg, sent, calls, marked = answered
    marked.append("m1")                                   # the reply in the brief's thread is already handled
    SAID["fresh"].append(letter_in("t-new", "По S500: запросите платёжки.", "m2", "S500 платёжки"))
    SAID["reply"] = reply("нужно больше данных", ["платёжные поручения"])
    director.replies(None, "test", None, TODAY)
    row = sheet.tabs["Сводки"][0]
    assert row["тред Gmail"] == "t-dir,t-new" and "письмо m2" in row["ответ директора"]
    assert sent[-1][4] == "t-new" and sent[-1][1] == "Re: S500 платёжки" and sent[-1][5] == "<m2@x>"
    assert json.loads(row["ждёт директор"])["thread"] == "t-new" and "m2" in marked


def test_a_personal_letter_is_only_marked_seen(answered):
    sheet, tg, sent, calls, marked = answered
    marked.append("m1")
    SAID["fresh"].append(letter_in("t-home", "Как дела, когда приедешь?", "m3", "Привет"))
    SAID["reply"] = reply("без решения", station="")
    n = len(tg)
    director.replies(None, "test", None, TODAY)
    assert marked[-1] == "seen:m3" and len(tg) == n and sent == []
    assert "письмо m3" not in sheet.tabs["Сводки"][0]["ответ директора"]


def test_telegram_cards_are_valid_markup_with_escaped_values():
    """Every value from a seller, a listing or a model is escaped: a «<» in it never breaks a card."""
    import re
    body = "S500 — ФЭС <1 MW> & Co\n\nОбъявление: https://x.example/?a=1&b=2\n\nПроект <b>.\n\nРиски:\n— срок < 60 дн."
    card = brief.brief_html(body)
    assert "<b>S500 — ФЭС &lt;1 MW&gt; &amp; Co</b>" in card and "<b>Риски:</b>" in card
    assert '<a href="https://x.example/?a=1&amp;b=2">Объявление</a>' in card and "&lt;b&gt;" in card
    tags = re.findall(r"</?(\w+)", card)
    assert set(tags) <= {"b", "a", "i", "code", "blockquote"} and tags.count("b") % 2 == 0
