from types import SimpleNamespace

from solar_outreach import drive


class FakeFiles:
    def __init__(self):
        self.created = []
        ex = lambda v: SimpleNamespace(execute=lambda: v)
        self.list = lambda **k: ex({"files": []})

        def create(body, fields, media_body=None):
            self.created.append(body)
            return ex({"id": f"F{len(self.created)}", "webViewLink": f"https://drive/{body['name']}"})
        self.create = create


def test_only_safe_types_saved_into_station_folder(monkeypatch):
    msg = {"id": "m1", "payload": {"parts": [
        {"filename": "notarialen_akt.PDF", "mimeType": "application/pdf", "body": {"attachmentId": "a1"}},
        {"filename": "invoice.exe", "mimeType": "application/pdf", "body": {"attachmentId": "a2"}},
        {"filename": "snimka.jpg", "mimeType": "image/jpeg", "body": {"data": "eHl6"}}]}}
    monkeypatch.setattr(drive.gmail, "attachment_bytes", lambda svc, m, p: b"%PDF")
    files = FakeFiles()
    out = drive.save(files, None, msg, "S019")
    assert out == ["notarialen_akt.PDF: https://drive/notarialen_akt.PDF", "invoice.exe (не сохранено: тип .exe)",
                   "snimka.jpg: https://drive/snimka.jpg"]
    assert [b["name"] for b in files.created] == ["Solar BG вложения", "S019", "notarialen_akt.PDF", "snimka.jpg"]
    assert files.created[3]["parents"] == ["F2"]                # папка станции создаётся один раз
