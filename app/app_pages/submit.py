"""Job submission page."""

from __future__ import annotations

import streamlit as st

from app_ui.views import render_concat_submission, render_job_submission, section_switch
from app_ui.sidebar import get_selected_session


st.markdown("## :material/upload_file: Submit")
st.caption("Add a new job to the selected session.")

session = get_selected_session()
if not session:
    st.info("Create a session from the sidebar.")
    st.stop()

submit_view = section_switch(
    "submit workflow",
    ["Reaction path search", "File concatenation"],
    key=f"{session['session_id']}_submit_view",
    captions={
        "Reaction path search": "Queue minimum-energy path search, IRC, and vibrational analysis.",
        "File concatenation": "Queue file concatenation and batch processing.",
    },
)

if submit_view == "Reaction path search":
    render_job_submission(session)
else:
    render_concat_submission(session)

