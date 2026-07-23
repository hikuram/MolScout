"""Write stable job metadata for configuration and input file tracking."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, Mapping

from .session_manager import job_dir


def relative_job_path(path: str | Path | None, job_root: Path) -> str | None:
    """Return a portable path relative to the job directory when possible."""
    if path is None:
        return None
    candidate = Path(path)
    try:
        return str(candidate.resolve().relative_to(job_root.resolve()))
    except (OSError, ValueError):
        return str(candidate)


def manifest_input(
    *,
    role: str,
    original_name: str,
    stored_path: str | Path,
    job_root: Path,
) -> dict[str, str]:
    """Build one input entry for a job manifest."""
    return {
        "role": role,
        "original_name": original_name,
        "stored_path": relative_job_path(stored_path, job_root) or str(stored_path),
        "format": Path(stored_path).suffix.lower().lstrip("."),
    }


def write_job_manifest(
    job: Mapping[str, object],
    *,
    submission_source: str,
    config_original_name: str | None,
    config_stored_path: str | Path | None,
    inputs: Iterable[Mapping[str, object]],
    runtime_config_path: str | Path | None = None,
    source_job: Mapping[str, object] | None = None,
    pyscf_config: Mapping[str, object] | None = None,
) -> Path:
    """Write job_manifest.json and return its path."""
    session_id = str(job["session_id"])
    job_id = str(job["job_id"])
    job_root = job_dir(session_id, job_id)
    manifest_path = job_root / "job_manifest.json"
    config_payload = {
        "original_name": config_original_name,
        "stored_path": relative_job_path(config_stored_path, job_root),
        "resolved_path": "run_output/resolved_config.json",
    }
    if runtime_config_path is not None:
        config_payload["runtime_path"] = relative_job_path(runtime_config_path, job_root)

    payload = {
        "version": 1,
        "job_id": job_id,
        "job_name": str(job.get("name") or job_id),
        "workflow": str(job.get("workflow") or ""),
        "created_at": job.get("created_at"),
        "submission_source": submission_source,
        "note": str(job.get("notes") or ""),
        "config": config_payload,
        "inputs": [dict(item) for item in inputs],
    }
    if source_job:
        payload["source_job"] = dict(source_job)
    if pyscf_config:
        payload["pyscf_config"] = dict(pyscf_config)
    manifest_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest_path
