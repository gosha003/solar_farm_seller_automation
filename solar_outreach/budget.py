"""Google Cloud spending cap. The budget (7 CAD ≈ $5 a month) limits nothing by itself, it only reports:
Google posts the spend to Pub/Sub several times a day. The daemon on the VM reads the subscription and, when
spend >= budget, sends an urgent alert and powers the VM off (a stopped VM and its 30 GB disk stay in the free tier).

VM only: credentials of the solar-bot service account from metadata (it may only read this subscription).
ponytail: Google's reports lag up to a day, so the real cap is "about $5", not exact.
"""
import base64
import json
import subprocess

import google.auth
from google.auth.exceptions import DefaultCredentialsError
from google.auth.transport.requests import AuthorizedSession

API = "https://pubsub.googleapis.com/v1/"


def latest(subscription: str, session=None) -> dict | None:
    """Newest Google spend report with the largest amount (costAmount, budgetAmount, currencyCode).
    None: no new reports, or not running on the VM."""
    if session is None:
        try:
            creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/pubsub"])
        except DefaultCredentialsError:
            return None
        session = AuthorizedSession(creds)
    resp = session.post(f"{API}{subscription}:pull", json={"maxMessages": 100}, timeout=30)
    resp.raise_for_status()  # 403/404 (role or subscription gone) must not look like "no new reports": the cap would be off
    msgs = resp.json().get("receivedMessages", [])
    if not msgs:
        return None
    session.post(f"{API}{subscription}:acknowledge", json={"ackIds": [m["ackId"] for m in msgs]}, timeout=30)
    reports = [json.loads(base64.b64decode(m["message"]["data"])) for m in msgs]
    return max((r for r in reports if r.get("budgetAmount")), key=lambda r: r["costAmount"], default=None)


def enforce(subscription: str, urgent, session=None) -> str | None:
    """Cap: spend >= budget -> urgent alert to the owner and power off the VM. Returns a line for «_state»/the digest."""
    r = latest(subscription, session)
    if not r:
        return None
    ratio = r["costAmount"] / r["budgetAmount"]
    if ratio >= 1:
        urgent(f"🛑 Расходы Google Cloud достигли бюджета ({ratio:.0%}). Выключаю VM, бот остановлен. "
               "Проверь Billing в консоли; включить: gcloud compute instances start solar-bot --zone us-east1-b")
        subprocess.run(["sudo", "systemctl", "poweroff"], check=False)
    cur = r.get("currencyCode", "")
    return f"{r['costAmount']:.2f} из {r['budgetAmount']:.0f} {cur} с {r.get('costIntervalStart', '')[:10]}"
