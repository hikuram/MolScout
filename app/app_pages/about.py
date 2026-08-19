"""About page."""

from __future__ import annotations

import streamlit as st

from app_ui.i18n import t

from app_core.paths import AUTO_REFRESH_SECONDS


st.set_page_config(page_title="MolScout [About]")
st.markdown("# :material/science: MolScout Remote Queue")
st.caption(
    t('Laboratory queue for shared execution of long-running workflows. Includes user sessions and server monitoring.')
)
st.markdown(
    '<span class="app-badge">1 worker</span>'
    f'<span class="app-badge">{AUTO_REFRESH_SECONDS}s refresh</span>'
    '<span class="app-badge">PostgreSQL metadata</span>',
    unsafe_allow_html=True,
)

st.divider()

st.markdown("### Page guide")
st.markdown(
    t('\n- **Queue**: View the shared queue and the selected session overview.\n- **Submit**: Submit reaction-path searches and file-concatenation jobs.\n- **Results**: Inspect session jobs, logs, result files, and ZIP downloads.\n- **Chemiscope**: Visualize trajectories and structures.\n- **Data**: Search artifacts across sessions and diagnose DB/filesystem consistency.\n- **PySCF**: Edit PySCF settings for the selected session.\n')
)

st.markdown("### Sidebar")
st.markdown(
    t('\nSession creation and selection, monitoring, environment checks, sample inputs, and the worker log are available from the shared sidebar on every page.\nTime-based cleanup is disabled during the PostgreSQL migration.\n')
)
