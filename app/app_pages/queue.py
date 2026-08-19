"""Queue page."""

from __future__ import annotations

from pathlib import Path
import streamlit as st

from app_ui.i18n import t

from app_ui.views import render_queue_panel, render_session_overview
from app_ui.sidebar import get_selected_session

from app_core.queue_manager import sync_queue_state
from app_core.job_runner import molscout_log_candidates, reload_job
from app_core.utils import tail_text

st.set_page_config(page_title="MolScout [Queue]")
st.markdown("## :material/lan: Queue")
st.caption(t('View the shared queue and the selected session overview.'))

render_queue_panel()

st.divider()
st.markdown("### :material/terminal: Running Logs (molscout.log)")
queue_state = sync_queue_state()
running_items = [item for item in queue_state["jobs"] if item["status"] == "running"]

if not running_items:
    st.caption(t('No job is currently running.'))
else:
    for item in running_items:
        job = reload_job(item["session_id"], item["job_id"])
        if not job:
            continue

        st.markdown(f"**Job ID:** `{job['job_id']}` (Session: `{item['session_id']}`)")

        output_dir = Path(job.get("output_dir", ""))
        log_path = next((path for path in molscout_log_candidates(output_dir) if path.exists()), None)

        if log_path:
            st.caption(f"log file: `{log_path.relative_to(output_dir)}`")
            st.code(tail_text(log_path, max_lines=200) or "(empty file)", language="text")
        else:
            st.caption(t('molscout.log has not been generated yet.'))

st.divider()
st.markdown(t('### :material/group_work: Selected session'))
session = get_selected_session()
if session:
    render_session_overview(session)
else:
    st.info(t('Create a session from the sidebar.'))
