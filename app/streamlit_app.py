"""Shared Streamlit GUI for MolScout remote usage."""

from __future__ import annotations

import streamlit as st

st.set_page_config(
    page_title="MolScout Remote GUI",
    page_icon=":material/science:",
    layout="wide",
)

from app_core.database import ensure_database
from app_core.paths import ensure_app_dirs
from app_ui.views import ensure_worker_running, inject_css
from app_ui.sidebar import render_database_sidebar, render_queue_sidebar


def main() -> None:
    ensure_app_dirs()
    ensure_database()
    ensure_worker_running()
    inject_css()

    page = st.navigation(
        [
            st.Page("app_pages/queue.py", title="Queue", icon=":material/lan:"),
            st.Page("app_pages/submit.py", title="Submit", icon=":material/upload_file:"),
            st.Page("app_pages/submit_json.py", title="Submit (JSON)", icon=":material/data_object:"),
            st.Page("app_pages/pyscf.py", title="PySCF", icon=":material/settings:"),
            st.Page("app_pages/results.py", title="Results", icon=":material/monitoring:"),
            st.Page("app_pages/chemiscope.py", title="Chemiscope", icon=":material/animation:"),
            st.Page("app_pages/data_catalog.py", title="Data", icon=":material/database:"),
            st.Page("app_pages/about.py", title="About", icon=":material/info:"),
        ],
        position="top",
    )

    database_pages = {"Results", "Chemiscope", "Data", "About"}
    if page.title in database_pages:
        render_database_sidebar()
    else:
        render_queue_sidebar()

    page.run()


main()
