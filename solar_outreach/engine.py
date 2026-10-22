"""Main loop. Every pass is idempotent and runs under a lock.

    python -m solar_outreach.engine            # one pass (cron, debugging)
    python -m solar_outreach.engine --daemon   # VM: runs forever, answers in Telegram within seconds (systemd: solar-bot)

Pass: inbox (classify, extract facts, escalate) -> the director's replies to briefs -> Telegram buttons and commands,
drafts and follow-ups, sending within limits, digest. Search is a separate timed process, `python -m solar_outreach.search`:
it is slow (polite pauses between site requests).
"""
import os
import signal
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

import requests

from solar_outreach import approval, budget, director, gmail_client, inbox, llm, notify, sheets
from solar_outreach.config import Busy, load_config, single_run

ALERT_AFTER = 3        # one-off Telegram/Google timeouts aren't worth an alert; three in a row are
INBOX_EVERY = 60       # seconds between mail checks; in between we wait on Telegram with long polling
PING_EVERY = 300       # every 5 min: Google Cloud spending cap and HEALTHCHECK_URL (optional external heartbeat)
FAIL_FILE = Path(tempfile.gettempdir()) / "solar_outreach_failures"  # the failure counter while the sheet is unreachable
STOP = {"asked": False, "busy": False}  # SIGTERM (deploy, systemctl restart) waits for the pass to finish


def run(test_n: int = 0, with_inbox: bool = True) -> int:
    cfg = load_config()
    if with_inbox:
        api, sid = sheets.service(), os.environ["SPREADSHEET_ID"]
        sheets.ensure_tabs(api, sid)
        mode = sheets.kv(api, sid, "Настройки").get("approval_mode", {}).get("значение") or cfg["approval_mode"]
        stats = inbox.process(api, sid, gmail_client.service(), auto=mode == "auto")  # approval re-reads it from here
        if stats["входящих"]:
            print("входящие:", stats, flush=True)
        try:  # the director's answers to briefs; a document request it drafts goes out in this same pass
            director.replies(api, sid, gmail_client.service())
        except Exception as e:  # his letter stays unhandled and is read again next pass; the sellers' pipeline goes on
            print(f"директор: {type(e).__name__}: {str(e)[:200]}", flush=True)
        if stats.get("LLM недоступен"):  # replies wait unread: approval still runs, but it counts as a failed pass
            res = approval.main(test_n, full=False)
            failure(f"LLM недоступен: {stats['LLM недоступен']}")
            return res
    return approval.main(test_n, full=with_inbox)  # only a pass with the inbox resets the failure counter


def failure(err: str) -> None:
    """Consecutive-failure counter in «_state» (approval.main resets it on a successful pass).
    If the sheet is unavailable the counter lives in a local file, so the owner isn't alerted every minute."""
    f = FAIL_FILE
    try:
        api, sid = sheets.service(), os.environ["SPREADSHEET_ID"]
        n = int(sheets.kv(api, sid, "_state").get("ошибок подряд", {}).get("значение") or 0) + 1
        sheets.set_kv(api, sid, "_state", {"ошибок подряд": n, "последняя ошибка": f"{datetime.now():%Y-%m-%d %H:%M} {err}"})
        f.unlink(missing_ok=True)
    except Exception:
        n = int(f.read_text() if f.exists() else 0) + 1
        f.write_text(str(n))
    if n == ALERT_AFTER:
        notify.urgent(notify.join(notify.head("⚠️", f"{n} запуска подряд с ошибкой"),
                                  f"{notify.b('Последняя:')} {notify.esc(err)}"))


def once(test_n: int = 0, with_inbox: bool = True) -> int | None:
    """A pass with error handling. Returns the Telegram offset, or None (failure / busy with search)."""
    try:
        with single_run("sheet", wait=600):  # search holds the lock only while writing
            res = run(test_n, with_inbox)
        FAIL_FILE.unlink(missing_ok=True)  # otherwise a second Sheets outage starts counting from the first one's 3+
        return res
    except Busy:
        return None
    except Exception as e:  # no secrets in the text: Telegram exceptions are already scrubbed
        print(f"{datetime.now():%H:%M} ошибка: {type(e).__name__}: {str(e)[:300]}", flush=True)
        failure(f"{type(e).__name__}: {str(e)[:300]}")
        return None


def stop(*_) -> None:
    """SIGTERM between passes exits at once; during a pass it waits for the pass to end (TimeoutStopSec in the unit),
    so a deploy never kills the bot between a Gmail send and its sheet write."""
    STOP["asked"] = True
    if not STOP["busy"]:
        sys.exit(0)


def daemon() -> None:
    last_inbox = last_ping = 0.0
    offset = None
    signal.signal(signal.SIGTERM, stop)
    while not STOP["asked"]:
        full = time.time() - last_inbox >= INBOX_EVERY
        STOP["busy"] = True
        try:
            res = once(with_inbox=full)
        finally:
            STOP["busy"] = False
        if STOP["asked"]:
            break
        if full:
            last_inbox = time.time()
        if res is None:
            time.sleep(30)
            continue
        offset = res
        if time.time() - last_ping >= PING_EVERY:
            last_ping = time.time()
            try:
                spent = budget.enforce(load_config()["gcp"]["budget_subscription"], notify.urgent)
                if spent:  # into the sheet and the digest: shows whether the project spends anything (external IP etc.)
                    sheets.set_kv(sheets.service(), os.environ["SPREADSHEET_ID"], "_state",
                                  {"расходы Google Cloud": f"{datetime.now():%Y-%m-%d %H:%M} {spent}"})
            except Exception as e:  # a failed budget check doesn't crash the bot; Google still sends an email
                print(f"бюджет: {type(e).__name__}", flush=True)
                try:  # the cap that powers the VM off is not working: the digest must say so
                    sheets.set_kv(sheets.service(), os.environ["SPREADSHEET_ID"], "_state",
                                  {"расходы Google Cloud": f"{datetime.now():%Y-%m-%d %H:%M} проверка не работает: {type(e).__name__}"})
                except Exception:
                    pass
            if os.environ.get("HEALTHCHECK_URL"):
                try:
                    requests.get(os.environ["HEALTHCHECK_URL"], timeout=10)
                except requests.RequestException:
                    pass
        try:  # until the next mail check, wait on Telegram; a tap or command wakes the loop at once
            notify.wait(offset, max(1, int(last_inbox + INBOX_EVERY - time.time())))
        except notify.TelegramError:
            time.sleep(10)
        time.sleep(1)  # a burst of taps: one pass instead of several (Sheets read limit is 60/min)


if __name__ == "__main__":
    load_config()
    if "--daemon" in sys.argv:
        daemon()
        sys.exit(0)  # SIGTERM: the pass finished, stop without another one
    n = int(sys.argv[sys.argv.index("--test") + 1]) if "--test" in sys.argv else 0
    if once(n) is None:
        sys.exit(1)
