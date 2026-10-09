"""Главный цикл одного запуска (раздел 9). Идемпотентный, под блокировкой.

    python -m solar_outreach.engine

Входящие (шаги 3-4) -> команды и кнопки Telegram, черновики и дожимы, отправка в лимитах, дайджест (2, 5, 7-9).
Поиск (шаг 6) отдельным запуском `python -m solar_outreach.search` по своему расписанию: он долгий (паузы к сайтам).
"""
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from solar_outreach import approval, gmail_client, inbox, notify, sheets
from solar_outreach.config import load_config, single_run

ALERT_AFTER = 3  # разовые таймауты Telegram/Google не стоят уведомления, серия из трёх стоит


def run(test_n: int = 0) -> None:
    cfg = load_config()
    api, sid = sheets.service(), os.environ["SPREADSHEET_ID"]
    sheets.ensure_tabs(api, sid)
    mode = sheets.kv(api, sid, "Настройки").get("approval_mode", {}).get("значение") or cfg["approval_mode"]
    stats = inbox.process(api, sid, gmail_client.service(), auto=mode == "auto")  # дальше approval перечитывает
    if stats["входящих"]:
        print("входящие:", stats)
    approval.main(test_n)


def failure(err: str) -> None:
    """Счётчик сбоев подряд в «_state» (approval.main обнуляет его на успешном запуске).
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


if __name__ == "__main__":
    n = int(sys.argv[sys.argv.index("--test") + 1]) if "--test" in sys.argv else 0
    with single_run("sheet"):
        try:
            run(n)
        except Exception as e:  # без секретов в тексте: исключения Telegram их уже вычищают
            failure(f"{type(e).__name__}: {str(e)[:300]}")
            raise
