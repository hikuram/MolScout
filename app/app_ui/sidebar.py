"""Sidebar controls shared by all pages."""

from __future__ import annotations

import streamlit as st

from app_core.paths import WORKER_LOG_FILE
from app_core.session_manager import create_session, list_jobs, list_sessions, touch_session
from app_core.utils import tail_text
from app_ui.views import (
    dependency_rows,
    format_app_time,
    format_worker_log_time,
    list_sample_cases,
    sidebar_monitor_fragment,
)

SELECTED_SESSION_STATE_KEY = "selected_session_id"
SESSION_SELECTOR_WIDGET_KEY = "session_selector_id"
SESSION_QUERY_PARAM_KEY = "session"
APPLIED_QUERY_SESSION_STATE_KEY = "selected_session_query_applied"
PENDING_WIDGET_SESSION_STATE_KEY = "selected_session_widget_pending"


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

@st.dialog("新規セッション作成")
def open_create_session_dialog() -> None:
    owner_label = st.text_input("表示名", value="")
    notes = st.text_input("セッションノート", value="")
    if st.button(":material/add: 作成", type="primary", width="stretch"):
        session = create_session(owner_label=owner_label or "anonymous", notes=notes)
        persist_selected_session(session["session_id"])
        st.session_state[SESSION_SELECTOR_WIDGET_KEY] = session["session_id"]
        st.rerun()


@st.dialog("環境チェック")
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
        st.caption(f"任意依存: {', '.join(optional_missing)} は未導入でも、該当機能を使わなければ問題ありません。")
    import_notes = [row["note"] for row in rows if row.get("note")]
    if import_notes:
        with st.expander("Import diagnostics", expanded=False):
            st.code("\n".join(import_notes), language="text")


@st.dialog("サンプル入力")
def open_samples_dialog() -> None:
    sample_cases = list_sample_cases()
    if not sample_cases:
        st.info("`core/sample_input/` に bundled sample pair が見つかりません。")
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
            st.info("セッションはまだありません。")
            if st.button(":material/add: セッションを作成", type="primary", width="stretch"):
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
            "セッション選択",
            session_ids,
            key=SESSION_SELECTOR_WIDGET_KEY,
            format_func=lambda session_id: labels[session_id],
            on_change=sync_selected_session_from_widget,
        )

        if st.button(":material/add: 新規セッションを追加", type="primary", width="stretch"):
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


def render_admin_sidebar() -> None:
    st.markdown("## :material/admin_panel_settings: Admin")
    cols = st.columns(2)
    if cols[0].button(":material/fact_check: 環境", width="stretch"):
        open_dependency_dialog()
    if cols[1].button(":material/science: サンプル", width="stretch"):
        open_samples_dialog()
    cols = st.columns(2)
    if cols[0].button(":material/article: Log", width="stretch"):
        open_worker_log_dialog()
    cols[1].button(
        ":material/delete_sweep: Cleanup",
        width="stretch",
        disabled=True,
        help="PostgreSQL移行中のためクリーンアップ機能は凍結しています。",
    )


def render_sidebar() -> dict | None:
    with st.sidebar:
        session = render_session_sidebar()
        sidebar_monitor_fragment()
        st.divider()
        render_admin_sidebar()
    return session
