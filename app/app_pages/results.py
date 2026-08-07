"""Results page for the database-selected job."""

from __future__ import annotations

import streamlit as st

from app_core.session_manager import get_job, get_session, list_jobs
from app_ui.sidebar import database_selection
from app_ui.views import render_job_detail


st.markdown("## :material/monitoring: Results")
st.caption("Database sidebarで選択したジョブのログ、生成物、ダウンロードを確認します。")

session_id, job_id = database_selection()
if not session_id:
    st.info("Database sidebarからSessionを選択してください。")
    st.stop()
if not job_id:
    st.info("Database sidebarからTarget Jobを選択してください。")
    st.stop()

session = get_session(session_id)
job = get_job(session_id, job_id)
if not session or not job:
    st.warning("選択したSessionまたはJobがDBに存在しません。SidebarのRefreshで選択肢を読み直してください。")
    st.stop()

jobs = list_jobs(session_id)
job_index = next(
    (index for index, item in enumerate(jobs) if str(item.get("job_id")) == job_id),
    None,
)
if job_index is None:
    jobs = [*jobs, job]
    job_index = len(jobs) - 1

st.caption(f"Session `{session_id}` / Job `{job_id}`")
st.markdown(
    f"#### :material/visibility: {job['job_id']} | "
    f"{job.get('workflow') or job.get('name') or '-'} | {job.get('status') or '-'}"
)
render_job_detail(session_id, job, jobs, job_index)
