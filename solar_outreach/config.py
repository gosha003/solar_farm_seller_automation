"""config.yaml + секреты из окружения (локально подхватывается .env)."""
import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

import yaml

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None

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


def update_env(values: dict[str, str], path: Path = ROOT / ".env") -> None:
    """Заменяет/добавляет ключи в .env, остальные строки не трогает."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    lines = [line for line in lines if line.partition("=")[0].strip() not in values]
    path.write_text("\n".join(lines + [f"{k}={v}" for k, v in values.items()]) + "\n", encoding="utf-8")


def dry_run() -> bool:
    """Реальная отправка только при явном DRY_RUN=false. Отсутствие, опечатка, что угодно другое = dry run."""
    return os.environ.get("DRY_RUN", "true").strip().lower() != "false"


class Busy(SystemExit):
    """Блокировка занята дольше, чем готовы ждать."""


@contextmanager
def single_run(name: str, stale_sec: int = 1800, wait: float = 0):
    """Блокировка от параллельного запуска (раздел 9). Занято -> ждём до wait секунд, потом Busy (запуск пропускается).
    ponytail: lock-файл на одной машине (VM); второй машине с тем же Sheet он не помешает."""
    lock = Path(tempfile.gettempdir()) / f"solar_outreach_{name}.lock"
    deadline = time.time() + wait
    if fcntl:  # Linux (VM): flock снимает ядро, даже если процесс убит (systemd restart, OOM)
        fd = os.open(lock, os.O_CREAT | os.O_RDWR)
        try:
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.time() >= deadline:
                        raise Busy(f"{name}: уже идёт другой запуск, пропускаю")
                    time.sleep(2)
            yield
        finally:
            os.close(fd)
        return
    while True:  # Windows: lock-файл, зависший снимается по stale_sec
        if lock.exists() and time.time() - lock.stat().st_mtime > stale_sec:
            lock.unlink(missing_ok=True)  # упавший процесс не держит блокировку вечно
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            if time.time() >= deadline:
                raise Busy(f"{name}: уже идёт другой запуск, пропускаю")
            time.sleep(2)
    try:
        yield
    finally:
        os.close(fd)
        lock.unlink(missing_ok=True)


def load_config(path: Path = PKG / "config.yaml") -> dict:
    load_env()
    return yaml.safe_load(path.read_text(encoding="utf-8"))
