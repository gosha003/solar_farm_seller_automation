"""Documents from other channels: a seller's Google Drive link (imported by the hourly pass) and files the owner
forwards to the bot with a station id. Made-up ids and files only."""
import hashlib
from datetime import date

import pytest

from solar_outreach import approval, brief, documents, drive, gmail_client, notify

FOLDER = "1AbCdEfGhIjKlMnOp"
TODAY = date(2026, 10, 22)


def test_only_google_drive_links_are_taken():
    text = (f"Папка: https://drive.google.com/drive/folders/{FOLDER}?usp=sharing и файл "
            '<a href="https://drive.google.com/file/d/1FileIdFileId01/view">тук</a>, '
            "https://drive.google.com/open?id=1FileIdFileId02 , https://evil.example/drive/folders/1NotOursNotOurs")
    assert documents.drive_links(text) == [("folder", FOLDER), ("file", "1FileIdFileId01"), ("file", "1FileIdFileId02")]


def test_a_shared_folder_is_listed_from_googles_embed_page(monkeypatch):
    pages = {FOLDER: '<a href="https://drive.google.com/file/d/1FileA/view?usp=drive_web"><div class="flip-entry-title">'
                     'Разрешение 7 &amp; анекс.pdf</div></a><a href="https://drive.google.com/drive/folders/1Sub"><div '
                     'class="flip-entry-title">Проект</div></a>',
             "1Sub": '<a href="https://drive.google.com/file/d/1FileB/view"><div class="flip-entry-title">Скица.pdf</div></a>'}

    class Page:
        def __init__(self, fid):
            self.text = pages[fid]

        def raise_for_status(self):
            pass
    monkeypatch.setattr(documents.requests, "get", lambda url, **kw: Page(url.rsplit("=", 1)[1]))
    assert documents.folder_items(FOLDER) == [("1FileA", "Разрешение 7 & анекс.pdf"), ("1FileB", "Скица.pdf")]


@pytest.fixture
def drive_fake(monkeypatch):
    put, tagged, fetched = [], [], []
    monkeypatch.setattr(drive, "put", lambda svc, name, data, st, source="": put.append((name, st, source)) or f"new-{name}")
    monkeypatch.setattr(drive, "tag", lambda svc, fid, source: tagged.append((fid, source)))
    files = {"1Done": ("Готово.pdf", b"old"), "1Same": ("Копия.pdf", b"same bytes"), "1New": ("Новый.pdf", b"new"),
             "1Bad": ("Закрыт.pdf", None)}

    def fetch(fid):
        fetched.append(fid)
        name, data = files[fid]
        if data is None:
            raise ValueError("файл не открыт по ссылке")
        return name, data
    monkeypatch.setattr(documents, "fetch", fetch)
    monkeypatch.setattr(documents, "folder_items", lambda fid: [("1Done", "Готово.pdf"), ("1Same", "Копия.pdf"),
                                                               ("1New", "Новый.pdf"), ("1Dwg", "Чертёж.dwg"), ("1Bad", "Закрыт.pdf")])
    return put, tagged, fetched


def test_import_skips_what_is_there_and_never_downloads_it_again(drive_fake):
    put, tagged, fetched = drive_fake
    existing = [{"id": "a", "appProperties": {"source": "1Done"}},
                {"id": "b", "md5Checksum": hashlib.md5(b"same bytes").hexdigest()}]      # the same file uploaded by hand
    added, problems = documents.import_drive(None, "S500", [("folder", FOLDER)], existing)
    assert added == ["Новый.pdf"] and put == [("Новый.pdf", "S500", "1New")]
    assert tagged == [("b", "1Same")] and fetched == ["1Same", "1New", "1Bad"]   # imported and .dwg never downloaded
    assert problems == ["Закрыт.pdf: файл не открыт по ссылке"]
    fetched.clear()
    existing[1]["appProperties"] = {"source": "1Same"}
    documents.import_drive(None, "S500", [("folder", FOLDER)], existing)
    assert fetched == ["1Bad"]                                                   # the next hour: only the failed one


def test_a_file_forwarded_to_the_bot_lands_in_the_station_folder(monkeypatch, drive_fake):
    put, _, _ = drive_fake
    monkeypatch.setattr(drive, "service", lambda: None)
    monkeypatch.setattr(drive, "station_files", lambda svc: {})
    monkeypatch.setattr(notify, "download", lambda fid: b"%PDF scan")
    stations = {"S500": {"id": "S500"}}
    doc = {"document": {"file_id": "f1", "file_unique_id": "u1", "file_name": "Анекс.pdf"}}
    reply = documents.from_telegram({**doc, "caption": "s500 от продавца"}, stations, TODAY)
    assert put == [("Анекс.pdf", "S500", "tg:u1")] and "Добавлено" in reply
    assert "Куда положить" in documents.from_telegram(doc, stations, TODAY) and len(put) == 1   # no station id: asked
    photo = {"photo": [{"file_id": "p1", "file_unique_id": "ph1aaaa", "file_size": 10},
                       {"file_id": "p2", "file_unique_id": "ph2bbbb", "file_size": 99}], "caption": "S500"}
    documents.from_telegram(photo, stations, TODAY)
    assert put[-1] == ("фото 22.10 ph2bbb.jpg", "S500", "tg:ph2bbbb")
    documents.from_telegram({"text": f"S500 https://drive.google.com/drive/folders/{FOLDER}"}, stations, TODAY)
    assert ("Новый.pdf", "S500", "1New") in put


def test_the_bot_routes_files_and_links_but_not_a_draft_edit(monkeypatch):
    got = []
    monkeypatch.setattr(approval.documents, "from_telegram", lambda msg, st, today: got.append(msg) or "ok")
    monkeypatch.setattr(approval.notify, "send", lambda text, buttons=None: 1)
    link = f"S500 https://drive.google.com/drive/folders/{FOLDER}"
    monkeypatch.setattr(approval.notify, "updates", lambda offset: [
        {"update_id": 1, "_owner": True, "message": {"document": {"file_id": "f"}, "caption": "S500"}},
        {"update_id": 2, "_owner": True, "message": {"text": link}}])
    approval.handle_updates({}, {}, {})
    assert len(got) == 2
    got.clear()
    monkeypatch.setattr(approval.notify, "updates", lambda offset: [{"update_id": 3, "_owner": True, "message": {"text": link}}])
    monkeypatch.setattr(approval.llm, "rewrite", lambda *a: (_ for _ in ()).throw(approval.llm.LLMError("x")))
    approval.handle_updates({"pending_edit": "D001"}, {}, {})                    # an edit in progress keeps the text
    assert got == []


def test_the_hourly_pass_imports_drive_links_from_the_sellers_own_thread(monkeypatch):
    sent = []
    monkeypatch.setattr(brief.notify, "send", lambda text, buttons=None: sent.append(text) or 1)
    monkeypatch.setattr(gmail_client, "service", lambda: None)
    monkeypatch.setattr(gmail_client, "thread_texts", lambda svc, tid: [
        f'<a href="https://drive.google.com/drive/folders/{FOLDER}">папка</a>'] if tid == "t-own" else [])
    imported = []
    monkeypatch.setattr(documents, "import_drive", lambda svc, st, links, existing, seen=None: imported.append((st, links)) or (["A.pdf"], []))
    stations = {"S500": {"id": "S500", "статус переписки": "ведёт владелец", "отбор": "кандидат", "заголовок": "ФЕЦ",
                         "примечание": "https://drive.google.com/file/d/1OurOwnFile0/view"},   # our saved attachment
                "S501": {"id": "S501", "статус переписки": "отказ", "отбор": "кандидат", "заголовок": "x", "примечание": ""}}
    dialogs = [{"id станции": "S500", "направление": "in", "краткое содержание": "прислал ссылку", "тред Gmail": "t-own"},
               {"id станции": "S501", "направление": "in", "краткое содержание": "", "тред Gmail": "t-own"}]
    files = {"S500": [{"id": "1OurOwnFile0"}]}
    assert brief.harvest(stations, dialogs, [], files, None, set()) == 1
    assert imported == [("S500", [("folder", FOLDER)])] and "Документы с Drive продавца" in sent[0]


def test_a_file_that_was_handled_is_never_fetched_again(drive_fake):
    put, tagged, fetched = drive_fake
    seen = set()
    documents.import_drive(None, "S500", [("folder", FOLDER)], [], seen)
    assert {"1Done", "1Same", "1New", "1Dwg", "1Bad"} <= seen       # stored, a duplicate, a drawing, a closed file
    fetched.clear()
    documents.import_drive(None, "S500", [("folder", FOLDER)], [], seen)
    assert fetched == []
