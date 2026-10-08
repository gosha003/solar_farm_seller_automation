"""config.yaml + секреты из окружения (локально подхватывается .env)."""
import os
from pathlib import Path

import yaml

PKG = Path(__file__).resolve().parent
ROOT = PKG.parent


def load_env(path: Path = ROOT / ".env") -> None:
    """KEY=VALUE построчно; уже заданные переменные окружения не перетирает (GitHub Secrets важнее)."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition("=")
        key = key.strip()
        if sep and key and not key.startswith("#"):
            os.environ.setdefault(key, value.strip())


def dry_run() -> bool:
    """Реальная отправка только при явном DRY_RUN=false. Отсутствие, опечатка, что угодно другое = dry run."""
    return os.environ.get("DRY_RUN", "true").strip().lower() != "false"


def load_config(path: Path = PKG / "config.yaml") -> dict:
    load_env()
    return yaml.safe_load(path.read_text(encoding="utf-8"))
