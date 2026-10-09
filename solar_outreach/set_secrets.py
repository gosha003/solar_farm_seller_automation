"""Ключи Anthropic и Telegram в .env: ввод скрыт, в историю shell не попадает, каждый ключ проверяется.

    python -m solar_outreach.set_secrets

Пустой ввод = пропустить шаг. Повторный запуск = ротация ключа.
"""
import getpass
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

import anthropic

from solar_outreach.config import update_env


def telegram(token: str, method: str, **params) -> dict | list:
    url = f"https://api.telegram.org/bot{token}/{method}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=15) as resp:
        return json.load(resp)["result"]


def set_anthropic() -> None:
    key = getpass.getpass("Anthropic API key (ввод скрыт, Enter = пропустить): ").strip()
    if not key:
        return
    # Console выдаёт sk-ant-api03-... (старые) и sk-ant-usr-... (новые). OAuth (sk-ant-oat) и admin-ключи
    # для Messages API не годятся; мусор от вставки (\x16 = Ctrl+V в старой консоли) ловим до сети.
    if (not key.startswith("sk-ant-") or key.startswith(("sk-ant-oat", "sk-ant-admin"))
            or not (key.isascii() and key.isprintable())):
        sys.exit(f"Это не API-ключ: начало {key[:10]!r}, длина {len(key)}. "
                 "Нужен ключ из Console → API Keys → Create Key.")
    try:
        anthropic.Anthropic(api_key=key).models.list(limit=1)  # бесплатный запрос, только проверка ключа
    except anthropic.AuthenticationError:
        sys.exit("Anthropic отверг ключ (401): отозван или скопирован не полностью.")
    except anthropic.BadRequestError as e:
        # sk-ant-usr- без workspace требует заголовок anthropic-workspace-id; проще пересоздать ключ с workspace.
        sys.exit(f"Anthropic ответил 400: {e.message}\n"
                 "Обычно это ключ без workspace: пересоздай его в Console, выбрав Workspace = Default.")
    except anthropic.APIStatusError as e:
        sys.exit(f"Anthropic ответил {e.status_code}: {e.message}")
    except anthropic.APIConnectionError:
        sys.exit("Нет связи с api.anthropic.com: сеть, прокси или антивирус.")
    update_env({"ANTHROPIC_API_KEY": key})
    print("Anthropic: OK")


def set_telegram() -> None:
    token = getpass.getpass("Telegram bot token (ввод скрыт, Enter = пропустить): ").strip()
    if not token:
        return
    try:
        bot = telegram(token, "getMe")
    except urllib.error.HTTPError:
        sys.exit("Telegram отверг токен. Проверь и повтори.")
    input(f"Отправь /start боту @{bot['username']} в Telegram, затем нажми Enter... ")
    chats = [u["message"]["chat"] for u in telegram(token, "getUpdates") if "message" in u]
    if not chats:
        sys.exit("Бот не получил сообщений. Отправь /start и повтори.")
    chat = chats[-1]
    who = chat.get("username") or chat.get("first_name", "")
    # Имя бота можно найти поиском, написать ему мог кто угодно: подтверждаем, что чат твой.
    if input(f"Чат: {who} (id {chat['id']}). Это ты? [y/N] ").strip().lower() != "y":
        sys.exit("Отменено.")
    telegram(token, "sendMessage", chat_id=chat["id"], text="Solar BG: бот подключён.")
    from solar_outreach.notify import COMMANDS
    telegram(token, "setMyCommands", commands=json.dumps([{"command": c, "description": d} for c, d in COMMANDS.items()]))
    update_env({"TELEGRAM_BOT_TOKEN": token, "TELEGRAM_CHAT_ID": str(chat["id"])})
    print("Telegram: OK, бот прислал тебе сообщение")


if __name__ == "__main__":
    set_anthropic()
    set_telegram()
