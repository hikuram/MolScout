"""Results page for the database-selected jobs."""

from __future__ import annotations

import streamlit as st

from app_core.session_manager import get_job, get_session, list_jobs
from app_ui.sidebar import database_job_selection, database_selection
from app_ui.views import render_job_detail


st.markdown("## :material/monitoring: Results")
st.caption("Database sidebarのSession Jobsテーブルで選択したジョブのログ、生成物、ダウンロードを確認します。")

session_id, selected_job_ids = database_job_selection()
_, focused_job_id = database_selection()
if not session_id:
    st.info("Database sidebarからSessionを選択してください。")
    st.stop()
if not selected_job_ids:
    st.info("Database sidebarのSession Jobsテーブルから1件以上のジョブを選択してください。")
    st.stop()

session = get_session(session_id)
if not session:
    st.warning("選択したSessionがDBに存在しません。SidebarのRefreshで選択肢を読み直してください。")
    st.stop()

jobs = list_jobs(session_id)
job_by_id = {str(item.get("job_id") or ""): item for item in jobs}
selected_jobs = [job_by_id[job_id] for job_id in selected_job_ids if job_id in job_by_id]
if not selected_jobs:
    st.warning("選択したJobがDBに存在しません。SidebarのRefreshで選択肢を読み直してください。")
    st.stop()

selected_job_ids = [str(item["job_id"]) for item in selected_jobs]
if focused_job_id not in selected_job_ids:
    focused_job_id = selected_job_ids[0]
job = get_job(session_id, focused_job_id)
if not job:
    st.warning("Target JobがDBに存在しません。SidebarのRefreshで選択肢を読み直してください。")
    st.stop()

job_index = next(
    (index for index, item in enumerate(jobs) if str(item.get("job_id")) == focused_job_id),
    None,
)
if job_index is None:
    jobs = [*jobs, job]
    job_index = len(jobs) - 1

st.set_page_config(page_title="MolScout [Results]")
st.caption(
    f"Session `{session_id}` / Selected jobs {len(selected_job_ids)} / Target Job `{focused_job_id}`"
)
if len(selected_jobs) > 1:
    st.dataframe(
        [
            {
                "Job": str(item.get("job_id") or "-"),
                "Workflow": str(item.get("workflow") or item.get("name") or "-"),
                "Status": str(item.get("status") or "-"),
                "Job Note": str(item.get("notes") or ""),
            }
            for item in selected_jobs
        ],
        hide_index=True,
        width="stretch",
        height=min(210, 36 + 35 * len(selected_jobs)),
    )
render_job_detail(session_id, job, jobs, job_index, show_summary=False)
