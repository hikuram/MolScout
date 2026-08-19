"""Job submission page."""

from __future__ import annotations

import streamlit as st

from app_ui.i18n import t

from app_ui.views import render_concat_submission, render_job_submission, section_switch
from app_ui.sidebar import get_selected_session


st.set_page_config(page_title="MolScout [Submit]")
st.markdown("## :material/upload_file: Submit")
st.caption(t('Add a new job to the selected session.'))

session = get_selected_session()
if not session:
    st.info(t('Create a session from the sidebar.'))
    st.stop()

submit_view = section_switch(
    "submit workflow",
    [t('Reaction path search'), t('File concatenation')],
    key=f"{session['session_id']}_submit_view",
    captions={
        t('Reaction path search'): t('Queue minimum-energy path search, IRC, and vibrational analysis.'),
        t('File concatenation'): t('Queue file concatenation and batch processing.'),
    },
)

if submit_view == t('Reaction path search'):
    render_job_submission(session)
else:
    render_concat_submission(session)

