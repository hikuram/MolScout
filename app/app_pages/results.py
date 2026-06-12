"""Results page."""

from __future__ import annotations

import streamlit as st

from app_ui.views import render_session_jobs
from app_ui.sidebar import get_selected_session


st.markdown("## :material/monitoring: Results")
st.caption("選択中セッションのジョブ、ログ、生成物、ダウンロードを確認します。")

session = get_selected_session()
if session:
    render_session_jobs(session)
else:
    st.info("サイドバーからセッションを作成してください。")

