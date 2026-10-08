"""Главный цикл одного запуска (раздел 9). Идемпотентный, под блокировкой.

    python -m solar_outreach.engine

Сейчас: входящие (шаг 3-4) -> команды/одобрения из Telegram + черновики + отправка одобренного (шаги 2, 7, 8).
Фаза 4 добавит дожимы (5), поиск по расписанию (6), лимиты, дайджест (9).
"""
import os
import sys

from solar_outreach import approval, gmail_client, inbox, notify, sheets
from solar_outreach.config import load_config, single_run


def run(test_n: int = 0) -> None:
    load_config()
    api, sid = sheets.service(), os.environ["SPREADSHEET_ID"]
    sheets.ensure_tabs(api, sid)
    stats = inbox.process(api, sid, gmail_client.service())  # пишет в таблицу сам; дальше approval перечитывает
    if stats["входящих"]:
        print("входящие:", stats)
    approval.main(test_n)


if __name__ == "__main__":
    n = int(sys.argv[sys.argv.index("--test") + 1]) if "--test" in sys.argv else 0
    with single_run("sheet"):
        try:
            run(n)
        except Exception as e:  # падение цикла = срочное уведомление владельцу, без секретов в тексте
            notify.urgent(f"⚠️ Ошибка запуска: {type(e).__name__}: {str(e)[:300]}")
            raise
