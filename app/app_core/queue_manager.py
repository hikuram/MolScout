"""Shared queue operations."""

from __future__ import annotations

from .job_runner import start_job_process, sync_job_status
from .paths import WORKER_PID_FILE
from .session_manager import delete_job_files, get_job, get_session, list_jobs, reorder_session_jobs, save_job, save_session
from .storage import mutate_queue_state, read_queue_state
from .utils import now_iso, pid_is_running


def queue_key(session_id: str, job_id: str) -> str:
    return f"{session_id}:{job_id}"


def queue_snapshot() -> dict:
    return read_queue_state()


def enqueue_job(job: dict) -> dict:
    def mutator(state: dict):
        key = queue_key(job["session_id"], job["job_id"])
        state["jobs"] = [item for item in state["jobs"] if item.get("queue_key") != key]
        job["status"] = "queued"
        save_job(job)
        state["jobs"].append(
            {
                "queue_key": key,
                "job_id": job["job_id"],
                "session_id": job["session_id"],
                "status": "queued",
                "queued_at": now_iso(),
            }
        )
        return state

    return mutate_queue_state(mutator)


def remove_from_queue(session_id: str, job_id: str) -> dict:
    def mutator(state: dict):
        key = queue_key(session_id, job_id)
        state["jobs"] = [item for item in state["jobs"] if item.get("queue_key") != key]
        if state.get("running_job_id") == key:
            state["running_job_id"] = None
        return state

    return mutate_queue_state(mutator)


def queued_items_for_session(session_id: str) -> list[dict]:
    jobs = []
    state = read_queue_state()
    for item in state["jobs"]:
        if item["session_id"] == session_id and item["status"] == "queued":
            jobs.append(item)
    return jobs


def reorder_queue_for_session(session_id: str, job_ids: list[str]) -> None:
    session_jobs = {job["job_id"]: job for job in list_jobs(session_id)}
    reorder_session_jobs(session_id, job_ids)

    def mutator(state: dict):
        queued = [item for item in state["jobs"] if item["status"] == "queued"]
        others = [item for item in state["jobs"] if item["status"] != "queued"]
        session_queue = [item for item in queued if item["session_id"] == session_id]
        remaining = [item for item in queued if item["session_id"] != session_id]
        ordered = []
        queued_by_id = {item["job_id"]: item for item in session_queue}
        for job_id in job_ids:
            if job_id in queued_by_id:
                ordered.append(queued_by_id.pop(job_id))
        ordered.extend(queued_by_id.values())
        state["jobs"] = others + ordered + remaining
        return state

    mutate_queue_state(mutator)
    for index, job_id in enumerate(job_ids):
        if job_id in session_jobs:
            job = session_jobs[job_id]
            if job["status"] == "queued":
                job["queue_position"] = index + 1
                save_job(job)


def delete_job_record(session_id: str, job_id: str) -> None:
    remove_from_queue(session_id, job_id)
    session = get_session(session_id)
    if session:
        session["job_order"] = [item for item in session.get("job_order", []) if item != job_id]
        save_session(session)
    delete_job_files(session_id, job_id)


def delete_job_from_queue(session_id: str, job_id: str) -> str:
    job = get_job(session_id, job_id)
    if not job:
        return "missing"

    job = sync_job_status(job)
    if job["status"] in {"running", "cancel_requested"}:
        job["delete_requested"] = True
        job["delete_requested_at"] = now_iso()
        save_job(job)
        return "deferred"

    delete_job_record(session_id, job_id)
    return "deleted"


def sync_queue_state() -> dict:
    deferred_deletes: list[tuple[str, str]] = []

    def mutator(state: dict):
        running_id = state.get("running_job_id")
        if running_id:
            match = next((item for item in state["jobs"] if item.get("queue_key") == running_id), None)
            if match:
                job = get_job(match["session_id"], match["job_id"])
                if job:
                    synced = sync_job_status(job)
                    match["status"] = synced["status"]
                    if synced["status"] not in {"running", "cancel_requested"}:
                        state["running_job_id"] = None
                        if synced.get("delete_requested"):
                            deferred_deletes.append((synced["session_id"], synced["job_id"]))
            else:
                state["running_job_id"] = None
        state["jobs"] = [
            item
            for item in state["jobs"]
            if item["status"] in {"queued", "running", "cancel_requested"}
        ]
        return state

    state = mutate_queue_state(mutator)
    for session_id, job_id in deferred_deletes:
        delete_job_record(session_id, job_id)
    return state


def run_next_queued_job() -> dict:
    def mutator(state: dict):
        if state.get("running_job_id"):
            return state
        next_item = next((item for item in state["jobs"] if item["status"] == "queued"), None)
        if not next_item:
            return state
        job = get_job(next_item["session_id"], next_item["job_id"])
        if not job:
            state["jobs"] = [item for item in state["jobs"] if item.get("queue_key") != next_item.get("queue_key")]
            return state
        started = start_job_process(job)
        next_item["status"] = "running"
        state["running_job_id"] = queue_key(started["session_id"], started["job_id"])
        return state

    return mutate_queue_state(mutator)


def worker_is_running() -> bool:
    if not WORKER_PID_FILE.exists():
        return False
    try:
        pid = int(WORKER_PID_FILE.read_text(encoding="utf-8").strip())
    except ValueError:
        WORKER_PID_FILE.unlink(missing_ok=True)
        return False
    if not pid_is_running(pid):
        WORKER_PID_FILE.unlink(missing_ok=True)
        return False

    cmdline_path = f"/proc/{pid}/cmdline"
    try:
        with open(cmdline_path, "rb") as handle:
            cmdline = handle.read().replace(b"\x00", b" ").decode("utf-8", errors="replace")
    except OSError:
        return True
    if "app_core.queue_worker" not in cmdline:
        WORKER_PID_FILE.unlink(missing_ok=True)
        return False
    return True
