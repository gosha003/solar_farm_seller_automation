"""Main loop. Every pass is idempotent and runs under a lock.

    python -m solar_outreach.engine            # one pass (cron, debugging)
    python -m solar_outreach.engine --daemon   # VM: runs forever, answers in Telegram within seconds (systemd: solar-bot)

Pass: inbox (classify, extract facts, escalate) and the director's replies to briefs, only at schedule.mail (twice a
day), on /mail, or before a letter goes into a thread the seller has written in since -> Telegram buttons and commands,
drafts, sending within limits, digest (every pass); follow-ups are drafted only right after a mail check. Between the
checks every pass still takes bounces and alo.bg notices (no LLM): the bounce stop and the PC routine rely on them.
Search is a separate timed process, `python -m solar_outreach.search`: it is slow (polite pauses between site requests).
"""
import os
import re
import signal
import sys
import tempfile
import time
from datetime import datetime, time as dtime, timedelta
from pathlib import Path

import requests

from solar_outreach import approval, budget, director, gmail_client, inbox, llm, notify, owner, sheets
from solar_outreach.config import Busy, load_config, single_run

ALERT_AFTER = 3        # one-off Telegram/Google timeouts aren't worth an alert; three in a row are
INBOX_EVERY = 60       # seconds between full passes (mail only when due); in between we wait on Telegram (long polling)
MAIL_KEY = "почта: последняя проверка"  # «_state»: owner's local time of the last mail check
PING_EVERY = 300       # every 5 min: Google Cloud spending cap and HEALTHCHECK_URL (optional external heartbeat)
FAIL_FILE = Path(tempfile.gettempdir()) / "solar_outreach_failures"  # the failure counter while the sheet is unreachable
STOP = {"asked": False, "busy": False}  # SIGTERM (deploy, systemctl restart) waits for the pass to finish


def mail_due(now: datetime, last: str, slots: list[str]) -> bool:
    """The newest scheduled time at or before now (today's, or yesterday's last) is later than the last mail check."""
    days = (now.date() - timedelta(days=1), now.date())
    latest = max(t for t in (datetime.combine(d, dtime.fromisoformat(s)) for d in days for s in slots) if t <= now)
    m = re.match(r"(\d{4})-(\d\d)-(\d\d)[ T](\d{1,2}):(\d\d)", last or "")  # the sheet may show «8:30» for 08:30
    return not m or datetime(*map(int, m.groups())) < latest  # no match: never checked


def run(test_n: int = 0, with_inbox: bool = True, mail: bool | None = None) -> int:
    """with_inbox: a full pass (drafts, follow-ups, sending, digest). Seller mail and the director's letters are read
    only at schedule.mail (owner's time) or after /mail; mail=True forces it (a manual single pass)."""
    cfg = load_config()
    if with_inbox:
        api, sid = sheets.service(), os.environ["SPREADSHEET_ID"]
        sheets.ensure_tabs(api, sid)
        settings = sheets.kv(api, sid, "Настройки")
        mode = settings.get("approval_mode", {}).get("значение") or cfg["approval_mode"]
        asked = settings.get(owner.MAIL_NOW, {}).get("значение") == "да"
        now = owner.local_now(cfg).replace(tzinfo=None)
        state = sheets.kv(api, sid, "_state")
        if mail is None:
            mail = asked or state.get(owner.MAIL_NEEDED, {}).get("значение") == "да" or mail_due(
                now, state.get(MAIL_KEY, {}).get("значение", ""), cfg["schedule"]["mail"])
        stats = inbox.process(api, sid, gmail_client.service(), auto=mode == "auto", light=not mail)
        if stats["входящих"]:
            print("входящие:", stats, flush=True)
        if mail:
            if stats.get("LLM недоступен"):  # replies wait unread and the check is due again: retried every pass
                res = approval.main(test_n, full=False, fresh=False)
                failure(f"LLM недоступен: {stats['LLM недоступен']}")
                return res
            try:  # the director's answers to briefs; a document request it drafts goes out in this same pass
                director.replies(api, sid, gmail_client.service())
            except Exception as e:  # the check stays due: retried every pass, at most until the alert
                print(f"директор: {type(e).__name__}: {str(e)[:200]}", flush=True)
                if failure(f"директор: {type(e).__name__}: {str(e)[:200]}") < ALERT_AFTER:
                    return approval.main(test_n, full=False)
            sheets.set_kv(api, sid, "_state", {MAIL_KEY: now.isoformat(timespec="minutes"), owner.MAIL_NEEDED: ""})
            if asked:  # the owner's /mail: the flag goes, and he hears back even when nothing came
                sheets.set_kv(api, sid, "Настройки", {owner.MAIL_NOW: "нет"})
                notify.try_send(notify.join(notify.head("📬", "Почта проверена"),
                                            f"Новых писем: {stats['входящих']}. Следующая проверка по расписанию: "
                                            f"{', '.join(cfg['schedule']['mail'])}."))
    return approval.main(test_n, full=with_inbox, fresh=bool(mail))  # only a full pass resets the failure counter


def failure(err: str) -> int:
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
    return n


def once(test_n: int = 0, with_inbox: bool = True, mail: bool | None = None) -> int | None:
    """A pass with error handling. Returns the Telegram offset, or None (failure / busy with search)."""
    try:
        with single_run("sheet", wait=600):  # search holds the lock only while writing
            res = run(test_n, with_inbox, mail)
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
    if once(n, mail=True) is None:  # run by hand: the mail is read now, whatever the schedule
        sys.exit(1)
