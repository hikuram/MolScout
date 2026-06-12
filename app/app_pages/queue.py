"""Queue page."""

from __future__ import annotations

import streamlit as st

from app_ui.views import render_queue_panel, render_session_overview
from app_ui.sidebar import get_selected_session


st.markdown("## :material/lan: Queue")
st.caption("共有キューと、選択中セッションの概要を確認します。")

render_queue_panel()

st.divider()
st.markdown("### :material/group_work: 選択中セッション")
session = get_selected_session()
if session:
    render_session_overview(session)
else:
    st.info("サイドバーからセッションを作成してください。")

