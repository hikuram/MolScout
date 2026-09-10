"""Chemiscope trajectory visualization page."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

from app_ui.i18n import t

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


TRAJECTORY_ROLE_FILTERS = (
    "All trajectories",
    "Initial path",
    "IRC",
    "Optpoints",
    "MF-SCAN",
    "Optimization",
    "TS optimization",
    "Other",
)

SCAN_COLUMN_PATTERN = re.compile(
    r"^SCAN_(bond|angle|dihedral)(?:_[0-9]+(?:_[0-9]+)*)? \[(Å|deg)\]$"
)
SCAN_UNIFIED_COLUMNS = {
    ("bond", "Å"): "SCAN_bond [Å]",
    ("angle", "deg"): "SCAN_angle [deg]",
    ("dihedral", "deg"): "SCAN_dihedral [deg]",
}

SERIES_LABEL_OPTIONS = (
    "Compact note",
    "Job Note",
    "Job ID",
    "Job Note + Job ID",
    "Job ID + Job Note",
    "Source path",
    "Custom",
)

COMPACT_NOTE_MAX_CHARS = 28


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


def compact_note_text(note: str, max_chars: int = COMPACT_NOTE_MAX_CHARS) -> str:
    """Return a short, single-line note suitable for plot legends."""
    first_line = str(note or "").strip().splitlines()[0] if str(note or "").strip() else ""
    compact = " ".join(first_line.split())
    if len(compact) <= max_chars:
        return compact
    if max_chars <= 1:
        return compact[:max_chars]
    return compact[: max_chars - 1].rstrip() + "…"


def short_job_tag(job_id: str) -> str:
    """Return a compact stable identifier for duplicate legend labels."""
    text = str(job_id or "-").strip() or "-"
    first = text.split("-", 1)[0]
    return first if first else text[:12]


def series_label_for_source(
    mode: str,
    *,
    job_id: str,
    job_note: str,
    source: str,
) -> str:
    """Return a readable series label while keeping empty notes usable."""
    job_id = str(job_id or "-")
    job_note = str(job_note or "").strip()
    source = str(source or job_id)
    if mode == "Compact note":
        return compact_note_text(job_note) or job_id
    if mode == "Job ID":
        return job_id
    if mode == "Job Note":
        return job_note or job_id
    if mode == "Job ID + Job Note":
        return f"{job_id} | {job_note}" if job_note else job_id
    if mode == "Source path":
        return source
    # Custom uses Compact note as its initial seed.
    return compact_note_text(job_note) or job_id


def compact_series_labels(rows: list[dict[str, str]]) -> dict[str, str]:
    """Build concise labels and disambiguate collisions without long legends."""
    bases = {
        row["source"]: series_label_for_source(
            "Compact note",
            job_id=row["job_id"],
            job_note=row["job_note"],
            source=row["source"],
        )
        for row in rows
    }

    grouped: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        grouped.setdefault(bases[row["source"]], []).append(row)

    labels: dict[str, str] = {}
    for base, members in grouped.items():
        if len(members) == 1:
            labels[members[0]["source"]] = base
            continue

        job_candidates = [f"{base} [{short_job_tag(row['job_id'])}]" for row in members]
        if len(set(job_candidates)) == len(job_candidates):
            for row, label in zip(members, job_candidates, strict=True):
                labels[row["source"]] = label
            continue

        # Multiple trajectories can belong to the same job. Add only the
        # trajectory basename in that less common case.
        for row in members:
            trajectory_tag = Path(row["trajectory"]).stem
            labels[row["source"]] = (
                f"{base} [{short_job_tag(row['job_id'])}:{trajectory_tag}]"
            )

    return labels


def selected_series_rows(
    selected_file_rows: pd.DataFrame,
    jobs_by_id: dict[str, dict],
) -> list[dict[str, str]]:
    """Build one metadata row per selected source trajectory."""
    rows: list[dict[str, str]] = []
    for _, selected_row in selected_file_rows.iterrows():
        rel_path = str(selected_row["rel_path"])
        job_id = str(selected_row["job_id"] or "")
        inside_job = trajectory_path_within_job(rel_path)
        source = f"{job_id}/{inside_job}" if job_id else rel_path
        job = jobs_by_id.get(job_id) or {}
        rows.append(
            {
                "source": source,
                "job_id": job_id or "-",
                "job_note": str(job.get("notes") or ""),
                "trajectory": inside_job,
            }
        )
    return rows


def filter_trajectory_files(files_df: pd.DataFrame, role_filter: str) -> pd.DataFrame:
    """Filter prepared trajectory rows by their analysis role."""
    if files_df.empty or role_filter == "All trajectories":
        return files_df.reset_index(drop=True)
    if "role" not in files_df.columns:
        return files_df.iloc[0:0].copy().reset_index(drop=True)
    return files_df[files_df["role"].astype(str) == role_filter].reset_index(drop=True)


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


def expected_result_name(job: dict | None) -> str:
    return Path(str((job or {}).get("result_name") or "result.csv")).name


def find_companion_csv(
    trajectory_path: Path,
    job: dict | None,
    csv_name: str,
) -> Path | None:
    """Locate a CSV companion in live or organized MolScout output layouts.

    Completed jobs move CSV files into ``Tables/`` while preserving the original
    relative subdirectory. Older/imported or currently running jobs can keep the
    file beside the trajectory. The resolver understands both forms.
    """
    csv_name = Path(str(csv_name)).name
    candidates = [
        trajectory_path.parent / csv_name,
        trajectory_path.parent / "Tables" / csv_name,
    ]

    output_dir_text = str((job or {}).get("output_dir") or "").strip()
    output_dir = Path(output_dir_text) if output_dir_text else None
    if output_dir is not None:
        candidates.extend(
            [
                output_dir / csv_name,
                output_dir / "Tables" / csv_name,
            ]
        )
        try:
            relative_parent = trajectory_path.parent.relative_to(output_dir)
        except ValueError:
            relative_parent = None
        if relative_parent is not None:
            candidates.append(output_dir / "Tables" / relative_parent / csv_name)
        if output_dir.exists():
            recursive_matches = sorted(output_dir.rglob(csv_name))
            # Only use a broad recursive fallback when it is unambiguous. A job
            # can contain multiple workflow subdirectories with identically named
            # CSVs; picking the first one would silently attach the wrong data.
            if len(recursive_matches) == 1:
                candidates.extend(recursive_matches)

    seen: set[str] = set()
    for candidate in candidates:
        candidate_key = str(candidate)
        if candidate_key in seen:
            continue
        seen.add(candidate_key)
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def trajectory_role(path: Path, job: dict | None = None) -> str:
    """Return the analysis role used by the Chemiscope filter and CSV resolver."""
    name = path.name.lower()
    stem = path.stem.lower()
    if stem == "init_path":
        if find_companion_csv(path, job, "mfscan_trace.csv") is not None:
            return "MF-SCAN"
        return "Initial path"
    if stem == "irc":
        return "IRC"
    if stem == "optpoints":
        return "Optpoints"
    if name.endswith("_tsopt.traj") or name.endswith("_tsopt.xyz"):
        return "TS optimization"
    if name.endswith("_opt.traj") or name.endswith("_opt.xyz"):
        return "Optimization"
    return "Other"


def companion_csv_names(role: str, job: dict | None) -> list[tuple[str, str]]:
    """Return ordered companion CSV kinds/names for a trajectory role."""
    if role == "Initial path":
        return [("frame", expected_result_name(job))]
    if role == "IRC":
        return [("frame", "irc_energy.csv")]
    if role == "Optpoints":
        return [("frame", "result_optpoints.csv")]
    if role == "MF-SCAN":
        return [
            ("frame", expected_result_name(job)),
            ("mfscan", "mfscan_trace.csv"),
        ]
    return []


def companion_csv_sources(
    trajectory_path: Path,
    job: dict | None,
    role: str,
) -> list[dict[str, object]]:
    """Resolve all companion CSVs expected for one trajectory role."""
    sources: list[dict[str, object]] = []
    for kind, csv_name in companion_csv_names(role, job):
        sources.append(
            {
                "kind": kind,
                "name": csv_name,
                "path": find_companion_csv(trajectory_path, job, csv_name),
            }
        )
    return sources


def prepare_mfscan_frame_properties(trace_df: pd.DataFrame) -> pd.DataFrame:
    """Reduce an MF-SCAN trace to rows that correspond to DFT output frames."""
    if "init_path_frame" not in trace_df.columns:
        raise ValueError("MF-SCAN trace is missing `init_path_frame`.")
    result = trace_df.copy()
    frame_values = pd.to_numeric(result["init_path_frame"], errors="coerce")
    valid = np.isfinite(frame_values.to_numpy(dtype=float))
    result = result.loc[valid].copy()
    if result.empty:
        raise ValueError("MF-SCAN trace contains no DFT anchor frame mapping.")
    result["# image"] = frame_values.loc[valid].astype(int)
    return result


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

prepared_files_df = files_df.copy()
prepared_files_df["job_id"] = prepared_files_df["rel_path"].map(trajectory_job_id)
prepared_files_df["trajectory"] = prepared_files_df["rel_path"].map(trajectory_path_within_job)
prepared_files_df["size"] = prepared_files_df["path"].map(
    lambda value: file_size_label(Path(str(value)))
)

roles: list[str] = []
companion_sources_column: list[list[dict[str, object]]] = []
property_sources: list[str] = []
for _, file_row in prepared_files_df.iterrows():
    trajectory_path = Path(str(file_row["path"]))
    job_id = str(file_row["job_id"] or "")
    job = jobs_by_id.get(job_id)
    role = trajectory_role(trajectory_path, job)
    companion_sources = companion_csv_sources(trajectory_path, job, role)
    labels: list[str] = []
    for source in companion_sources:
        source_path = source.get("path")
        if isinstance(source_path, Path):
            labels.append(property_source_label(source_path, trajectory_path, job))
        else:
            labels.append(f"{source.get('name', 'CSV')} (missing)")
    roles.append(role)
    companion_sources_column.append(companion_sources)
    property_sources.append(" + ".join(labels) if labels else "-")

prepared_files_df["role"] = roles
prepared_files_df["companion_sources"] = companion_sources_column
prepared_files_df["property_source"] = property_sources

available_roles = [
    role
    for role in TRAJECTORY_ROLE_FILTERS
    if role == "All trajectories" or role in set(prepared_files_df["role"].astype(str))
]
filter_key = f"{session_id}_chemiscope_role_filter_{'-'.join(selected_job_ids)}"
selected_filter = st.segmented_control(
    "Trajectory filter",
    options=available_roles,
    default="All trajectories",
    key=filter_key,
)
st.caption(
    t(
        "Tip: Filters also control companion CSV loading "
        "(e.g. `init_path.traj` → `result.csv`)."
    )
)

prepared_files_df = filter_trajectory_files(
    prepared_files_df,
    str(selected_filter or "All trajectories"),
)

if prepared_files_df.empty:
    st.info(t('No trajectory files match this filter.'))
    st.stop()

st.markdown("#### Found trajectory files")
st.caption(
    f"Role `{selected_filter or 'All trajectories'}`: {len(prepared_files_df):,} file(s)"
)
if include_xyz and selected_filter == "All trajectories":
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
    st.info(t("Select one or more trajectory rows to visualize."))
    st.stop()

selected_file_rows = prepared_files_df.iloc[selected_indices].reset_index(drop=True)
st.caption(f"Selected trajectories: {len(selected_file_rows):,}")

selection_kind = "single" if len(selected_file_rows) == 1 else "multi"
join_points_key = f"{session_id}_chemiscope_join_points"
join_context_key = f"{session_id}_chemiscope_join_points_context"
if st.session_state.get(join_context_key) != selection_kind:
    st.session_state[join_points_key] = selection_kind == "single"
    st.session_state[join_context_key] = selection_kind

series_rows = selected_series_rows(selected_file_rows, jobs_by_id)
series_label_by_source: dict[str, str] = {}

with st.container(border=True):
    multi_trajectory = len(selected_file_rows) > 1
    view_cols = st.columns([1, 1, 1, 1] if multi_trajectory else [1, 1, 1])
    join_points = view_cols[0].toggle(
        "Join points",
        key=join_points_key,
        help=t(
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
                help=t(
                    "Treat different atom-index targets as one comparison axis when "
                    "all selected trajectories use the same coordinate type. Bond, "
                    "angle, and dihedral scans are never mixed."
                ),
            )
        )
    if len(selected_file_rows) > 1 and join_points:
        st.caption(
            t(
                "Join points is enabled for a combined dataset; the last frame of one "
                "trajectory will also connect to the first frame of the next trajectory."
            )
        )

    if len(series_rows) > 1:
        label_mode_key = f"{session_id}_chemiscope_series_label_mode_v2"
        label_mode = st.selectbox(
            "Series label",
            options=list(SERIES_LABEL_OPTIONS),
            index=0,
            key=label_mode_key,
            help=t(
                "Controls the label used for Quick plot series and the Chemiscope "
                "symbol property. Compact note uses the first Job Note line, shortens "
                "long text, and adds a short Job ID only when labels collide."
            ),
        )
        if label_mode == "Custom":
            compact_default_labels = compact_series_labels(series_rows)
            selected_series_signature = hashlib.sha1(
                "|".join(row["source"] for row in series_rows).encode("utf-8")
            ).hexdigest()[:12]
            custom_rows = pd.DataFrame(
                [
                    {
                        "Job": row["job_id"],
                        "Job Note": row["job_note"],
                        "Trajectory": row["trajectory"],
                        "Label": compact_default_labels[row["source"]],
                    }
                    for row in series_rows
                ]
            )
            edited_rows = st.data_editor(
                custom_rows,
                hide_index=True,
                width="stretch",
                height=min(260, 36 + 35 * len(custom_rows)),
                disabled=["Job", "Job Note", "Trajectory"],
                num_rows="fixed",
                key=f"{session_id}_chemiscope_custom_series_labels_{selected_series_signature}",
                column_config={
                    "Job": st.column_config.TextColumn("Job", width="medium"),
                    "Job Note": st.column_config.TextColumn("Job Note", width="large"),
                    "Trajectory": st.column_config.TextColumn("Trajectory", width="large"),
                    "Label": st.column_config.TextColumn("Label", width="large"),
                },
            )
            for row, (_, edited_row) in zip(series_rows, edited_rows.iterrows(), strict=True):
                label_value = edited_row.get("Label")
                custom_label = "" if pd.isna(label_value) else str(label_value).strip()
                series_label_by_source[row["source"]] = (
                    custom_label or compact_default_labels[row["source"]]
                )
        elif label_mode == "Compact note":
            series_label_by_source = compact_series_labels(series_rows)
        else:
            for row in series_rows:
                series_label_by_source[row["source"]] = series_label_for_source(
                    str(label_mode),
                    job_id=row["job_id"],
                    job_note=row["job_note"],
                    source=row["source"],
                )
    else:
        only_row = series_rows[0]
        series_label_by_source[only_row["source"]] = only_row["source"]

all_structures: list = []
frame_tables: list[pd.DataFrame] = []
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
    selected_role = str(selected_row.get("role") or trajectory_role(selected_path, job))
    companion_sources = selected_row.get("companion_sources")
    if not isinstance(companion_sources, list):
        companion_sources = companion_csv_sources(selected_path, job, selected_role)

    source_labels: list[str] = []
    for companion in companion_sources:
        csv_name = str(companion.get("name") or "CSV")
        csv_kind = str(companion.get("kind") or "frame")
        csv_path = companion.get("path")
        if not isinstance(csv_path, Path):
            source_labels.append(f"{csv_name} (missing)")
            property_messages.append(
                f"`{job_id}/{selected_path.name}`: `{csv_name}` was not found for "
                f"the {selected_role} role; trajectory metadata is still available."
            )
            continue

        try:
            csv_stat = csv_path.stat()
            properties_df = load_frame_properties_csv(
                str(csv_path),
                int(csv_stat.st_mtime_ns),
                int(csv_stat.st_size),
            )
            if csv_kind == "mfscan":
                properties_df = prepare_mfscan_frame_properties(properties_df)
            local_table, added_columns = merge_frame_properties(local_table, properties_df)
            csv_fields.update(added_columns)
            source_labels.append(property_source_label(csv_path, selected_path, job))
        except Exception as error:
            source_labels.append(f"{csv_name} (error)")
            property_messages.append(
                f"`{job_id}/{selected_path.name}`: failed to load `{csv_name}` ({error})"
            )

    property_source = " + ".join(source_labels) if source_labels else "-"

    inside_job = trajectory_path_within_job(rel_path)
    source_label = f"{job_id}/{inside_job}" if job_id else rel_path
    local_table["dataset_index"] = np.arange(offset, offset + len(local_table), dtype=int)
    local_table["job"] = job_id or "-"
    local_table["trajectory"] = selected_path.name
    local_table["source"] = source_label
    local_table["series_label"] = series_label_by_source.get(source_label, source_label)
    local_table["property_source"] = property_source

    metadata_columns = [
        "dataset_index",
        "job",
        "trajectory",
        "source",
        "series_label",
        "step",
        "property_source",
    ]
    remaining_columns = [
        column for column in local_table.columns if column not in metadata_columns
    ]
    local_table = local_table[[*metadata_columns, *remaining_columns]]

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
            "start_index": offset,
            "property_source": property_source,
            "series_label": series_label_by_source.get(source_label, source_label),
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
        default_x = (
            scan_unified_column
            if scan_unified_column in x_options
            else next((column for column in x_options if column.startswith("SCAN_")), "step")
        )
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
        x_context_key = f"{session_id}_chemiscope_x_axis_context"
        x_context = hashlib.sha1(
            (
                "|".join(
                    f"{item['rel_path']}:{item['mtime_ns']}:{item['size_bytes']}"
                    for item in loaded_trajectories
                )
                + f"|unify={int(bool(unify_scan_targets))}|common={scan_unified_column or '-'}"
            ).encode("utf-8")
        ).hexdigest()[:16]
        if st.session_state.get(x_context_key) != x_context:
            # A new trajectory selection or SCAN-unification state gets the most
            # useful comparison axis once. Subsequent manual axis choices persist.
            st.session_state[x_key] = default_x
            st.session_state[x_context_key] = x_context
        elif st.session_state.get(x_key) not in x_options:
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
                "series_label": frame_table["series_label"].astype(str),
                "source": frame_table["source"].astype(str),
            }
        )
        plot_df = plot_df.replace([np.inf, -np.inf], np.nan).dropna(
            subset=["__plot_x", "__plot_y"]
        )
        if plot_df.empty:
            st.info("No finite values are available for the selected axes.")
        elif len(loaded_trajectories) > 1:
            # Keep long-form rows so each trajectory can have its own measured
            # SCAN coordinates. Pivoting on floating-point X values creates NaN
            # gaps whenever two scans do not hit exactly the same coordinates.
            multi_plot = plot_df.sort_values(["series_label", "__plot_x", "source"])
            st.line_chart(
                multi_plot,
                x="__plot_x",
                y="__plot_y",
                color="series_label",
                x_label=str(plot_x),
                y_label=str(plot_y),
                height=320,
            )
        else:
            single_plot = plot_df.sort_values("__plot_x")
            st.line_chart(
                single_plot,
                x="__plot_x",
                y="__plot_y",
                x_label=str(plot_x),
                y_label=str(plot_y),
                height=320,
            )
    else:
        st.info(t('No numeric energy or force properties were found in Atoms.info or arrays.'))

st.markdown("#### Structure viewer")
if len(loaded_trajectories) > 1:
    st.caption(
        t(
            "Combined comparisons open with one structure viewer. Additional structures "
            "can still be pinned in Chemiscope when a multi-view comparison is useful."
        )
    )

# Prefer the selected Target Job for the initial viewer, but keep a single pin
# even when many trajectories are combined. Multi-view remains available as an
# explicit Chemiscope interaction instead of being forced on initial load.
initial_trajectory = next(
    (
        item
        for item in loaded_trajectories
        if item["job_id"] == focused_job_id
        and Path(str(item["path"])).name == "init_path.traj"
    ),
    next(
        (item for item in loaded_trajectories if item["job_id"] == focused_job_id),
        loaded_trajectories[0],
    ),
)
initial_pinned_indices = [int(initial_trajectory["start_index"])]

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
        pinned_indices=initial_pinned_indices,
    )
    with st.expander("Chemiscope settings", expanded=False):
        st.json(settings)

    viewer_mode = str(mode or "default")
    viewer_identity_parts = [
        (
            f"{item['rel_path']}:{item['mtime_ns']}:{item['size_bytes']}:"
            f"{item['property_source']}:{item['series_label']}"
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
