"""About page."""

from __future__ import annotations

import streamlit as st

from app_core.config import APP_VERSION, REPOSITORY_URL
from app_core.paths import AUTO_REFRESH_SECONDS
from app_ui.i18n import t


st.set_page_config(page_title="MolScout [About]")
st.markdown("# :material/science: MolScout")
st.caption(
    t(
        "Reaction-path exploration and QC application for shared execution, "
        "review, and comparison of molecular calculations."
    )
)

info_cols = st.columns([1, 2])
with info_cols[0]:
    st.metric(t("Version"), f"v{APP_VERSION}")
with info_cols[1]:
    st.markdown(f"**Repository:** [{REPOSITORY_URL}]({REPOSITORY_URL})")
    st.caption(t("Source code, release history, and project documentation."))

st.markdown(
    '<span class="app-badge">1 worker</span>'
    f'<span class="app-badge">{AUTO_REFRESH_SECONDS}s refresh</span>'
    '<span class="app-badge">PostgreSQL metadata</span>',
    unsafe_allow_html=True,
)

st.divider()

st.markdown(t("### What MolScout does"))
st.markdown(
    t(
        "MolScout supports initial-path searches, TS optimization, IRC, VIB, SCAN / MF-SCAN, "
        "selected DFT refinement, and result review. Chemiscope can combine trajectories from "
        "multiple jobs with compatible CSV properties for comparison."
    )
)

st.markdown(t("### Important notes"))
st.markdown(
    t(
        "- Treat automatic path, TS, optpoints, SCAN, and vibration outputs as calculation aids, not final chemical judgments.\n"
        "- Inspect structures, energies, convergence, and imaginary modes before using results in downstream decisions.\n"
        "- JSON and command-line routes are intended for advanced or repeatable settings that are easier to preserve outside the GUI.\n"
    )
)

st.markdown(t("### Page guide"))
st.markdown(
    t(
        "\n- **Queue**: View the shared queue and the selected session overview.\n"
        "- **Submit**: Submit reaction-path searches and file-concatenation jobs.\n"
        "- **Submit (JSON)**: Reuse or submit detailed MolScout settings from JSON.\n"
        "- **Results**: Inspect session jobs, logs, result files, and ZIP downloads.\n"
        "- **Chemiscope**: Compare trajectories, companion CSV properties, and structures across selected jobs.\n"
        "- **Data**: Search artifacts across sessions and diagnose DB/filesystem consistency.\n"
        "- **PySCF**: Edit PySCF settings for the selected session.\n"
    )
)

st.markdown(t("### Sidebar"))
st.markdown(
    t(
        "\nSession creation and selection, monitoring, environment checks, sample inputs, and the worker log are available from the shared sidebar on every page.\n"
        "On Results / Chemiscope / Data, selected-job state can also be preserved in the Share URL shown after Refresh.\n"
        "Time-based cleanup is disabled during the PostgreSQL migration.\n"
    )
)
