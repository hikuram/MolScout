"""Low-cost input validation shared by MolScout core and the Streamlit UI."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import math
from pathlib import Path
import tempfile
from typing import Iterable, Mapping, Sequence

from ase import Atoms
from ase.data import atomic_numbers
from ase.io import read as ase_read



ALPB_TO_SMD_SOLVENT = {
    "water": "water",
    "acetonitrile": "acetonitrile",
    "methanol": "methanol",
    "ethanol": "ethanol",
    "ch2cl2": "dichloromethane",
    "thf": "tetrahydrofuran",
    "toluene": "toluene",
    "dmf": "N,N-dimethylformamide",
    "dmso": "dimethylsulfoxide",
    "acetone": "acetone",
    "dioxane": "1,4-dioxane",
    "ether": "diethylether",
}


@dataclass(frozen=True)
class ValidationIssue:
    severity: str
    code: str
    message: str


def error(code: str, message: str) -> ValidationIssue:
    return ValidationIssue("error", code, message)


def warning(code: str, message: str) -> ValidationIssue:
    return ValidationIssue("warning", code, message)


def split_issues(issues: Iterable[ValidationIssue]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    for issue in issues:
        if issue.severity == "error":
            errors.append(issue.message)
        else:
            warnings.append(issue.message)
    return errors, warnings


def _decode_xyz(data: bytes | str) -> str:
    if isinstance(data, str):
        return data
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("XYZ input must use UTF-8 encoding.") from exc


def parse_xyz_frames_strict(data: bytes | str, *, label: str = "XYZ") -> list[Atoms]:
    """Parse all XYZ frames without silently dropping malformed atom rows."""
    text = _decode_xyz(data)
    lines = text.splitlines()
    frames: list[Atoms] = []
    cursor = 0

    while cursor < len(lines):
        while cursor < len(lines) and not lines[cursor].strip():
            cursor += 1
        if cursor >= len(lines):
            break

        header_line = cursor + 1
        try:
            natoms = int(lines[cursor].strip())
        except ValueError as exc:
            raise ValueError(
                f"{label}: line {header_line} must contain the XYZ atom count."
            ) from exc
        if natoms <= 0:
            raise ValueError(f"{label}: atom count must be positive at line {header_line}.")
        cursor += 1

        if cursor >= len(lines):
            raise ValueError(f"{label}: missing XYZ comment line after line {header_line}.")
        cursor += 1

        symbols: list[str] = []
        positions: list[list[float]] = []
        for atom_index in range(natoms):
            if cursor >= len(lines):
                raise ValueError(
                    f"{label}: header declares {natoms} atoms, but the file ends after "
                    f"{atom_index} coordinate rows."
                )
            line_no = cursor + 1
            parts = lines[cursor].split()
            cursor += 1
            if len(parts) < 4:
                raise ValueError(
                    f"{label}: coordinate line {line_no} must contain element, x, y, z."
                )
            symbol = parts[0]
            if symbol not in atomic_numbers:
                raise ValueError(f"{label}: unknown element symbol '{symbol}' at line {line_no}.")
            try:
                coords = [float(parts[1]), float(parts[2]), float(parts[3])]
            except ValueError as exc:
                raise ValueError(
                    f"{label}: invalid numeric coordinate at line {line_no}."
                ) from exc
            if not all(math.isfinite(value) for value in coords):
                raise ValueError(f"{label}: non-finite coordinate at line {line_no}.")
            symbols.append(symbol)
            positions.append(coords)

        frames.append(Atoms(symbols=symbols, positions=positions))

    if not frames:
        raise ValueError(f"{label}: no valid XYZ frame was found.")
    return frames


def read_structure_bytes_strict(
    data: bytes,
    *,
    suffix: str = ".xyz",
    label: str = "input",
    index: int = -1,
) -> Atoms:
    suffix = suffix.lower()
    if suffix == ".xyz":
        return parse_xyz_frames_strict(data, label=label)[index]

    with tempfile.NamedTemporaryFile(suffix=suffix) as handle:
        handle.write(data)
        handle.flush()
        atoms = ase_read(handle.name, index=index)
    if not isinstance(atoms, Atoms):
        raise ValueError(f"{label}: expected one structure, got multiple frames.")
    return atoms


def read_structure_file_strict(path: str | Path, *, label: str | None = None, index: int = -1) -> Atoms:
    path = Path(path)
    display = label or path.name
    if path.suffix.lower() == ".xyz":
        return parse_xyz_frames_strict(path.read_bytes(), label=display)[index]
    atoms = ase_read(path, index=index)
    if not isinstance(atoms, Atoms):
        raise ValueError(f"{display}: expected one structure, got multiple frames.")
    return atoms


def validate_endpoint_pair(
    reactant: Atoms,
    product: Atoms,
    *,
    left_label: str = "Reactant",
    right_label: str = "Product",
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    reactant_symbols = reactant.get_chemical_symbols()
    product_symbols = product.get_chemical_symbols()
    reactant_count = Counter(reactant_symbols)
    product_count = Counter(product_symbols)

    if reactant_count != product_count:
        issues.append(error(
            "stoichiometry_mismatch",
            f"{left_label}/{right_label} elemental composition differs: "
            f"{left_label}={dict(sorted(reactant_count.items()))}, "
            f"{right_label}={dict(sorted(product_count.items()))}.",
        ))
        return issues

    if reactant_symbols != product_symbols:
        issues.append(error(
            "atom_order_mismatch",
            f"{left_label}/{right_label} contain the same elemental composition but the element order differs. "
            "Path and trajectory workflows require consistent atom correspondence.",
        ))
    return issues


def validate_charge_spin(atoms: Atoms, charge: int, multiplicity: int, *, label: str = "structure") -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if multiplicity < 1:
        return [error("invalid_multiplicity", "Multiplicity must be a positive integer.")]

    neutral_electrons = sum(atomic_numbers[symbol] for symbol in atoms.get_chemical_symbols())
    electrons = neutral_electrons - int(charge)
    unpaired = int(multiplicity) - 1

    if electrons <= 0:
        issues.append(error(
            "invalid_electron_count",
            f"{label}: charge {charge} leaves a non-positive electron count ({electrons}).",
        ))
        return issues
    if unpaired > electrons:
        issues.append(error(
            "invalid_spin_count",
            f"{label}: multiplicity {multiplicity} requires more unpaired electrons than the "
            f"{electrons} electrons available.",
        ))
    if (electrons % 2) != (unpaired % 2):
        issues.append(error(
            "charge_spin_parity",
            f"{label}: charge/multiplicity parity is inconsistent: {electrons} electrons with "
            f"multiplicity {multiplicity} is not physically admissible.",
        ))
    return issues


def validate_scan_constraints(
    scan_indices: Sequence[int],
    fixed_atoms: Sequence[int],
    n_atoms: int,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    scan = [int(value) for value in scan_indices]
    fixed = [int(value) for value in fixed_atoms]

    if len(set(scan)) != len(scan):
        issues.append(error("scan_duplicate_index", "SCAN indices must not contain duplicates."))

    for label, values in (("SCAN", scan), ("FIXED_ATOMS", fixed)):
        invalid = sorted({value for value in values if value < 0 or value >= n_atoms})
        if invalid:
            issues.append(error(
                f"{label.lower()}_index_range",
                f"{label} contains out-of-range atom indices {invalid}; valid range is 0..{n_atoms - 1}.",
            ))

    if not scan:
        return issues

    overlap = sorted(set(scan) & set(fixed))
    if overlap and len(overlap) == len(set(scan)):
        issues.append(error(
            "scan_fully_fixed",
            "All atoms defining the SCAN coordinate are also in FIXED_ATOMS. "
            "The requested coordinate cannot move.",
        ))
    elif overlap:
        issues.append(warning(
            "scan_partly_fixed",
            f"SCAN atoms {overlap} are also fixed. This can be intentional, but verify that the "
            "remaining atoms can change the requested coordinate.",
        ))
    return issues


def _normalize_solvent_token(value: object) -> str:
    return str(value or "").strip().lower().replace(" ", "").replace("-", "")


def _pyscf_profile_solvation(profile: Mapping[str, object] | None) -> tuple[bool, str | None]:
    if not isinstance(profile, Mapping) or not bool(profile.get("with_solvent", False)):
        return False, None
    if str(profile.get("solvent_model", "SMD")).upper() != "SMD":
        return True, None
    value = str(profile.get("solvent", "water"))
    aliases = {
        _normalize_solvent_token(smd): smd
        for smd in ALPB_TO_SMD_SOLVENT.values()
    }
    aliases.update({
        "dmf": "N,N-dimethylformamide",
        "dmso": "dimethylsulfoxide",
        "thf": "tetrahydrofuran",
        "dcm": "dichloromethane",
        "ch2cl2": "dichloromethane",
        "ether": "diethylether",
        "et2o": "diethylether",
        "dioxane": "1,4-dioxane",
    })
    return True, aliases.get(_normalize_solvent_token(value), value)


def _matching_alpb(smd_solvent: str | None) -> str | None:
    if smd_solvent is None:
        return None
    normalized = _normalize_solvent_token(smd_solvent)
    for alpb, smd in ALPB_TO_SMD_SOLVENT.items():
        if _normalize_solvent_token(smd) == normalized:
            return alpb
    return None


def validate_mixed_level_solvation(
    config: Mapping[str, object],
    pyscf_config: Mapping[str, object],
) -> list[ValidationIssue]:
    """Warn only when an OrbMol/PySCF mixed level has an available matching correction."""
    comparisons: list[tuple[str, str | None, str, Mapping[str, object] | None]] = []

    if bool(config.get("SCAN_MF_ON", False)):
        guide_type = str(config.get("SCAN_MF_MLIP_CALC_TYPE", "orbmol")).lower()
        alpb = str(config.get("ALPB_SOLVENT", "None")) if guide_type == "orbmol+alpb" else None
        comparisons.append(("MF-SCAN guide", alpb, "PySCF anchor", pyscf_config.get("pyscf")))
    else:
        primary = str(config.get("CALC_TYPE", "")).lower()
        refine = str(config.get("REFINE_CALC_TYPE", "")).lower()
        if bool(config.get("REFINE_ENERGY_ON", False)) and primary in {"orbmol", "orbmol+alpb"} and refine in {"pyscf", "pyscf_high"}:
            alpb = str(config.get("ALPB_SOLVENT", "None")) if primary == "orbmol+alpb" else None
            comparisons.append(("primary OrbMol level", alpb, f"refinement {refine}", pyscf_config.get(refine)))

    issues: list[ValidationIssue] = []
    for low_label, alpb_solvent, high_label, profile in comparisons:
        smd_enabled, smd_solvent = _pyscf_profile_solvation(profile)
        alpb_enabled = bool(alpb_solvent and str(alpb_solvent) != "None")
        expected_alpb = _matching_alpb(smd_solvent) if smd_enabled else None
        expected_smd = ALPB_TO_SMD_SOLVENT.get(str(alpb_solvent)) if alpb_enabled else None

        if smd_enabled and alpb_enabled:
            matched = (
                expected_smd is not None
                and _normalize_solvent_token(expected_smd) == _normalize_solvent_token(smd_solvent)
            )
            if not matched and (expected_alpb is not None or expected_smd is not None):
                suggestions = []
                if expected_alpb is not None:
                    suggestions.append(f"ALPB ({expected_alpb}) for the current SMD solvent")
                if expected_smd is not None:
                    suggestions.append(f"SMD ({expected_smd}) for the current ALPB solvent")
                issues.append(warning(
                    "mixed_solvation_mismatch",
                    f"Mixed calculation levels use different solvents: ALPB ({alpb_solvent}) vs "
                    f"SMD ({smd_solvent}). A matched correction pair is available: "
                    + " or ".join(suggestions)
                    + ".",
                ))
        elif smd_enabled and expected_alpb is not None:
            issues.append(warning(
                "mixed_solvation_missing_alpb",
                f"Mixed calculation levels detected: {high_label} uses SMD ({smd_solvent}) while "
                f"the {low_label} is in vacuum. Matching ALPB ({expected_alpb}) is available.",
            ))
        elif alpb_enabled and expected_smd is not None:
            issues.append(warning(
                "mixed_solvation_missing_smd",
                f"Mixed calculation levels detected: {low_label} uses ALPB ({alpb_solvent}) while "
                f"the {high_label} is in vacuum. Matching SMD ({expected_smd}) is available.",
            ))

    return issues


def _resolve_installed_smd_name(solvent: str) -> str:
    """Resolve a solvent against the installed PySCF database, including old PySCF releases."""
    from pyscf.solvent import smd

    resolver = None
    try:
        from pyscf.solvent._solvent_data import resolve_solvent_name
        resolver = resolve_solvent_name
    except (ImportError, AttributeError):
        pass

    if resolver is not None:
        return str(resolver(solvent))

    normalized = _normalize_solvent_token(solvent)
    aliases = {
        "dmf": "N,N-dimethylformamide",
        "dmso": "dimethylsulfoxide",
        "thf": "tetrahydrofuran",
        "dcm": "dichloromethane",
        "ch2cl2": "dichloromethane",
        "mecn": "acetonitrile",
        "acn": "acetonitrile",
        "meoh": "methanol",
        "etoh": "ethanol",
        "et2o": "diethylether",
        "ether": "diethylether",
        "dioxane": "1,4-dioxane",
    }
    if normalized in aliases:
        candidate = aliases[normalized]
        if candidate in smd.solvent_db:
            return candidate
    for candidate in smd.solvent_db:
        if _normalize_solvent_token(candidate) == normalized:
            return str(candidate)
    raise ValueError(f"SMD solvent '{solvent}' is not available in the installed PySCF database.")


def _normalize_pyscf_method_name(value: object) -> str:
    return str(value or "").strip().lower().replace("_", "-").replace(" ", "")


def _resolve_he_smoke_profile(profile: Mapping[str, object]) -> tuple[str, object]:
    """Resolve only the method/basis pieces needed for a He smoke test.

    This deliberately does not resolve or validate ECP settings. ECP validity is
    molecule-dependent and remains the responsibility of the normal calculator
    construction path.
    """
    xc_input = str(profile.get("xc", "")).strip()
    if not xc_input:
        raise ValueError("XC must not be empty")

    method_key = _normalize_pyscf_method_name(xc_input)
    vdzp_methods = {
        "r2scan-d4/vdzp": ("r2scan", "Grimme vDZP", "d4:r2scan"),
        "b3lyp-d4/vdzp": ("b3lyp", "Grimme vDZP", "d4:b3lyp"),
        "b97-d3bj/vdzp": ("GGA_XC_B97_D", "Grimme vDZP", "d3bj:b97d"),
        "wb97x-d4/vdzp": ("wb97x-v", "Grimme vDZP", "d4:wb97x"),
    }
    if method_key in vdzp_methods:
        xc, basis, _disp = vdzp_methods[method_key]
        return xc, basis

    if method_key.endswith("3c"):
        from gpu4pyscf.drivers.dft_3c_driver import parse_3c

        pyscf_xc, _nlc, basis, _ecp, (_xc_disp, _disp), _xc_gcp = parse_3c(method_key)
        return str(pyscf_xc), basis

    basis = profile.get("basis")
    if basis is None or (isinstance(basis, str) and not basis.strip()):
        raise ValueError("Basis must not be empty for a non-3c method")
    return xc_input, basis


def _validate_he_profile_smoke(profile_name: str, profile: Mapping[str, object]) -> list[ValidationIssue]:
    """Build a closed-shell He PySCF object without SCF or ECP validation."""
    issues: list[ValidationIssue] = []
    try:
        from pyscf import dft, gto

        xc, basis = _resolve_he_smoke_profile(profile)
        # ECP is intentionally omitted: this is a lightweight configuration
        # smoke test, not an element-specific ECP compatibility check.
        mol = gto.M(atom="He 0 0 0", basis=basis, charge=0, spin=0, verbose=0)

        if str(xc).strip().lower().startswith("skala"):
            # Import only. Constructing SkalaKS may load model resources and is too
            # heavy for a settings-page smoke test.
            import skala.pyscf  # noqa: F401
        else:
            # parse_xc catches misspelled/unsupported LibXC names without an SCF run.
            dft.libxc.parse_xc(xc)
            mf = dft.RKS(mol)
            mf.xc = xc
    except Exception as exc:
        issues.append(error(
            "pyscf_he_smoke_failed",
            f"Profile '{profile_name}': He smoke test failed for XC/basis settings: {exc}",
        ))
    return issues


def validate_pyscf_config_for_save(config: Mapping[str, object]) -> list[ValidationIssue]:
    """Run a lightweight He smoke test and validate configured SMD settings.

    The test intentionally excludes ECP compatibility. ECPs are element-specific
    and are validated naturally when the real calculator is constructed.
    """
    issues: list[ValidationIssue] = []
    try:
        from pyscf import dft, gto
    except Exception as exc:
        return [error("pyscf_unavailable", f"PySCF validation could not start: {exc}")]

    for profile_name in ("pyscf", "pyscf_high"):
        profile = config.get(profile_name, {})
        if not isinstance(profile, Mapping):
            issues.append(error(
                "invalid_pyscf_profile",
                f"Profile '{profile_name}' must be a mapping.",
            ))
            continue

        issues.extend(_validate_he_profile_smoke(profile_name, profile))

        if not bool(profile.get("with_solvent", False)):
            continue

        model = str(profile.get("solvent_model", "SMD")).upper()
        if model != "SMD":
            issues.append(error(
                "unsupported_solvent_model",
                f"Profile '{profile_name}': MolScout currently supports only SMD, not '{model}'.",
            ))
            continue

        solvent = str(profile.get("solvent", "water"))
        try:
            canonical = _resolve_installed_smd_name(solvent)
            # Keep the solvent test independent from the user's XC. This tells us
            # whether the configured SMD model/name itself can be constructed.
            mol = gto.M(atom="He 0 0 0", basis="sto-3g", charge=0, spin=0, verbose=0)
            mf = dft.RKS(mol)
            mf.xc = "pbe"
            mf = mf.SMD()
            mf.with_solvent.solvent = canonical
            if profile.get("eps") is not None:
                eps = float(profile["eps"])
                if not math.isfinite(eps) or eps <= 0:
                    raise ValueError("custom eps must be a finite positive number")
                mf.with_solvent.eps = eps
            mf.with_solvent.build()
            if isinstance(profile, dict):
                # Persist an exact database key so the saved profile also works
                # with PySCF releases that do not resolve common aliases.
                profile["solvent"] = canonical
        except Exception as exc:
            issues.append(error(
                "invalid_smd_configuration",
                f"Profile '{profile_name}': SMD validation failed for solvent '{solvent}': {exc}",
            ))
    return issues
