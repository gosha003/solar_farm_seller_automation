"""The owner's Telegram bot. No webhooks: getUpdates on every loop run, and between runs
the daemon on the VM waits for updates with long polling (wait), so it answers within seconds.
"""
import os

import requests

API = "https://api.telegram.org/bot{token}/{method}"
LIMIT = 4000  # Telegram allows 4096 characters per message


class TelegramError(Exception):
    pass


def call(method: str, **params) -> dict | list:
    """The token is part of the URL, so requests exceptions (URL in the text) never escape: only method and description."""
    try:
        r = requests.post(API.format(token=os.environ["TELEGRAM_BOT_TOKEN"], method=method), json=params,
                          timeout=30 + params.get("timeout", 0))  # long polling holds the connection for `timeout` seconds
        data = r.json()
    except (requests.RequestException, ValueError) as e:
        raise TelegramError(f"{method}: {type(e).__name__}") from None
    if not data.get("ok"):
        raise TelegramError(f"{method}: {r.status_code} {data.get('description', '')}")
    return data["result"]


def send(text: str, buttons: list[list[tuple[str, str]]] | None = None) -> int:
    """Plain text without parse_mode: sellers write anything and Markdown would break. Returns message_id."""
    params = {"chat_id": os.environ["TELEGRAM_CHAT_ID"], "text": text[:LIMIT], "disable_web_page_preview": True}
    if buttons:
        params["reply_markup"] = {"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in row] for row in buttons]}
    return call("sendMessage", **params)["message_id"]


def try_send(text: str) -> None:
    """A notice whose loss is harmless (📤 sent, ⛔ skipped): a Telegram blip must not abort a pass after letters went out."""
    try:
        send(text)
    except Exception:  # Telegram down or not configured on this machine
        pass


def urgent(text: str) -> None:
    """Urgent: Telegram right away; if Telegram is down, an email to the owner prefixed [SOLAR]."""
    try:
        send(text)
    except TelegramError:
        email_owner(text.split("\n", 1)[0][:80], text)


def email_owner(subject: str, text: str) -> None:
    from solar_outreach import gmail_client
    from solar_outreach.config import load_config
    cfg = load_config()
    gmail_client.send(gmail_client.service(), cfg["owner"]["email"], f"{cfg['notify']['email_prefix']} {subject}", text)


def updates(offset: int) -> list[dict]:
    """Only updates from the owner's chat; everything else is dropped silently (anyone can find the bot and write to it)."""
    owner = str(os.environ["TELEGRAM_CHAT_ID"])
    out = []
    for u in call("getUpdates", offset=offset, timeout=0, allowed_updates=["message", "callback_query"]):
        chat = (u.get("message") or (u.get("callback_query") or {}).get("message") or {}).get("chat", {})
        sender = (u.get("callback_query") or u.get("message") or {}).get("from", {})
        u["_owner"] = str(chat.get("id")) == owner and str(sender.get("id")) == owner
        out.append(u)
    return out


def wait(offset: int, seconds: int) -> None:
    """Long polling: return as soon as the bot has an update (or after `seconds`). Updates are not acknowledged:
    the next loop run picks them up with the same offset."""
    call("getUpdates", offset=offset, timeout=seconds, limit=1, allowed_updates=["message", "callback_query"])


def answer_callback(callback_id: str, text: str = "") -> None:
    """Telegram accepts an answer to a button tap for only a few seconds; with polling every N minutes it is already "too old".
    It is cosmetic (removes the spinner on the button), so the error is ignored."""
    try:
        call("answerCallbackQuery", callback_query_id=callback_id, text=text)
    except TelegramError:
        pass


COMMANDS = {"stats": "статистика", "status": "карточка станции: /status S019", "queue": "ручная очередь",
            "digest": "дайджест сейчас", "pause": "остановить отправку продавцам", "resume": "возобновить отправку",
            "auto": "/auto on или /auto off", "blacklist": "больше не писать: /blacklist email",
            "sent": "я сам отправил: /sent S020", "note": "ответ продавца из WhatsApp: /note S020 текст"}


def set_commands() -> None:
    """The "/" menu in Telegram (setMyCommands): one-off, called from set_secrets."""
    call("setMyCommands", commands=[{"command": c, "description": d} for c, d in COMMANDS.items()])


def draft_buttons(draft_id: str) -> list[list[tuple[str, str]]]:
    return [[("✅ Одобрить", f"a:{draft_id}"), ("✏️ Править", f"e:{draft_id}"), ("⏭ Пропустить", f"s:{draft_id}")]]
