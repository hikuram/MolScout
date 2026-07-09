"""About page."""

from __future__ import annotations

import streamlit as st

from app_core.paths import AUTO_REFRESH_SECONDS, SESSION_RETENTION_DAYS


st.markdown("# :material/science: MolScout Remote Queue")
st.caption(
    "Laboratory queue for shared execution of long-running workflows."
    "Includes user sessions and server monitoring."
)
st.markdown(
    '<span class="app-badge">1 worker</span>'
    f'<span class="app-badge">{AUTO_REFRESH_SECONDS}s refresh</span>'
    f'<span class="app-badge">{SESSION_RETENTION_DAYS} day retention</span>',
    unsafe_allow_html=True,
)

st.divider()

st.markdown("### Page guide")
st.markdown(
    """
- **Queue**: View the shared queue and selected session overview.
- **Submit**: Submit reaction-path and file-concatenation jobs.
- **Results**: Inspect session jobs, logs, output files, and ZIP downloads.
- **PySCF**: Edit PySCF settings for the selected session.
"""
)

st.markdown("### Sidebar")
st.markdown(
    """
Session creation and selection, monitoring, environment checks, sample listings, worker logs, and cleanup are available from the shared sidebar on every page.
Cleanup runs from a confirmation dialog.
"""
)
