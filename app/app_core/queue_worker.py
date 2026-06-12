"""Background worker that dispatches queued jobs one at a time."""

from __future__ import annotations

import os
import time
from pathlib import Path

from .paths import AUTO_REFRESH_SECONDS, WORKER_LOCK_FILE, WORKER_LOG_FILE, WORKER_PID_FILE, ensure_app_dirs
from .queue_manager import run_next_queued_job, sync_queue_state
from .utils import locked_file, now_iso


def append_worker_log(message: str) -> None:
    WORKER_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with WORKER_LOG_FILE.open("a", encoding="utf-8") as handle:
        handle.write(f"[{now_iso()}] {message}\n")


def worker_loop() -> None:
    ensure_app_dirs()
    WORKER_PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
    append_worker_log("worker started")
    with locked_file(WORKER_LOCK_FILE):
        idle_ticks = 0
        while True:
            state = sync_queue_state()
            has_running = state.get("running_job_id") is not None
            has_queued = any(item["status"] == "queued" for item in state["jobs"])
            if not has_running and has_queued:
                run_next_queued_job()
                idle_ticks = 0
            elif not has_running and not has_queued:
                idle_ticks += 1
            else:
                idle_ticks = 0

            if idle_ticks >= 24:
                append_worker_log("worker exiting after idle timeout")
                break
            time.sleep(AUTO_REFRESH_SECONDS)
    if WORKER_PID_FILE.exists():
        WORKER_PID_FILE.unlink()


if __name__ == "__main__":
    worker_loop()
