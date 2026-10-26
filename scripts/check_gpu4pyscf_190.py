#!/usr/bin/env python3
"""Small real-GPU smoke test of MolScout's GPU4PySCF v1.9.0 export path.

Run inside MolScout's rebuilt Docker image with a CUDA GPU. The regression
suite in tests/ only uses mocks; this script validates live GPU numerics.

Usage:
    python /opt/MolScout/scripts/check_gpu4pyscf_190.py \\
        --outdir /workspace/gpu190_smoke --include-open-shell
    python /opt/MolScout/scripts/check_gpu4pyscf_190.py \\
        --outdir /workspace/gpu190_smoke --with-mbis --grid-level 3
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from time import perf_counter

import numpy as np

CORE = Path(__file__).resolve().parents[1] / "core"
sys.path.insert(0, str(CORE))


def run_case(
    name: str,
    *,
    atom: str,
    spin: int,
    outdir: Path,
    schemes: list[str],
    grid_level: int,
    fast_export: bool = False,
) -> dict:
    from pyscf import gto
    from gpu4pyscf.dft import RKS, UKS
    from pyscf_exporter import export_pyscf_single_point

    mol = gto.M(atom=atom, basis="sto-3g", charge=0, spin=spin, verbose=0)
    mf = (UKS(mol, xc="PBE") if spin else RKS(mol, xc="PBE"))
    mf.conv_tol = 1e-9
    mf.max_cycle = 80
    mf.kernel()
    if not mf.converged:
        raise RuntimeError(f"GPU SCF did not converge: {name}")

    t0 = perf_counter()
    _, q_gpu = mf.mulliken_pop(verbose=0)
    gpu_mulliken_time = perf_counter() - t0
    t0 = perf_counter()
    mf_cpu = mf.to_cpu()
    conversion_time = perf_counter() - t0
    t0 = perf_counter()
    _, q_cpu = mf_cpu.mulliken_pop(verbose=0)
    cpu_mulliken_time = perf_counter() - t0

    q_gpu = np.asarray(q_gpu, dtype=float)
    q_cpu = np.asarray(q_cpu, dtype=float)
    diff = float(np.max(np.abs(q_gpu - q_cpu)))
    if not np.isfinite(q_gpu).all() or diff > 1e-5:
        raise AssertionError(f"Direct Mulliken disagrees with CPU: {diff:.3g}")

    prefix = str(outdir / name)
    data = export_pyscf_single_point(
        None,
        prefix=prefix,
        method=mf,
        population_analyses=schemes,
        population_grid_level=grid_level,
        fast_export=fast_export,
    )
    required = ("mulliken_raw_charge", "dip_moment") if fast_export else (
        "mulliken_raw_charge", "mulliken_charge", "dip_moment")
    for key in required:
        if key not in data:
            raise AssertionError(f"Missing field {key}: {data}")
    if abs(data["mulliken_raw_charge_residual"]) > 1e-5:
        raise AssertionError("Mulliken charge not conserved")
    if not (outdir / f"{name}_pyscf.json").is_file():
        raise AssertionError("JSON export missing")
    if fast_export:
        if (outdir / f"{name}.molden").exists():
            raise AssertionError("Molden unexpectedly emitted in fast mode")
    elif not (outdir / f"{name}.molden").is_file():
        raise AssertionError("Molden export missing")

    for method in ("mbis", "hirshfeld"):
        if method in schemes and f"{method}_charge" not in data:
            raise RuntimeError(f"Optional {method} failed: {data.get(f'{method}_error')}")

    result = {
        "name": name,
        "energy_hartree": float(mf.e_tot),
        "spin": spin,
        "charge": int(mol.charge),
        "mulliken_gpu_vs_cpu_max_abs_e": diff,
        "mulliken_gpu_seconds": gpu_mulliken_time,
        "scf_to_cpu_seconds": conversion_time,
        "mulliken_cpu_seconds": cpu_mulliken_time,
        "legacy_meta_lowdin_charge": data.get("mulliken_charge"),
        "fast_export": fast_export,
        "mulliken_raw_charge": data["mulliken_raw_charge"],
        "population_methods": schemes,
    }
    for method in ("mbis", "hirshfeld"):
        if f"{method}_charge_residual" in data:
            result[f"{method}_charge_residual"] = data[f"{method}_charge_residual"]
    return result


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--outdir", type=Path, default=Path("gpu190_smoke"))
    p.add_argument("--include-open-shell", action="store_true")
    p.add_argument("--with-mbis", action="store_true")
    p.add_argument("--fast-export", action="store_true")
    p.add_argument("--with-hirshfeld", action="store_true")
    p.add_argument("--grid-level", type=int, default=3)
    args = p.parse_args()
    args.outdir.mkdir(parents=True, exist_ok=True)
    schemes = ["mulliken"]
    if args.with_mbis:
        schemes.append("mbis")
    if args.with_hirshfeld:
        schemes.append("hirshfeld")
    results = [run_case(
        "water", atom="O 0 0 0; H 0 0.757 0.587; H 0 -0.757 0.587",
        spin=0, outdir=args.outdir, schemes=schemes, grid_level=args.grid_level,
        fast_export=args.fast_export,
    )]
    if args.include_open_shell:
        results.append(run_case(
            "hydrogen", atom="H 0 0 0", spin=1, outdir=args.outdir,
            schemes=schemes, grid_level=args.grid_level,
            fast_export=args.fast_export,
        ))
    report = args.outdir / "smoke_report.json"
    report.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(json.dumps(results, indent=2))
    print(f"PASS; report: {report}")


if __name__ == "__main__":
    main()
