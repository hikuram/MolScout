"""Sidebar controls shared by all pages."""

from __future__ import annotations

import streamlit as st

from app_core.cleanup_manager import run_cleanup
from app_core.paths import WORKER_LOG_FILE
from app_core.session_manager import create_session, list_jobs, list_sessions, touch_session
from app_core.storage import read_app_state, write_app_state
from app_core.utils import tail_text
from app_ui.views import (
    dependency_rows,
    format_app_time,
    format_worker_log_time,
    list_sample_cases,
    sidebar_monitor_fragment,
)


@st.dialog("新規セッション作成")
def open_create_session_dialog() -> None:
    owner_label = st.text_input("表示名", value="")
    notes = st.text_input("セッションノート", value="")
    if st.button(":material/add: 作成", type="primary", width="stretch"):
        session = create_session(owner_label=owner_label or "anonymous", notes=notes)
        st.session_state["selected_session_id"] = session["session_id"]
        state = read_app_state()
        state["selected_session_id"] = session["session_id"]
        write_app_state(state)
        st.rerun()


@st.dialog("環境チェック")
def open_dependency_dialog() -> None:
    for row in dependency_rows():
        color = "green" if row["status"] == "ready" else "orange"
        st.markdown(f"- `{row['package']}` :{color}[{row['status']}]")
        st.caption(row["label"])


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


@st.dialog("クリーンアップ確認")
def open_cleanup_dialog() -> None:
    st.warning("期限切れセッションと不要な待機キュー項目を削除します。")
    st.caption("実行中ジョブは停止しません。削除対象は保持期限と現在のキュー状態から判定されます。")
    if st.button(":material/delete_sweep: クリーンアップを実行", type="primary", width="stretch"):
        result = run_cleanup()
        st.success(
            f"クリーンアップ完了: 待機エントリー {result['queue_entries_removed']}件、"
            f"期限切れセッション {result['sessions_deleted']}件を削除しました。"
        )


def get_selected_session() -> dict | None:
    sessions = list_sessions()
    if not sessions:
        return None

    state = read_app_state()
    session_ids = [item["session_id"] for item in sessions]
    selected = st.session_state.get("selected_session_id") or state.get("selected_session_id")
    if selected not in session_ids:
        selected = session_ids[0]
        st.session_state["selected_session_id"] = selected
        state["selected_session_id"] = selected
        write_app_state(state)

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

        state = read_app_state()
        session_ids = [item["session_id"] for item in sessions]
        selected = st.session_state.get("selected_session_id") or state.get("selected_session_id")
        if selected not in session_ids:
            selected = session_ids[0]
            st.session_state["selected_session_id"] = selected
            state["selected_session_id"] = selected
            write_app_state(state)

        labels = {
            item["session_id"]: f"{item['session_id']} | {item.get('owner_label', 'anonymous')} | jobs {len(list_jobs(item['session_id']))}"
            for item in sessions
        }
        selected_id = st.selectbox(
            "セッション選択",
            session_ids,
            index=session_ids.index(str(selected)),
            key="selected_session_id",
            format_func=lambda session_id: labels[session_id],
        )
        if selected_id != selected:
            state["selected_session_id"] = selected_id
            write_app_state(state)
            st.rerun()

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
    if cols[1].button(":material/delete_sweep: Cleanup", width="stretch"):
        open_cleanup_dialog()


def render_sidebar() -> dict | None:
    with st.sidebar:
        session = render_session_sidebar()
        sidebar_monitor_fragment()
        st.divider()
        render_admin_sidebar()
    return session
