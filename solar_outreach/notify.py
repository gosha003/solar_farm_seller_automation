"""Telegram-бот владельца (раздел 10). Опрос getUpdates при каждом запуске, без вебхуков.
Email-запас ([SOLAR] на owner@example.com) добавляется в Фазе 3 вместе с gmail_client.
"""
import os

import requests

API = "https://api.telegram.org/bot{token}/{method}"
LIMIT = 4000  # у Telegram 4096 символов на сообщение


class TelegramError(Exception):
    pass


def call(method: str, **params) -> dict | list:
    """Токен входит в URL, поэтому исключения requests (с URL в тексте) наружу не выпускаем: только метод и описание."""
    try:
        r = requests.post(API.format(token=os.environ["TELEGRAM_BOT_TOKEN"], method=method), json=params, timeout=30)
        data = r.json()
    except (requests.RequestException, ValueError) as e:
        raise TelegramError(f"{method}: {type(e).__name__}") from None
    if not data.get("ok"):
        raise TelegramError(f"{method}: {r.status_code} {data.get('description', '')}")
    return data["result"]


def send(text: str, buttons: list[list[tuple[str, str]]] | None = None) -> int:
    """Простой текст без parse_mode: продавцы пишут что угодно, Markdown сломался бы. Возвращает message_id."""
    params = {"chat_id": os.environ["TELEGRAM_CHAT_ID"], "text": text[:LIMIT], "disable_web_page_preview": True}
    if buttons:
        params["reply_markup"] = {"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in row] for row in buttons]}
    return call("sendMessage", **params)["message_id"]


def updates(offset: int) -> list[dict]:
    """Только апдейты из чата владельца; остальное молча отбрасываем (бот находим поиском, писать может кто угодно)."""
    owner = str(os.environ["TELEGRAM_CHAT_ID"])
    out = []
    for u in call("getUpdates", offset=offset, timeout=0, allowed_updates=["message", "callback_query"]):
        chat = (u.get("message") or (u.get("callback_query") or {}).get("message") or {}).get("chat", {})
        sender = (u.get("callback_query") or u.get("message") or {}).get("from", {})
        u["_owner"] = str(chat.get("id")) == owner and str(sender.get("id")) == owner
        out.append(u)
    return out


def answer_callback(callback_id: str, text: str = "") -> None:
    """Telegram принимает ответ на нажатие только несколько секунд; при опросе раз в N минут он уже «too old».
    Это косметика (убрать «часики» на кнопке), поэтому ошибку игнорируем."""
    try:
        call("answerCallbackQuery", callback_query_id=callback_id, text=text)
    except TelegramError:
        pass


def draft_buttons(draft_id: str) -> list[list[tuple[str, str]]]:
    return [[("✅ Одобрить", f"a:{draft_id}"), ("✏️ Править", f"e:{draft_id}"), ("⏭ Пропустить", f"s:{draft_id}")]]
