"""Results page."""

from __future__ import annotations

import streamlit as st

from app_ui.views import render_session_jobs
from app_ui.sidebar import get_selected_session


st.markdown("## :material/monitoring: Results")
st.caption("View jobs, logs, generated outputs, and downloads for the selected session.")

session = get_selected_session()
if session:
    render_session_jobs(session)
else:
    st.info("Create a session from the sidebar.")

