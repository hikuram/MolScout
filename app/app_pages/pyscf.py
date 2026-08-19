"""PySCF settings page."""

from __future__ import annotations

import streamlit as st

from app_ui.i18n import t

from app_ui.views import render_session_config
from app_ui.sidebar import get_selected_session


st.set_page_config(page_title="MolScout [PySCF]")
st.markdown("## :material/settings: PySCF")
st.caption(t('Edit PySCF settings for the selected session.'))

session = get_selected_session()
if session:
    render_session_config(session)
else:
    st.info(t('Create a session from the sidebar.'))

