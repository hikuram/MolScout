"""PySCF settings page."""

from __future__ import annotations

import streamlit as st

from app_ui.views import render_session_config
from app_ui.sidebar import get_selected_session


st.markdown("## :material/settings: PySCF")
st.caption("選択中セッションの PySCF 設定を編集します。")

session = get_selected_session()
if session:
    render_session_config(session)
else:
    st.info("サイドバーからセッションを作成してください。")

