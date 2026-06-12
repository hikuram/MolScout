"""Job submission page."""

from __future__ import annotations

import streamlit as st

from app_ui.views import render_concat_submission, render_job_submission, section_switch
from app_ui.sidebar import get_selected_session


st.markdown("## :material/upload_file: Submit")
st.caption("選択中セッションに新しいジョブを追加します。")

session = get_selected_session()
if not session:
    st.info("サイドバーからセッションを作成してください。")
    st.stop()

submit_view = section_switch(
    "submit workflow",
    ["反応経路探索", "ファイル連結処理"],
    key=f"{session['session_id']}_submit_view",
    captions={
        "反応経路探索": "最小エネルギー経路探索、IRC、振動解析をキューに追加します。",
        "ファイル連結処理": "ファイル連結とバッチ処理をキューに追加します。",
    },
)

if submit_view == "反応経路探索":
    render_job_submission(session)
else:
    render_concat_submission(session)

