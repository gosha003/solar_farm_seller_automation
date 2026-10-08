"""Раздел 14: перед публикацией в репозитории не должно быть ключей. Гоняется и локально, и в CI."""
import re
import subprocess
from pathlib import Path

from solar_outreach.config import ROOT

SECRET = re.compile(
    r"sk-ant-[\w-]{20,}"                 # Anthropic
    r"|GOCSPX-[\w-]{20,}|1//0[\w-]{20,}"  # Google client secret / refresh token
    r"|\d{8,10}:AA[\w-]{30,}"            # Telegram bot token
    r"|gh[pousr]_[A-Za-z0-9]{30,}"       # GitHub token
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----"
)


def publishable_files() -> list[str]:
    """Tracked + новые неигнорируемые файлы: ровно то, что уйдёт после `git add -A`."""
    out = subprocess.run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                         cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [f for f in out.split("\0") if f]


def test_no_secret_files_or_keys_would_be_published():
    files = publishable_files()
    assert files, "git ls-files пуст: тест запущен не из репозитория?"
    bad_names = [f for f in files if Path(f).name == ".env" or Path(f).name.startswith("client_secret")]
    assert not bad_names, bad_names
    leaks = [f for f in files if (ROOT / f).is_file()
             and SECRET.search((ROOT / f).read_text(encoding="utf-8", errors="ignore"))]
    assert not leaks, leaks


def test_pattern_catches_samples():
    assert SECRET.search("ANTHROPIC_API_KEY=sk-ant-api03-" + "x" * 40)
    assert SECRET.search("TELEGRAM_BOT_TOKEN=123456789:AA" + "b" * 33)
    assert not SECRET.search("TELEGRAM_BOT_TOKEN=\nphone: +359 88 000 0000")
