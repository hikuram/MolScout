"""Chemiscope trajectory viewer helpers."""

from __future__ import annotations

import hashlib
import math
from datetime import datetime
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


@st.cache_data(show_spinner=False)
def load_structures(path_text: str, mtime_ns: int, size_bytes: int, max_frames: int) -> list[Any]:
    import ase.io

    del mtime_ns, size_bytes
    frames: list[Any] = []
    for atoms in ase.io.iread(path_text, index=":"):
        frames.append(atoms)
        if max_frames > 0 and len(frames) >= max_frames:
            break
    return frames


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
) -> tuple[dict[str, Any], dict[str, Any]]:
    import chemiscope

    n_frames = len(structures)
    properties: dict[str, Any] = {
        "step": {
            "target": "structure",
            "values": frame_table["step"].astype(int).tolist(),
            "description": "Frame index in the selected trajectory file.",
        },
        "source": {
            "target": "structure",
            "values": [source_name] * n_frames,
            "description": "Source trajectory file name.",
        },
        "natoms": {
            "target": "structure",
            "values": frame_table["natoms"].astype(int).tolist(),
        },
    }

    for column, units in [
        ("energy", "eV"),
        ("relative_energy", "eV"),
        ("max_force", "eV/Ang"),
        ("mean_force", "eV/Ang"),
    ]:
        if column in frame_table and finite_column(frame_table, column):
            properties[column] = {
                "target": "structure",
                "values": [math.nan if pd.isna(value) else float(value) for value in frame_table[column].tolist()],
                "units": units,
            }

    if finite_column(frame_table, "relative_energy"):
        y_prop = "relative_energy"
        color_prop = "relative_energy"
    elif finite_column(frame_table, "energy"):
        y_prop = "energy"
        color_prop = "energy"
    elif finite_column(frame_table, "max_force"):
        y_prop = "max_force"
        color_prop = "max_force"
    else:
        y_prop = "step"
        color_prop = "source"

    settings = chemiscope.quick_settings(
        x="step",
        y=y_prop,
        map_color=color_prop,
        symbol="source",
        trajectory=join_points,
        structure_settings={
            "keepOrientation": True,
            "playbackDelay": int(playback_delay),
        },
    )
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


def viewer_key(path_text: str, n_frames: int) -> str:
    digest = hashlib.sha1(f"{path_text}:{n_frames}".encode("utf-8")).hexdigest()
    return f"chemiscope_viewer_{digest[:12]}"
