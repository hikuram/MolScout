"""Session and job metadata management."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from .config import INPUT_EXTENSIONS
from .database import (
    delete_job_payload,
    delete_session_payload,
    list_job_payloads,
    list_session_payloads,
    read_job_payload,
    read_session_payload,
    write_job_payload,
    write_session_payload,
)
from .paths import ARCHIVES_DIR, CORE_DIR, SESSIONS_DIR
from .utils import now_iso, parse_iso, safe_name, slug_with_timestamp


def session_dir(session_id: str) -> Path:
    return SESSIONS_DIR / session_id


def session_meta_path(session_id: str) -> Path:
    """Legacy metadata path retained only for migration/reference purposes."""
    return session_dir(session_id) / "session.json"


def jobs_dir(session_id: str) -> Path:
    return session_dir(session_id) / "jobs"


def job_dir(session_id: str, job_id: str) -> Path:
    return jobs_dir(session_id) / job_id


def job_meta_path(session_id: str, job_id: str) -> Path:
    """Legacy metadata path retained only for migration/reference purposes."""
    return job_dir(session_id, job_id) / "job.json"


def session_archive_path(session_id: str) -> Path:
    return ARCHIVES_DIR / f"session-{session_id}.zip"


def job_archive_path(session_id: str, job_id: str) -> Path:
    return ARCHIVES_DIR / f"job-{session_id}-{job_id}.zip"


def default_pyscf_config_path() -> Path:
    return CORE_DIR / "pyscf_config.json"


def default_pyscf_config() -> dict:
    return json.loads(default_pyscf_config_path().read_text(encoding="utf-8"))


def session_pyscf_config_path(session_id: str) -> Path:
    return session_dir(session_id) / "pyscf_config.json"


def ensure_session_pyscf_config_payload(payload: dict) -> dict:
    config = payload.get("pyscf_config")
    if isinstance(config, dict) and config:
        return payload

    legacy_path = session_pyscf_config_path(payload["session_id"])
    if legacy_path.exists():
        try:
            payload["pyscf_config"] = json.loads(legacy_path.read_text(encoding="utf-8"))
            return payload
        except (OSError, json.JSONDecodeError):
            pass

    payload["pyscf_config"] = default_pyscf_config()
    return payload


def default_session_payload(session_id: str, owner_label: str) -> dict:
    return ensure_session_pyscf_config_payload({
        "session_id": session_id,
        "owner_label": owner_label or "anonymous",
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "last_accessed_at": now_iso(),
        "notes": "",
        "job_order": [],
    })


def default_job_payload(session_id: str, job_id: str) -> dict:
    job_root = job_dir(session_id, job_id)
    return {
        "session_id": session_id,
        "job_id": job_id,
        "name": job_id,
        "workflow": "",
        "script_name": "",
        "status": "queued",
        "queue_position": None,
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "started_at": None,
        "finished_at": None,
        "pid": None,
        "exit_code": None,
        "completion_reason": "",
        "status_message": "",
        "cancel_requested_at": None,
        "runtime_status_file": "",
        "owner_label": "",
        "charge": 0,
        "method": "",
        "result_name": "result.csv",
        "inputs": [],
        "output_dir": str(job_root / "run_output"),
        "stdout_log": str(job_root / "stdout.log"),
        "command": [],
        "manifest_path": str(job_root / "job_manifest.json"),
        "artifact_cataloged_at": None,
        "artifact_catalog_count": 0,
        "artifact_catalog_marked_missing": 0,
        "artifact_catalog_error": "",
        "notes": "",
        "delete_requested": False,
        "delete_requested_at": None,
    }


def create_session(owner_label: str, notes: str = "") -> dict:
    session_id = slug_with_timestamp(owner_label or "session")
    root = session_dir(session_id)
    (root / "jobs").mkdir(parents=True, exist_ok=True)
    (root / "artifacts").mkdir(parents=True, exist_ok=True)
    (root / "logs").mkdir(parents=True, exist_ok=True)
    payload = default_session_payload(session_id, owner_label)
    payload["notes"] = notes
    write_session_payload(ensure_session_pyscf_config_payload(payload))
    return payload


def list_sessions() -> list[dict]:
    sessions = [ensure_session_pyscf_config_payload(payload) for payload in list_session_payloads()]
    sessions.sort(key=lambda item: item.get("updated_at", ""), reverse=True)
    return sessions


def get_session(session_id: str) -> dict | None:
    payload = read_session_payload(session_id)
    return ensure_session_pyscf_config_payload(payload) if payload else None


def save_session(payload: dict) -> None:
    payload = ensure_session_pyscf_config_payload(payload)
    payload["updated_at"] = now_iso()
    write_session_payload(payload)


def touch_session(session_id: str) -> dict | None:
    """Record session access without changing its content update timestamp."""
    payload = get_session(session_id)
    if not payload:
        return None
    payload["last_accessed_at"] = now_iso()
    write_session_payload(payload)
    return payload


def next_job_id(session_id: str, workflow: str) -> str:
    prefix = safe_name(workflow.lower().replace(" ", "-"), fallback="job")
    existing = {job["job_id"] for job in list_job_payloads(session_id)}
    existing.update(path.name for path in jobs_dir(session_id).glob("*") if path.is_dir())
    index = 1
    while True:
        candidate = f"{index:03d}-{prefix}"
        if candidate not in existing:
            return candidate
        index += 1


def create_job(session_id: str, owner_label: str, workflow: str) -> dict:
    job_id = next_job_id(session_id, workflow)
    root = job_dir(session_id, job_id)
    for child in ["inputs", "artifacts"]:
        (root / child).mkdir(parents=True, exist_ok=True)
    payload = default_job_payload(session_id, job_id)
    payload["name"] = workflow
    payload["workflow"] = workflow
    payload["owner_label"] = owner_label or "anonymous"
    write_job_payload(payload)

    session = get_session(session_id)
    if session:
        session["job_order"] = [*session.get("job_order", []), job_id]
        save_session(session)
    return payload


def list_jobs(session_id: str) -> list[dict]:
    session = get_session(session_id)
    if not session:
        return []
    payloads = {payload["job_id"]: payload for payload in list_job_payloads(session_id)}
    ordered = []
    for job_id in session.get("job_order", []):
        if job_id in payloads:
            ordered.append(payloads.pop(job_id))
    ordered.extend(sorted(payloads.values(), key=lambda item: item["job_id"]))
    return ordered


def get_job(session_id: str, job_id: str) -> dict | None:
    return read_job_payload(session_id, job_id)


def save_job(payload: dict) -> None:
    payload["updated_at"] = now_iso()
    write_job_payload(payload)


def reorder_session_jobs(session_id: str, new_order: list[str]) -> None:
    session = get_session(session_id)
    if not session:
        return
    session["job_order"] = new_order
    save_session(session)


def delete_job_files(session_id: str, job_id: str) -> None:
    delete_job_payload(session_id, job_id)
    shutil.rmtree(job_dir(session_id, job_id), ignore_errors=True)


def delete_session_files(session_id: str) -> None:
    delete_session_payload(session_id)
    shutil.rmtree(session_dir(session_id), ignore_errors=True)


def session_is_expired(session_payload: dict, retention_days: int) -> bool:
    last_seen = parse_iso(session_payload.get("last_accessed_at")) or parse_iso(session_payload.get("updated_at"))
    if not last_seen:
        return False
    current = parse_iso(now_iso())
    if not current:
        return False
    return (current - last_seen).days >= retention_days


def list_existing_inputs(session_id: str) -> list[Path]:
    root = session_dir(session_id)
    if not root.exists():
        return []
    paths = [
        path
        for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in INPUT_EXTENSIONS
    ]
    return sorted(paths, key=lambda path: path.stat().st_mtime, reverse=True)
