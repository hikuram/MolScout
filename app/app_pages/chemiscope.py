"""Chemiscope trajectory visualization page."""

from __future__ import annotations

import fnmatch
import hashlib
from pathlib import Path

import pandas as pd
import streamlit as st

from app_core.session_manager import list_jobs, session_dir
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
from app_ui.sidebar import get_selected_session
from app_ui.views import file_size_label, format_app_time


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
    st.error("Trajectory visualization に必要な dependency が不足しています。")
    st.code(
        "pip install ase pandas numpy 'chemiscope[streamlit]'",
        language="bash",
    )
    st.exception(error)


def filter_files_for_jobs(files_df: pd.DataFrame, job_ids: list[str]) -> pd.DataFrame:
    prefixes = tuple(f"jobs/{job_id}/" for job_id in job_ids)
    return files_df[files_df["rel_path"].astype(str).str.startswith(prefixes)].reset_index(drop=True)


st.markdown("## :material/animation: Chemiscope")
st.caption("選択中セッションの trajectory / XYZ を chemiscope で確認します。")

session = get_selected_session()
if not session:
    st.info("サイドバーからセッションを作成してください。")
    st.stop()

session_id = session["session_id"]
root = session_dir(session_id)
jobs = list_jobs(session_id)

st.markdown("#### Session jobs")
st.caption(f"Directory to scan: `{root}`")

if not jobs:
    st.info("このセッションにはまだジョブがありません。")
    st.stop()

jobs_df = pd.DataFrame(jobs)
for col in ["job_id", "workflow", "status", "created_at", "notes"]:
    if col not in jobs_df.columns:
        jobs_df[col] = ""

display_jobs_df = jobs_df[["job_id", "workflow", "status", "created_at", "notes"]].copy()
display_jobs_df["created_at"] = display_jobs_df["created_at"].map(format_app_time)

job_id_signature = hashlib.sha1(
    "|".join(display_jobs_df["job_id"].astype(str).tolist()).encode("utf-8")
).hexdigest()[:12]
selection_event = st.dataframe(
    display_jobs_df,
    hide_index=True,
    on_select="rerun",
    selection_mode="multi-row",
    key=f"{session_id}_chemiscope_job_selection_{job_id_signature}",
    height=250,
    column_config={
        "job_id": "Job ID",
        "workflow": "Workflow",
        "status": "Status",
        "created_at": "Created",
        "notes": "Notes",
    },
)

raw_selected_indices = selection_event.selection.rows
selected_indices = [
    index
    for index in raw_selected_indices
    if isinstance(index, int) and 0 <= index < len(display_jobs_df)
]

if not selected_indices:
    st.info("👆 trajectory を探すジョブを上のテーブルで 1 件以上選択してください。")
    st.stop()

selected_job_ids = display_jobs_df.iloc[selected_indices]["job_id"].astype(str).tolist()

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
    st.info("選択した job 内に trajectory file が見つかりません。")
    st.stop()

filter_key = f"{session_id}_chemiscope_traj_filter_{'-'.join(selected_job_ids)}"
selected_filter = st.segmented_control(
    "ファイル名フィルター",
    options=list(TRAJECTORY_FILTERS),
    default="*.traj",
    key=filter_key,
)
filtered_files_df = filter_trajectory_files(files_df, str(selected_filter or "*.traj"))

if filtered_files_df.empty:
    st.info("このフィルターに一致する trajectory file はありません。")
    st.stop()

st.markdown("#### Found trajectory files")
st.caption(f"`{selected_filter}` に一致するファイル: {len(filtered_files_df):,} 件")
if include_xyz and selected_filter == "*.traj":
    st.caption("Include XYZ が有効なため、XYZ / extxyz file も表示しています。")
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
    st.warning("選択したファイルから structure を読み取れませんでした。")
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
        st.info("Atoms.info / arrays に energy または force の数値プロパティが見つかりません。")

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
    chemiscope.streamlit.viewer(
        dataset,
        mode=str(mode or "default"),
        key=viewer_key(str(selected_path), len(structures)),
        width="stretch",
        height=720,
    )
except ImportError as error:
    render_dependency_hint(error)
except Exception as error:
    st.error("Chemiscope failed to render this trajectory.")
    st.exception(error)

if selected_path.suffix.lower() == ".traj":
    st.markdown("#### trajectoryのダウンロード")
    try:
        extxyz_data = trajectory_to_extxyz(
            str(selected_path),
            int(selected_row["mtime_ns"]),
            int(selected_row["size_bytes"]),
        )
        if extxyz_data:
            st.download_button(
                "選択中のtrajectoryをextxyzでダウンロード",
                data=extxyz_data,
                file_name=f"{selected_path.name}.xyz",
                mime="chemical/x-xyz",
                width="stretch",
            )
        else:
            st.warning("選択したtrajectoryに出力可能なframeがありません。")
    except ImportError as error:
        render_dependency_hint(error)
    except Exception as error:
        st.error("extxyzへの変換に失敗しました。")
        st.exception(error)

