"""PostgreSQL persistence for application-managed metadata."""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterable

import psycopg
from psycopg.types.json import Jsonb

_SCHEMA_LOCK = threading.Lock()
_SCHEMA_READY = False

_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS app_state (
        singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton),
        payload JSONB NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS sessions (
        session_id TEXT PRIMARY KEY,
        payload JSONB NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS jobs (
        session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
        job_id TEXT NOT NULL,
        payload JSONB NOT NULL,
        PRIMARY KEY (session_id, job_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS queue_state (
        singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton),
        payload JSONB NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS artifacts (
        relative_path TEXT PRIMARY KEY,
        session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
        job_id TEXT,
        filename TEXT NOT NULL,
        artifact_type TEXT NOT NULL,
        artifact_role TEXT NOT NULL,
        extension TEXT NOT NULL,
        size_bytes BIGINT NOT NULL,
        modified_at TIMESTAMPTZ NOT NULL,
        registered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        availability_status TEXT NOT NULL DEFAULT 'available',
        metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
        FOREIGN KEY (session_id, job_id)
            REFERENCES jobs(session_id, job_id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX IF NOT EXISTS artifacts_session_job_idx ON artifacts (session_id, job_id)",
    "CREATE INDEX IF NOT EXISTS artifacts_type_idx ON artifacts (artifact_type)",
    "CREATE INDEX IF NOT EXISTS artifacts_modified_idx ON artifacts (modified_at DESC)",
    "CREATE INDEX IF NOT EXISTS artifacts_availability_idx ON artifacts (availability_status)",
)

_ARTIFACT_COLUMNS = (
    "relative_path",
    "session_id",
    "job_id",
    "filename",
    "artifact_type",
    "artifact_role",
    "extension",
    "size_bytes",
    "modified_at",
    "registered_at",
    "last_seen_at",
    "availability_status",
    "metadata",
)


def _connect(*, autocommit: bool = False) -> psycopg.Connection:
    return psycopg.connect(autocommit=autocommit)


def ensure_database() -> None:
    """Create the application schema once per process."""
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    with _SCHEMA_LOCK:
        if _SCHEMA_READY:
            return
        with _connect(autocommit=True) as connection:
            with connection.cursor() as cursor:
                for statement in _SCHEMA_STATEMENTS:
                    cursor.execute(statement)
        _SCHEMA_READY = True


def read_app_state_payload(default: dict) -> dict:
    ensure_database()
    with _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO app_state (singleton, payload) VALUES (TRUE, %s) ON CONFLICT (singleton) DO NOTHING",
                (Jsonb(default),),
            )
            cursor.execute("SELECT payload FROM app_state WHERE singleton = TRUE")
            row = cursor.fetchone()
    return dict(row[0]) if row else dict(default)


def write_app_state_payload(payload: dict) -> None:
    ensure_database()
    with _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO app_state (singleton, payload)
                VALUES (TRUE, %s)
                ON CONFLICT (singleton) DO UPDATE SET payload = EXCLUDED.payload
                """,
                (Jsonb(payload),),
            )


def read_queue_state_payload(default: dict) -> dict:
    ensure_database()
    with _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO queue_state (singleton, payload) VALUES (TRUE, %s) ON CONFLICT (singleton) DO NOTHING",
                (Jsonb(default),),
            )
            cursor.execute("SELECT payload FROM queue_state WHERE singleton = TRUE")
            row = cursor.fetchone()
    return dict(row[0]) if row else dict(default)


def mutate_queue_state_payload(default: dict, mutator: Callable[[dict], dict | None]) -> dict:
    ensure_database()
    with _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO queue_state (singleton, payload) VALUES (TRUE, %s) ON CONFLICT (singleton) DO NOTHING",
                (Jsonb(default),),
            )
            cursor.execute("SELECT payload FROM queue_state WHERE singleton = TRUE FOR UPDATE")
            row = cursor.fetchone()
            state = dict(row[0]) if row else dict(default)
            new_state = mutator(state) or state
            cursor.execute(
                "UPDATE queue_state SET payload = %s WHERE singleton = TRUE",
                (Jsonb(new_state),),
            )
    return new_state


def list_session_payloads() -> list[dict]:
    ensure_database()
    with _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT payload FROM sessions")
            rows = cursor.fetchall()
    return [dict(row[0]) for row in rows]


def read_session_payload(session_id: str) -> dict | None:
    ensure_database()
    with _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT payload FROM sessions WHERE session_id = %s", (session_id,))
            row = cursor.fetchone()
    return dict(row[0]) if row else None


def write_session_payload(payload: dict) -> None:
    ensure_database()
    with _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO sessions (session_id, payload)
                VALUES (%s, %s)
                ON CONFLICT (session_id) DO UPDATE SET payload = EXCLUDED.payload
                """,
                (payload["session_id"], Jsonb(payload)),
            )


def delete_session_payload(session_id: str) -> None:
    ensure_database()
    with _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM sessions WHERE session_id = %s", (session_id,))


def list_job_payloads(session_id: str) -> list[dict]:
    ensure_database()
    with _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT payload FROM jobs WHERE session_id = %s", (session_id,))
            rows = cursor.fetchall()
    return [dict(row[0]) for row in rows]


def read_job_payload(session_id: str, job_id: str) -> dict | None:
    ensure_database()
    with _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT payload FROM jobs WHERE session_id = %s AND job_id = %s",
                (session_id, job_id),
            )
            row = cursor.fetchone()
    return dict(row[0]) if row else None


def write_job_payload(payload: dict) -> None:
    ensure_database()
    with _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO jobs (session_id, job_id, payload)
                VALUES (%s, %s, %s)
                ON CONFLICT (session_id, job_id) DO UPDATE SET payload = EXCLUDED.payload
                """,
                (payload["session_id"], payload["job_id"], Jsonb(payload)),
            )


def delete_job_payload(session_id: str, job_id: str) -> None:
    ensure_database()
    with _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM jobs WHERE session_id = %s AND job_id = %s",
                (session_id, job_id),
            )


def _artifact_row(row: tuple) -> dict:
    payload = dict(zip(_ARTIFACT_COLUMNS, row, strict=True))
    payload["metadata"] = dict(payload.get("metadata") or {})
    return payload


def upsert_artifact_records(records: Iterable[dict]) -> int:
    """Insert or refresh artifact catalog records."""
    prepared = list(records)
    if not prepared:
        return 0

    values = [
        (
            item["relative_path"],
            item["session_id"],
            item.get("job_id"),
            item["filename"],
            item["artifact_type"],
            item["artifact_role"],
            item["extension"],
            int(item["size_bytes"]),
            item["modified_at"],
            Jsonb(dict(item.get("metadata") or {})),
        )
        for item in prepared
    ]

    ensure_database()
    with _connect() as connection:
        with connection.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO artifacts (
                    relative_path, session_id, job_id, filename,
                    artifact_type, artifact_role, extension,
                    size_bytes, modified_at, metadata,
                    availability_status, last_seen_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'available', NOW())
                ON CONFLICT (relative_path) DO UPDATE SET
                    session_id = EXCLUDED.session_id,
                    job_id = EXCLUDED.job_id,
                    filename = EXCLUDED.filename,
                    artifact_type = EXCLUDED.artifact_type,
                    artifact_role = EXCLUDED.artifact_role,
                    extension = EXCLUDED.extension,
                    size_bytes = EXCLUDED.size_bytes,
                    modified_at = EXCLUDED.modified_at,
                    metadata = EXCLUDED.metadata,
                    availability_status = 'available',
                    last_seen_at = NOW()
                """,
                values,
            )
    return len(prepared)


def mark_artifacts_missing(session_id: str, job_id: str | None, current_paths: Iterable[str]) -> int:
    """Mark catalog entries absent from the latest scan as missing."""
    paths = list(current_paths)
    ensure_database()
    with _connect() as connection:
        with connection.cursor() as cursor:
            if paths:
                cursor.execute(
                    """
                    UPDATE artifacts
                    SET availability_status = 'missing'
                    WHERE session_id = %s
                      AND job_id IS NOT DISTINCT FROM %s
                      AND NOT (relative_path = ANY(%s))
                      AND availability_status <> 'missing'
                    """,
                    (session_id, job_id, paths),
                )
            else:
                cursor.execute(
                    """
                    UPDATE artifacts
                    SET availability_status = 'missing'
                    WHERE session_id = %s
                      AND job_id IS NOT DISTINCT FROM %s
                      AND availability_status <> 'missing'
                    """,
                    (session_id, job_id),
                )
            return cursor.rowcount


def list_artifact_records() -> list[dict]:
    ensure_database()
    with _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT {', '.join(_ARTIFACT_COLUMNS)} FROM artifacts ORDER BY relative_path"
            )
            rows = cursor.fetchall()
    return [_artifact_row(row) for row in rows]


def read_artifact_record(relative_path: str) -> dict | None:
    ensure_database()
    with _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT {', '.join(_ARTIFACT_COLUMNS)} FROM artifacts WHERE relative_path = %s",
                (relative_path,),
            )
            row = cursor.fetchone()
    return _artifact_row(row) if row else None


def artifact_filter_values() -> dict[str, list[str]]:
    ensure_database()
    with _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT DISTINCT artifact_type FROM artifacts ORDER BY artifact_type")
            artifact_types = [str(row[0]) for row in cursor.fetchall()]
            cursor.execute(
                """
                SELECT DISTINCT COALESCE(payload->>'status', '')
                FROM jobs
                WHERE COALESCE(payload->>'status', '') <> ''
                ORDER BY 1
                """
            )
            job_statuses = [str(row[0]) for row in cursor.fetchall()]
    return {"artifact_types": artifact_types, "job_statuses": job_statuses}


def artifact_summary() -> dict:
    ensure_database()
    with _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    COUNT(*),
                    COALESCE(SUM(size_bytes) FILTER (WHERE availability_status = 'available'), 0),
                    COUNT(*) FILTER (WHERE availability_status = 'missing'),
                    COUNT(DISTINCT session_id),
                    COUNT(DISTINCT (session_id, job_id)) FILTER (WHERE job_id IS NOT NULL)
                FROM artifacts
                """
            )
            row = cursor.fetchone() or (0, 0, 0, 0, 0)
    return {
        "artifact_count": int(row[0]),
        "available_bytes": int(row[1]),
        "missing_count": int(row[2]),
        "session_count": int(row[3]),
        "job_count": int(row[4]),
    }


def search_artifact_records(
    *,
    text: str = "",
    session_id: str = "",
    job_id: str = "",
    artifact_type: str = "",
    availability_status: str = "",
    job_status: str = "",
    limit: int = 500,
) -> list[dict]:
    """Search artifacts with selected session and job metadata."""
    where = []
    params: list[object] = []

    if text.strip():
        pattern = f"%{text.strip()}%"
        where.append(
            """
            (
                a.filename ILIKE %s OR a.relative_path ILIKE %s OR
                a.artifact_role ILIKE %s OR a.metadata::text ILIKE %s OR
                COALESCE(j.payload->>'name', '') ILIKE %s OR
                COALESCE(j.payload->>'workflow', '') ILIKE %s OR
                COALESCE(j.payload->>'method', '') ILIKE %s OR
                COALESCE(j.payload->>'notes', '') ILIKE %s OR
                COALESCE(s.payload->>'owner_label', '') ILIKE %s
            )
            """
        )
        params.extend([pattern] * 9)
    if session_id:
        where.append("a.session_id = %s")
        params.append(session_id)
    if job_id:
        where.append("a.job_id = %s")
        params.append(job_id)
    if artifact_type:
        where.append("a.artifact_type = %s")
        params.append(artifact_type)
    if availability_status:
        where.append("a.availability_status = %s")
        params.append(availability_status)
    if job_status:
        where.append("COALESCE(j.payload->>'status', '') = %s")
        params.append(job_status)

    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    params.append(max(1, min(int(limit), 5000)))

    query = f"""
        SELECT
            a.relative_path,
            a.session_id,
            a.job_id,
            a.filename,
            a.artifact_type,
            a.artifact_role,
            a.extension,
            a.size_bytes,
            a.modified_at,
            a.availability_status,
            a.metadata,
            COALESCE(s.payload->>'owner_label', '') AS owner_label,
            COALESCE(j.payload->>'name', '') AS job_name,
            COALESCE(j.payload->>'workflow', '') AS workflow,
            COALESCE(j.payload->>'method', '') AS method,
            COALESCE(j.payload->>'status', '') AS job_status,
            COALESCE(j.payload->>'notes', '') AS job_notes,
            COALESCE(j.payload->>'finished_at', '') AS finished_at
        FROM artifacts a
        JOIN sessions s ON s.session_id = a.session_id
        LEFT JOIN jobs j ON j.session_id = a.session_id AND j.job_id = a.job_id
        {where_sql}
        ORDER BY a.modified_at DESC, a.relative_path
        LIMIT %s
    """

    keys = (
        "relative_path",
        "session_id",
        "job_id",
        "filename",
        "artifact_type",
        "artifact_role",
        "extension",
        "size_bytes",
        "modified_at",
        "availability_status",
        "metadata",
        "owner_label",
        "job_name",
        "workflow",
        "method",
        "job_status",
        "job_notes",
        "finished_at",
    )
    ensure_database()
    with _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, params)
            rows = cursor.fetchall()
    records = [dict(zip(keys, row, strict=True)) for row in rows]
    for item in records:
        item["metadata"] = dict(item.get("metadata") or {})
    return records
