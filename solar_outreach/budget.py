"""Потолок расходов Google Cloud. Бюджет (7 CAD ≈ $5 в месяц) сам ничего не ограничивает, только сообщает:
Google несколько раз в сутки шлёт сумму расходов в Pub/Sub. Демон на VM читает подписку и при расходах
>= бюджета присылает срочное уведомление и выключает VM (остановленная VM и диск 30 ГБ в бесплатном лимите).

Только на VM: учётка сервис-аккаунта solar-bot из metadata (право только читать эту подписку).
ponytail: отчёты Google приходят с задержкой до суток, поэтому реальный потолок «около $5», а не ровно.
"""
import base64
import json
import subprocess

import google.auth
from google.auth.exceptions import DefaultCredentialsError
from google.auth.transport.requests import AuthorizedSession

API = "https://pubsub.googleapis.com/v1/"


def spent_ratio(subscription: str, session=None) -> float | None:
    """Максимум costAmount / budgetAmount среди новых отчётов. None: отчётов нет или мы не на VM."""
    if session is None:
        try:
            creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/pubsub"])
        except DefaultCredentialsError:
            return None
        session = AuthorizedSession(creds)
    msgs = session.post(f"{API}{subscription}:pull", json={"maxMessages": 100}, timeout=30).json().get("receivedMessages", [])
    if not msgs:
        return None
    session.post(f"{API}{subscription}:acknowledge", json={"ackIds": [m["ackId"] for m in msgs]}, timeout=30)
    reports = [json.loads(base64.b64decode(m["message"]["data"])) for m in msgs]
    return max(r["costAmount"] / r["budgetAmount"] for r in reports if r.get("budgetAmount"))


def enforce(subscription: str, urgent, session=None) -> None:
    ratio = spent_ratio(subscription, session)
    if ratio is not None and ratio >= 1:
        urgent(f"🛑 Расходы Google Cloud достигли бюджета ({ratio:.0%}). Выключаю VM, бот остановлен. "
               "Проверь Billing в консоли; включить: gcloud compute instances start solar-bot --zone us-east1-b")
        subprocess.run(["sudo", "systemctl", "poweroff"], check=False)
