#!/usr/bin/env python3
"""Import legacy MolScout JSON metadata into PostgreSQL."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
APP_DIR = PROJECT_ROOT / "app"
for entry in (str(APP_DIR), str(PROJECT_ROOT)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

CURRENT_DATA_DIR = PROJECT_ROOT / "data"
LEGACY_ACTIVE_STATUSES = {"running", "cancel_requested"}
QUEUE_IMPORTABLE_STATUSES = {"queued"}
DATA_ROOT_CHILDREN = {"sessions", "queue", "archives", "logs", "tmp", "locks"}


class MigrationError(RuntimeError):
    """Raised when legacy data cannot be migrated safely."""


@dataclass
class LegacyBundle:
    app_state: dict[str, Any]
    queue_state: dict[str, Any]
    sessions: list[dict[str, Any]] = field(default_factory=list)
    jobs: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    source_files: list[str] = field(default_factory=list)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def default_app_state() -> dict[str, Any]:
    return {
        "created_at": now_iso(),
        "last_cleanup_at": None,
        "selected_session_id": None,
    }


def default_queue_state() -> dict[str, Any]:
    return {
        "updated_at": now_iso(),
        "running_job_id": None,
        "jobs": [],
    }


def load_default_pyscf_config() -> dict[str, Any]:
    path = PROJECT_ROOT / "core" / "pyscf_config.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MigrationError(f"Cannot read default PySCF configuration: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise MigrationError(f"Default PySCF configuration is not a JSON object: {path}")
    return payload


def default_session_payload(session_id: str) -> dict[str, Any]:
    timestamp = now_iso()
    return {
        "session_id": session_id,
        "owner_label": "anonymous",
        "created_at": timestamp,
        "updated_at": timestamp,
        "last_accessed_at": timestamp,
        "notes": "",
        "job_order": [],
    }


def default_job_payload(session_id: str, job_id: str, data_dir: Path) -> dict[str, Any]:
    timestamp = now_iso()
    job_root = data_dir / "sessions" / session_id / "jobs" / job_id
    return {
        "session_id": session_id,
        "job_id": job_id,
        "name": job_id,
        "workflow": "",
        "script_name": "",
        "status": "queued",
        "queue_position": None,
        "created_at": timestamp,
        "updated_at": timestamp,
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


def read_json_object(path: Path, *, required: bool) -> dict[str, Any] | None:
    if not path.exists():
        if required:
            raise MigrationError(f"Required JSON file is missing: {path}")
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise MigrationError(f"Cannot read JSON file: {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise MigrationError(
            f"Invalid JSON file: {path}: line {exc.lineno}, column {exc.colno}: {exc.msg}"
        ) from exc
    if not isinstance(payload, dict):
        raise MigrationError(f"Expected a JSON object: {path}")
    return payload


def remap_path_string(value: str, data_dir: Path) -> str:
    """Map absolute legacy data paths to the current data directory."""
    path = Path(value)
    if not path.is_absolute():
        return value

    parts = path.parts
    for index, part in enumerate(parts):
        if part not in DATA_ROOT_CHILDREN:
            continue
        if index == 0 or parts[index - 1] != "data":
            continue
        if part == "sessions" and index + 1 >= len(parts):
            continue
        return str(data_dir.joinpath(*parts[index:]))
    return value


def remap_paths(value: Any, data_dir: Path) -> Any:
    if isinstance(value, str):
        return remap_path_string(value, data_dir)
    if isinstance(value, list):
        return [remap_paths(item, data_dir) for item in value]
    if isinstance(value, dict):
        return {key: remap_paths(item, data_dir) for key, item in value.items()}
    return value


def normalize_session(
    path: Path,
    session_id: str,
    data_dir: Path,
    warnings: list[str],
) -> dict[str, Any]:
    source = read_json_object(path, required=True)
    assert source is not None
    source_id = source.get("session_id")
    if source_id and str(source_id) != session_id:
        raise MigrationError(
            f"Session ID mismatch: directory={session_id!r}, payload={source_id!r}, file={path}"
        )

    payload = default_session_payload(session_id)
    payload.update(remap_paths(source, data_dir))
    payload["session_id"] = session_id

    job_order = payload.get("job_order", [])
    if not isinstance(job_order, list):
        warnings.append(f"{path}: job_order was not a list and was reset.")
        payload["job_order"] = []
    else:
        payload["job_order"] = [str(item) for item in job_order]

    config = payload.get("pyscf_config")
    if not isinstance(config, dict) or not config:
        config_path = path.parent / "pyscf_config.json"
        config_payload = read_json_object(config_path, required=False)
        if config_payload:
            payload["pyscf_config"] = config_payload
        else:
            payload["pyscf_config"] = load_default_pyscf_config()
            warnings.append(f"{path}: PySCF configuration was missing; the project default was used.")
    return payload


def normalize_job(
    path: Path,
    session_id: str,
    job_id: str,
    data_dir: Path,
    warnings: list[str],
) -> dict[str, Any]:
    source = read_json_object(path, required=True)
    assert source is not None

    source_session_id = source.get("session_id")
    source_job_id = source.get("job_id")
    if source_session_id and str(source_session_id) != session_id:
        raise MigrationError(
            f"Job session ID mismatch: directory={session_id!r}, payload={source_session_id!r}, file={path}"
        )
    if source_job_id and str(source_job_id) != job_id:
        raise MigrationError(
            f"Job ID mismatch: directory={job_id!r}, payload={source_job_id!r}, file={path}"
        )

    payload = default_job_payload(session_id, job_id, data_dir)
    payload.update(remap_paths(source, data_dir))
    payload["session_id"] = session_id
    payload["job_id"] = job_id

    status = str(payload.get("status") or "queued")
    if status in LEGACY_ACTIVE_STATUSES:
        payload["status"] = "failed"
        payload["completion_reason"] = "legacy_import_interrupted"
        payload["status_message"] = "Imported from legacy storage after an interrupted active job."
        payload["pid"] = None
        payload["cancel_requested_at"] = None
        payload["finished_at"] = payload.get("finished_at") or now_iso()
        warnings.append(f"{path}: status {status!r} was converted to 'failed'.")

    payload["artifact_cataloged_at"] = payload.get("artifact_cataloged_at")
    try:
        payload["artifact_catalog_count"] = int(payload.get("artifact_catalog_count") or 0)
        payload["artifact_catalog_marked_missing"] = int(
            payload.get("artifact_catalog_marked_missing") or 0
        )
    except (TypeError, ValueError):
        warnings.append(f"{path}: invalid artifact catalog counters were reset.")
        payload["artifact_catalog_count"] = 0
        payload["artifact_catalog_marked_missing"] = 0
    payload["artifact_catalog_error"] = str(payload.get("artifact_catalog_error") or "")
    return payload


def normalize_queue(
    source: dict[str, Any] | None,
    jobs_by_key: dict[tuple[str, str], dict[str, Any]],
    warnings: list[str],
) -> dict[str, Any]:
    payload = default_queue_state()
    if source:
        payload.update(source)

    raw_jobs = payload.get("jobs", [])
    if not isinstance(raw_jobs, list):
        warnings.append("queue.json: jobs was not a list and was reset.")
        raw_jobs = []

    normalized_jobs: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for index, item in enumerate(raw_jobs):
        if not isinstance(item, dict):
            warnings.append(f"queue.json: entry {index} was not an object and was skipped.")
            continue
        session_id = str(item.get("session_id") or "")
        job_id = str(item.get("job_id") or "")
        key = (session_id, job_id)
        if not session_id or not job_id:
            warnings.append(f"queue.json: entry {index} has no session_id/job_id and was skipped.")
            continue
        if key not in jobs_by_key:
            warnings.append(
                f"queue.json: {session_id}/{job_id} has no imported job record and was skipped."
            )
            continue
        if key in seen:
            warnings.append(f"queue.json: duplicate {session_id}/{job_id} was skipped.")
            continue

        job_status = str(jobs_by_key[key].get("status") or "")
        item_status = str(item.get("status") or job_status)
        if job_status not in QUEUE_IMPORTABLE_STATUSES or item_status not in QUEUE_IMPORTABLE_STATUSES:
            warnings.append(
                f"queue.json: {session_id}/{job_id} with status {item_status!r} was removed from the queue."
            )
            continue

        normalized = dict(item)
        normalized["session_id"] = session_id
        normalized["job_id"] = job_id
        normalized["queue_key"] = f"{session_id}:{job_id}"
        normalized["status"] = "queued"
        normalized_jobs.append(normalized)
        seen.add(key)

    payload["jobs"] = normalized_jobs
    payload["running_job_id"] = None
    payload["updated_at"] = now_iso()
    return payload


def discover_legacy_data(data_dir: Path) -> LegacyBundle:
    data_dir = data_dir.resolve()
    if not data_dir.exists() or not data_dir.is_dir():
        raise MigrationError(f"Data directory does not exist: {data_dir}")

    warnings: list[str] = []
    source_files: list[str] = []

    app_state_path = data_dir / "app_state.json"
    app_state_source = read_json_object(app_state_path, required=False)
    app_state = default_app_state()
    if app_state_source:
        app_state.update(remap_paths(app_state_source, data_dir))
        source_files.append(str(app_state_path))
    else:
        warnings.append(f"Legacy app state was not found: {app_state_path}")

    sessions: list[dict[str, Any]] = []
    jobs: list[dict[str, Any]] = []
    jobs_by_session: dict[str, list[str]] = {}
    sessions_root = data_dir / "sessions"

    for session_path in sorted(sessions_root.glob("*/session.json")):
        session_id = session_path.parent.name
        session = normalize_session(session_path, session_id, data_dir, warnings)
        sessions.append(session)
        source_files.append(str(session_path))

        discovered_job_ids: list[str] = []
        jobs_root = session_path.parent / "jobs"
        for job_path in sorted(jobs_root.glob("*/job.json")):
            job_id = job_path.parent.name
            jobs.append(normalize_job(job_path, session_id, job_id, data_dir, warnings))
            discovered_job_ids.append(job_id)
            source_files.append(str(job_path))
        jobs_by_session[session_id] = discovered_job_ids

    if not sessions:
        warnings.append(f"No legacy session.json files were found below: {sessions_root}")

    known_session_dirs = {str(session["session_id"]) for session in sessions}
    orphan_job_files = [
        path
        for path in sorted(sessions_root.glob("*/jobs/*/job.json"))
        if path.parents[2].name not in known_session_dirs
    ]
    if orphan_job_files:
        joined = ", ".join(str(path) for path in orphan_job_files[:5])
        if len(orphan_job_files) > 5:
            joined += f", ... ({len(orphan_job_files)} total)"
        raise MigrationError(f"Job metadata exists without session.json: {joined}")

    session_ids = {str(session["session_id"]) for session in sessions}
    selected_session_id = app_state.get("selected_session_id")
    if selected_session_id and str(selected_session_id) not in session_ids:
        warnings.append(
            f"app_state.json: selected_session_id {selected_session_id!r} was not imported and was cleared."
        )
        app_state["selected_session_id"] = None

    for session in sessions:
        session_id = str(session["session_id"])
        discovered = jobs_by_session.get(session_id, [])
        discovered_set = set(discovered)
        requested_order = [str(item) for item in session.get("job_order", [])]
        missing = [item for item in requested_order if item not in discovered_set]
        if missing:
            warnings.append(
                f"{session_id}: job_order referenced missing jobs: {', '.join(missing)}"
            )
        order = [item for item in requested_order if item in discovered_set]
        order.extend(item for item in discovered if item not in order)
        session["job_order"] = order

    jobs_by_key = {
        (str(job["session_id"]), str(job["job_id"])): job
        for job in jobs
    }
    queue_path = data_dir / "queue" / "queue.json"
    queue_source = read_json_object(queue_path, required=False)
    if queue_source:
        source_files.append(str(queue_path))
    else:
        warnings.append(f"Legacy queue state was not found: {queue_path}")
    queue_state = normalize_queue(queue_source, jobs_by_key, warnings)
    imported_queue_keys = {
        (str(item["session_id"]), str(item["job_id"]))
        for item in queue_state.get("jobs", [])
    }
    for job in jobs:
        key = (str(job["session_id"]), str(job["job_id"]))
        if str(job.get("status") or "") != "queued" or key in imported_queue_keys:
            continue
        job["status"] = "failed"
        job["completion_reason"] = "legacy_import_queue_missing"
        job["status_message"] = (
            "Imported from legacy storage without a matching queued entry; automatic execution was disabled."
        )
        job["pid"] = None
        job["finished_at"] = job.get("finished_at") or now_iso()
        warnings.append(
            f"{job['session_id']}/{job['job_id']}: queued status had no matching queue entry and was converted to 'failed'."
        )

    if not source_files:
        raise MigrationError(f"No legacy metadata JSON files were found in: {data_dir}")

    return LegacyBundle(
        app_state=app_state,
        queue_state=queue_state,
        sessions=sessions,
        jobs=jobs,
        warnings=warnings,
        source_files=source_files,
    )


def connect_database(database_url: str | None, *, autocommit: bool = False):
    try:
        import psycopg
    except ImportError as exc:
        raise MigrationError(
            "psycopg is not installed. Run this script inside the MolScout application container."
        ) from exc
    if database_url:
        return psycopg.connect(database_url, autocommit=autocommit)
    return psycopg.connect(autocommit=autocommit)


def ensure_schema(database_url: str | None) -> None:
    try:
        from app_core import database
    except ImportError as exc:
        raise MigrationError(f"Cannot import MolScout database module: {exc}") from exc

    if database_url is None:
        database.ensure_database()
        return

    statements = getattr(database, "_SCHEMA_STATEMENTS", None)
    if not statements:
        raise MigrationError("MolScout database schema statements are unavailable.")
    with connect_database(database_url, autocommit=True) as connection:
        with connection.cursor() as cursor:
            for statement in statements:
                cursor.execute(statement)


def inspect_database(database_url: str | None) -> dict[str, Any]:
    ensure_schema(database_url)
    with connect_database(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM sessions")
            sessions = int(cursor.fetchone()[0])
            cursor.execute("SELECT COUNT(*) FROM jobs")
            jobs = int(cursor.fetchone()[0])
            cursor.execute("SELECT COUNT(*) FROM artifacts")
            artifacts = int(cursor.fetchone()[0])
            cursor.execute("SELECT payload FROM queue_state WHERE singleton = TRUE")
            queue_row = cursor.fetchone()
    queue_jobs = []
    if queue_row and isinstance(queue_row[0], dict):
        queue_jobs = list(queue_row[0].get("jobs", []))
    return {
        "sessions": sessions,
        "jobs": jobs,
        "artifacts": artifacts,
        "queue_jobs": len(queue_jobs),
    }


def database_is_empty(summary: dict[str, Any]) -> bool:
    return all(int(summary.get(key, 0)) == 0 for key in ("sessions", "jobs", "artifacts", "queue_jobs"))


def import_bundle(bundle: LegacyBundle, database_url: str | None) -> None:
    try:
        from psycopg.types.json import Jsonb
    except ImportError as exc:
        raise MigrationError(
            "psycopg is not installed. Run this script inside the MolScout application container."
        ) from exc

    with connect_database(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "LOCK TABLE app_state, sessions, jobs, queue_state, artifacts IN EXCLUSIVE MODE"
            )
            cursor.execute("SELECT COUNT(*) FROM sessions")
            session_count = int(cursor.fetchone()[0])
            cursor.execute("SELECT COUNT(*) FROM jobs")
            job_count = int(cursor.fetchone()[0])
            cursor.execute("SELECT COUNT(*) FROM artifacts")
            artifact_count = int(cursor.fetchone()[0])
            cursor.execute("SELECT payload FROM queue_state WHERE singleton = TRUE FOR UPDATE")
            queue_row = cursor.fetchone()
            queue_jobs = list(queue_row[0].get("jobs", [])) if queue_row and isinstance(queue_row[0], dict) else []
            if any((session_count, job_count, artifact_count, len(queue_jobs))):
                raise MigrationError(
                    "Database is not empty. Migration was aborted before writing any metadata."
                )

            cursor.execute(
                """
                INSERT INTO app_state (singleton, payload)
                VALUES (TRUE, %s)
                ON CONFLICT (singleton) DO UPDATE SET payload = EXCLUDED.payload
                """,
                (Jsonb(bundle.app_state),),
            )

            for session in bundle.sessions:
                cursor.execute(
                    "INSERT INTO sessions (session_id, payload) VALUES (%s, %s)",
                    (session["session_id"], Jsonb(session)),
                )

            for job in bundle.jobs:
                cursor.execute(
                    "INSERT INTO jobs (session_id, job_id, payload) VALUES (%s, %s, %s)",
                    (job["session_id"], job["job_id"], Jsonb(job)),
                )

            cursor.execute(
                """
                INSERT INTO queue_state (singleton, payload)
                VALUES (TRUE, %s)
                ON CONFLICT (singleton) DO UPDATE SET payload = EXCLUDED.payload
                """,
                (Jsonb(bundle.queue_state),),
            )


def active_queue_worker(data_dir: Path) -> bool:
    pid_path = data_dir / "queue" / "worker.pid"
    try:
        pid = int(pid_path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return False
    try:
        command = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return False
    return b"app_core.queue_worker" in command


def build_summary(
    bundle: LegacyBundle,
    data_dir: Path,
    database_summary: dict[str, Any],
    *,
    dry_run: bool,
) -> dict[str, Any]:
    statuses: dict[str, int] = {}
    for job in bundle.jobs:
        status = str(job.get("status") or "unknown")
        statuses[status] = statuses.get(status, 0) + 1
    return {
        "data_directory": str(data_dir.resolve()),
        "source_files": len(bundle.source_files),
        "sessions_found": len(bundle.sessions),
        "jobs_found": len(bundle.jobs),
        "job_statuses": statuses,
        "queued_jobs_importable": len(bundle.queue_state.get("jobs", [])),
        "warnings": bundle.warnings,
        "database_before": database_summary,
        "dry_run": dry_run,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Import legacy MolScout session, job, queue, and app-state JSON into PostgreSQL."
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=CURRENT_DATA_DIR,
        help=f"Legacy data directory (default: {CURRENT_DATA_DIR}).",
    )
    parser.add_argument(
        "--database-url",
        help="Optional PostgreSQL connection URL. PG* environment variables are used by default.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate files and database state without writing metadata rows.",
    )
    parser.add_argument(
        "--index-artifacts",
        action="store_true",
        help="Build the artifact catalog after a successful import.",
    )
    parser.add_argument(
        "--allow-running-worker",
        action="store_true",
        help="Allow queued legacy jobs to be imported while a queue worker is active.",
    )
    return parser.parse_args()


def run_artifact_index(data_dir: Path) -> dict[str, Any]:
    if data_dir.resolve() != CURRENT_DATA_DIR.resolve():
        raise MigrationError(
            "--index-artifacts requires --data-dir to match the application's current data directory: "
            f"{CURRENT_DATA_DIR}"
        )
    try:
        from app_core.artifact_manager import scan_all_artifacts
    except ImportError as exc:
        raise MigrationError(f"Cannot import artifact catalog module: {exc}") from exc
    return scan_all_artifacts(source="legacy_import", dry_run=False)


def main() -> int:
    args = parse_args()
    try:
        bundle = discover_legacy_data(args.data_dir)
        database_summary = inspect_database(args.database_url)
        summary = build_summary(
            bundle,
            args.data_dir,
            database_summary,
            dry_run=args.dry_run,
        )
        worker_active = active_queue_worker(args.data_dir)
        summary["queue_worker_active"] = worker_active

        if not database_is_empty(database_summary):
            summary["error"] = "Database is not empty."
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 2

        if args.dry_run:
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 0

        if bundle.queue_state.get("jobs") and worker_active and not args.allow_running_worker:
            summary["error"] = (
                "A queue worker is active and queued legacy jobs would become runnable. "
                "Stop the application container or pass --allow-running-worker explicitly."
            )
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 2

        import_bundle(bundle, args.database_url)
        summary["imported"] = {
            "sessions": len(bundle.sessions),
            "jobs": len(bundle.jobs),
            "queue_jobs": len(bundle.queue_state.get("jobs", [])),
            "app_state": 1,
            "queue_state": 1,
        }

        if args.index_artifacts:
            artifact_summary = run_artifact_index(args.data_dir)
            summary["artifact_index"] = artifact_summary
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 1 if artifact_summary.get("errors") else 0

        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    except MigrationError as exc:
        print(f"Migration error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Migration interrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
