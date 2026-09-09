"""Chemiscope trajectory visualization page."""

from __future__ import annotations

import fnmatch
import hashlib
import re
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

from app_ui.i18n import t, tf

from app_core.session_manager import get_job, get_session, session_dir
from app_core.trajectory_viewer import (
    DEFAULT_MAX_FRAMES,
    build_chemiscope_dataset,
    build_frame_table,
    finite_column,
    load_frame_properties_csv,
    load_structures,
    merge_frame_properties,
    render_passive_chemiscope,
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

SCAN_COLUMN_PATTERN = re.compile(
    r"^SCAN_(bond|angle|dihedral)(?:_[0-9]+(?:_[0-9]+)*)? \[(Å|deg)\]$"
)
SCAN_UNIFIED_COLUMNS = {
    ("bond", "Å"): "SCAN_bond [Å]",
    ("angle", "deg"): "SCAN_angle [deg]",
    ("dihedral", "deg"): "SCAN_dihedral [deg]",
}


def scan_column_kind(column: str) -> tuple[str, str] | None:
    """Return the SCAN coordinate kind/unit encoded in a result CSV column."""
    match = SCAN_COLUMN_PATTERN.match(str(column))
    if not match:
        return None
    scan_type, unit = match.groups()
    expected_unit = "Å" if scan_type == "bond" else "deg"
    if unit != expected_unit:
        return None
    return scan_type, unit


def unify_scan_target_columns(
    frame_table: pd.DataFrame,
) -> tuple[pd.DataFrame, str | None, str]:
    """Add one common SCAN coordinate column across compatible sources.

    Atom indices are deliberately ignored, but coordinate types are never mixed:
    bond, angle, and dihedral scans remain distinct even when their units match.
    Original per-target columns are preserved for inspection.
    """
    scan_columns = {
        str(column): scan_column_kind(str(column))
        for column in frame_table.columns
        if scan_column_kind(str(column)) is not None
    }
    if not scan_columns:
        return frame_table, None, "no recognized SCAN coordinate columns were found"

    if "source" in frame_table.columns:
        source_groups = [
            (str(source), group.index)
            for source, group in frame_table.groupby("source", sort=False, dropna=False)
        ]
    else:
        source_groups = [("dataset", frame_table.index)]

    source_columns: dict[str, str] = {}
    source_kinds: dict[str, tuple[str, str]] = {}
    for source, indices in source_groups:
        present: list[tuple[str, tuple[str, str]]] = []
        for column, kind in scan_columns.items():
            values = pd.to_numeric(frame_table.loc[indices, column], errors="coerce")
            if np.isfinite(values.to_numpy(dtype=float)).any():
                present.append((column, kind))

        if not present:
            return (
                frame_table,
                None,
                f"source `{source}` has no recognized SCAN coordinate",
            )

        kinds = {kind for _, kind in present}
        if len(kinds) != 1:
            labels = ", ".join(sorted(kind[0] for kind in kinds))
            return (
                frame_table,
                None,
                f"source `{source}` contains mixed SCAN coordinate types ({labels})",
            )
        if len(present) != 1:
            columns = ", ".join(column for column, _ in present)
            return (
                frame_table,
                None,
                f"source `{source}` has multiple SCAN target columns ({columns})",
            )

        source_columns[source] = present[0][0]
        source_kinds[source] = present[0][1]

    selected_kinds = set(source_kinds.values())
    if len(selected_kinds) != 1:
        labels = ", ".join(sorted(kind[0] for kind in selected_kinds))
        return (
            frame_table,
            None,
            f"selected trajectories use different SCAN coordinate types ({labels})",
        )

    common_kind = next(iter(selected_kinds))
    unified_column = SCAN_UNIFIED_COLUMNS[common_kind]
    unified_values = pd.Series(np.nan, index=frame_table.index, dtype=float)
    for source, indices in source_groups:
        source_column = source_columns[source]
        unified_values.loc[indices] = pd.to_numeric(
            frame_table.loc[indices, source_column], errors="coerce"
        ).to_numpy(dtype=float)

    result = frame_table.copy()
    result[unified_column] = unified_values
    finite_count = int(np.isfinite(unified_values.to_numpy(dtype=float)).sum())
    if finite_count == len(result):
        detail = f"{common_kind[0]} targets were unified as `{unified_column}`"
    else:
        detail = (
            f"{common_kind[0]} targets were unified as `{unified_column}`, but "
            f"{len(result) - finite_count} frame(s) have no finite coordinate"
        )
    return result, unified_column, detail


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


def trajectory_job_id(rel_path: str) -> str:
    parts = Path(str(rel_path)).parts
    if len(parts) >= 3 and parts[0] == "jobs":
        return str(parts[1])
    return ""


def trajectory_path_within_job(rel_path: str) -> str:
    parts = Path(str(rel_path)).parts
    if len(parts) >= 3 and parts[0] == "jobs":
        return Path(*parts[2:]).as_posix()
    return Path(str(rel_path)).as_posix()


def trajectory_role(path: Path) -> str:
    name = path.name
    if name == "init_path.traj":
        return "Initial path"
    if name == "irc.traj":
        return "IRC"
    if name == "optpoints.traj":
        return "Opt points"
    if name.endswith("_tsopt.traj"):
        return "TS optimization"
    if name.endswith("_opt.traj"):
        return "Optimization"
    return "Trajectory"


def companion_result_csv(trajectory_path: Path, job: dict | None) -> Path | None:
    """Return the result CSV paired with an init_path trajectory, when present.

    MolScout artifact organization places CSV files under ``Tables/`` in normal
    completed jobs, while older/imported jobs can still keep ``result.csv`` next
    to the trajectory. Check both layouts and then fall back to a bounded recursive
    search under the recorded output directory.
    """
    if trajectory_path.name != "init_path.traj":
        return None

    result_name = Path(str((job or {}).get("result_name") or "result.csv")).name
    candidates = [
        trajectory_path.parent / result_name,
        trajectory_path.parent / "Tables" / result_name,
    ]

    output_dir_text = str((job or {}).get("output_dir") or "").strip()
    output_dir = Path(output_dir_text) if output_dir_text else None
    if output_dir is not None:
        candidates.extend(
            [
                output_dir / result_name,
                output_dir / "Tables" / result_name,
            ]
        )
        try:
            relative_parent = trajectory_path.parent.relative_to(output_dir)
        except ValueError:
            relative_parent = None
        if relative_parent is not None:
            candidates.append(output_dir / "Tables" / relative_parent / result_name)
        if output_dir.exists():
            candidates.extend(sorted(output_dir.rglob(result_name)))

    seen: set[str] = set()
    for candidate in candidates:
        candidate_key = str(candidate)
        if candidate_key in seen:
            continue
        seen.add(candidate_key)
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def property_source_label(
    result_csv: Path, trajectory_path: Path, job: dict | None = None
) -> str:
    """Return a compact path label for the CSV property source."""
    output_dir_text = str((job or {}).get("output_dir") or "").strip()
    if output_dir_text:
        try:
            return result_csv.relative_to(Path(output_dir_text)).as_posix()
        except ValueError:
            pass
    try:
        return result_csv.relative_to(trajectory_path.parent).as_posix()
    except ValueError:
        return result_csv.name


def expected_result_name(job: dict | None) -> str:
    return Path(str((job or {}).get("result_name") or "result.csv")).name


def numeric_columns(df: pd.DataFrame) -> list[str]:
    columns: list[str] = []
    for column in df.columns:
        values = pd.to_numeric(df[column], errors="coerce").to_numpy(dtype=float)
        if np.isfinite(values).any():
            columns.append(str(column))
    return columns


def prioritized_plot_columns(columns: list[str]) -> list[str]:
    priority: list[str] = []
    for unified_column in SCAN_UNIFIED_COLUMNS.values():
        if unified_column in columns and unified_column not in priority:
            priority.append(unified_column)
    priority.extend(
        column
        for column in columns
        if column.startswith("SCAN_") and column not in priority
    )
    for preferred in [
        "Delta E vs. reactant [kcal/mol]",
        "Heavy-RMSD vs frame 0 [Å]",
        "Heavy-RMSD vs prev frame [Å]",
        "relative_energy",
        "energy",
        "energy [eV]",
        "max_force",
        "mean_force",
        "step",
    ]:
        if preferred in columns and preferred not in priority:
            priority.append(preferred)
    priority.extend(column for column in columns if column not in priority)
    return priority


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

jobs_by_id = {str(item.get("job_id") or ""): item for item in selected_jobs}
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
    top_cols = st.columns([1, 1, 1, 1])
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
        "Max frames / file",
        min_value=1,
        max_value=10000,
        value=DEFAULT_MAX_FRAMES,
        step=50,
        key=f"{session_id}_chemiscope_max_frames",
    )
    if top_cols[3].button(":material/refresh: Refresh scan", width="stretch"):
        scan_trajectory_files.clear()
        load_structures.clear()
        load_frame_properties_csv.clear()
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

prepared_files_df = filtered_files_df.copy()
prepared_files_df["job_id"] = prepared_files_df["rel_path"].map(trajectory_job_id)
prepared_files_df["trajectory"] = prepared_files_df["rel_path"].map(trajectory_path_within_job)
prepared_files_df["role"] = prepared_files_df["path"].map(lambda value: trajectory_role(Path(str(value))))
prepared_files_df["size"] = prepared_files_df["path"].map(
    lambda value: file_size_label(Path(str(value)))
)

property_sources: list[str] = []
for _, file_row in prepared_files_df.iterrows():
    trajectory_path = Path(str(file_row["path"]))
    job_id = str(file_row["job_id"] or "")
    job = jobs_by_id.get(job_id)
    result_csv = companion_result_csv(trajectory_path, job)
    if result_csv is not None:
        property_sources.append(property_source_label(result_csv, trajectory_path, job))
    elif trajectory_path.name == "init_path.traj":
        property_sources.append(f"{expected_result_name(job)} (missing)")
    else:
        property_sources.append("-")
prepared_files_df["property_source"] = property_sources

st.markdown("#### Found trajectory files")
st.caption(tf(
    "Files matching `{filter_name}`: {count:,}",
    filter_name=selected_filter,
    count=len(prepared_files_df),
))
if include_xyz and selected_filter == "*.traj":
    st.caption(t('XYZ and extxyz files are also shown because Include XYZ is enabled.'))

selection_signature = hashlib.sha1(
    "|".join(prepared_files_df["rel_path"].astype(str)).encode("utf-8")
).hexdigest()[:12]
trajectory_table_key = (
    f"{session_id}_chemiscope_trajectory_table_"
    f"{'-'.join(selected_job_ids)}_{selection_signature}"
)

if trajectory_table_key not in st.session_state:
    preferred_rows = prepared_files_df.index[
        (prepared_files_df["job_id"] == focused_job_id)
        & prepared_files_df["trajectory"].astype(str).map(lambda value: Path(value).name == "init_path.traj")
    ].tolist()
    if not preferred_rows:
        preferred_rows = prepared_files_df.index[
            prepared_files_df["job_id"] == focused_job_id
        ].tolist()
    default_row = int(preferred_rows[0]) if preferred_rows else 0
    st.session_state[trajectory_table_key] = {"selection": {"rows": [default_row]}}

selection_event = st.dataframe(
    prepared_files_df[
        ["job_id", "trajectory", "role", "size", "modified", "property_source"]
    ].rename(
        columns={
            "job_id": "Job",
            "trajectory": "Trajectory",
            "role": "Role",
            "size": "Size",
            "modified": "Modified",
            "property_source": "Property source",
        }
    ),
    hide_index=True,
    width="stretch",
    height=min(360, 36 + 35 * len(prepared_files_df)),
    on_select="rerun",
    selection_mode="multi-row",
    key=trajectory_table_key,
    column_config={
        "Job": st.column_config.TextColumn("Job", width="medium"),
        "Trajectory": st.column_config.TextColumn("Trajectory", width="large"),
        "Role": st.column_config.TextColumn("Role", width="small"),
        "Size": st.column_config.TextColumn("Size", width="small"),
        "Modified": st.column_config.TextColumn("Modified", width="medium"),
        "Property source": st.column_config.TextColumn("Property source", width="medium"),
    },
)
selected_indices = [
    index
    for index in selection_event.selection.rows
    if isinstance(index, int) and 0 <= index < len(prepared_files_df)
]
if not selected_indices:
    st.info("Select one or more trajectory rows to visualize.")
    st.stop()

selected_file_rows = prepared_files_df.iloc[selected_indices].reset_index(drop=True)
st.caption(f"Selected trajectories: {len(selected_file_rows):,}")

selection_kind = "single" if len(selected_file_rows) == 1 else "multi"
join_points_key = f"{session_id}_chemiscope_join_points"
join_context_key = f"{session_id}_chemiscope_join_points_context"
if st.session_state.get(join_context_key) != selection_kind:
    st.session_state[join_points_key] = selection_kind == "single"
    st.session_state[join_context_key] = selection_kind

with st.container(border=True):
    multi_trajectory = len(selected_file_rows) > 1
    view_cols = st.columns([1, 1, 1, 1] if multi_trajectory else [1, 1, 1])
    join_points = view_cols[0].toggle(
        "Join points",
        key=join_points_key,
        help=(
            "For multiple trajectories this is disabled by default. Enabling it "
            "connects points in dataset order, including the boundary between sources."
        ),
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
    unify_scan_targets = False
    if multi_trajectory:
        unify_scan_key = f"{session_id}_chemiscope_unify_scan_targets"
        st.session_state.setdefault(unify_scan_key, True)
        unify_scan_targets = bool(
            view_cols[3].toggle(
                "Unify SCAN targets",
                key=unify_scan_key,
                help=(
                    "Treat different atom-index targets as one comparison axis when "
                    "all selected trajectories use the same coordinate type. Bond, "
                    "angle, and dihedral scans are never mixed."
                ),
            )
        )
    if len(selected_file_rows) > 1 and join_points:
        st.caption(
            "Join points is enabled for a combined dataset; the last frame of one "
            "trajectory will also connect to the first frame of the next trajectory."
        )

all_structures: list = []
frame_tables: list[pd.DataFrame] = []
source_starts: list[int] = []
loaded_trajectories: list[dict] = []
csv_fields: set[str] = set()
property_messages: list[str] = []

offset = 0
for _, selected_row in selected_file_rows.iterrows():
    selected_path = Path(str(selected_row["path"]))
    rel_path = str(selected_row["rel_path"])
    job_id = str(selected_row["job_id"] or "")
    job = jobs_by_id.get(job_id)

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
        st.error(f"Failed to read {rel_path}")
        st.exception(error)
        st.stop()

    if not structures:
        st.warning(f"No structures could be read from {rel_path}.")
        st.stop()

    local_table = build_frame_table(structures)
    result_csv = companion_result_csv(selected_path, job)
    property_source = "-"
    if result_csv is not None:
        try:
            csv_stat = result_csv.stat()
            result_df = load_frame_properties_csv(
                str(result_csv),
                int(csv_stat.st_mtime_ns),
                int(csv_stat.st_size),
            )
            local_table, added_columns = merge_frame_properties(local_table, result_df)
            csv_fields.update(added_columns)
            property_source = property_source_label(result_csv, selected_path, job)
        except Exception as error:
            property_source = f"{result_csv.name} (error)"
            property_messages.append(
                f"`{job_id}/{selected_path.name}`: failed to load `{result_csv.name}` ({error})"
            )
    elif selected_path.name == "init_path.traj":
        missing_name = expected_result_name(job)
        property_source = f"{missing_name} (missing)"
        property_messages.append(
            f"`{job_id}/{selected_path.name}`: `{missing_name}` was not found; "
            "trajectory metadata is still available."
        )

    inside_job = trajectory_path_within_job(rel_path)
    source_label = f"{job_id}/{inside_job}" if job_id else rel_path
    local_table["dataset_index"] = np.arange(offset, offset + len(local_table), dtype=int)
    local_table["job"] = job_id or "-"
    local_table["trajectory"] = selected_path.name
    local_table["source"] = source_label
    local_table["property_source"] = property_source

    metadata_columns = [
        "dataset_index",
        "job",
        "trajectory",
        "source",
        "step",
        "property_source",
    ]
    remaining_columns = [
        column for column in local_table.columns if column not in metadata_columns
    ]
    local_table = local_table[[*metadata_columns, *remaining_columns]]

    source_starts.append(offset)
    all_structures.extend(structures)
    frame_tables.append(local_table)
    loaded_trajectories.append(
        {
            "job_id": job_id,
            "path": selected_path,
            "rel_path": rel_path,
            "mtime_ns": int(selected_row["mtime_ns"]),
            "size_bytes": int(selected_row["size_bytes"]),
            "frames": len(structures),
            "property_source": property_source,
        }
    )
    offset += len(structures)

frame_table = pd.concat(frame_tables, ignore_index=True, sort=False)
structures = all_structures

scan_unified_column: str | None = None
scan_unify_detail = ""
if len(loaded_trajectories) > 1 and unify_scan_targets:
    frame_table, scan_unified_column, scan_unify_detail = unify_scan_target_columns(frame_table)

if property_messages:
    with st.expander("Property source warnings", expanded=False):
        for message in property_messages:
            st.warning(message)

metric_cols = st.columns(5)
metric_cols[0].metric("Frames loaded", f"{len(structures):,}")
metric_cols[1].metric("Trajectories", f"{len(loaded_trajectories):,}")

natoms_values = pd.to_numeric(frame_table["natoms"], errors="coerce").dropna().unique()
if len(natoms_values) == 1:
    metric_cols[2].metric("Atoms", f"{int(natoms_values[0]):,}")
else:
    metric_cols[2].metric("Atoms", "mixed")

formula_values = frame_table["formula"].dropna().astype(str).unique().tolist()
if len(formula_values) == 1:
    formula_label = formula_values[0]
elif formula_values:
    formula_label = f"{len(formula_values)} formulas"
else:
    formula_label = "n/a"
metric_cols[3].metric("Formula", formula_label)

if finite_column(frame_table, "relative_energy"):
    relative = pd.to_numeric(frame_table["relative_energy"], errors="coerce")
    span = relative.max() - relative.min()
    metric_cols[4].metric("Energy span", f"{span:.3f} eV")
else:
    metric_cols[4].metric("Energy span", "n/a")

if csv_fields:
    st.caption(
        f"CSV fields merged: {len(csv_fields):,} / "
        + ", ".join(sorted(csv_fields))
    )
else:
    st.caption("CSV fields merged: 0")

if len(loaded_trajectories) > 1 and unify_scan_targets:
    if scan_unified_column:
        st.caption(
            f"SCAN target unification: {scan_unify_detail}. Original target columns are retained."
        )
    else:
        st.caption(f"SCAN target unification skipped: {scan_unify_detail}.")

left, right = st.columns([1, 2])
with left:
    st.markdown("#### Frame properties")
    st.dataframe(frame_table, hide_index=True, height=420, width="stretch")
with right:
    st.markdown("#### Quick plot")
    available_numeric = numeric_columns(frame_table)
    plot_columns = prioritized_plot_columns(available_numeric)
    x_options = [column for column in plot_columns if column not in {"dataset_index", "natoms"}]
    y_options = [column for column in plot_columns if column not in {"dataset_index", "natoms", "step"}]

    if x_options and y_options:
        default_x = next((column for column in x_options if column.startswith("SCAN_")), "step")
        if default_x not in x_options:
            default_x = x_options[0]
        default_y = next(
            (
                column
                for column in [
                    "Delta E vs. reactant [kcal/mol]",
                    "relative_energy",
                    "energy",
                    "energy [eV]",
                ]
                if column in y_options
            ),
            y_options[0],
        )

        x_key = f"{session_id}_chemiscope_x_axis"
        y_key = f"{session_id}_chemiscope_y_axis"
        if st.session_state.get(x_key) not in x_options:
            st.session_state[x_key] = default_x
        if st.session_state.get(y_key) not in y_options:
            st.session_state[y_key] = default_y

        axis_cols = st.columns(2)
        plot_x = axis_cols[0].selectbox("X axis", options=x_options, key=x_key)
        plot_y = axis_cols[1].selectbox("Y axis", options=y_options, key=y_key)

        # Build the plotting frame from independent Series. If X and Y use the
        # same source property, selecting columns by label first would create
        # duplicate column names and ``plot_df[plot_x]`` would become a 2-D
        # DataFrame, which ``pd.to_numeric`` cannot accept.
        plot_df = pd.DataFrame(
            {
                "__plot_x": pd.to_numeric(frame_table[plot_x], errors="coerce"),
                "__plot_y": pd.to_numeric(frame_table[plot_y], errors="coerce"),
                "source": frame_table["source"].astype(str),
            }
        )
        plot_df = plot_df.replace([np.inf, -np.inf], np.nan).dropna(
            subset=["__plot_x", "__plot_y"]
        )
        if plot_df.empty:
            st.info("No finite values are available for the selected axes.")
        elif len(loaded_trajectories) > 1:
            pivot = plot_df.pivot_table(
                index="__plot_x",
                columns="source",
                values="__plot_y",
                aggfunc="first",
            ).sort_index()
            pivot.index.name = str(plot_x)
            st.line_chart(pivot, height=320)
        else:
            single_plot = plot_df.sort_values("__plot_x").rename(
                columns={"__plot_x": str(plot_x), "__plot_y": str(plot_y)}
            )
            st.line_chart(single_plot, x=str(plot_x), y=str(plot_y), height=320)
    else:
        st.info(t('No numeric energy or force properties were found in Atoms.info or arrays.'))

st.markdown("#### Structure viewer")
if len(source_starts) > 9:
    st.caption(
        "Chemiscope can pin up to 9 structures initially. The first frame of the "
        "first 9 selected trajectories will be opened in parallel viewers; all "
        "selected frames remain in the dataset."
    )

try:
    import chemiscope.streamlit

    source_name = (
        Path(str(loaded_trajectories[0]["path"])).name
        if len(loaded_trajectories) == 1
        else f"{len(loaded_trajectories)} selected trajectories"
    )
    dataset, settings = build_chemiscope_dataset(
        structures=structures,
        frame_table=frame_table,
        source_name=source_name,
        join_points=bool(join_points),
        playback_delay=int(playback_delay),
        pinned_indices=source_starts,
    )
    with st.expander("Chemiscope settings", expanded=False):
        st.json(settings)

    viewer_mode = str(mode or "default")
    viewer_identity_parts = [
        (
            f"{item['rel_path']}:{item['mtime_ns']}:{item['size_bytes']}:"
            f"{item['property_source']}"
        )
        for item in loaded_trajectories
    ]
    viewer_identity = (
        "|".join(viewer_identity_parts)
        + f"|join={int(bool(join_points))}|delay={int(playback_delay)}"
    )
    component_key = viewer_key(
        viewer_identity,
        len(structures),
        viewer_mode,
        max(int(item["mtime_ns"]) for item in loaded_trajectories),
    )

    @st.fragment
    def render_chemiscope():
        # Keep animation state inside Chemiscope. The official Streamlit wrapper
        # echoes the active structure index back to Python and can re-apply a
        # stale index on the next rerun while an animation is still advancing.
        render_passive_chemiscope(
            dataset,
            mode=viewer_mode,
            key=component_key,
            width="stretch",
            height=720,
        )

    render_chemiscope()
except ImportError as error:
    render_dependency_hint(error)
except Exception as error:
    st.error("Chemiscope failed to render the selected trajectories.")
    st.exception(error)

traj_downloads = [
    item for item in loaded_trajectories if Path(str(item["path"])).suffix.lower() == ".traj"
]
if traj_downloads:
    st.markdown(t('#### Download trajectory'))
    for item in traj_downloads:
        selected_path = Path(str(item["path"]))
        try:
            extxyz_data = trajectory_to_extxyz(
                str(selected_path),
                int(item["mtime_ns"]),
                int(item["size_bytes"]),
            )
            if extxyz_data:
                job_id = str(item.get("job_id") or "")
                label = (
                    t('Download selected trajectory as extxyz')
                    if len(traj_downloads) == 1
                    else f"Download {job_id}/{selected_path.name} as extxyz"
                )
                file_prefix = f"{job_id}_" if len(traj_downloads) > 1 and job_id else ""
                button_digest = hashlib.sha1(str(item["rel_path"]).encode("utf-8")).hexdigest()[:12]
                st.download_button(
                    label,
                    data=extxyz_data,
                    file_name=f"{file_prefix}{selected_path.name}.xyz",
                    mime="chemical/x-xyz",
                    width="stretch",
                    key=f"chemiscope_extxyz_{button_digest}",
                )
            else:
                st.warning(f"{item['rel_path']} contains no frames that can be exported.")
        except ImportError as error:
            render_dependency_hint(error)
            break
        except Exception as error:
            st.error(f"Failed to convert {item['rel_path']} to extxyz.")
            st.exception(error)
