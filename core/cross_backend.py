"""Optional PET-OMol single-point cross-checks of existing Primary MLIP Delta E.

Only an already evaluated primary energy series is eligible.  Neither the path
nor the primary energy values are re-evaluated by this module.  Models run one
at a time after the main workflow and never modify the source trajectory.
"""

from __future__ import annotations

import gc
import math
import os
from pathlib import Path
from time import perf_counter
from typing import Callable

import pandas as pd

from utils import log

PET_OMOL_MODELS = ("pet-omol-s", "pet-omol-l")
PET_OMOL_VERSION = "1.0.0"
SUPPORTED_PRECISIONS = ("float32", "float64")
SUPPORTED_SOURCES = ("auto", "optpoints", "initial_path")
PRIMARY_DELTA_COLUMN = "Delta E vs. reactant [kcal/mol]"
PRIMARY_ENERGY_COLUMN = "energy [eV]"


class CrossBackendConfigError(ValueError):
    """Invalid or inconsistent optional cross-backend configuration."""


def validate_settings(models: object, precision: object, source: object) -> list[str]:
    if not isinstance(models, list) or any(not isinstance(model, str) for model in models):
        raise CrossBackendConfigError("CROSS_BACKEND_MODELS must be a list of PET-OMol model names.")
    if len(set(models)) != len(models) or any(model not in PET_OMOL_MODELS for model in models):
        raise CrossBackendConfigError("Only distinct 'pet-omol-s' and 'pet-omol-l' are supported.")
    if precision not in SUPPORTED_PRECISIONS:
        raise CrossBackendConfigError("CROSS_BACKEND_PRECISION must be float32 or float64.")
    if source not in SUPPORTED_SOURCES:
        raise CrossBackendConfigError("CROSS_BACKEND_SOURCE must be auto, optpoints, or initial_path.")
    return [model for model in PET_OMOL_MODELS if model in models]


def select_primary_series(config: object) -> tuple[Path, Path, str]:
    """Locate matching trajectory/CSV files without deriving energies from XYZ.

    The optpoints CSV contains its own single-point primary energies, whereas
    in a concatenation batch with optimization, energies may refer to frame_*_opt
    geometries rather than the original init_path.traj.  Refuse that mismatch.
    """
    calc_type = str(getattr(config, "CALC_TYPE", "")).lower()
    if calc_type not in {"orbmol", "orbmol+alpb"}:
        raise CrossBackendConfigError("Cross-backend Delta E requires an OrbMol Primary MLIP series.")
    if getattr(config, "PRESERVE_CSV_ON", False):
        raise CrossBackendConfigError("Figure refresh has no new Primary energy evaluation.")
    if getattr(config, "SCAN_MF_ON", False):
        raise CrossBackendConfigError("MF-SCAN contains DFT anchors, not a homogeneous Primary MLIP Delta E series.")

    method = str(getattr(config, "INIT_PATH_METHOD", "")).upper()
    source = str(getattr(config, "CROSS_BACKEND_SOURCE", "auto"))
    if method == "CAT" and bool(getattr(config, "REFINE_INPUT_ON", False)):
        raise CrossBackendConfigError(
            "CAT with structure optimization changes the primary energy geometries; "
            "the initial trajectory cannot be compared safely."
        )

    opt_traj = Path("optpoints/optpoints.traj")
    opt_csv = Path("optpoints/result_optpoints.csv")
    has_opt = (
        method != "CAT"
        and bool(getattr(config, "PICK_OPTPOINTS_ON", False))
        and opt_traj.is_file()
        and opt_csv.is_file()
    )
    if source == "optpoints" and not has_opt:
        raise CrossBackendConfigError("An evaluated optpoints trajectory/CSV pair is not available.")
    if source == "optpoints" or (source == "auto" and has_opt):
        return opt_traj, opt_csv, "optpoints"

    path_traj = Path(str(getattr(config, "I_TRAJ", "init_path.traj")))
    if method != "CAT" and bool(getattr(config, "PICK_OPTPOINTS_ON", False)) and hasattr(config, "ORIG_R_CSV"):
        path_csv = Path(str(config.ORIG_R_CSV))
    else:
        path_csv = Path(str(getattr(config, "R_CSV", "result.csv")))
    if not path_traj.is_file() or not path_csv.is_file():
        raise CrossBackendConfigError(f"Missing matched trajectory/CSV: {path_traj}, {path_csv}.")
    return path_traj, path_csv, "initial_path"


def _read_snapshots(source_path: Path) -> list:
    from ase.io.trajectory import Trajectory

    with Trajectory(str(source_path), mode="r") as traj:
        return [atoms.copy() for atoms in traj]


def _default_calculator_factory(model: str, precision: str, device: str):
    # Lazy imports: no new MLIP stack is touched when the option is disabled.
    import torch
    from upet.ase import UPETCalculator

    checkpoint_dir = Path(
        os.environ.get(
            "MOLSCOUT_UPET_CHECKPOINT_DIR",
            str(Path(__file__).resolve().parent.parent / ".cache" / "upet_models" / "models"),
        )
    )
    checkpoint_path = checkpoint_dir / f"{model}-v{PET_OMOL_VERSION}.ckpt"
    if not checkpoint_path.is_file():
        raise FileNotFoundError(
            f"UPET checkpoint is missing: {checkpoint_path}. "
            "Rebuild the Docker image with UPET checkpoint prefetch enabled."
        )

    torch.set_float32_matmul_precision("highest")
    return UPETCalculator(
        checkpoint_path=str(checkpoint_path),
        dtype=torch.float64 if precision == "float64" else torch.float32,
        device=device,
        non_conservative=False,
        check_consistency=False,
    )


def _release_calculator(device: str) -> None:
    gc.collect()
    if device.startswith("cuda"):
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except (ImportError, RuntimeError):
            pass


def _aligned_primary_data(df: pd.DataFrame, frames: list) -> pd.DataFrame:
    required = ("# image", PRIMARY_ENERGY_COLUMN, PRIMARY_DELTA_COLUMN)
    if not all(column in df.columns for column in required):
        raise CrossBackendConfigError(f"The Primary CSV must contain {', '.join(required)}.")
    if len(df) != len(frames) or len(frames) < 2:
        raise CrossBackendConfigError("A matching Primary Delta E series with at least 2 frames is required.")
    indices = pd.to_numeric(df["# image"], errors="coerce")
    if indices.isna().any() or indices.tolist() != list(range(len(frames))):
        raise CrossBackendConfigError("Primary CSV frame IDs do not match the trajectory order.")
    primary = df.copy()
    for col in (PRIMARY_ENERGY_COLUMN, PRIMARY_DELTA_COLUMN):
        primary[col] = pd.to_numeric(primary[col], errors="coerce")
        if not all(math.isfinite(float(value)) for value in primary[col]):
            raise CrossBackendConfigError(f"Missing or nonfinite Primary energies in {col}.")

    # A Delta E comparison is meaningless across a changing chemical composition
    # or atom permutation.  Fixed global charge/spin is separately enforced.
    numbers_0 = list(frames[0].get_atomic_numbers())
    if any(list(atoms.get_atomic_numbers()) != numbers_0 for atoms in frames[1:]):
        raise CrossBackendConfigError("Primary frames must have identical atom identities and order.")
    return primary


def compare_primary_with_pet(
    config: object,
    *,
    calculator_factory: Callable[[str, str, str], object] | None = None,
    snapshot_reader: Callable[[Path], list] | None = None,
    destination: str | Path = "cross_backend.csv",
) -> Path | None:
    """Write one row per structure/model; never re-optimize or replace Primary.

    Errors in a selected secondary model are recorded per row.  A missing
    reference (frame 0) never causes a later frame to become the new Delta E
    origin.  The caller is responsible for keeping failures nonfatal.
    """
    models = validate_settings(
        getattr(config, "CROSS_BACKEND_MODELS", []),
        getattr(config, "CROSS_BACKEND_PRECISION", "float32"),
        getattr(config, "CROSS_BACKEND_SOURCE", "auto"),
    )
    if not models:
        return None

    source_traj, source_csv, series = select_primary_series(config)
    frames = (snapshot_reader or _read_snapshots)(source_traj)
    primary = _aligned_primary_data(pd.read_csv(source_csv), frames)
    device = str(getattr(config, "DEVICE", "cuda"))
    precision = str(getattr(config, "CROSS_BACKEND_PRECISION", "float32"))
    charge, mult = int(getattr(config, "CHARGE", 0)), int(getattr(config, "MULT", 1))
    factor = float(getattr(config, "EV_TO_KCAL_MOL", 23.0605))
    primary_method = str(getattr(config, "CALC_TYPE", "orbmol")).lower()
    primary_alpb = primary_method == "orbmol+alpb"
    base = {
        "series": series,
        "source_traj": str(source_traj),
        "source_csv": str(source_csv),
        "primary_method": primary_method,
        "primary_orbmol_version": str(getattr(config, "ORBMOL_VERSION", "v2")),
        "primary_energy_basis": "OrbMol + ALPB effective" if primary_alpb else "OrbMol vacuum",
        "pet_energy_basis": "PET-OMol vacuum (no ALPB)",
        "charge": charge,
        "multiplicity": mult,
        "precision": precision,
        "checkpoint_version": PET_OMOL_VERSION,
    }
    rows: list[dict[str, object]] = []
    make_calculator = calculator_factory or _default_calculator_factory

    for model in models:
        calculator = None
        energies: list[float] = []
        errors: list[str] = []
        elapsed: list[float] = []
        log("Info", f"Cross-backend: evaluating {model} {PET_OMOL_VERSION} on {len(frames)} {series} frames.")
        try:
            calculator = make_calculator(model, precision, device)
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            log("Warn", f"Cross-backend {model}: could not load model: {message}")
            energies = [math.nan] * len(frames)
            errors = [message] * len(frames)
            elapsed = [math.nan] * len(frames)
        else:
            for idx, atoms in enumerate(frames):
                probe = atoms.copy()  # no deepcopy of an attached Torch calculator
                probe.info["charge"] = charge
                probe.info["spin"] = mult
                probe.info["spin_multiplicity"] = mult
                probe.calc = calculator
                start = perf_counter()
                try:
                    value = float(probe.get_potential_energy())
                    if not math.isfinite(value):
                        raise ValueError("Nonfinite PET energy")
                    energies.append(value)
                    errors.append("")
                except Exception as exc:
                    message = f"{type(exc).__name__}: {exc}"
                    log("Warn", f"Cross-backend {model} frame {idx}: {message}")
                    energies.append(math.nan)
                    errors.append(message)
                finally:
                    elapsed.append(perf_counter() - start)
                    probe.calc = None
        finally:
            calculator = None
            _release_calculator(device)

        ref_energy = energies[0]
        for idx, energy in enumerate(energies):
            delta = (energy - ref_energy) * factor if math.isfinite(energy) and math.isfinite(ref_energy) else math.nan
            primary_delta = float(primary.iloc[idx][PRIMARY_DELTA_COLUMN])
            row = {
                **base,
                "model": model,
                "# image": idx,
                "previous_#image": primary.iloc[idx].get("previous_#image", math.nan),
                "primary_energy_eV": float(primary.iloc[idx][PRIMARY_ENERGY_COLUMN]),
                "primary_deltaE_kcalmol": primary_delta,
                "energy_raw_eV": energy,
                "deltaE_kcalmol": delta,
                "deltaE_minus_primary_kcalmol": (
                    delta - primary_delta if math.isfinite(delta) and not primary_alpb else math.nan
                ),
                "elapsed_s": elapsed[idx],
                "status": "ok" if not errors[idx] and math.isfinite(delta) else "error",
                "error": errors[idx] if errors[idx] else ("Reference frame unavailable" if not math.isfinite(delta) else ""),
            }
            rows.append(row)

    output = Path(destination)
    output.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(output, index=False)
    successes = sum(row["status"] == "ok" for row in rows)
    log("I/O", f"Cross-backend: wrote {output} ({successes}/{len(rows)} successful comparisons).")
    if primary_alpb:
        log("Warn", "Cross-backend: Primary includes ALPB whereas PET-OMol energies are vacuum-only; "
            "do not interpret model disagreement as a like-for-like accuracy comparison.")
    return output
