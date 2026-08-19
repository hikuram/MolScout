"""Sidebar controls shared by all pages."""

from __future__ import annotations

import hashlib
from pathlib import Path
from urllib.parse import urlencode, urlsplit, urlunsplit

import streamlit as st

from app_ui.i18n import t, tf

from app_core.paths import WORKER_LOG_FILE
from app_core.session_manager import create_session, list_jobs, list_sessions, touch_session
from app_core.utils import tail_text
from app_ui.views import (
    dependency_rows,
    format_app_time,
    format_worker_log_time,
    list_sample_cases,
    sidebar_monitor_fragment,
    cached_selected_jobs_archive,
)

SELECTED_SESSION_STATE_KEY = "selected_session_id"
SESSION_SELECTOR_WIDGET_KEY = "session_selector_id"
SESSION_QUERY_PARAM_KEY = "session"
APPLIED_QUERY_SESSION_STATE_KEY = "selected_session_query_applied"
PENDING_WIDGET_SESSION_STATE_KEY = "selected_session_widget_pending"

DB_SELECTED_SESSION_STATE_KEY = "database_selected_session_id"
DB_SESSION_SELECTOR_WIDGET_KEY = "database_session_selector_id"
DB_SELECTED_JOB_STATE_KEY = "database_selected_job_id"
DB_JOB_SELECTOR_WIDGET_KEY = "database_job_selector_id"
DB_REFRESH_GENERATION_STATE_KEY = "database_refresh_generation"
DB_SESSION_QUERY_PARAM_KEY = "db_session"
DB_JOB_QUERY_PARAM_KEY = "db_job"
DB_QUERY_TARGET_APPLIED_STATE_KEY = "database_query_target_applied"
DB_MULTI_JOB_MODE_STATE_KEY = "database_multi_job_mode"
DB_MULTI_JOB_MODE_WIDGET_KEY = "database_multi_job_mode_widget"
DB_SELECTED_JOB_IDS_STATE_KEY = "database_selected_job_ids"
DB_ARCHIVE_FLAT_STATE_KEY = "database_archive_flat"
DB_ARCHIVE_MERGED_STATE_KEY = "database_archive_merged_csv"
DB_PENDING_MULTI_SELECTION_STATE_KEY = "database_pending_multi_selection"
DB_JOB_TABLE_SYNC_STATE_KEY = "database_job_table_sync"


def current_query_session_id() -> str:
    """Return the session ID from the current URL query parameter."""
    raw_value = st.query_params.get(SESSION_QUERY_PARAM_KEY)
    if isinstance(raw_value, list):
        raw_value = raw_value[0] if raw_value else ""
    return str(raw_value or "")


def persist_selected_session(session_id: str) -> None:
    """Keep internal state and URL params in sync."""
    st.session_state[SELECTED_SESSION_STATE_KEY] = session_id
    st.session_state[APPLIED_QUERY_SESSION_STATE_KEY] = session_id
    st.query_params[SESSION_QUERY_PARAM_KEY] = session_id


def sync_selected_session_from_widget() -> None:
    """Sync state from sidebar selectbox."""
    selected = st.session_state.get(SESSION_SELECTOR_WIDGET_KEY)
    if selected:
        st.session_state[PENDING_WIDGET_SESSION_STATE_KEY] = str(selected)
        persist_selected_session(str(selected))


def resolve_selected_session_id(sessions: list[dict]) -> str:
    """Resolve active session ID safely for each browser session.

    A just-clicked selectbox value is protected so it cannot be overwritten by
    a stale query parameter during the same rerun. URL values are still honored
    on initial load and when the browser URL changes externally.
    """
    session_ids = [item["session_id"] for item in sessions]
    url_session = current_query_session_id()

    # 1. Trust a widget change that was just delivered by the selectbox callback.
    pending_widget_session = st.session_state.get(PENDING_WIDGET_SESSION_STATE_KEY, "")
    if pending_widget_session in session_ids:
        if url_session == pending_widget_session:
            st.session_state.pop(PENDING_WIDGET_SESSION_STATE_KEY, None)
        persist_selected_session(str(pending_widget_session))
        return str(pending_widget_session)

    # 2. Honor URL only when it has not already been applied. This keeps
    #    initial deep links and browser navigation working without making a
    #    stale URL win over a fresh selectbox change.
    applied_url_session = st.session_state.get(APPLIED_QUERY_SESSION_STATE_KEY)
    if url_session in session_ids and url_session != applied_url_session:
        persist_selected_session(url_session)
        return url_session

    # 3. Check the widget. This keeps the per-browser Streamlit state isolated.
    widget_session = st.session_state.get(SESSION_SELECTOR_WIDGET_KEY)
    if widget_session in session_ids:
        local_session = st.session_state.get(SELECTED_SESSION_STATE_KEY)
        if widget_session != local_session:
            persist_selected_session(str(widget_session))
        return str(widget_session)

    # 4. Check Session State
    local_session = st.session_state.get(SELECTED_SESSION_STATE_KEY)
    if local_session in session_ids:
        persist_selected_session(str(local_session))
        return local_session

    # 5. Fallback to first session
    fallback = session_ids[0] if session_ids else ""
    if fallback:
        persist_selected_session(fallback)

    return fallback

@st.dialog(t('Create new session'))
def open_create_session_dialog() -> None:
    owner_label = st.text_input(t('Display name'), value="")
    notes = st.text_input(t('Session notes'), value="")
    if st.button(t(':material/add: Create'), type="primary", width="stretch"):
        session = create_session(owner_label=owner_label or "anonymous", notes=notes)
        persist_selected_session(session["session_id"])
        st.session_state[SESSION_SELECTOR_WIDGET_KEY] = session["session_id"]
        st.rerun()


@st.dialog(t('Environment check'))
def open_dependency_dialog() -> None:
    rows = dependency_rows()
    st.dataframe(
        [
            {
                "package": row["package"],
                "status": row["status"],
                "version": row["version"],
                "import": row["import"],
                "purpose": row["label"],
            }
            for row in rows
        ],
        hide_index=True,
        width="stretch",
    )
    optional_missing = [row["package"] for row in rows if row["status"] == "optional"]
    if optional_missing:
        st.caption(tf(
            "Optional dependencies: {packages}. These are only required for the corresponding features.",
            packages=", ".join(optional_missing),
        ))
    import_notes = [row["note"] for row in rows if row.get("note")]
    if import_notes:
        with st.expander("Import diagnostics", expanded=False):
            st.code("\n".join(import_notes), language="text")


@st.dialog(t('Sample inputs'))
def open_samples_dialog() -> None:
    sample_cases = list_sample_cases()
    if not sample_cases:
        st.info(t('No bundled sample pairs were found under `core/sample_input/`.'))
        return
    for name in sample_cases:
        st.write(f"- `{name}`")


@st.dialog("Worker log")
def open_worker_log_dialog() -> None:
    log_text = tail_text(WORKER_LOG_FILE, max_lines=160) or "No worker log yet."
    st.text_area(
        "Log",
        value=format_worker_log_time(log_text),
        height=420,
        disabled=True,
        label_visibility="collapsed",
    )


def get_selected_session() -> dict | None:
    sessions = list_sessions()
    if not sessions:
        return None

    selected = resolve_selected_session_id(sessions)
    return touch_session(str(selected))


def render_session_sidebar() -> dict | None:
    sessions = list_sessions()

    with st.container(border=True):
        st.markdown("## :material/group_work: Session")
        if not sessions:
            st.info(t('No sessions yet.'))
            if st.button(t(':material/add: Create session'), type="primary", width="stretch"):
                open_create_session_dialog()
            return None

        session_ids = [item["session_id"] for item in sessions]
        selected = resolve_selected_session_id(sessions)
        widget_selection = st.session_state.get(SESSION_SELECTOR_WIDGET_KEY)
        if widget_selection not in session_ids or widget_selection != selected:
            st.session_state[SESSION_SELECTOR_WIDGET_KEY] = selected

        labels = {
            item["session_id"]: f"{item['session_id']} | {item.get('owner_label', 'anonymous')} | jobs {len(list_jobs(item['session_id']))}"
            for item in sessions
        }
        selected_id = st.selectbox(
            t('Select session'),
            session_ids,
            key=SESSION_SELECTOR_WIDGET_KEY,
            format_func=lambda session_id: labels[session_id],
            on_change=sync_selected_session_from_widget,
        )

        if st.button(t(':material/add: Add new session'), type="primary", width="stretch"):
            open_create_session_dialog()

        session = touch_session(selected_id)
        jobs = list_jobs(selected_id)
        cols = st.columns(2)
        cols[0].metric("Owner", session.get("owner_label", "anonymous"))
        cols[1].metric("Jobs", len(jobs))
        st.caption(f"Updated: {format_app_time(session.get('updated_at'))}")
        if session.get("notes"):
            st.caption(session["notes"])
        return session


def _query_param_text(key: str) -> str:
    raw_value = st.query_params.get(key)
    if isinstance(raw_value, list):
        raw_value = raw_value[0] if raw_value else ""
    return str(raw_value or "")


def _persist_database_query_target(session_id: str, job_id: str = "") -> None:
    if session_id:
        st.query_params[DB_SESSION_QUERY_PARAM_KEY] = session_id
    else:
        st.query_params.pop(DB_SESSION_QUERY_PARAM_KEY, None)
    if job_id:
        st.query_params[DB_JOB_QUERY_PARAM_KEY] = job_id
    else:
        st.query_params.pop(DB_JOB_QUERY_PARAM_KEY, None)
    st.session_state[DB_QUERY_TARGET_APPLIED_STATE_KEY] = f"{session_id}|{job_id}"


def _apply_database_query_target(sessions: list[dict]) -> None:
    session_id = _query_param_text(DB_SESSION_QUERY_PARAM_KEY)
    job_id = _query_param_text(DB_JOB_QUERY_PARAM_KEY)
    token = f"{session_id}|{job_id}"
    if token == str(st.session_state.get(DB_QUERY_TARGET_APPLIED_STATE_KEY) or ""):
        return

    session_ids = {str(item.get("session_id") or "") for item in sessions}
    if session_id in session_ids:
        jobs = list_jobs(session_id)
        job_ids = {str(item.get("job_id") or "") for item in jobs}
        st.session_state[DB_SELECTED_SESSION_STATE_KEY] = session_id
        st.session_state[DB_SESSION_SELECTOR_WIDGET_KEY] = session_id
        if job_id in job_ids:
            st.session_state[DB_SELECTED_JOB_STATE_KEY] = job_id
            st.session_state[DB_JOB_SELECTOR_WIDGET_KEY] = job_id
            st.session_state[DB_MULTI_JOB_MODE_STATE_KEY] = False
            st.session_state[DB_MULTI_JOB_MODE_WIDGET_KEY] = False
            st.session_state[DB_SELECTED_JOB_IDS_STATE_KEY] = [job_id]
            st.session_state[DB_JOB_TABLE_SYNC_STATE_KEY] = {
                "session_id": session_id,
                "job_ids": [job_id],
            }

    st.session_state[DB_QUERY_TARGET_APPLIED_STATE_KEY] = token


def database_page_url(page_path: str, session_id: str, job_id: str) -> str:
    """Build an absolute database deep link that opens in a new browser tab."""
    try:
        current_url = str(st.context.url or "")
    except Exception:
        return ""
    if not current_url:
        return ""

    parsed = urlsplit(current_url)
    current_path = parsed.path.rstrip("/")
    if "/" in current_path:
        root_path = current_path.rsplit("/", 1)[0]
    else:
        root_path = ""
    target_path = f"{root_path}/{page_path.lstrip('/')}"
    query = urlencode(
        {
            DB_SESSION_QUERY_PARAM_KEY: str(session_id),
            DB_JOB_QUERY_PARAM_KEY: str(job_id),
        }
    )
    return urlunsplit((parsed.scheme, parsed.netloc, target_path, query, ""))


def _resolve_database_session_id(sessions: list[dict]) -> str:
    session_ids = [str(item["session_id"]) for item in sessions]
    selected = str(st.session_state.get(DB_SELECTED_SESSION_STATE_KEY) or "")
    if selected in session_ids:
        return selected

    queue_selected = str(st.session_state.get(SELECTED_SESSION_STATE_KEY) or "")
    fallback = queue_selected if queue_selected in session_ids else (session_ids[0] if session_ids else "")
    if fallback:
        st.session_state[DB_SELECTED_SESSION_STATE_KEY] = fallback
    return fallback


def _sync_database_session_from_widget() -> None:
    selected = str(st.session_state.get(DB_SESSION_SELECTOR_WIDGET_KEY) or "")
    if not selected:
        return
    st.session_state[DB_SELECTED_SESSION_STATE_KEY] = selected
    st.session_state[DB_SELECTED_JOB_STATE_KEY] = ""
    st.session_state[DB_MULTI_JOB_MODE_STATE_KEY] = False
    st.session_state[DB_MULTI_JOB_MODE_WIDGET_KEY] = False
    st.session_state[DB_SELECTED_JOB_IDS_STATE_KEY] = []
    st.session_state.pop(DB_JOB_TABLE_SYNC_STATE_KEY, None)
    _persist_database_query_target(selected)


def _resolve_database_job_id(jobs: list[dict]) -> str:
    job_ids = [str(item["job_id"]) for item in jobs]
    selected = str(st.session_state.get(DB_SELECTED_JOB_STATE_KEY) or "")
    if selected in job_ids:
        return selected

    fallback = job_ids[-1] if job_ids else ""
    if fallback:
        st.session_state[DB_SELECTED_JOB_STATE_KEY] = fallback
    return fallback


def _sync_database_job_from_widget() -> None:
    selected = str(st.session_state.get(DB_JOB_SELECTOR_WIDGET_KEY) or "")
    if selected:
        st.session_state[DB_SELECTED_JOB_STATE_KEY] = selected
        if not bool(st.session_state.get(DB_MULTI_JOB_MODE_STATE_KEY, False)):
            st.session_state[DB_SELECTED_JOB_IDS_STATE_KEY] = [selected]
        session_id = str(st.session_state.get(DB_SELECTED_SESSION_STATE_KEY) or "")
        _persist_database_query_target(session_id, selected)


def database_selection() -> tuple[str, str]:
    """Return the database sidebar session and focused job selection."""
    return (
        str(st.session_state.get(DB_SELECTED_SESSION_STATE_KEY) or ""),
        str(st.session_state.get(DB_SELECTED_JOB_STATE_KEY) or ""),
    )


def database_job_selection() -> tuple[str, list[str]]:
    """Return the database sidebar session and selected job set."""
    session_id = str(st.session_state.get(DB_SELECTED_SESSION_STATE_KEY) or "")
    focused_job_id = str(st.session_state.get(DB_SELECTED_JOB_STATE_KEY) or "")
    if not bool(st.session_state.get(DB_MULTI_JOB_MODE_STATE_KEY, False)):
        return session_id, [focused_job_id] if focused_job_id else []

    selected_job_ids = [
        str(job_id)
        for job_id in st.session_state.get(DB_SELECTED_JOB_IDS_STATE_KEY, [])
        if str(job_id)
    ]
    return session_id, selected_job_ids


def set_database_selection(session_id: str, job_id: str = "") -> None:
    """Set the database browsing target without changing the queue session."""
    normalized_session_id = str(session_id or "")
    normalized_job_id = str(job_id or "")
    st.session_state[DB_SELECTED_SESSION_STATE_KEY] = normalized_session_id
    st.session_state[DB_SELECTED_JOB_STATE_KEY] = normalized_job_id
    st.session_state[DB_MULTI_JOB_MODE_STATE_KEY] = False
    st.session_state[DB_MULTI_JOB_MODE_WIDGET_KEY] = False
    st.session_state[DB_SELECTED_JOB_IDS_STATE_KEY] = [normalized_job_id] if normalized_job_id else []
    st.session_state[DB_JOB_TABLE_SYNC_STATE_KEY] = {
        "session_id": normalized_session_id,
        "job_ids": [normalized_job_id] if normalized_job_id else [],
    }
    _persist_database_query_target(normalized_session_id, normalized_job_id)


def set_database_multi_selection(session_id: str, job_ids: list[str]) -> None:
    """Queue a multi-job table selection for the next sidebar render."""
    normalized = [str(job_id) for job_id in job_ids if str(job_id)]
    st.session_state[DB_PENDING_MULTI_SELECTION_STATE_KEY] = {
        "session_id": str(session_id or ""),
        "job_ids": normalized,
    }


def _apply_pending_database_multi_selection(sessions: list[dict]) -> None:
    pending = st.session_state.pop(DB_PENDING_MULTI_SELECTION_STATE_KEY, None)
    if not isinstance(pending, dict):
        return

    session_id = str(pending.get("session_id") or "")
    requested_job_ids = [str(job_id) for job_id in pending.get("job_ids", []) if str(job_id)]
    session_ids = {str(item.get("session_id") or "") for item in sessions}
    if session_id not in session_ids:
        return

    jobs = list_jobs(session_id)
    available_job_ids = [str(item.get("job_id") or "") for item in jobs]
    selected_job_ids = [job_id for job_id in requested_job_ids if job_id in available_job_ids]
    if not selected_job_ids:
        return

    active_job_id = selected_job_ids[0]
    st.session_state[DB_SELECTED_SESSION_STATE_KEY] = session_id
    st.session_state[DB_SESSION_SELECTOR_WIDGET_KEY] = session_id
    st.session_state[DB_SELECTED_JOB_STATE_KEY] = active_job_id
    st.session_state[DB_JOB_SELECTOR_WIDGET_KEY] = active_job_id
    st.session_state[DB_MULTI_JOB_MODE_STATE_KEY] = True
    st.session_state[DB_MULTI_JOB_MODE_WIDGET_KEY] = True
    st.session_state[DB_SELECTED_JOB_IDS_STATE_KEY] = selected_job_ids
    st.session_state[DB_JOB_TABLE_SYNC_STATE_KEY] = {
        "session_id": session_id,
        "job_ids": selected_job_ids,
    }
    _persist_database_query_target(session_id, active_job_id)


def _archive_signature(jobs: list[dict], job_ids: tuple[str, ...]) -> tuple[tuple[str, str, str], ...]:
    selected = set(job_ids)
    return tuple(
        (
            str(job.get("job_id") or ""),
            str(job.get("status") or ""),
            str(job.get("updated_at") or job.get("finished_at") or job.get("created_at") or ""),
        )
        for job in jobs
        if str(job.get("job_id") or "") in selected
    )


def _render_multi_job_mode_toggle(session_id: str, active_job_id: str) -> bool:
    previous_enabled = bool(st.session_state.get(DB_MULTI_JOB_MODE_STATE_KEY, False))
    st.session_state.setdefault(DB_MULTI_JOB_MODE_WIDGET_KEY, previous_enabled)
    enabled = bool(
        st.toggle(
            "Select multiple jobs",
            key=DB_MULTI_JOB_MODE_WIDGET_KEY,
        )
    )
    st.session_state[DB_MULTI_JOB_MODE_STATE_KEY] = enabled

    if enabled != previous_enabled:
        selected_job_ids = [active_job_id] if active_job_id else []
        st.session_state[DB_SELECTED_JOB_IDS_STATE_KEY] = selected_job_ids
        if enabled:
            st.session_state[DB_JOB_TABLE_SYNC_STATE_KEY] = {
                "session_id": session_id,
                "job_ids": selected_job_ids,
            }
        else:
            st.session_state.pop(DB_JOB_TABLE_SYNC_STATE_KEY, None)
    elif not enabled:
        st.session_state[DB_SELECTED_JOB_IDS_STATE_KEY] = [active_job_id] if active_job_id else []

    return enabled


def _render_session_jobs_selector(
    session_id: str,
    jobs: list[dict],
    active_job_id: str,
    *,
    multi_enabled: bool,
) -> list[str]:
    job_ids = [str(item.get("job_id") or "") for item in jobs]
    rows = [
        {
            "Job": str(item.get("job_id") or "-"),
            "Workflow": str(item.get("workflow") or item.get("name") or "-"),
            "Status": str(item.get("status") or "-"),
            "Job Note": str(item.get("notes") or ""),
        }
        for item in jobs
    ]
    table_height = min(245, 36 + 35 * len(rows))
    column_config = {
        "Job": st.column_config.TextColumn("Job", width="medium"),
        "Workflow": st.column_config.TextColumn("Workflow", width="medium"),
        "Status": st.column_config.TextColumn("Status", width="small"),
        "Job Note": st.column_config.TextColumn("Job Note", width="large"),
    }

    st.markdown("**Session Jobs**")
    if not multi_enabled:
        st.dataframe(
            rows,
            hide_index=True,
            width="stretch",
            height=table_height,
            column_config=column_config,
        )
        selected_job_ids = [active_job_id] if active_job_id in job_ids else []
        st.session_state[DB_SELECTED_JOB_IDS_STATE_KEY] = selected_job_ids
        return selected_job_ids

    signature = hashlib.sha1("|".join(job_ids).encode("utf-8")).hexdigest()[:12]
    table_key = f"database_session_jobs_{session_id}_{signature}"
    state_selected = [
        str(job_id)
        for job_id in st.session_state.get(DB_SELECTED_JOB_IDS_STATE_KEY, [])
        if str(job_id) in job_ids
    ]
    if not state_selected and active_job_id in job_ids:
        state_selected = [active_job_id]
        st.session_state[DB_SELECTED_JOB_IDS_STATE_KEY] = state_selected

    pending = st.session_state.get(DB_JOB_TABLE_SYNC_STATE_KEY)
    if isinstance(pending, dict) and str(pending.get("session_id") or "") == session_id:
        requested = [str(job_id) for job_id in pending.get("job_ids", [])]
        selected_rows = [index for index, job_id in enumerate(job_ids) if job_id in requested]
        st.session_state[table_key] = {"selection": {"rows": selected_rows}}
        st.session_state.pop(DB_JOB_TABLE_SYNC_STATE_KEY, None)
    elif table_key not in st.session_state and state_selected:
        selected_rows = [index for index, job_id in enumerate(job_ids) if job_id in state_selected]
        st.session_state[table_key] = {"selection": {"rows": selected_rows}}

    selection_event = st.dataframe(
        rows,
        hide_index=True,
        width="stretch",
        height=table_height,
        on_select="rerun",
        selection_mode="multi-row",
        key=table_key,
        column_config=column_config,
    )
    raw_selected_rows = selection_event.selection.rows
    selected_rows = [
        index
        for index in raw_selected_rows
        if isinstance(index, int) and 0 <= index < len(job_ids)
    ]
    selected_job_ids = [job_ids[index] for index in selected_rows]
    st.session_state[DB_SELECTED_JOB_IDS_STATE_KEY] = selected_job_ids

    if selected_job_ids:
        focused_job_id = str(st.session_state.get(DB_SELECTED_JOB_STATE_KEY) or "")
        if focused_job_id not in selected_job_ids:
            focused_job_id = selected_job_ids[0]
            st.session_state[DB_SELECTED_JOB_STATE_KEY] = focused_job_id
            st.session_state[DB_JOB_SELECTOR_WIDGET_KEY] = focused_job_id
            _persist_database_query_target(session_id, focused_job_id)

    return selected_job_ids


def _render_selected_jobs_archive(
    session_id: str,
    jobs: list[dict],
    selected_job_ids: list[str],
) -> None:
    if not selected_job_ids:
        return

    st.caption(f"Selected jobs: {len(selected_job_ids)}")
    with st.expander("Archive options", expanded=False):
        st.session_state.setdefault(DB_ARCHIVE_FLAT_STATE_KEY, True)
        st.session_state.setdefault(DB_ARCHIVE_MERGED_STATE_KEY, True)
        flat = st.toggle("Flat ZIP", key=DB_ARCHIVE_FLAT_STATE_KEY)
        merged = st.toggle("Include merged CSV", key=DB_ARCHIVE_MERGED_STATE_KEY)

    selected_tuple = tuple(selected_job_ids)
    state_key = (
        "database_selected_jobs_archive::"
        + session_id
        + "::"
        + "|".join(selected_tuple)
        + f"::{int(bool(flat))}:{int(bool(merged))}"
    )
    if st.button(
        ":material/folder_zip: Generate ZIP",
        width="stretch",
        key="database_generate_selected_jobs_zip",
    ):
        with st.spinner("Building ZIP..."):
            archive_path = cached_selected_jobs_archive(
                session_id,
                selected_tuple,
                bool(flat),
                bool(merged),
                _archive_signature(jobs, selected_tuple),
            )
        st.session_state[state_key] = archive_path

    archive_raw = st.session_state.get(state_key)
    archive_path = Path(str(archive_raw)) if archive_raw else None
    if archive_path and archive_path.exists():
        st.download_button(
            ":material/download: Download ZIP",
            data=archive_path.read_bytes(),
            file_name=archive_path.name,
            mime="application/zip",
            width="stretch",
            key="database_download_selected_jobs_zip",
        )


def render_database_sidebar() -> dict | None:
    """Render database browsing context without changing the queue session."""
    sessions = list_sessions()
    _apply_database_query_target(sessions)
    _apply_pending_database_multi_selection(sessions)

    with st.sidebar:
        with st.container(border=True):
            st.markdown("## :material/database: Database")
            if not sessions:
                st.info(t('No sessions yet.'))
                if st.button(
                    ":material/refresh: Refresh",
                    width="stretch",
                    key="database_refresh_empty",
                ):
                    st.session_state[DB_REFRESH_GENERATION_STATE_KEY] = (
                        int(st.session_state.get(DB_REFRESH_GENERATION_STATE_KEY, 0)) + 1
                    )
                return None

            session_ids = [str(item["session_id"]) for item in sessions]
            selected_session_id = _resolve_database_session_id(sessions)
            if st.session_state.get(DB_SESSION_SELECTOR_WIDGET_KEY) != selected_session_id:
                st.session_state[DB_SESSION_SELECTOR_WIDGET_KEY] = selected_session_id

            session_labels = {
                str(item["session_id"]): (
                    f"{item['session_id']} | {item.get('owner_label', 'anonymous')} | "
                    f"jobs {len(item.get('job_order', []))}"
                )
                for item in sessions
            }
            selected_session_id = st.selectbox(
                "Session",
                session_ids,
                key=DB_SESSION_SELECTOR_WIDGET_KEY,
                format_func=lambda session_id: session_labels[session_id],
                on_change=_sync_database_session_from_widget,
            )
            st.session_state[DB_SELECTED_SESSION_STATE_KEY] = selected_session_id

            jobs = list_jobs(selected_session_id)
            selected_job = None
            if jobs:
                job_ids = [str(item["job_id"]) for item in jobs]
                active_job_id = _resolve_database_job_id(jobs)
                multi_enabled = _render_multi_job_mode_toggle(selected_session_id, active_job_id)
                selected_job_ids = _render_session_jobs_selector(
                    selected_session_id,
                    jobs,
                    active_job_id,
                    multi_enabled=multi_enabled,
                )

                job_labels = {
                    str(item["job_id"]): (
                        f"{item['job_id']} | {item.get('workflow') or item.get('name') or '-'} | "
                        f"{item.get('status') or '-'}"
                    )
                    for item in jobs
                }
                target_options = selected_job_ids if multi_enabled else job_ids
                if target_options:
                    focused_job_id = str(st.session_state.get(DB_SELECTED_JOB_STATE_KEY) or "")
                    if focused_job_id not in target_options:
                        focused_job_id = target_options[0]
                    if st.session_state.get(DB_JOB_SELECTOR_WIDGET_KEY) != focused_job_id:
                        st.session_state[DB_JOB_SELECTOR_WIDGET_KEY] = focused_job_id

                    selected_job_id = st.selectbox(
                        "Target Job",
                        target_options,
                        key=DB_JOB_SELECTOR_WIDGET_KEY,
                        format_func=lambda job_id: job_labels[job_id],
                        on_change=_sync_database_job_from_widget,
                    )
                    st.session_state[DB_SELECTED_JOB_STATE_KEY] = selected_job_id
                    if not multi_enabled:
                        selected_job_ids = [selected_job_id]
                        st.session_state[DB_SELECTED_JOB_IDS_STATE_KEY] = selected_job_ids
                    _persist_database_query_target(selected_session_id, selected_job_id)
                    selected_job = next(
                        (item for item in jobs if str(item["job_id"]) == selected_job_id),
                        None,
                    )

                    if multi_enabled:
                        _render_selected_jobs_archive(selected_session_id, jobs, selected_job_ids)

                    if selected_job:
                        st.markdown("**Target Summary**")
                        cols = st.columns(2)
                        cols[0].metric("Status", selected_job.get("status") or "-")
                        cols[1].metric(
                            "Indexed Files",
                            int(selected_job.get("artifact_catalog_count") or 0),
                        )
                        workflow = selected_job.get("workflow") or selected_job.get("name") or "-"
                        method = selected_job.get("method") or "-"
                        st.caption(f"Workflow: {workflow}")
                        st.caption(f"Method: {method}")
                        st.caption(
                            f"Charge: {selected_job.get('charge', 0)} | "
                            f"Multiplicity: {selected_job.get('mult', 1)}"
                        )
                        st.caption(
                            f"Created: {format_app_time(selected_job.get('created_at'))} | "
                            f"Exit: {'-' if selected_job.get('exit_code') is None else selected_job.get('exit_code')}"
                        )
                        selected_steps = [
                            label
                            for key, label in [
                                ("initial_path", "Initial Path"),
                                ("ts_opt", "TS Opt"),
                                ("irc", "IRC"),
                                ("vib", "Vib & Thermo"),
                                ("refine", "Energy Refine"),
                            ]
                            if selected_job.get("workflow_steps", {}).get(key)
                        ]
                        if selected_steps:
                            st.caption("Steps: " + ", ".join(selected_steps))

                        with st.expander("Run Settings", expanded=False):
                            st.caption(
                                f"Temperature: {selected_job.get('temperature', 298.15):.2f} K"
                            )
                            st.caption(
                                f"TBLITE: {selected_job.get('tblite_method', 'hybrid')}"
                            )
                            overrides = selected_job.get("config_overrides", {})
                            st.caption(
                                f"OrbMol: {overrides.get('ORBMOL_VERSION', '-')} | "
                                f"ALPB: {overrides.get('ALPB_SOLVENT', '-')}"
                            )
                            st.caption(
                                f"TBLITE accuracy: {overrides.get('TBLITE_ACCURACY', '-')}"
                            )
                            st.caption(
                                f"Refine input: {overrides.get('REFINE_INPUT_ON', False)} | "
                                f"Pick opt points: {overrides.get('PICK_OPTPOINTS_ON', True)}"
                            )
                            st.caption(
                                f"Save figures: {overrides.get('SAVE_FIG_ON', True)} | "
                                f"Initial path: {overrides.get('INIT_PATH_METHOD', 'DMF')}"
                            )
                            if selected_job.get("notes"):
                                st.caption(f"Notes: {selected_job.get('notes')}")

                        if selected_job.get("completion_reason") or selected_job.get("status_message"):
                            st.caption(
                                f"Result: {selected_job.get('completion_reason') or '-'}"
                                + (
                                    f" | {selected_job.get('status_message')}"
                                    if selected_job.get("status_message")
                                    else ""
                                )
                            )
                elif multi_enabled:
                    st.caption(t('Select one or more jobs from the Session Jobs table.'))
            else:
                st.session_state[DB_SELECTED_JOB_IDS_STATE_KEY] = []
                st.session_state[DB_MULTI_JOB_MODE_STATE_KEY] = False
                st.session_state[DB_MULTI_JOB_MODE_WIDGET_KEY] = False
                st.session_state.pop(DB_SELECTED_JOB_STATE_KEY, None)
                st.session_state.pop(DB_JOB_SELECTOR_WIDGET_KEY, None)
                st.info(t('This session has no jobs.'))

            if st.button(
                ":material/refresh: Refresh",
                width="stretch",
                key="database_refresh",
                help=t('Reload the database and files from their current state.'),
            ):
                st.session_state[DB_REFRESH_GENERATION_STATE_KEY] = (
                    int(st.session_state.get(DB_REFRESH_GENERATION_STATE_KEY, 0)) + 1
                )

            st.caption("Manual refresh only")
            return selected_job


def render_admin_sidebar() -> None:
    st.markdown("## :material/admin_panel_settings: Admin")
    cols = st.columns(2)
    if cols[0].button(t(':material/fact_check: Environment'), width="stretch"):
        open_dependency_dialog()
    if cols[1].button(t(':material/science: Samples'), width="stretch"):
        open_samples_dialog()
    cols = st.columns(2)
    if cols[0].button(":material/article: Log", width="stretch"):
        open_worker_log_dialog()
    cols[1].button(
        ":material/delete_sweep: Cleanup",
        width="stretch",
        disabled=True,
        help=t('Cleanup is disabled during the PostgreSQL migration.'),
    )


def render_queue_sidebar() -> dict | None:
    """Render the existing queue/session sidebar unchanged in behavior."""
    with st.sidebar:
        session = render_session_sidebar()
        sidebar_monitor_fragment()
        st.divider()
        render_admin_sidebar()
    return session


def render_sidebar() -> dict | None:
    """Backward-compatible alias for the queue-style sidebar."""
    return render_queue_sidebar()
