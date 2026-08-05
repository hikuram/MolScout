"""Catalog selected filesystem artifacts without moving their contents into PostgreSQL."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .database import (
    list_artifact_records,
    mark_artifacts_missing,
    upsert_artifact_records,
)
from .paths import DATA_DIR, SESSIONS_DIR
from .session_manager import job_dir, list_jobs, list_sessions, session_dir

STRUCTURE_EXTENSIONS = {".xyz", ".extxyz", ".mol", ".sdf", ".pdb", ".cif", ".molden"}
TRAJECTORY_EXTENSIONS = {".traj"}
TABLE_EXTENSIONS = {".csv", ".tsv"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".svg"}
TEXT_EXTENSIONS = {".log", ".out", ".err", ".txt"}
CONFIG_EXTENSIONS = {".json", ".yaml", ".yml", ".toml"}
DATA_EXTENSIONS = {".dat", ".cube", ".npz", ".npy"}
ARCHIVE_EXTENSIONS = {".zip"}
CATALOG_EXTENSIONS = (
    STRUCTURE_EXTENSIONS
    | TRAJECTORY_EXTENSIONS
    | TABLE_EXTENSIONS
    | IMAGE_EXTENSIONS
    | TEXT_EXTENSIONS
    | CONFIG_EXTENSIONS
    | DATA_EXTENSIONS
    | ARCHIVE_EXTENSIONS
)

LEGACY_METADATA_NAMES = {"session.json", "job.json", "queue.json", "app_state.json"}
TECHNICAL_NAMES = {"worker.pid", "worker.lock", ".gitignore"}
TECHNICAL_SUFFIXES = {".exit", ".pid", ".lock", ".tmp"}


def artifact_path(relative_path: str) -> Path:
    """Resolve one catalog path under DATA_DIR and reject path traversal."""
    root = DATA_DIR.resolve()
    candidate = (root / relative_path).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError(f"Artifact path is outside data directory: {relative_path}")
    return candidate


def is_catalog_candidate(path: Path) -> bool:
    if path.is_symlink() or not path.is_file():
        return False
    if path.name in LEGACY_METADATA_NAMES or path.name in TECHNICAL_NAMES:
        return False
    if path.name.startswith("."):
        return False
    if path.name.endswith(".runtime.json"):
        return False
    if path.suffix.lower() in TECHNICAL_SUFFIXES:
        return False
    return path.suffix.lower() in CATALOG_EXTENSIONS


def artifact_type_for(path: Path) -> str:
    extension = path.suffix.lower()
    if extension in TRAJECTORY_EXTENSIONS:
        return "trajectory"
    if extension in STRUCTURE_EXTENSIONS:
        return "structure"
    if extension in TABLE_EXTENSIONS:
        return "table"
    if extension in IMAGE_EXTENSIONS:
        return "image"
    if extension in TEXT_EXTENSIONS:
        return "log_or_text"
    if extension in CONFIG_EXTENSIONS:
        return "configuration"
    if extension in ARCHIVE_EXTENSIONS:
        return "archive"
    return "scientific_data"


def artifact_role_for(path: Path, *, job_root: Path | None = None, result_name: str = "") -> str:
    name = path.name.lower()
    extension = path.suffix.lower()
    relative_parts: tuple[str, ...] = ()
    if job_root is not None:
        try:
            relative_parts = tuple(part.lower() for part in path.relative_to(job_root).parts)
        except ValueError:
            relative_parts = ()

    if name == "job_manifest.json":
        return "manifest"
    if name in {"submitted_config.json", "resolved_config.json", "pyscf_config.json"}:
        return "configuration"
    if relative_parts and relative_parts[0] == "inputs":
        return "input"
    if result_name and name == result_name.lower():
        return "primary_result"
    if name == "init_path.traj":
        return "initial_path"
    if name == "irc.traj":
        return "irc_trajectory"
    if name == "optpoints.traj":
        return "optimization_points"
    if name.endswith("_tsopt.traj"):
        return "transition_state_trajectory"
    if name.endswith("_opt.traj"):
        return "optimized_trajectory"
    if extension in TRAJECTORY_EXTENSIONS:
        return "trajectory"
    if name.endswith("_opt.xyz"):
        return "optimized_structure"
    if extension in STRUCTURE_EXTENSIONS:
        return "structure"
    if extension in TABLE_EXTENSIONS:
        return "result_table"
    if extension in IMAGE_EXTENSIONS:
        return "figure"
    if extension in TEXT_EXTENSIONS:
        return "log"
    if extension in CONFIG_EXTENSIONS:
        return "configuration"
    if extension in ARCHIVE_EXTENSIONS:
        return "archive"
    return "data"


def _portable_relative_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except (OSError, ValueError):
        return path.relative_to(root).as_posix()


def _manifest_metadata(job_root: Path) -> tuple[dict[str, dict], dict]:
    manifest_path = job_root / "job_manifest.json"
    if not manifest_path.exists():
        return {}, {}
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}, {}

    entries: dict[str, dict] = {}
    for item in manifest.get("inputs", []):
        stored_path = str(item.get("stored_path") or "")
        if not stored_path:
            continue
        entries[Path(stored_path).as_posix()] = {
            "manifest_role": item.get("role", ""),
            "original_name": item.get("original_name", ""),
            "input_format": item.get("format", ""),
        }

    config = manifest.get("config", {})
    if isinstance(config, dict):
        for key in ("stored_path", "runtime_path", "resolved_path"):
            stored_path = str(config.get(key) or "")
            if stored_path:
                entries[Path(stored_path).as_posix()] = {
                    "manifest_role": f"config_{key.replace('_path', '')}",
                    "original_name": config.get("original_name", ""),
                }
    return entries, manifest


def _record_for_path(
    path: Path,
    *,
    session_id: str,
    job_id: str | None,
    scope_root: Path,
    source: str,
    result_name: str = "",
    manifest_entries: dict[str, dict] | None = None,
    manifest: dict | None = None,
) -> dict:
    stat = path.stat()
    scope_relative = _portable_relative_path(path, scope_root)
    data_relative = _portable_relative_path(path, DATA_DIR)
    metadata = {
        "source": source,
        "scope_relative_path": scope_relative,
        "top_level_directory": scope_relative.split("/", 1)[0] if "/" in scope_relative else "",
    }
    if manifest_entries and scope_relative in manifest_entries:
        metadata.update(manifest_entries[scope_relative])
    if path.name == "job_manifest.json" and manifest:
        metadata.update({
            "submission_source": manifest.get("submission_source", ""),
            "manifest_version": manifest.get("version"),
        })

    return {
        "relative_path": data_relative,
        "session_id": session_id,
        "job_id": job_id,
        "filename": path.name,
        "artifact_type": artifact_type_for(path),
        "artifact_role": artifact_role_for(path, job_root=scope_root if job_id else None, result_name=result_name),
        "extension": path.suffix.lower(),
        "size_bytes": stat.st_size,
        "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
        "metadata": metadata,
    }


def discover_job_artifacts(session_id: str, job: dict, *, source: str) -> list[dict]:
    job_id = str(job["job_id"])
    root = job_dir(session_id, job_id)
    if not root.exists():
        return []
    manifest_entries, manifest = _manifest_metadata(root)
    records = []
    for path in sorted(root.rglob("*")):
        if not is_catalog_candidate(path):
            continue
        records.append(
            _record_for_path(
                path,
                session_id=session_id,
                job_id=job_id,
                scope_root=root,
                source=source,
                result_name=str(job.get("result_name") or ""),
                manifest_entries=manifest_entries,
                manifest=manifest,
            )
        )
    return records


def discover_session_artifacts(session_id: str, *, source: str) -> list[dict]:
    root = session_dir(session_id)
    if not root.exists():
        return []
    jobs_root = root / "jobs"
    records = []
    for path in sorted(root.rglob("*")):
        if jobs_root == path or jobs_root in path.parents:
            continue
        if not is_catalog_candidate(path):
            continue
        records.append(
            _record_for_path(
                path,
                session_id=session_id,
                job_id=None,
                scope_root=root,
                source=source,
            )
        )
    return records


def scan_job_artifacts(
    session_id: str,
    job: dict,
    *,
    source: str = "manual_scan",
    dry_run: bool = False,
) -> dict:
    records = discover_job_artifacts(session_id, job, source=source)
    if not dry_run:
        upsert_artifact_records(records)
        missing_count = mark_artifacts_missing(
            session_id,
            str(job["job_id"]),
            [item["relative_path"] for item in records],
        )
    else:
        missing_count = 0
    return {
        "session_id": session_id,
        "job_id": str(job["job_id"]),
        "artifact_count": len(records),
        "marked_missing": missing_count,
    }


def scan_session_artifacts(
    session_id: str,
    *,
    source: str = "manual_scan",
    dry_run: bool = False,
) -> dict:
    records = discover_session_artifacts(session_id, source=source)
    if not dry_run:
        upsert_artifact_records(records)
        missing_count = mark_artifacts_missing(
            session_id,
            None,
            [item["relative_path"] for item in records],
        )
    else:
        missing_count = 0
    return {
        "session_id": session_id,
        "artifact_count": len(records),
        "marked_missing": missing_count,
    }


def scan_all_artifacts(*, source: str = "full_scan", dry_run: bool = False) -> dict:
    """Catalog eligible artifacts for every session and job known to PostgreSQL."""
    summary = {
        "sessions_scanned": 0,
        "jobs_scanned": 0,
        "artifacts_found": 0,
        "marked_missing": 0,
        "errors": [],
        "dry_run": dry_run,
    }
    for session in list_sessions():
        session_id = str(session["session_id"])
        summary["sessions_scanned"] += 1
        try:
            result = scan_session_artifacts(session_id, source=source, dry_run=dry_run)
            summary["artifacts_found"] += result["artifact_count"]
            summary["marked_missing"] += result["marked_missing"]
        except (OSError, ValueError) as exc:
            summary["errors"].append(f"{session_id}: {exc}")

        for job in list_jobs(session_id):
            summary["jobs_scanned"] += 1
            try:
                result = scan_job_artifacts(session_id, job, source=source, dry_run=dry_run)
                summary["artifacts_found"] += result["artifact_count"]
                summary["marked_missing"] += result["marked_missing"]
            except (OSError, ValueError) as exc:
                summary["errors"].append(f"{session_id}/{job['job_id']}: {exc}")
    return summary


def _all_discovered_records(source: str = "diagnostic") -> tuple[list[dict], list[dict]]:
    records: list[dict] = []
    issues: list[dict] = []
    for session in list_sessions():
        session_id = str(session["session_id"])
        try:
            records.extend(discover_session_artifacts(session_id, source=source))
        except (OSError, ValueError) as exc:
            issues.append({
                "issue": "session_scan_error",
                "session_id": session_id,
                "job_id": "",
                "relative_path": f"sessions/{session_id}",
                "detail": str(exc),
            })
        for job in list_jobs(session_id):
            job_id = str(job["job_id"])
            try:
                records.extend(discover_job_artifacts(session_id, job, source=source))
            except (OSError, ValueError) as exc:
                issues.append({
                    "issue": "job_scan_error",
                    "session_id": session_id,
                    "job_id": job_id,
                    "relative_path": f"sessions/{session_id}/jobs/{job_id}",
                    "detail": str(exc),
                })
    return records, issues


def diagnose_artifact_catalog() -> dict:
    """Compare PostgreSQL catalog entries with the current filesystem."""
    registered = list_artifact_records()
    registered_by_path = {item["relative_path"]: item for item in registered}
    issues: list[dict] = []

    for item in registered:
        try:
            path = artifact_path(item["relative_path"])
        except ValueError as exc:
            issues.append({
                "issue": "invalid_catalog_path",
                "session_id": item["session_id"],
                "job_id": item.get("job_id") or "",
                "relative_path": item["relative_path"],
                "detail": str(exc),
            })
            continue
        if not path.exists():
            issues.append({
                "issue": "registered_file_missing",
                "session_id": item["session_id"],
                "job_id": item.get("job_id") or "",
                "relative_path": item["relative_path"],
                "detail": "DB record exists, but the file is absent.",
            })
            continue
        if not path.is_file():
            issues.append({
                "issue": "registered_path_not_file",
                "session_id": item["session_id"],
                "job_id": item.get("job_id") or "",
                "relative_path": item["relative_path"],
                "detail": "Catalog path no longer points to a regular file.",
            })
            continue
        current_size = path.stat().st_size
        if current_size != int(item["size_bytes"]):
            issues.append({
                "issue": "size_changed",
                "session_id": item["session_id"],
                "job_id": item.get("job_id") or "",
                "relative_path": item["relative_path"],
                "detail": f"DB={item['size_bytes']} bytes, file={current_size} bytes",
            })
        if item.get("availability_status") == "missing":
            issues.append({
                "issue": "file_returned_after_missing",
                "session_id": item["session_id"],
                "job_id": item.get("job_id") or "",
                "relative_path": item["relative_path"],
                "detail": "File exists although the catalog status is missing; rescan will restore it.",
            })

    discovered, discovery_issues = _all_discovered_records()
    issues.extend(discovery_issues)
    for item in discovered:
        if item["relative_path"] not in registered_by_path:
            issues.append({
                "issue": "eligible_file_unregistered",
                "session_id": item["session_id"],
                "job_id": item.get("job_id") or "",
                "relative_path": item["relative_path"],
                "detail": f"{item['artifact_type']} / {item['artifact_role']}",
            })

    sessions = {str(item["session_id"]): item for item in list_sessions()}
    jobs_by_session = {
        session_id: {str(job["job_id"]): job for job in list_jobs(session_id)}
        for session_id in sessions
    }

    for session_id in sessions:
        root = session_dir(session_id)
        if not root.exists():
            issues.append({
                "issue": "session_directory_missing",
                "session_id": session_id,
                "job_id": "",
                "relative_path": _portable_relative_path(root, DATA_DIR),
                "detail": "Session exists in DB, but its directory is absent.",
            })
        for job_id in jobs_by_session[session_id]:
            root = job_dir(session_id, job_id)
            if not root.exists():
                issues.append({
                    "issue": "job_directory_missing",
                    "session_id": session_id,
                    "job_id": job_id,
                    "relative_path": _portable_relative_path(root, DATA_DIR),
                    "detail": "Job exists in DB, but its directory is absent.",
                })

    if SESSIONS_DIR.exists():
        for session_root in sorted(path for path in SESSIONS_DIR.iterdir() if path.is_dir()):
            session_id = session_root.name
            if session_id not in sessions:
                issues.append({
                    "issue": "orphan_session_directory",
                    "session_id": session_id,
                    "job_id": "",
                    "relative_path": _portable_relative_path(session_root, DATA_DIR),
                    "detail": "Directory has no matching session record.",
                })
                continue
            jobs_root = session_root / "jobs"
            if not jobs_root.exists():
                continue
            known_jobs = jobs_by_session.get(session_id, {})
            for job_root in sorted(path for path in jobs_root.iterdir() if path.is_dir()):
                if job_root.name not in known_jobs:
                    issues.append({
                        "issue": "orphan_job_directory",
                        "session_id": session_id,
                        "job_id": job_root.name,
                        "relative_path": _portable_relative_path(job_root, DATA_DIR),
                        "detail": "Directory has no matching job record.",
                    })

    counts: dict[str, int] = {}
    for item in issues:
        counts[item["issue"]] = counts.get(item["issue"], 0) + 1
    return {
        "registered_count": len(registered),
        "eligible_files_found": len(discovered),
        "issue_count": len(issues),
        "issue_counts": counts,
        "issues": issues,
    }
