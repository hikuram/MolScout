"""Chemiscope trajectory viewer helpers."""

from __future__ import annotations

import hashlib
import math
from datetime import datetime
from io import StringIO
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import streamlit as st

TRAJECTORY_EXTENSIONS = (".traj", ".xyz", ".extxyz")
ENERGY_KEYS = (
    "energy",
    "potential_energy",
    "free_energy",
    "dftb_energy_eV",
    "mlip_energy",
    "orbmol_energy",
    "xtb_energy",
    "E",
)
FORCE_KEYS = (
    "forces",
    "force",
    "dftb_forces_eV_per_Ang",
)
DEFAULT_MAX_FRAMES = 400


_PASSIVE_CHEMISCOPE_COMPONENT = None


def render_passive_chemiscope(
    dataset: dict[str, Any],
    *,
    mode: str,
    key: str,
    width: str | int = "stretch",
    height: int = 720,
) -> None:
    """Render Chemiscope without echoing selection state back from Python."""
    global _PASSIVE_CHEMISCOPE_COMPONENT

    if _PASSIVE_CHEMISCOPE_COMPONENT is None:
        import chemiscope.streamlit
        import streamlit.components.v1 as components

        component_dir = Path(chemiscope.streamlit.__file__).resolve().parent
        _PASSIVE_CHEMISCOPE_COMPONENT = components.declare_component(
            "molscout_chemiscope_viewer",
            path=str(component_dir),
        )

    if mode not in ("default", "structure", "map"):
        raise ValueError(
            f"Invalid mode '{mode}', expected 'default', 'structure', or 'map'"
        )

    _PASSIVE_CHEMISCOPE_COMPONENT(
        dataset=dataset,
        mode=mode,
        key=key,
        width=width,
        height=height,
        no_info_panel=False,
        default=None,
    )


def safe_resolve(path_text: str) -> Path | None:
    try:
        return Path(path_text).expanduser().resolve()
    except OSError:
        return None


@st.cache_data(show_spinner=False, ttl=30)
def scan_trajectory_files(root_text: str, include_xyz: bool, max_files: int) -> pd.DataFrame:
    root = safe_resolve(root_text)
    if root is None or not root.exists() or not root.is_dir():
        return pd.DataFrame()

    extensions = [".traj"]
    if include_xyz:
        extensions.extend([".xyz", ".extxyz"])

    rows: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        if len(rows) >= max_files:
            break
        if not path.is_file() or path.suffix.lower() not in extensions:
            continue
        try:
            stat = path.stat()
            rel_path = path.relative_to(root).as_posix()
        except OSError:
            continue
        rows.append(
            {
                "rel_path": rel_path,
                "path": str(path),
                "suffix": path.suffix.lower(),
                "size_kb": round(stat.st_size / 1024, 1),
                "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                "mtime_ns": int(stat.st_mtime_ns),
                "size_bytes": int(stat.st_size),
            }
        )

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    return df.sort_values(["modified", "rel_path"], ascending=[False, True]).reset_index(drop=True)


def _read_limited_structures(
    path_text: str,
    max_frames: int,
    *,
    file_format: str | None = None,
) -> list[Any]:
    import ase.io

    frames: list[Any] = []
    kwargs: dict[str, Any] = {"index": ":"}
    if file_format is not None:
        kwargs["format"] = file_format

    for atoms in ase.io.iread(path_text, **kwargs):
        frames.append(atoms)
        if max_frames > 0 and len(frames) >= max_frames:
            break
    return frames


@st.cache_data(show_spinner=False)
def load_structures(path_text: str, mtime_ns: int, size_bytes: int, max_frames: int) -> list[Any]:
    """Load structures for the Chemiscope preview.

    ASE treats ``.xyz`` files as extended XYZ by default. Some MolScout or
    externally edited XYZ files contain stale/malformed extended-XYZ metadata
    even though their element/coordinate columns are still valid. In that
    case, retry as plain XYZ so Chemiscope can still display the structures.

    The fallback intentionally discards extended metadata such as energies,
    forces, and custom per-atom fields. Native ``.traj`` files keep the normal
    ASE read path and therefore preserve their metadata.
    """
    del mtime_ns, size_bytes

    suffix = Path(path_text).suffix.lower()
    if suffix in {".xyz", ".extxyz"}:
        try:
            return _read_limited_structures(
                path_text,
                max_frames,
                file_format="extxyz",
            )
        except Exception as extxyz_error:
            try:
                return _read_limited_structures(
                    path_text,
                    max_frames,
                    file_format="xyz",
                )
            except Exception as xyz_error:
                raise RuntimeError(
                    f"Failed to read XYZ file '{Path(path_text).name}' as either "
                    f"extended XYZ ({extxyz_error}) or plain XYZ ({xyz_error})."
                ) from xyz_error

    return _read_limited_structures(path_text, max_frames)


@st.cache_data(show_spinner=False)
def load_frame_properties_csv(path_text: str, mtime_ns: int, size_bytes: int) -> pd.DataFrame:
    """Load a frame-aligned CSV used to enrich Chemiscope structure properties.

    ``mtime_ns`` and ``size_bytes`` are cache-busting inputs supplied by the
    caller. They intentionally do not participate in the CSV parsing itself.
    """
    del mtime_ns, size_bytes
    return pd.read_csv(path_text)


def merge_frame_properties(
    frame_table: pd.DataFrame,
    properties_df: pd.DataFrame,
    *,
    image_column: str = "# image",
) -> tuple[pd.DataFrame, list[str]]:
    """Merge a result CSV into a frame table using the local frame index.

    The CSV join is deliberately key-based rather than row-position based.
    This keeps truncated previews (``Max frames / file``) aligned and avoids
    silently shifting properties when a CSV contains extra rows.
    """
    if image_column not in properties_df.columns:
        raise ValueError(f"CSV is missing the frame key column: {image_column}")

    source = properties_df.copy()
    image_values = pd.to_numeric(source[image_column], errors="coerce")
    valid = np.isfinite(image_values.to_numpy(dtype=float))
    source = source.loc[valid].copy()
    source[image_column] = image_values.loc[valid].astype(int)
    source = source.drop_duplicates(subset=[image_column], keep="last")
    source = source.rename(columns={image_column: "step"})

    rename_map: dict[str, str] = {}
    occupied = set(frame_table.columns) | {"step"}
    for column in source.columns:
        if column == "step":
            continue
        candidate = str(column)
        if candidate in occupied:
            candidate = f"CSV: {candidate}"
            suffix = 2
            while candidate in occupied:
                candidate = f"CSV: {column} ({suffix})"
                suffix += 1
        rename_map[column] = candidate
        occupied.add(candidate)

    source = source.rename(columns=rename_map)
    added_columns = [rename_map[column] for column in rename_map]
    keep_columns = ["step", *added_columns]
    merged = frame_table.merge(
        source[keep_columns],
        how="left",
        on="step",
        validate="one_to_one",
    )
    return merged, added_columns


@st.cache_data(show_spinner=False)
def trajectory_to_extxyz(path_text: str, mtime_ns: int, size_bytes: int) -> bytes:
    """Convert every frame in a trajectory file to extended XYZ bytes."""
    import ase.io

    del mtime_ns, size_bytes
    frames = list(ase.io.iread(path_text, index=":"))
    if not frames:
        return b""

    buffer = StringIO()
    ase.io.write(buffer, frames, format="extxyz")
    return buffer.getvalue().encode("utf-8")


def _as_float(value: Any) -> float:
    if value is None:
        return math.nan
    if isinstance(value, np.ndarray):
        if value.size == 0:
            return math.nan
        value = value.reshape(-1)[0]
    try:
        return float(value)
    except (TypeError, ValueError):
        return math.nan


def _get_energy(atoms: Any) -> float:
    info = getattr(atoms, "info", {}) or {}
    for key in ENERGY_KEYS:
        if key in info:
            value = _as_float(info.get(key))
            if math.isfinite(value):
                return value

    calc = getattr(atoms, "calc", None)
    results = getattr(calc, "results", {}) or {}
    for key in ENERGY_KEYS:
        if key in results:
            value = _as_float(results.get(key))
            if math.isfinite(value):
                return value
    return math.nan


def _get_force_array(atoms: Any) -> np.ndarray | None:
    arrays = getattr(atoms, "arrays", {}) or {}
    for key in FORCE_KEYS:
        if key in arrays:
            forces = np.asarray(arrays[key], dtype=float)
            if forces.ndim == 2 and forces.shape[1] == 3:
                return forces

    calc = getattr(atoms, "calc", None)
    results = getattr(calc, "results", {}) or {}
    for key in FORCE_KEYS:
        if key in results:
            forces = np.asarray(results[key], dtype=float)
            if forces.ndim == 2 and forces.shape[1] == 3:
                return forces
    return None


def _max_force(atoms: Any) -> float:
    forces = _get_force_array(atoms)
    if forces is None or forces.size == 0:
        return math.nan
    return float(np.nanmax(np.linalg.norm(forces, axis=1)))


def _mean_force(atoms: Any) -> float:
    forces = _get_force_array(atoms)
    if forces is None or forces.size == 0:
        return math.nan
    return float(np.nanmean(np.linalg.norm(forces, axis=1)))


def build_frame_table(structures: list[Any]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for idx, atoms in enumerate(structures):
        energy = _get_energy(atoms)
        rows.append(
            {
                "step": idx,
                "energy": energy,
                "relative_energy": math.nan,
                "max_force": _max_force(atoms),
                "mean_force": _mean_force(atoms),
                "natoms": len(atoms),
                "formula": atoms.get_chemical_formula(),
            }
        )

    df = pd.DataFrame(rows)
    if "energy" in df and df["energy"].notna().any():
        finite = df["energy"].replace([np.inf, -np.inf], np.nan).dropna()
        if not finite.empty:
            df["relative_energy"] = df["energy"] - finite.min()
    return df


def finite_column(df: pd.DataFrame, column: str) -> bool:
    if column not in df:
        return False
    series = pd.to_numeric(df[column], errors="coerce")
    return bool(np.isfinite(series).any())


def build_chemiscope_dataset(
    structures: list[Any],
    frame_table: pd.DataFrame,
    source_name: str,
    join_points: bool,
    playback_delay: int,
    *,
    pinned_indices: list[int] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build a Chemiscope dataset from one or more concatenated trajectories.

    Any frame-table column that is complete across every loaded structure is
    exported as a structure property. Numeric columns must contain only finite
    values; incomplete CSV-derived fields remain visible in the Streamlit table
    but are intentionally omitted from Chemiscope.
    """
    import chemiscope

    n_frames = len(structures)
    if len(frame_table) != n_frames:
        raise ValueError(
            f"Frame table length ({len(frame_table)}) does not match structures ({n_frames})."
        )

    properties: dict[str, Any] = {}
    numeric_properties: set[str] = set()
    text_properties: set[str] = set()

    builtin_units = {
        "energy": "eV",
        "relative_energy": "eV",
        "max_force": "eV/Ang",
        "mean_force": "eV/Ang",
    }
    descriptions = {
        "step": "Frame index within the source trajectory.",
        "dataset_index": "Frame index after concatenating the selected trajectories.",
        "source": "Unique source trajectory identifier.",
        "trajectory": "Source trajectory file name.",
        "job": "MolScout job identifier.",
        "property_source": "CSV file used to enrich this trajectory, when available.",
    }

    internal_columns = {"formula"}
    for column in frame_table.columns:
        if column in internal_columns:
            continue

        series = frame_table[column]
        if len(series) != n_frames:
            continue

        numeric = pd.to_numeric(series, errors="coerce")
        numeric_values = numeric.to_numpy(dtype=float)
        original_non_null = series.notna().all()

        # Treat a column as numeric only when every original value is present
        # and every converted value is finite. This prevents strings such as
        # job IDs from being accidentally coerced into a partial number array.
        if original_non_null and np.isfinite(numeric_values).all():
            values: list[Any]
            if pd.api.types.is_integer_dtype(series.dtype):
                values = numeric.astype(int).tolist()
            else:
                values = numeric_values.tolist()
            prop: dict[str, Any] = {
                "target": "structure",
                "values": values,
            }
            if column in builtin_units:
                prop["units"] = builtin_units[column]
            if column in descriptions:
                prop["description"] = descriptions[column]
            properties[str(column)] = prop
            numeric_properties.add(str(column))
            continue

        if not original_non_null:
            continue

        text_values = [str(value) for value in series.tolist()]
        if any(value == "" for value in text_values):
            continue
        prop = {
            "target": "structure",
            "values": text_values,
        }
        if column in descriptions:
            prop["description"] = descriptions[column]
        properties[str(column)] = prop
        text_properties.add(str(column))

    if "step" not in properties:
        properties["step"] = {
            "target": "structure",
            "values": list(range(n_frames)),
            "description": descriptions["step"],
        }
        numeric_properties.add("step")

    if "source" not in properties:
        properties["source"] = {
            "target": "structure",
            "values": [source_name] * n_frames,
            "description": descriptions["source"],
        }
        text_properties.add("source")

    unified_scan_properties = [
        column
        for column in ("SCAN_bond [Å]", "SCAN_angle [deg]", "SCAN_dihedral [deg]")
        if column in numeric_properties
    ]
    scan_properties = [
        *unified_scan_properties,
        *(
            str(column)
            for column in frame_table.columns
            if str(column).startswith("SCAN_")
            and str(column) in numeric_properties
            and str(column) not in unified_scan_properties
        ),
    ]
    delta_energy_name = "Delta E vs. reactant [kcal/mol]"

    if scan_properties:
        x_prop = str(scan_properties[0])
    else:
        x_prop = "step"

    y_candidates = [
        delta_energy_name,
        "relative_energy",
        "energy",
        "max_force",
        "mean_force",
    ]
    y_prop = next(
        (candidate for candidate in y_candidates if candidate in numeric_properties),
        "step",
    )
    color_prop = y_prop if y_prop in numeric_properties else "source"

    job_count = (
        frame_table["job"].astype(str).nunique()
        if "job" in frame_table.columns and "job" in text_properties
        else 0
    )
    if job_count > 1:
        symbol_prop = "job"
    elif "source" in text_properties:
        symbol_prop = "source"
    elif "job" in text_properties:
        symbol_prop = "job"
    else:
        symbol_prop = None

    settings = chemiscope.quick_settings(
        x=x_prop,
        y=y_prop,
        map_color=color_prop,
        symbol=symbol_prop,
        trajectory=join_points,
        structure_settings={
            "keepOrientation": True,
            "playbackDelay": int(playback_delay),
        },
    )
    if pinned_indices:
        settings["pinned"] = [
            int(index) for index in pinned_indices[:9]
            if isinstance(index, (int, np.integer)) and 0 <= int(index) < n_frames
        ]

    dataset = chemiscope.create_input(
        structures=structures,
        properties=properties,
        settings=settings,
        metadata={
            "name": f"MolScout trajectory preview: {source_name}",
            "description": "Generated from the selected MolScout session.",
        },
    )
    return dataset, settings


def viewer_key(path_text: str, n_frames: int, mode: str, mtime_ns: int) -> str:
    digest = hashlib.sha1(
        f"{path_text}:{n_frames}:{mode}:{mtime_ns}".encode("utf-8")
    ).hexdigest()
    return f"chemiscope_viewer_{digest[:12]}"
