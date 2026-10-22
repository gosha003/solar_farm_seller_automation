import pytest
import requests

from solar_outreach import notify

TOKEN = "123:fake-telegram-token"


class Resp:
    status_code = 400

    def json(self):
        return {"ok": False, "description": "Bad Request: query is too old"}


def test_errors_never_contain_token(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setattr(notify.requests, "post", lambda *a, **k: Resp())
    with pytest.raises(notify.TelegramError) as e:
        notify.call("sendMessage", chat_id=1, text="x")
    assert TOKEN not in str(e.value) and "query is too old" in str(e.value)

    def net_fail(url, **k):
        raise requests.ConnectionError(f"failed {url}")
    monkeypatch.setattr(notify.requests, "post", net_fail)
    with pytest.raises(notify.TelegramError) as e:
        notify.call("getUpdates")
    assert TOKEN not in str(e.value)


def test_stale_callback_answer_is_ignored(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setattr(notify.requests, "post", lambda *a, **k: Resp())
    notify.answer_callback("cb1", "ok")  # doesn't crash


def test_long_messages_split_without_losing_text_or_breaking_markup():
    letter = ("Здравейте, " * 400 + "& <x>\n\n") * 3
    text = notify.join(notify.head("📝", "Черновик", "S020"), notify.quote(letter))
    parts = notify.split(text)
    assert len(parts) > 1 and all(len(p) <= notify.LIMIT for p in parts)
    assert all(p.count("<blockquote") == p.count("</blockquote>") for p in parts)
    assert notify.plain("".join(parts)).count("Здравейте") == letter.count("Здравейте")


def test_markup_telegram_rejects_still_arrives_as_plain_text(monkeypatch):
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    sent = []

    def call(method, **params):
        sent.append(params)
        if params.get("parse_mode"):
            raise notify.TelegramError("sendMessage: 400 Bad Request: can't parse entities")
        return {"message_id": 7}
    monkeypatch.setattr(notify, "call", call)
    assert notify.send(f"{notify.b('S020')} {notify.link('https://x.example/?a=1&b=2', 'тред')}") == 7
    assert sent[-1]["text"] == "S020 тред (https://x.example/?a=1&b=2)" and "parse_mode" not in sent[-1]


def test_values_are_escaped_and_numbers_readable():
    assert notify.b("<x&y>") == "<b>&lt;x&amp;y&gt;</b>" and notify.link("javascript:alert(1)", "x") == "x"
    assert [notify.num(v) for v in ("1160000", "1499,6", "неизвестно")] == ["1 160 000", "1 499,6", "неизвестно"]
