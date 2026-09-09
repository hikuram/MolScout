"""Shared Streamlit application entry logic for MolScout."""

from __future__ import annotations

from app_ui.i18n import set_language


def run_app(language: str = "en") -> None:
    """Run the shared application with a fixed UI language."""
    set_language(language)

    import streamlit as st

    from app_core.database import ensure_database
    from app_core.paths import ensure_app_dirs
    from app_ui.sidebar import (
        prepare_database_sidebar_state,
        render_database_sidebar,
        render_queue_sidebar,
        render_session_sidebar,
    )
    from app_ui.views import ensure_worker_running, inject_css

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

    database_pages = {"Results", "Chemiscope", "Data"}
    database_mode = page.title in database_pages

    # Database deep links and pending job selections may change the active
    # session. Apply them before creating the shared session widget so Streamlit
    # never needs to rewrite a widget key after the widget has been rendered.
    database_sessions = prepare_database_sidebar_state() if database_mode else None

    # One persistent session selector is rendered from the entrypoint for every
    # page. Streamlit therefore keeps the widget alive across st.navigation page
    # changes and the choice remains local to this browser tab/session.
    with st.sidebar:
        render_session_sidebar(
            database_mode=database_mode,
            sessions=database_sessions,
        )

    if database_mode:
        render_database_sidebar(database_sessions)
    else:
        render_queue_sidebar()

    page.run()
