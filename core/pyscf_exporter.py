import json
from typing import Any, Dict, Optional

import numpy as np
from pyscf.tools import molden
from utils import log


def _to_numpy_recursive(value: Any) -> Any:
    """Convert CuPy-like arrays inside nested containers to NumPy/Python objects."""
    if hasattr(value, "get") and hasattr(value, "shape"):
        return value.get()
    if isinstance(value, np.ndarray):
        return value
    if isinstance(value, (list, tuple)):
        converted = [_to_numpy_recursive(v) for v in value]
        return type(value)(converted)
    if isinstance(value, dict):
        return {k: _to_numpy_recursive(v) for k, v in value.items()}
    return value


def _resolve_pyscf_method(calc: Any) -> Any:
    """
    Resolve the most useful PySCF mean-field-like object from an ASE calculator.

    Source priority:
    1. calc.method
    2. calc.method_scan
    3. calc.g_scanner.base
    4. calc.g_scanner.method

    However, among available candidates, prefer one that already carries
    runtime results needed for export.
    """
    if calc is None:
        raise RuntimeError("atoms.calc is None. No PySCF calculator is attached.")

    candidates = [
        getattr(calc, "method", None),
        getattr(calc, "method_scan", None),
    ]

    g_scanner = getattr(calc, "g_scanner", None)
    if g_scanner is not None:
        candidates.extend(
            [
                getattr(g_scanner, "base", None),
                getattr(g_scanner, "method", None),
            ]
        )

    valid = []
    for obj in candidates:
        if obj is None:
            continue
        if hasattr(obj, "mol"):
            valid.append(obj)

    if not valid:
        raise RuntimeError(
            "Could not resolve a PySCF method object from atoms.calc. "
            "Expected one of: calc.method, calc.method_scan, or calc.g_scanner.base."
        )

    def score(obj: Any) -> tuple[int, int]:
        orbital_count = sum(
            getattr(obj, attr, None) is not None
            for attr in ("mo_coeff", "mo_energy", "mo_occ")
        )
        energy_count = sum(
            getattr(obj, attr, None) is not None
            for attr in ("e_tot", "converged")
        )
        return (orbital_count, energy_count)

    return max(valid, key=score)


def _make_cpu_reference_method(mf: Any) -> Any:
    """
    Build a CPU-side reference method suitable for analysis/export.

    Priority:
    1. scanner/base -> to_cpu()
    2. method -> to_cpu()
    3. already CPU object -> itself
    """
    if hasattr(mf, "base") and hasattr(mf.base, "to_cpu"):
        return mf.base.to_cpu()

    if hasattr(mf, "to_cpu"):
        return mf.to_cpu()

    return mf


def _copy_runtime_results(src: Any, dst: Any) -> None:
    """
    Copy runtime SCF results from src to dst after CPU conversion.

    Some scanner/to_cpu paths lose dynamic attributes such as mo_coeff/e_tot.
    """
    runtime_attrs = [
        "e_tot",
        "mo_energy",
        "mo_coeff",
        "mo_occ",
        "converged",
        "cycles",
    ]

    for attr in runtime_attrs:
        if hasattr(src, attr):
            value = getattr(src, attr)
            if value is not None:
                setattr(dst, attr, _to_numpy_recursive(value))

    src_summary = getattr(src, "scf_summary", None)
    if isinstance(src_summary, dict):
        dst.scf_summary = {k: _to_numpy_recursive(v) for k, v in src_summary.items()}

    if getattr(dst, "with_solvent", None) is None and getattr(src, "with_solvent", None) is not None:
        dst.with_solvent = src.with_solvent


def _get_scf_summary(mf: Any) -> Dict[str, Any]:
    summary = getattr(mf, "scf_summary", None)
    if isinstance(summary, dict):
        return {k: _to_numpy_recursive(v) for k, v in summary.items()}
    return {}


def _safe_jsonify(value: Any) -> Any:
    """Make values JSON-safe."""
    value = _to_numpy_recursive(value)

    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, (np.floating, np.integer)):
        return value.item()
    if isinstance(value, dict):
        return {k: _safe_jsonify(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe_jsonify(v) for v in value]
    return value


def _extract_orbital_info(mf_cpu: Any, mol: Any, data: Dict[str, Any]) -> None:
    mo_energy = getattr(mf_cpu, "mo_energy", None)
    if mo_energy is None:
        data["mo_energy"] = None
        return

    mo_energy = np.asarray(_to_numpy_recursive(mo_energy))
    data["mo_energy"] = mo_energy.tolist()

    if mo_energy.ndim == 2:
        na, nb = mf_cpu.nelec
        data["na"] = int(na)
        data["nb"] = int(nb)
        data["e_homo_alpha"] = float(mo_energy[0][na - 1]) if na > 0 and na - 1 < len(mo_energy[0]) else None
        data["e_lumo_alpha"] = float(mo_energy[0][na]) if na < len(mo_energy[0]) else None
        data["e_homo_beta"] = float(mo_energy[1][nb - 1]) if nb > 0 and nb - 1 < len(mo_energy[1]) else None
        data["e_lumo_beta"] = float(mo_energy[1][nb]) if nb < len(mo_energy[1]) else None
    else:
        nocc = mol.nelectron // 2
        homo = float(mo_energy[nocc - 1]) if nocc > 0 and nocc - 1 < len(mo_energy) else None
        lumo = float(mo_energy[nocc]) if nocc < len(mo_energy) else None
        data["nocc"] = int(nocc)
        data["na"] = int(nocc)
        data["nb"] = int(nocc)
        data["e_homo"] = homo
        data["e_lumo"] = lumo
        data["e_homo_alpha"] = homo
        data["e_lumo_alpha"] = lumo
        data["e_homo_beta"] = homo
        data["e_lumo_beta"] = lumo


def _extract_population_info(mf_cpu: Any, data: Dict[str, Any]) -> None:
    try:
        mul_pop, dip_mom = mf_cpu.analyze(verbose=0)
        data["mulliken_pop"] = mul_pop[0].tolist()
        data["mulliken_charge"] = mul_pop[1].tolist()
        data["dip_moment"] = dip_mom.tolist()
    except Exception as e:
        data["population_analysis_error"] = str(e)


def export_pyscf_single_point(atoms, prefix: str = "job", method: Optional[Any] = None) -> Dict[str, Any]:
    """
    Export PySCF single-point results to JSON and Molden.

    This function accepts either:
    - atoms with a PySCF-based ASE calculator attached, or
    - an explicit PySCF method object via method=...

    It is designed to work for both:
    - gpu4pyscf ASE wrappers
    - local/custom 3c PySCF calculators
    """
    if method is None:
        calc = getattr(atoms, "calc", None)
        mf_src = _resolve_pyscf_method(calc)
    else:
        mf_src = method

    mf_cpu = _make_cpu_reference_method(mf_src)
    _copy_runtime_results(mf_src, mf_cpu)

    mol = mf_cpu.mol
    scf_summary = _get_scf_summary(mf_cpu)

    data: Dict[str, Any] = {}
    data["name"] = prefix

    # Structure information
    data["symbols"] = [mol.atom_symbol(i) for i in range(mol.natm)]
    data["positions"] = mol.atom_coords(unit="ANG").tolist()

    # Molecule settings
    data["charge"] = int(mol.charge)
    data["spin"] = int(mol.spin)
    data["basis"] = _safe_jsonify(mol.basis)
    data["ecp"] = _safe_jsonify(mol.ecp)
    data["symmetry"] = _safe_jsonify(mol.symmetry)

    # DFT/SCF settings
    data["xc"] = _safe_jsonify(getattr(mf_src, "xc", getattr(mf_cpu, "xc", "HF")))
    data["nlc"] = _safe_jsonify(getattr(mf_src, "nlc", getattr(mf_cpu, "nlc", "")))
    data["disp"] = _safe_jsonify(getattr(mf_src, "disp", getattr(mf_cpu, "disp", None)))
    data["scf_conv_tol"] = _safe_jsonify(getattr(mf_src, "conv_tol", getattr(mf_cpu, "conv_tol", None)))
    data["max_cycle"] = _safe_jsonify(getattr(mf_src, "max_cycle", getattr(mf_cpu, "max_cycle", None)))

    # Grid settings
    if hasattr(mf_src, "grids"):
        atom_grid = getattr(mf_src.grids, "atom_grid", None)
        if isinstance(atom_grid, tuple):
            atom_grid = list(atom_grid)
        elif isinstance(atom_grid, dict) and not atom_grid:
            atom_grid = None
        data["grids_atom_grid"] = _safe_jsonify(atom_grid)
        data["grids_level"] = _safe_jsonify(getattr(mf_src.grids, "level", None))

        prune_func = getattr(mf_src.grids, "prune", None)
        data["grids_prune"] = prune_func.__name__ if hasattr(prune_func, "__name__") else str(prune_func)

    if hasattr(mf_src, "nlcgrids"):
        nlc_atom_grid = getattr(mf_src.nlcgrids, "atom_grid", None)
        if isinstance(nlc_atom_grid, tuple):
            nlc_atom_grid = list(nlc_atom_grid)
        elif isinstance(nlc_atom_grid, dict) and not nlc_atom_grid:
            nlc_atom_grid = None
        data["nlcgrids_atom_grid"] = _safe_jsonify(nlc_atom_grid)
        data["nlcgrids_level"] = _safe_jsonify(getattr(mf_src.nlcgrids, "level", None))

    # Solvent settings
    with_solvent = getattr(mf_src, "with_solvent", None)
    if with_solvent is None:
        with_solvent = getattr(mf_cpu, "with_solvent", None)

    if with_solvent is not None:
        data["solvent_method"] = _safe_jsonify(getattr(with_solvent, "method", "unknown"))
        data["solvent_name"] = _safe_jsonify(getattr(with_solvent, "solvent", None))
        data["solvent_eps"] = _safe_jsonify(getattr(with_solvent, "eps", None))
    else:
        data["solvent_method"] = None
        data["solvent_name"] = None
        data["solvent_eps"] = None

    # Energy results
    data["e_tot"] = _safe_jsonify(getattr(mf_cpu, "e_tot", None))
    data["e1"] = _safe_jsonify(scf_summary.get("e1", 0.0))
    data["e_coul"] = _safe_jsonify(scf_summary.get("coul", 0.0))
    data["e_xc"] = _safe_jsonify(scf_summary.get("exc", 0.0))
    data["e_disp"] = _safe_jsonify(scf_summary.get("dispersion", 0.0))
    data["e_solvent"] = _safe_jsonify(scf_summary.get("e_solvent", 0.0))

    # Orbital and population analysis
    _extract_orbital_info(mf_cpu, mol, data)
    _extract_population_info(mf_cpu, data)

    # Export JSON
    json_filename = f"{prefix}_pyscf.json"
    try:
        with open(json_filename, "w", encoding="utf-8") as f:
            json.dump(_safe_jsonify(data), f, indent=4)
        log("I/O", f"Saved JSON data to {json_filename}")
    except Exception as e:
        log("Warn", f"JSON export failed: {e}")

    # Export Molden
    molden_filename = f"{prefix}.molden"
    try:
        mo_coeff = _to_numpy_recursive(getattr(mf_cpu, "mo_coeff", None))
        mo_energy = _to_numpy_recursive(getattr(mf_cpu, "mo_energy", None))
        mo_occ = _to_numpy_recursive(getattr(mf_cpu, "mo_occ", None))

        if mo_coeff is None or mo_energy is None or mo_occ is None:
            raise RuntimeError(
                "Missing orbital data for Molden export: "
                f"mo_coeff={mo_coeff is None}, "
                f"mo_energy={mo_energy is None}, "
                f"mo_occ={mo_occ is None}"
            )

        with open(molden_filename, "w", encoding="utf-8") as f:
            molden.header(mol, f)
            molden.orbital_coeff(
                mol,
                f,
                mo_coeff,
                ene=mo_energy,
                occ=mo_occ,
            )
        log("I/O", f"Saved Molden file to {molden_filename}")
    except Exception as e:
        log("Warn", f"Molden export failed: {e}")

    return data
