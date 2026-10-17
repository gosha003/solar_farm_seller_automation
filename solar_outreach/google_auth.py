"""One-time Google OAuth authorization (Desktop flow).

    python -m solar_outreach.google_auth [path to client_secret.json]

Opens a browser, checks that the signed-in account is exactly owner.email from the config,
and writes GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET / GOOGLE_REFRESH_TOKEN to .env (which is gitignored).
"""
import sys
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from solar_outreach.config import ROOT, load_config, update_env

SCOPES = [  # nothing broader than needed
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
]


def credentials():
    """Credentials from .env / secrets (refresh token): shared sign-in for Gmail, Sheets and Drive."""
    import os
    from google.oauth2.credentials import Credentials
    return Credentials(None, refresh_token=os.environ["GOOGLE_REFRESH_TOKEN"], token_uri="https://oauth2.googleapis.com/token",
                       client_id=os.environ["GOOGLE_CLIENT_ID"], client_secret=os.environ["GOOGLE_CLIENT_SECRET"], scopes=SCOPES)


def main() -> None:
    owner = load_config()["owner"]["email"]
    secret = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "client_secret.json"
    flow = InstalledAppFlow.from_client_secrets_file(str(secret), SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent", login_hint=owner)

    email = build("gmail", "v1", credentials=creds).users().getProfile(userId="me").execute()["emailAddress"]
    if email.lower() != owner.lower():
        sys.exit(f"Авторизован {email}, а нужен {owner}. Повтори и выбери правильный аккаунт.")
    if not creds.refresh_token:
        sys.exit("Google не вернул refresh_token: отзови доступ на myaccount.google.com/permissions и повтори.")

    update_env({
        "GOOGLE_CLIENT_ID": creds.client_id,
        "GOOGLE_CLIENT_SECRET": creds.client_secret,
        "GOOGLE_REFRESH_TOKEN": creds.refresh_token,
    })
    print(f"OK: {email}, токен записан в .env")


if __name__ == "__main__":
    main()
