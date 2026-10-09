import base64
from types import SimpleNamespace

from solar_outreach import gmail_client as gmail


def b64(s):
    return base64.urlsafe_b64encode(s.encode()).decode()


def msg(frm="seller@abv.bg", body="Здравейте, централата работи.", labels=("INBOX",), subject="Re: Запитване", **kw):
    return {"id": kw.get("id", "m1"), "threadId": "t1", "labelIds": list(labels),
            "payload": {"mimeType": "multipart/alternative", "headers": [
                {"name": "From", "value": f"Seller <{frm}>"}, {"name": "Subject", "value": subject},
                {"name": "Message-ID", "value": "<abc@mail>"}],
                "parts": [{"mimeType": "text/plain", "body": {"data": b64(body)}},
                          {"mimeType": "application/pdf", "filename": "notarialen_akt.pdf", "body": {}}]}}


def test_text_strips_quote_and_lists_attachments():
    m = msg(body="Да, работи от 2020.\n\nНа 8.10.2026 Иван Петров написа:\n> Здравейте")
    assert gmail.text(m) == "Да, работи от 2020."
    assert gmail.attachments(m) == ["notarialen_akt.pdf"]
    assert gmail.strip_quote("Yes.\n\nOn Wed, Oct 8, 2026 Ivan wrote:\n> hi") == "Yes."


def test_bounce_detection():
    assert gmail.is_bounce(msg(frm="mailer-daemon@googlemail.com"))
    assert gmail.is_bounce(msg(subject="Delivery Status Notification (Failure)"))
    assert not gmail.is_bounce(msg())


class FakeSvc:
    """Минимальный Gmail API: записывает вызовы, тред t1 наш (есть метка L1), t2 чужой."""
    def __init__(self):
        self.calls = []
        threads = {"t1": {"messages": [msg(labels=("SENT", "L1"), frm="owner@example.com", id="m0"), msg()]},
                   "t2": {"messages": [msg(id="x1")]}}
        ex = lambda v: SimpleNamespace(execute=lambda: v)
        self.labels = lambda: SimpleNamespace(list=lambda **k: ex({"labels": [{"name": "SolarBG", "id": "L1"},
                                                                               {"name": "SolarBG/обработано", "id": "L2"}]}))
        self.threads = lambda: SimpleNamespace(
            list=lambda **k: ex({"threads": [{"id": "t1"}, {"id": "t2"}]}),  # t2 «подсунули»: должен отсеяться
            get=lambda userId, id, format: ex(threads[id]))

        def send(userId, body):
            self.calls.append(("send", body))
            return ex({"id": "s1", "threadId": body.get("threadId", "new")})

        def modify(userId, id, body):
            self.calls.append(("modify", id, body))
            return ex({})
        self.messages = lambda: SimpleNamespace(send=send, modify=modify)


def test_incoming_reads_only_our_threads_and_skips_own_messages():
    got = gmail.incoming(FakeSvc())
    assert [m["id"] for m in got] == ["m1"]          # не m0 (наше), не x1 (чужой тред)


def test_dry_run_blocks_sellers_but_allows_owner(monkeypatch):
    monkeypatch.setenv("DRY_RUN", "true")  # явно: .env владельца может содержать false, load_env его не перетрёт
    svc = FakeSvc()
    assert gmail.send(svc, "seller@abv.bg", "s", "b") == {"dry_run": True} and svc.calls == []
    assert gmail.send(svc, "Ivan <owner@example.com>", "[SOLAR] t", "b")["id"] == "s1"
    kinds = [c[0] for c in svc.calls]
    assert kinds == ["send", "modify"] and svc.calls[1][2] == {"addLabelIds": ["L1"]}   # метка SolarBG
    monkeypatch.setenv("DRY_RUN", "false")
    assert gmail.send(svc, "seller@abv.bg", "s", "b")["id"] == "s1"


def test_message_headers_are_plain_and_named():
    from solar_outreach.config import load_config
    m = gmail.build_message("seller@abv.bg", "Тема", "Текст", load_config(), in_reply_to="<abc@mail>")
    raw = m.as_string()
    assert "owner@example.com" in m["From"] and "Иван Петров" in str(m["From"])
    assert m["In-Reply-To"] == "<abc@mail>" and m.get_content_type() == "text/plain"
    assert "List-Unsubscribe" not in raw and "Precedence" not in raw and "<img" not in raw
