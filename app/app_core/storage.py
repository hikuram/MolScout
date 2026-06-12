"""Persistent app storage and models."""

from __future__ import annotations

from pathlib import Path

from .paths import APP_STATE_FILE, QUEUE_FILE, QUEUE_LOCK_FILE, STATE_LOCK_FILE
from .utils import locked_file, now_iso, read_json, write_json


def default_app_state() -> dict:
    return {
        "created_at": now_iso(),
        "last_cleanup_at": None,
        "selected_session_id": None,
    }


def default_queue_state() -> dict:
    return {
        "updated_at": now_iso(),
        "running_job_id": None,
        "jobs": [],
    }


def read_app_state() -> dict:
    with locked_file(STATE_LOCK_FILE):
        state = read_json(APP_STATE_FILE, default_app_state())
        return {**default_app_state(), **state}


def write_app_state(state: dict) -> None:
    with locked_file(STATE_LOCK_FILE):
        current = {**default_app_state(), **state}
        write_json(APP_STATE_FILE, current)


def read_queue_state() -> dict:
    with locked_file(QUEUE_LOCK_FILE):
        state = read_json(QUEUE_FILE, default_queue_state())
        merged = default_queue_state()
        merged.update(state)
        merged["jobs"] = list(state.get("jobs", []))
        for item in merged["jobs"]:
            if "queue_key" not in item:
                item["queue_key"] = f"{item['session_id']}:{item['job_id']}"
        running_job_id = merged.get("running_job_id")
        if running_job_id and ":" not in str(running_job_id):
            session_id = next(
                (item["session_id"] for item in merged["jobs"] if item["job_id"] == running_job_id and item["status"] == "running"),
                None,
            )
            if session_id:
                merged["running_job_id"] = f"{session_id}:{running_job_id}"
        return merged


def mutate_queue_state(mutator):
    with locked_file(QUEUE_LOCK_FILE):
        state = read_json(QUEUE_FILE, default_queue_state())
        merged = default_queue_state()
        merged.update(state)
        merged["jobs"] = list(state.get("jobs", []))
        for item in merged["jobs"]:
            if "queue_key" not in item:
                item["queue_key"] = f"{item['session_id']}:{item['job_id']}"
        running_job_id = merged.get("running_job_id")
        if running_job_id and ":" not in str(running_job_id):
            session_id = next(
                (item["session_id"] for item in merged["jobs"] if item["job_id"] == running_job_id and item["status"] == "running"),
                None,
            )
            if session_id:
                merged["running_job_id"] = f"{session_id}:{running_job_id}"
        new_state = mutator(merged) or merged
        new_state["updated_at"] = now_iso()
        write_json(QUEUE_FILE, new_state)
        return new_state


def read_json_file(path: Path, default):
    return read_json(path, default)


def write_json_file(path: Path, payload) -> None:
    write_json(path, payload)
