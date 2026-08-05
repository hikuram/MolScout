"""Frozen cleanup routines retained for a later PostgreSQL-aware redesign."""

from __future__ import annotations

from .paths import SESSION_RETENTION_DAYS
from .queue_manager import queue_snapshot, remove_from_queue, sync_queue_state
from .session_manager import delete_job_files, delete_session_files, get_job, list_jobs, list_sessions, save_job, session_is_expired


def cleanup_stale_jobs() -> dict[str, int]:
    removed = 0
    synced = sync_queue_state()
    for item in list(synced["jobs"]):
        job = get_job(item["session_id"], item["job_id"])
        if not job:
            remove_from_queue(item["session_id"], item["job_id"])
            removed += 1
            continue
        if job["status"] in {"deleted", "completed", "failed", "cancelled"}:
            remove_from_queue(item["session_id"], item["job_id"])
            removed += 1
    return {"queue_entries_removed": removed}


def cleanup_expired_sessions(retention_days: int = SESSION_RETENTION_DAYS) -> dict[str, int]:
    deleted_sessions = 0
    deleted_jobs = 0
    for session in list_sessions():
        if not session_is_expired(session, retention_days):
            continue
        jobs = list_jobs(session["session_id"])
        if any(job["status"] == "running" for job in jobs):
            continue
        for job in jobs:
            remove_from_queue(session["session_id"], job["job_id"])
            delete_job_files(session["session_id"], job["job_id"])
            deleted_jobs += 1
        delete_session_files(session["session_id"])
        deleted_sessions += 1
    return {"sessions_deleted": deleted_sessions, "jobs_deleted": deleted_jobs}


def run_cleanup(retention_days: int = SESSION_RETENTION_DAYS) -> dict[str, int]:
    raise RuntimeError("Cleanup is frozen during the PostgreSQL migration.")
