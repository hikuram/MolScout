"""Persistent application state backed by PostgreSQL."""

from __future__ import annotations

from pathlib import Path

from .database import (
    mutate_queue_state_payload,
    read_app_state_payload,
    read_queue_state_payload,
    write_app_state_payload,
)
from .utils import now_iso, read_json, write_json


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
    state = read_app_state_payload(default_app_state())
    return {**default_app_state(), **state}


def write_app_state(state: dict) -> None:
    current = {**default_app_state(), **state}
    write_app_state_payload(current)


def normalize_queue_state(state: dict) -> dict:
    merged = default_queue_state()
    merged.update(state)
    merged["jobs"] = list(state.get("jobs", []))
    for item in merged["jobs"]:
        if "queue_key" not in item:
            item["queue_key"] = f"{item['session_id']}:{item['job_id']}"
    running_job_id = merged.get("running_job_id")
    if running_job_id and ":" not in str(running_job_id):
        session_id = next(
            (
                item["session_id"]
                for item in merged["jobs"]
                if item["job_id"] == running_job_id and item["status"] == "running"
            ),
            None,
        )
        if session_id:
            merged["running_job_id"] = f"{session_id}:{running_job_id}"
    return merged


def read_queue_state() -> dict:
    return normalize_queue_state(read_queue_state_payload(default_queue_state()))


def mutate_queue_state(mutator):
    def database_mutator(state: dict) -> dict:
        merged = normalize_queue_state(state)
        new_state = mutator(merged) or merged
        new_state["updated_at"] = now_iso()
        return new_state

    return mutate_queue_state_payload(default_queue_state(), database_mutator)


def read_json_file(path: Path, default):
    """Read calculation-side JSON files that remain on the filesystem."""
    return read_json(path, default)


def write_json_file(path: Path, payload) -> None:
    """Write calculation-side JSON files that remain on the filesystem."""
    write_json(path, payload)
