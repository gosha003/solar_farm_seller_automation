import base64
import re
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
    """Minimal Gmail API: records calls; thread t1 is ours (has label L1), t2 isn't."""
    def __init__(self):
        self.calls = []
        threads = {"t1": {"messages": [msg(labels=("SENT", "L1"), frm="owner@example.com", id="m0"), msg()]},
                   "t2": {"messages": [msg(id="x1")]}}
        ex = lambda v: SimpleNamespace(execute=lambda **k: v)
        self.labels = lambda: SimpleNamespace(list=lambda **k: ex({"labels": [{"name": "SolarBG", "id": "L1"},
                                                                               {"name": "SolarBG/обработано", "id": "L2"}]}))
        self.threads = lambda: SimpleNamespace(
            list=lambda **k: ex({"threads": [{"id": "t1"}, {"id": "t2"}]}),  # t2 was slipped in: must be filtered out
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
    assert [m["id"] for m in got] == ["m1"]          # not m0 (ours), not x1 (someone else's thread)


def test_adopt_labels_mail_from_known_sellers_by_address_or_corporate_domain():
    assert gmail.contact_query(["contact@agency.example", "Ivan@Gmail.com", "owner@example.com", "", "-"],
                               "owner@example.com") == "agency.example OR ivan@gmail.com"
    svc, queries = FakeSvc(), []
    ex = lambda v: SimpleNamespace(execute=lambda **k: v)
    modify = svc.messages().modify
    svc.messages = lambda: SimpleNamespace(list=lambda **k: queries.append(k["q"]) or ex({"messages": [{"id": "z1"}]}),
                                           modify=modify)
    assert gmail.adopt(svc, ["contact@agency.example"]) == 1
    assert queries[0].startswith("from:(agency.example) -label:") and "-from:me" in queries[0]
    assert svc.calls == [("modify", "z1", {"addLabelIds": ["L1"]})]
    assert gmail.adopt(FakeSvc(), []) == 0


def test_dry_run_blocks_sellers_but_allows_owner(monkeypatch):
    monkeypatch.setenv("DRY_RUN", "true")  # explicit: the owner's .env may contain false and load_env won't override it
    svc = FakeSvc()
    assert gmail.send(svc, "seller@abv.bg", "s", "b") == {"dry_run": True} and svc.calls == []
    assert gmail.send(svc, "Ivan <owner@example.com>", "[SOLAR] t", "b")["id"] == "s1"
    kinds = [c[0] for c in svc.calls]
    assert kinds == ["send", "modify"] and svc.calls[1][2] == {"addLabelIds": ["L1"]}   # SolarBG label
    monkeypatch.setenv("DRY_RUN", "false")
    assert gmail.send(svc, "seller@abv.bg", "s", "b")["id"] == "s1"


def test_message_headers_are_plain_and_named():
    from solar_outreach.config import load_config
    m = gmail.build_message("seller@abv.bg", "Тема", "Текст", load_config(), in_reply_to="<abc@mail>")
    raw = m.as_string()
    assert "owner@example.com" in m["From"] and "Иван Петров" in str(m["From"])
    assert m["In-Reply-To"] == "<abc@mail>" and m.get_content_type() == "text/plain"
    assert "List-Unsubscribe" not in raw and "Precedence" not in raw and "<img" not in raw


def test_outlook_quotes_and_charsets():
    bg = "Да, работи.\n\nОт: Иван Петров <owner@example.com>\nИзпратено: 1 октомври 2026 г.\nЗдравейте, видях обявата"
    assert gmail.strip_quote(bg) == "Да, работи."
    assert gmail.strip_quote("Да.\n-----Исходное сообщение-----\nЗдравствуйте") == "Да."
    assert gmail.strip_quote("От: 2019 работи, от собственика.") == "От: 2019 работи, от собственика."
    part = {"headers": [{"name": "Content-Type", "value": 'text/plain; charset="koi8-r"'}]}
    assert gmail.decode("Работает".encode("koi8-r"), part) == "Работает"
    assert gmail.decode(b"ok", {"headers": [{"name": "Content-Type", "value": "text/plain; charset=x-unknown"}]}) == "ok"


def test_delay_and_public_domains():
    assert gmail.is_delay(msg(frm="mailer-daemon@googlemail.com", subject="Delivery Status Notification (Delay)"))
    assert not gmail.is_delay(msg(frm="mailer-daemon@googlemail.com", subject="Delivery Status Notification (Failure)"))
    assert all(gmail.is_public(d) for d in ("gmx.de", "yahoo.co.uk", "bk.ru", "hotmail.bg", "abv.bg"))
    assert not gmail.is_public("agency-solar.example") and not gmail.is_public("me-solar.example")
    assert gmail.contact_query(["s@gmx.de"], "owner@example.com") == "s@gmx.de"


def test_label_failure_after_send_still_returns_the_letter(monkeypatch):
    monkeypatch.setenv("DRY_RUN", "false")
    svc = FakeSvc()
    send = svc.messages().send

    def modify(userId, id, body):
        raise RuntimeError("503")
    svc.messages = lambda: SimpleNamespace(send=send, modify=modify)
    assert gmail.send(svc, "seller@abv.bg", "s", "b") == {"id": "s1", "threadId": "new"}


def test_followup_replies_to_the_last_message_of_its_thread(monkeypatch):
    monkeypatch.setenv("DRY_RUN", "false")
    svc = FakeSvc()
    gmail.send(svc, "seller@abv.bg", "Re: Запитване", "b", thread_id="t1")
    raw = base64.urlsafe_b64decode(svc.calls[0][1]["raw"]).decode()
    assert "In-Reply-To: <abc@mail>" in raw and svc.calls[0][1]["threadId"] == "t1"


def test_inline_answers_between_our_quoted_questions_are_kept():
    body = ("Здравейте,\nотговорите са по-долу.\n\nOn Wed, Oct 8, 2026 Ivan wrote:\n> 1. Централата действаща ли е?\n"
            "Да, от 2019 г.\n> 2. Каква е мощността?\n1,5 MW\n> 3. Цена?\n")
    assert gmail.strip_quote(body) == "Здравейте,\nотговорите са по-долу.\nДа, от 2019 г.\n1,5 MW"


def test_failure_before_the_send_is_not_sent(monkeypatch):
    import pytest
    monkeypatch.setenv("DRY_RUN", "false")
    svc = FakeSvc()

    def boom(**k):
        raise ConnectionResetError("reset")
    svc.labels = lambda: SimpleNamespace(list=lambda **k: SimpleNamespace(execute=boom))
    with pytest.raises(gmail.NotSent):
        gmail.send(svc, "seller@abv.bg", "s", "b")
    assert svc.calls == []


def test_the_directors_letter_in_a_seller_thread_is_not_a_sellers_reply():
    svc = FakeSvc()
    assert gmail.unread_in(svc, "t1") and not gmail.unread_in(svc, "t2")       # t2 isn't ours
    ex = lambda v: SimpleNamespace(execute=lambda **k: v)
    t1 = {"messages": [msg(labels=("SENT", "L1"), frm="owner@example.com", id="m0"),
                       msg(labels=("L1", "L2"), id="m1"),                                # read by inbox already
                       msg(frm="director@example.com", labels=("L1",), id="m2")]}       # the owner forwarded it to him
    svc.threads = lambda: SimpleNamespace(list=lambda **k: ex({"threads": [{"id": "t1"}]}),
                                          get=lambda userId, id, format: ex(t1))
    assert gmail.incoming(svc) == [] and not gmail.unread_in(svc, "t1")


def test_his_letters_older_than_the_brief_are_not_replies_to_it():
    ex = lambda v: SimpleNamespace(execute=lambda **k: v)
    labels = [{"name": "SolarBG/директор", "id": "D1"}, {"name": "SolarBG/директор/обработано", "id": "D2"}]

    def at(mid, frm, when, labels_=()):
        return {**msg(frm=frm, labels=labels_, id=mid), "internalDate": str(when)}
    t = {"messages": [at("old", "director@example.com", 100),                    # a personal letter from before
                      at("brief", "owner@example.com", 200, ("SENT", "D1")),
                      at("new", "director@example.com", 300)]}                   # his reply has no label

    def listing(userId, labelIds, q="", maxResults=100):  # Gmail: labelIds and q must match one and the same message
        frm = re.search(r"from:\((.+?)\)", q)
        hit = any(set(labelIds) <= set(m["labelIds"]) and (not frm or frm[1] in m["payload"]["headers"][0]["value"])
                  for m in t["messages"])
        return ex({"threads": [{"id": "t"}] if hit else []})
    svc = SimpleNamespace(labels=lambda: SimpleNamespace(list=lambda **k: ex({"labels": labels})),
                          threads=lambda: SimpleNamespace(list=listing, get=lambda userId, id, format: ex(t)))
    assert [m["id"] for m in gmail.replies_from(svc, "director@example.com", "SolarBG/директор")] == ["new"]


def test_a_trashed_conversation_is_not_unread_mail():
    svc = FakeSvc()
    ex = lambda v: SimpleNamespace(execute=lambda **k: v)
    t1 = {"messages": [msg(labels=("SENT", "L1"), frm="owner@example.com", id="m0"), msg(labels=("L1", "TRASH"), id="m1")]}
    svc.threads = lambda: SimpleNamespace(list=lambda **k: ex({"threads": []}), get=lambda userId, id, format: ex(t1))
    assert not gmail.unread_in(svc, "t1")                            # inbox never lists it, so nothing would read it
