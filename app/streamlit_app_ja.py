"""Japanese-assisted launcher for the MolScout Streamlit application."""

from __future__ import annotations

import streamlit as st

st.set_page_config(
    page_title="MolScout Remote GUI",
    page_icon=":material/science:",
    layout="wide",
)

from app_main import run_app

run_app("ja")
