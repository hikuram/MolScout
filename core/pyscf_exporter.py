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


def _record_charge_result(
    data: Dict[str, Any],
    prefix: str,
    charges: Any,
    total_charge: int,
    *,
    populations: Any = None,
    multipoles: Optional[tuple[Any, ...]] = None,
) -> None:
    """Store an atom-resolved charge model and its charge-conservation residual."""
    q = np.asarray(_to_numpy_recursive(charges), dtype=float)
    if q.shape != (len(data["symbols"]),):
        raise ValueError(f"{prefix} charges have unexpected shape {q.shape}")
    if not np.isfinite(q).all():
        raise ValueError(f"{prefix} charges contain non-finite values")

    data[f"{prefix}_charge"] = q.tolist()
    data[f"{prefix}_charge_sum"] = float(q.sum())
    data[f"{prefix}_charge_residual"] = float(q.sum() - total_charge)
    if populations is not None:
        data[f"{prefix}_pop"] = _safe_jsonify(populations)
    if multipoles is not None:
        labels = ("dipoles", "quadrupoles", "octupoles", "r2_moment", "r3_moment", "r4_moment")
        if len(multipoles) != len(labels):
            raise ValueError(f"{prefix} multipole result has {len(multipoles)} components")
        for label, values in zip(labels, multipoles):
            data[f"{prefix}_{label}"] = _safe_jsonify(values)


def _extract_direct_mulliken(mf_src: Any, data: Dict[str, Any]) -> None:
    """Use the v1.9 GPU4PySCF Mulliken API before converting the SCF object to CPU.

    GPU4PySCF's v1.9 implementation transfers the density and overlap matrices
    to CPU for the final population analysis, but avoids the full `to_cpu()` plus
    `analyze()` round trip required by the previous MolScout implementation.
    """
    try:
        pop, charges = mf_src.mulliken_pop(verbose=0)
        _record_charge_result(
            data, "mulliken_raw", charges, int(mf_src.mol.charge), populations=pop,
        )
        data["mulliken_raw_backend"] = type(mf_src).__module__
    except Exception as exc:
        # Do not make the SCF result/export unusable if optional analysis fails.
        data["mulliken_raw_error"] = str(exc)
        log("Warn", f"Direct Mulliken analysis failed: {exc}")


def _extract_partitioned_populations(
    mf_src: Any,
    data: Dict[str, Any],
    methods: tuple[str, ...],
    grid_level: int,
    hirshfeld_xc: str,
) -> None:
    """Optional GPU MBIS / Hirshfeld atomic multipoles, from v1.9.0.

    Both functions return 7 arrays: charges, atom-centered dipoles,
    quadrupoles, octupoles, and radial moments r2/r3/r4. These are not the
    molecular dipole moment and must not overwrite the old `dip_moment` field.
    """
    requested = [x for x in ("mbis", "hirshfeld") if x in methods]
    if not requested:
        return

    mol = mf_src.mol
    # Upstream explicitly advises against ECP-based partitioned multipoles.
    # Refuse instead of silently saving charges with questionable core density.
    if len(getattr(mol, "_ecpbas", ())) > 0 or bool(getattr(mol, "pseudo", None)):
        for scheme in requested:
            data[f"{scheme}_error"] = "Skipped: ECP/pseudopotential atomic multipoles are not supported by MolScout."
        return
    if hasattr(mol, "atom_charges") and np.any(np.asarray(mol.atom_charges()) == 0):
        # Both upstream implementations explicitly reject ghost atoms.
        for scheme in requested:
            data[f"{scheme}_error"] = "Skipped: ghost atoms are unsupported in GPU4PySCF v1.9.0."
        return
    if "mbis" in requested and int(mol.charge) < 0:
        data["mbis_warning"] = (
            "GPU4PySCF warns that diffuse negative charge can make MBIS "
            "shell fitting unreliable; inspect partition widths and charge residual."
        )

    try:
        from gpu4pyscf.dft.gen_grid import Grids

        grids = Grids(mol)
        grids.level = grid_level
        dm = mf_src.make_rdm1()
    except Exception as exc:
        for scheme in requested:
            data[f"{scheme}_error"] = f"Failed to initialize GPU density/grid: {exc}"
        return

    for scheme in requested:
        try:
            if scheme == "mbis":
                from gpu4pyscf.qmmm.mbis import mbis

                result = mbis(mol, grids, dm)
            else:
                from gpu4pyscf.qmmm.hirshfeld import hirshfeld

                # This XC setting describes neutral reference atoms, not the
                # converged molecular SCF functional. Keep it reproducible.
                data["hirshfeld_reference_xc"] = hirshfeld_xc
                result = hirshfeld(mol, grids, dm, xc=hirshfeld_xc)

            if len(result) != 7:
                raise ValueError(f"Expected 7 multipole arrays, got {len(result)}")
            _record_charge_result(
                data, scheme, result[0], int(mol.charge), multipoles=tuple(result[1:]),
            )
            data[f"{scheme}_grid_level"] = grid_level
        except Exception as exc:
            data[f"{scheme}_error"] = str(exc)
            log("Warn", f"{scheme.upper()} population analysis failed: {exc}")


def _extract_population_info(
    mf_cpu: Any, data: Dict[str, Any], *, include_meta_lowdin: bool = True,
) -> None:
    """Preserve the existing JSON keys' *meta-Lowdin* population semantics.

    PySCF `analyze()` uses `with_meta_lowdin=True` by default. Historic MolScout
    exports named the resulting populations `mulliken_*`. Keep those values
    comparable across versions, while `mulliken_raw_*` is explicitly the new
    conventional Mulliken scheme computed on the source method.
    """
    if not include_meta_lowdin:
        # In fast export mode retain the source SCF on GPU. The source method's
        # dip_moment() performs only the required density-matrix host transfer.
        try:
            data["dip_moment"] = _safe_jsonify(mf_cpu.dip_moment(verbose=0))
        except Exception as exc:
            data["dipole_analysis_error"] = str(exc)
        return
    try:
        # Avoid the extra orbital printing/analysis in `analyze()`; compute only
        # the already-exported meta-Lowdin populations and dipole moment.
        mul_pop = mf_cpu.mulliken_meta(verbose=0)
        data["mulliken_pop"] = _safe_jsonify(mul_pop[0])
        data["mulliken_charge"] = _safe_jsonify(mul_pop[1])
        data["mulliken_scheme"] = "meta_lowdin (legacy MolScout keys)"
        data["mulliken_charge_residual"] = float(
            np.asarray(_to_numpy_recursive(mul_pop[1]), dtype=float).sum() - int(mf_cpu.mol.charge)
        )
    except Exception as exc:
        # Some custom SCF classes only expose `analyze()`.
        try:
            mul_pop, _ = mf_cpu.analyze(verbose=0, with_meta_lowdin=True)
            data["mulliken_pop"] = _safe_jsonify(mul_pop[0])
            data["mulliken_charge"] = _safe_jsonify(mul_pop[1])
            data["mulliken_scheme"] = "meta_lowdin (legacy MolScout keys)"
        except Exception as fallback_exc:
            data["population_analysis_error"] = f"{exc}; analyze fallback: {fallback_exc}"

    try:
        data["dip_moment"] = _safe_jsonify(mf_cpu.dip_moment(verbose=0))
    except Exception as exc:
        data["dipole_analysis_error"] = str(exc)


def export_pyscf_single_point(
    atoms,
    prefix: str = "job",
    method: Optional[Any] = None,
    *,
    population_analyses: tuple[str, ...] | list[str] = ("mulliken",),
    population_grid_level: int = 5,
    hirshfeld_xc: str = "PBE",
    fast_export: bool = False,
) -> Dict[str, Any]:
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

    allowed = {"mulliken", "mbis", "hirshfeld"}
    if isinstance(population_analyses, str):
        raise ValueError("population_analyses must be a list, not a string")
    requested = tuple(str(method).lower() for method in population_analyses)
    unknown = set(requested) - allowed
    if unknown:
        raise ValueError(f"Unknown population analysis method(s): {sorted(unknown)}")
    if not isinstance(population_grid_level, int) or not 0 <= population_grid_level <= 9:
        raise ValueError("population_grid_level must be an integer between 0 and 9")
    if not isinstance(hirshfeld_xc, str) or not hirshfeld_xc.strip():
        raise ValueError("hirshfeld_xc must be a non-empty string")
    if not isinstance(fast_export, bool):
        raise ValueError("fast_export must be a boolean")

    # Source-side methods can use the converged GPU density matrix without
    # converting the entire SCF object to CPU just for population analysis.
    population_data: Dict[str, Any] = {
        "symbols": [mf_src.mol.atom_symbol(i) for i in range(mf_src.mol.natm)]
    }
    if "mulliken" in requested:
        _extract_direct_mulliken(mf_src, population_data)
    _extract_partitioned_populations(
        mf_src, population_data, requested, population_grid_level, hirshfeld_xc,
    )

    # Backward-compatible mode needs CPU for meta-Lowdin and Molden. The
    # explicitly selected fast mode avoids conversion of the full SCF object,
    # but therefore omits both the historical meta-Lowdin and Molden outputs.
    if fast_export:
        mf_cpu = mf_src
    else:
        mf_cpu = _make_cpu_reference_method(mf_src)
        _copy_runtime_results(mf_src, mf_cpu)

    mol = mf_cpu.mol
    scf_summary = _get_scf_summary(mf_cpu)

    data: Dict[str, Any] = {}
    data["name"] = prefix
    data.update(population_data)
    data["population_analyses_requested"] = list(requested)
    data["population_export_mode"] = "fast" if fast_export else "compatible"
    data["molden_requested"] = not fast_export

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
    _extract_population_info(mf_cpu, data, include_meta_lowdin=not fast_export)

    # Export JSON
    json_filename = f"{prefix}_pyscf.json"
    try:
        with open(json_filename, "w", encoding="utf-8") as f:
            json.dump(_safe_jsonify(data), f, indent=4)
        log("I/O", f"Saved JSON data to {json_filename}")
    except Exception as e:
        log("Warn", f"JSON export failed: {e}")

    if not fast_export:
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
                if np.asarray(mo_coeff).ndim == 3:
                    # PySCF's Molden writer accepts one spin channel at a time.
                    # The beta call must *not* introduce a second [MO] section.
                    for spin_index, spin_name in enumerate(("Alpha", "Beta")):
                        molden.orbital_coeff(
                            mol, f, mo_coeff[spin_index], spin=spin_name,
                            ene=mo_energy[spin_index], occ=mo_occ[spin_index],
                        )
                else:
                    molden.orbital_coeff(
                        mol, f, mo_coeff, ene=mo_energy, occ=mo_occ,
                    )
            log("I/O", f"Saved Molden file to {molden_filename}")
        except Exception as e:
            log("Warn", f"Molden export failed: {e}")

    return data
