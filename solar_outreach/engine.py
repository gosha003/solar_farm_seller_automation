"""Главный цикл (раздел 9). Каждый проход идемпотентный, под блокировкой.

    python -m solar_outreach.engine            # один проход (cron, отладка)
    python -m solar_outreach.engine --daemon   # VM: постоянно, ответ в Telegram за секунды (systemd: solar-bot)

Проход: входящие (шаги 3-4) -> кнопки и команды Telegram, черновики и дожимы, отправка в лимитах, дайджест (2, 5, 7-9).
Поиск (шаг 6) отдельным процессом `python -m solar_outreach.search` по таймеру: он долгий (паузы к сайтам).
"""
import os
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

import requests

from solar_outreach import approval, budget, gmail_client, inbox, notify, sheets
from solar_outreach.config import Busy, load_config, single_run

ALERT_AFTER = 3        # разовые таймауты Telegram/Google не стоят уведомления, серия из трёх стоит
INBOX_EVERY = 60       # секунд между проверками почты; Telegram между ними ждём long polling'ом
PING_EVERY = 300       # раз в 5 мин: потолок расходов Google Cloud и HEALTHCHECK_URL (необязательно, внешний пульс)


def run(test_n: int = 0, with_inbox: bool = True) -> int:
    cfg = load_config()
    if with_inbox:
        api, sid = sheets.service(), os.environ["SPREADSHEET_ID"]
        sheets.ensure_tabs(api, sid)
        mode = sheets.kv(api, sid, "Настройки").get("approval_mode", {}).get("значение") or cfg["approval_mode"]
        stats = inbox.process(api, sid, gmail_client.service(), auto=mode == "auto")  # дальше approval перечитывает
        if stats["входящих"]:
            print("входящие:", stats, flush=True)
    return approval.main(test_n)


def failure(err: str) -> None:
    """Счётчик сбоев подряд в «_state» (approval.main обнуляет его на успешном проходе).
    Таблица недоступна: счётчик в локальном файле, чтобы не слать уведомление каждую минуту."""
    f = Path(tempfile.gettempdir()) / "solar_outreach_failures"
    try:
        api, sid = sheets.service(), os.environ["SPREADSHEET_ID"]
        n = int(sheets.kv(api, sid, "_state").get("ошибок подряд", {}).get("значение") or 0) + 1
        sheets.set_kv(api, sid, "_state", {"ошибок подряд": n, "последняя ошибка": f"{datetime.now():%Y-%m-%d %H:%M} {err}"})
        f.unlink(missing_ok=True)
    except Exception:
        n = int(f.read_text() if f.exists() else 0) + 1
        f.write_text(str(n))
    if n == ALERT_AFTER:
        notify.urgent(f"⚠️ {n} запуска подряд с ошибкой. Последняя: {err}")


def once(test_n: int = 0, with_inbox: bool = True) -> int | None:
    """Проход с обработкой ошибок. Возвращает offset Telegram или None (сбой / занято поиском)."""
    try:
        with single_run("sheet", wait=600):  # поиск держит блокировку только на время записи
            return run(test_n, with_inbox)
    except Busy:
        return None
    except Exception as e:  # без секретов в тексте: исключения Telegram их уже вычищают
        print(f"{datetime.now():%H:%M} ошибка: {type(e).__name__}: {str(e)[:300]}", flush=True)
        failure(f"{type(e).__name__}: {str(e)[:300]}")
        return None


def daemon() -> None:
    last_inbox = last_ping = 0.0
    offset = None
    while True:
        full = time.time() - last_inbox >= INBOX_EVERY
        res = once(with_inbox=full)
        if full:
            last_inbox = time.time()
        if res is None:
            time.sleep(30)
            continue
        offset = res
        if time.time() - last_ping >= PING_EVERY:
            last_ping = time.time()
            try:
                budget.enforce(load_config()["gcp"]["budget_subscription"], notify.urgent)
            except Exception as e:  # сбой проверки бюджета не роняет бота; Google ещё пришлёт письмо
                print(f"бюджет: {type(e).__name__}", flush=True)
            if os.environ.get("HEALTHCHECK_URL"):
                try:
                    requests.get(os.environ["HEALTHCHECK_URL"], timeout=10)
                except requests.RequestException:
                    pass
        try:  # до следующей проверки почты ждём Telegram; нажатие или команда будят цикл сразу
            notify.wait(offset, max(1, int(last_inbox + INBOX_EVERY - time.time())))
        except notify.TelegramError:
            time.sleep(10)
        time.sleep(1)  # пачка нажатий подряд: один проход вместо нескольких (лимит чтений Sheets 60/мин)


if __name__ == "__main__":
    load_config()
    if "--daemon" in sys.argv:
        daemon()
    n = int(sys.argv[sys.argv.index("--test") + 1]) if "--test" in sys.argv else 0
    if once(n) is None:
        sys.exit(1)
