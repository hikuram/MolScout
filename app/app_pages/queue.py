"""Queue page."""

from __future__ import annotations

from pathlib import Path
import streamlit as st

from app_ui.views import render_queue_panel, render_session_overview
from app_ui.sidebar import get_selected_session

from app_core.queue_manager import sync_queue_state
from app_core.job_runner import reload_job
from app_core.utils import tail_text

st.markdown("## :material/lan: Queue")
st.caption("共有キューと、選択中セッションの概要を確認します。")

render_queue_panel()

st.divider()
st.markdown("### :material/terminal: Running Logs (stdout)")
queue_state = sync_queue_state()
running_items = [item for item in queue_state["jobs"] if item["status"] == "running"]

if not running_items:
    st.caption("現在実行中のジョブはありません。")
else:
    for item in running_items:
        job = reload_job(item["session_id"], item["job_id"])
        if not job:
            continue

        st.markdown(f"**Job ID:** `{job['job_id']}` (Session: `{item['session_id']}`)")

        stdout_path = Path(job.get("stdout_log", ""))

        if stdout_path.exists():
            st.code(tail_text(stdout_path, max_lines=200) or "(empty file)", language="text")
        else:
            st.caption("stdout.log はまだ生成されていません。")

st.divider()
st.markdown("### :material/group_work: 選択中セッション")
session = get_selected_session()
if session:
    render_session_overview(session)
else:
    st.info("サイドバーからセッションを作成してください。")
