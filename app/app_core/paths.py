"""Filesystem paths used by the Streamlit GUI."""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
APP_DIR = PROJECT_ROOT / "app"
CORE_DIR = PROJECT_ROOT / "core"

DATA_DIR = APP_DIR / "data"
SESSIONS_DIR = DATA_DIR / "sessions"
QUEUE_DIR = DATA_DIR / "queue"
ARCHIVES_DIR = DATA_DIR / "archives"
LOG_DIR = DATA_DIR / "logs"
TEMP_DIR = DATA_DIR / "tmp"
LOCK_DIR = DATA_DIR / "locks"

APP_STATE_FILE = DATA_DIR / "app_state.json"
QUEUE_FILE = QUEUE_DIR / "queue.json"
WORKER_PID_FILE = QUEUE_DIR / "worker.pid"
WORKER_LOG_FILE = LOG_DIR / "queue_worker.log"
WORKER_LOCK_FILE = LOCK_DIR / "worker.lock"
QUEUE_LOCK_FILE = LOCK_DIR / "queue.lock"
STATE_LOCK_FILE = LOCK_DIR / "state.lock"

RUNS_LEGACY_DIR = APP_DIR / "runs"
UPLOADS_LEGACY_DIR = APP_DIR / "uploads"
JOB_LOGS_LEGACY_DIR = APP_DIR / "job_logs"

AUTO_REFRESH_SECONDS = 5
SESSION_RETENTION_DAYS = 30


def ensure_app_dirs() -> None:
    for path in [
        DATA_DIR,
        SESSIONS_DIR,
        QUEUE_DIR,
        ARCHIVES_DIR,
        LOG_DIR,
        TEMP_DIR,
        LOCK_DIR,
        RUNS_LEGACY_DIR,
        UPLOADS_LEGACY_DIR,
        JOB_LOGS_LEGACY_DIR,
    ]:
        path.mkdir(parents=True, exist_ok=True)
