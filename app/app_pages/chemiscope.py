"""Chemiscope trajectory visualization page."""

from __future__ import annotations

import fnmatch
from pathlib import Path

import pandas as pd
import streamlit as st

from app_ui.i18n import t, tf

from app_core.session_manager import get_job, get_session, session_dir
from app_core.trajectory_viewer import (
    DEFAULT_MAX_FRAMES,
    build_chemiscope_dataset,
    build_frame_table,
    finite_column,
    load_structures,
    scan_trajectory_files,
    trajectory_to_extxyz,
    viewer_key,
)
from app_ui.sidebar import database_job_selection, database_selection
from app_ui.views import file_size_label


TRAJECTORY_FILTERS = (
    "*.traj",
    "init_path.traj",
    "irc.traj",
    "optpoints.traj",
    "*_opt.traj",
    "*_tsopt.traj",
)


def filter_trajectory_files(files_df: pd.DataFrame, pattern: str) -> pd.DataFrame:
    if files_df.empty or pattern == "*.traj":
        return files_df.reset_index(drop=True)

    names = files_df["rel_path"].map(lambda value: Path(str(value)).name)
    mask = names.map(lambda name: fnmatch.fnmatchcase(name, pattern))
    return files_df[mask].reset_index(drop=True)


def render_dependency_hint(error: Exception) -> None:
    st.error(t('Required dependencies for trajectory visualization are missing.'))
    st.code(
        "pip install ase pandas numpy 'chemiscope[streamlit]'",
        language="bash",
    )
    st.exception(error)


def filter_files_for_jobs(files_df: pd.DataFrame, job_ids: list[str]) -> pd.DataFrame:
    prefixes = tuple(f"jobs/{job_id}/" for job_id in job_ids)
    return files_df[files_df["rel_path"].astype(str).str.startswith(prefixes)].reset_index(drop=True)


st.set_page_config(page_title="MolScout [Chemiscope]")
st.markdown("## :material/animation: Chemiscope")
st.caption(t('Inspect trajectory and XYZ files from the selected session with Chemiscope.'))

session_id, selected_job_ids = database_job_selection()
_, focused_job_id = database_selection()
if not session_id:
    st.info(t('Select a session from the Database sidebar.'))
    st.stop()
if not selected_job_ids:
    st.info(t('Select one or more jobs from the Session Jobs table in the Database sidebar.'))
    st.stop()

session = get_session(session_id)
if not session:
    st.warning(t('The selected session does not exist in the database. Use Refresh in the sidebar to reload the options.'))
    st.stop()

selected_jobs = []
valid_job_ids = []
for selected_job_id in selected_job_ids:
    selected_job = get_job(session_id, selected_job_id)
    if selected_job:
        valid_job_ids.append(selected_job_id)
        selected_jobs.append(selected_job)
selected_job_ids = valid_job_ids
if not selected_job_ids:
    st.warning(t('The selected job does not exist in the database. Use Refresh in the sidebar to reload the options.'))
    st.stop()
if focused_job_id not in selected_job_ids:
    focused_job_id = selected_job_ids[0]

root = session_dir(session_id)
st.caption(
    f"Session `{session_id}` / Selected jobs {len(selected_job_ids)} / Target Job `{focused_job_id}`"
)
if len(selected_jobs) > 1:
    st.dataframe(
        [
            {
                "Job": str(item.get("job_id") or "-"),
                "Workflow": str(item.get("workflow") or item.get("name") or "-"),
                "Status": str(item.get("status") or "-"),
                "Job Note": str(item.get("notes") or ""),
            }
            for item in selected_jobs
        ],
        hide_index=True,
        width="stretch",
        height=min(210, 36 + 35 * len(selected_jobs)),
    )

with st.container(border=True):
    top_cols = st.columns([1, 1, 1])
    include_xyz = top_cols[0].toggle(
        "Include XYZ",
        value=False,
        key=f"{session_id}_chemiscope_include_xyz",
    )
    max_files = top_cols[1].number_input(
        "Max files",
        min_value=10,
        max_value=5000,
        value=500,
        step=10,
        key=f"{session_id}_chemiscope_max_files",
    )
    max_frames = top_cols[2].number_input(
        "Max frames",
        min_value=1,
        max_value=10000,
        value=DEFAULT_MAX_FRAMES,
        step=50,
        key=f"{session_id}_chemiscope_max_frames",
    )

    view_cols = st.columns([1, 1, 1, 1])
    join_points = view_cols[0].toggle(
        "Join points",
        value=True,
        key=f"{session_id}_chemiscope_join_points",
    )
    playback_delay = view_cols[1].slider(
        "Playback delay ms",
        min_value=20,
        max_value=1000,
        value=100,
        step=20,
        key=f"{session_id}_chemiscope_playback_delay",
    )
    mode = view_cols[2].segmented_control(
        "Viewer mode",
        options=["default", "structure", "map"],
        default="default",
        key=f"{session_id}_chemiscope_mode",
    )
    if view_cols[3].button(":material/refresh: Refresh scan", width="stretch"):
        scan_trajectory_files.clear()
        load_structures.clear()
        st.rerun()

files_df = scan_trajectory_files(str(root), bool(include_xyz), int(max_files))
if not files_df.empty:
    files_df = filter_files_for_jobs(files_df, selected_job_ids)

if files_df.empty:
    st.info(t('No trajectory files were found in the selected jobs.'))
    st.stop()

filter_key = f"{session_id}_chemiscope_traj_filter_{'-'.join(selected_job_ids)}"
selected_filter = st.segmented_control(
    t('Filename filter'),
    options=list(TRAJECTORY_FILTERS),
    default="*.traj",
    key=filter_key,
)
filtered_files_df = filter_trajectory_files(files_df, str(selected_filter or "*.traj"))

if filtered_files_df.empty:
    st.info(t('No trajectory files match this filter.'))
    st.stop()

st.markdown("#### Found trajectory files")
st.caption(tf(
    "Files matching `{filter_name}`: {count:,}",
    filter_name=selected_filter,
    count=len(filtered_files_df),
))
if include_xyz and selected_filter == "*.traj":
    st.caption(t('XYZ and extxyz files are also shown because Include XYZ is enabled.'))
display_df = filtered_files_df[["rel_path", "suffix", "size_kb", "modified"]].copy()
display_df["size"] = filtered_files_df["path"].map(
    lambda value: file_size_label(Path(str(value)))
)
st.dataframe(
    display_df[["rel_path", "suffix", "size", "modified"]],
    hide_index=True,
    height=240,
)

file_key = f"{session_id}_chemiscope_file_{'-'.join(selected_job_ids)}"
file_options = filtered_files_df["rel_path"].tolist()
if st.session_state.get(file_key) not in file_options:
    st.session_state[file_key] = file_options[0]
selected_rel = st.selectbox(
    "Trajectory file",
    options=file_options,
    key=file_key,
)
selected_row = filtered_files_df.loc[filtered_files_df["rel_path"] == selected_rel].iloc[0]
selected_path = Path(str(selected_row["path"]))

try:
    structures = load_structures(
        str(selected_path),
        int(selected_row["mtime_ns"]),
        int(selected_row["size_bytes"]),
        int(max_frames),
    )
except ImportError as error:
    render_dependency_hint(error)
    st.stop()
except Exception as error:
    st.error(f"Failed to read {selected_path.name}")
    st.exception(error)
    st.stop()

if not structures:
    st.warning(t('No structures could be read from the selected file.'))
    st.stop()

frame_table = build_frame_table(structures)

metric_cols = st.columns(4)
metric_cols[0].metric("Frames loaded", f"{len(structures):,}")
metric_cols[1].metric("Atoms", f"{int(frame_table['natoms'].iloc[0]):,}")
metric_cols[2].metric("Formula", str(frame_table["formula"].iloc[0]))
if finite_column(frame_table, "relative_energy"):
    span = frame_table["relative_energy"].max() - frame_table["relative_energy"].min()
    metric_cols[3].metric("Energy span", f"{span:.3f} eV")
else:
    metric_cols[3].metric("Energy span", "n/a")

left, right = st.columns([1, 2])
with left:
    st.markdown("#### Frame properties")
    st.dataframe(frame_table, hide_index=True, height=360)
with right:
    st.markdown("#### Quick plot")
    plot_cols = [
        column
        for column in ["relative_energy", "energy", "max_force", "mean_force"]
        if finite_column(frame_table, column)
    ]
    if plot_cols:
        plot_col = st.selectbox("Y axis", options=plot_cols, key=f"{session_id}_chemiscope_y_axis")
        st.line_chart(frame_table, x="step", y=plot_col, height=320)
    else:
        st.info(t('No numeric energy or force properties were found in Atoms.info or arrays.'))

st.markdown("#### Structure viewer")
try:
    import chemiscope.streamlit

    dataset, settings = build_chemiscope_dataset(
        structures=structures,
        frame_table=frame_table,
        source_name=selected_path.name,
        join_points=bool(join_points),
        playback_delay=int(playback_delay),
    )
    with st.expander("Chemiscope settings", expanded=False):
        st.json(settings)
    @st.fragment
    def render_chemiscope():
        chemiscope.streamlit.viewer(
            dataset, 
            mode=str(mode or "default"),
            key=viewer_key(str(selected_path), len(structures)),
            width="stretch", height=720,
        )
    render_chemiscope()
except ImportError as error:
    render_dependency_hint(error)
except Exception as error:
    st.error("Chemiscope failed to render this trajectory.")
    st.exception(error)

if selected_path.suffix.lower() == ".traj":
    st.markdown(t('#### Download trajectory'))
    try:
        extxyz_data = trajectory_to_extxyz(
            str(selected_path),
            int(selected_row["mtime_ns"]),
            int(selected_row["size_bytes"]),
        )
        if extxyz_data:
            st.download_button(
                t('Download selected trajectory as extxyz'),
                data=extxyz_data,
                file_name=f"{selected_path.name}.xyz",
                mime="chemical/x-xyz",
                width="stretch",
            )
        else:
            st.warning(t('The selected trajectory contains no frames that can be exported.'))
    except ImportError as error:
        render_dependency_hint(error)
    except Exception as error:
        st.error(t('Failed to convert the trajectory to extxyz.'))
        st.exception(error)

