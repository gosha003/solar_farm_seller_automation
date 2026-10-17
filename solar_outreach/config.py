"""config.yaml (+ private config.local.yaml) and secrets from the environment (.env is picked up locally)."""
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
    """KEY=VALUE per line; never overwrites variables already set in the environment (real env wins)."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition("=")
        key = key.strip()
        if sep and key and not key.startswith("#"):
            os.environ.setdefault(key, value.strip())


def update_env(values: dict[str, str], path: Path = ROOT / ".env") -> None:
    """Replaces/adds keys in .env and leaves other lines untouched."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    lines = [line for line in lines if line.partition("=")[0].strip() not in values]
    path.write_text("\n".join(lines + [f"{k}={v}" for k, v in values.items()]) + "\n", encoding="utf-8")


def dry_run() -> bool:
    """Real sending only with an explicit DRY_RUN=false. Missing, misspelled, anything else = dry run."""
    return os.environ.get("DRY_RUN", "true").strip().lower() != "false"


class Busy(SystemExit):
    """The lock is held longer than we are willing to wait."""


@contextmanager
def single_run(name: str, stale_sec: int = 1800, wait: float = 0):
    """Guard against parallel runs. Taken -> wait up to `wait` seconds, then Busy (the run is skipped).
    ponytail: lock file on one machine (the VM); it won't stop a second machine using the same Sheet."""
    lock = Path(tempfile.gettempdir()) / f"solar_outreach_{name}.lock"
    deadline = time.time() + wait
    if fcntl:  # Linux (VM): the kernel releases flock even if the process is killed (systemd restart, OOM)
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
    while True:  # Windows: lock file; a stale one is cleared after stale_sec
        if lock.exists() and time.time() - lock.stat().st_mtime > stale_sec:
            lock.unlink(missing_ok=True)  # a crashed process doesn't hold the lock forever
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


LOCAL = PKG / "config.local.yaml"  # gitignored: real owner contacts and price targets; tests set it to None


def merge(base: dict, over: dict) -> dict:
    for key, value in over.items():
        base[key] = merge(base[key], value) if isinstance(value, dict) and isinstance(base.get(key), dict) else value
    return base


def load_config(path: Path = PKG / "config.yaml") -> dict:
    load_env()
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    if LOCAL and LOCAL.exists():
        merge(cfg, yaml.safe_load(LOCAL.read_text(encoding="utf-8")) or {})
    return cfg
