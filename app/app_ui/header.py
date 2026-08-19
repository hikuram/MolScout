"""Shared page chrome."""

from __future__ import annotations

import streamlit as st

from app_ui.i18n import t

from app_core.paths import AUTO_REFRESH_SECONDS, SESSION_RETENTION_DAYS


def render_header() -> None:
    """Render the app header above top navigation content."""
    with st.container(border=True):
        st.markdown("# :material/science: MolScout Remote Queue")
        st.caption(
            t('Laboratory queue for shared execution of long-running workflows. Includes user sessions and server monitoring.')
        )
        st.markdown(
            '<span class="app-badge">1 worker</span>'
            f'<span class="app-badge">{AUTO_REFRESH_SECONDS}s refresh</span>'
            f'<span class="app-badge">{SESSION_RETENTION_DAYS} day retention</span>',
            unsafe_allow_html=True,
        )

