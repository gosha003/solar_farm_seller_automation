"""Edge cases found by reviewing the exactly-once and paused-dialog fixes (made-up data only)."""
import base64
import json

from solar_outreach import approval, gmail_client as gmail, inbox, llm, owner, search, site_queue
from solar_outreach.config import load_config
from solar_outreach.sources import Listing, imot

CFG = load_config()


def test_late_tap_on_an_older_reply_does_not_overtake_the_newer_one(monkeypatch):
    out = []
    monkeypatch.setattr(approval.notify, "send", lambda text, buttons=None: out.append(text))
    monkeypatch.setattr(approval.notify, "answer_callback", lambda *a: None)
    monkeypatch.setattr(approval.notify, "updates", lambda offset: [
        {"update_id": 5, "_owner": True, "callback_query": {"id": "c5", "data": "a:D002"}}])
    reply = {"id станции": "S001", "тип": "ответ", "канал": "телефон (ручная)", "кому": "+359888000111", "текст": "t"}
    by_id = {"D002": {**reply, "id": "D002", "статус": "на одобрении", "_row": 3},
             "D003": {**reply, "id": "D003", "статус": "одобрено", "_row": 4}}
    st = {"id": "S001", "статус переписки": "черновик ответа", "ссылка": "u"}
    changed, queue, approved = approval.handle_updates({"tg_offset": "5"}, {"S001": st}, by_id)
    assert queue == [] and approved == 0 and changed["D002"]["статус"] == "заменено новым ответом"


def test_alo_reply_whose_draft_failed_is_read_again(monkeypatch):
    c = owner.Ctx(None, None, CFG, {"S020": {"id": "S020", "канал": "сайт (ручная)", "ссылка": "u", "статус переписки": "ответ на alo",
                                            "заголовок": "x"}}, {}, [], {}, {})
    cl = llm.Classification(category="question_we_can_answer", language="bg", summary_ru="?", seller_questions=["Кои сте?"],
                            new_contact="", confidence="high", facts=[])
    monkeypatch.setattr(owner.llm, "classify", lambda text, st: cl)

    def down(*a, **k):
        raise llm.LLMUnavailable("API: 529")
    monkeypatch.setattr(owner.inbox, "manual_reply_draft", down)
    assert "Пришли ещё раз" in owner.note(c, "S020", "Кои сте?", inbox.date.today()) and c.new_dialogs == []


def test_a_sent_row_whose_text_was_edited_still_closes_its_draft(sheet):
    sheet.add("Станции", {"id": "S004", "статус переписки": "одобрено, ждёт отправки", "ссылка": "https://www.alo.bg/x-1",
                          "канал": "сайт (ручная)"})
    sheet.add("Черновики", {"id": "D001", "id станции": "S004", "тип": "первое", "канал": "сайт (ручная)", "статус": "одобрено",
                            "текст": "оригинал"})
    sheet.add("Ручная очередь", {"id станции": "S004", "канал": "сайт (ручная)", "ссылка на объявление": "https://www.alo.bg/x-1",
                                 "текст": "оригинал, поправен от собственика", "статус": "отправить вручную"})
    site_queue.sent(None, None, "S004", "оригинал, поправен от собственика")
    assert sheet.tabs["Черновики"][0]["статус"] == "отправлено"            # follow-ups start from it


def mail(mid, body):
    return {"id": mid, "threadId": "t1", "payload": {"mimeType": "text/plain", "headers": [
        {"name": "From", "value": "seller@example.org"}, {"name": "Subject", "value": "Re: x"}, {"name": "Message-ID", "value": f"<{mid}@x>"}],
        "body": {"data": base64.urlsafe_b64encode(body.encode()).decode()}}}


def test_merged_reply_remembers_both_messages_and_a_redirect_is_added_once(sheet, monkeypatch):
    from tests.test_inbox_process import STATION, classified
    monkeypatch.setenv("DRY_RUN", "false")
    msgs, done = [mail("m1", "Работи от 2020."), mail("m2", "Панели Longi.")], []
    answers = {"Работи от 2020.": classified("info_provided", [("5 год ввода", "2020")]),
               "Панели Longi.": classified("info_provided", [("8 панели", "Longi")]),
               "Пишете на брокера: 0888 000 111": classified("broker_redirect", new_contact="0888 000 111")}
    monkeypatch.setattr(inbox.gmail, "adopt", lambda svc, emails: 0)
    monkeypatch.setattr(inbox.gmail, "incoming", lambda svc: list(msgs))
    monkeypatch.setattr(inbox.gmail, "mark_done", lambda svc, m: done.append(m["id"]))
    monkeypatch.setattr(inbox.notify, "send", lambda text, buttons=None: 1)
    monkeypatch.setattr(inbox.notify, "urgent", lambda text: None)
    monkeypatch.setattr(inbox.llm, "classify", lambda body, st: answers[body])
    sheet.add("Станции", STATION)
    sheet.add("Диалоги", {"id станции": "S001", "направление": "out", "тред Gmail": "t1"})
    inbox.process(None, None, None, auto=True)
    assert sheet.tabs["Черновики"][0]["in-reply-to"].split() == ["<m1@x>", "<m2@x>"]
    msgs[:] = [mail("m1", "Работи от 2020.")]                                # mark_done had failed for m1
    inbox.process(None, None, None, auto=True)
    assert len(sheet.tabs["Черновики"]) == 1
    msgs[:] = [mail("r1", "Пишете на брокера: 0888 000 111")]
    inbox.process(None, None, None)
    inbox.process(None, None, None)                                          # read again after a failed mark_done
    assert [s["телефон"] for s in sheet.tabs["Станции"][1:]] == ["+359888000111"]


def test_permit_project_naming_its_grid_company_still_gets_the_project_note():
    project = search.to_row(Listing("bazar.bg", "u1", "Продавам ФЕЦ 2 MW",
                                    "Проект с издадено разрешение за строеж, договор за присъединяване с ЕВН."), CFG)
    running = search.to_row(Listing("bazar.bg", "u2", "Продавам ФЕЦ 2 MW",
                                    "Годишно производство 2 800 MWh. Документи: разрешение за строеж, договор с ЕВН."), CFG)
    assert "разрешение на строительство в тексте" in project["примечание"]
    assert "разрешение на строительство в тексте" not in running["примечание"]


def test_imot_project_cards_in_future_tense_stay_projects():
    from solar_outreach.sources import is_station_sale
    for text in ("Проект за фотоволтаичен парк 2 MW, очаквано въвеждане в експлоатация 2027 г.",
                 "Проект ФЕЦ 2 MW, ще произвежда 2 800 MWh"):
        html = f'<div class="item"><a href="//www.imot.bg/obiava-1">x</a> СОЛАРЕН ПАРК {text}</div>'
        [title] = imot.card_links(html)[1].values()
        assert title.endswith("проект") and not is_station_sale(title), text


def test_html_only_reply_is_cut_at_the_quote():
    html = ('<div>Да, работи.</div><div class="gmail_quote">On Wed, Oct 8, 2026 Ivan &lt;<a href="mailto:owner@example.com">'
            'owner@example.com</a>&gt; wrote:<blockquote>1. Каква е исканата цена?</blockquote></div>')
    m = {"payload": {"mimeType": "text/html", "body": {"data": base64.urlsafe_b64encode(html.encode()).decode()}}}
    assert "исканата цена" not in gmail.text(m) and "Да, работи." in gmail.text(m)


def test_import_keeps_a_land_title_whose_permit_is_in_the_text(tmp_path):
    f = tmp_path / "found.json"
    f.write_text(json.dumps([
        {"url": "https://a.example/1", "title": "Земеделска земя 25 декара", "text": "Разрешение за строеж на ФЕЦ 2 MW."},
        {"url": "https://a.example/2", "title": "Земеделска земя 25 декара", "text": "Подходяща за ФЕЦ 2 MW."}],
        ensure_ascii=False), encoding="utf-8")
    assert [l.url for l in search.imported(str(f), set())] == ["https://a.example/1"]
