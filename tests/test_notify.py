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
