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


def latest(subscription: str, session=None) -> dict | None:
    """Новый отчёт Google о расходах с наибольшей суммой (costAmount, budgetAmount, currencyCode).
    None: новых отчётов нет или мы не на VM."""
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
    return max((r for r in reports if r.get("budgetAmount")), key=lambda r: r["costAmount"], default=None)


def enforce(subscription: str, urgent, session=None) -> str | None:
    """Потолок: расходы >= бюджета -> срочно владельцу и выключить VM. Возвращает строку для «_state»/дайджеста."""
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
