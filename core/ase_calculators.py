"""
ase_calculators.py
Utility functions for setting up ASE calculators (PySCF, OrbMol, etc.).
"""

import os
import sys
import json
from typing import Any, Dict
from orb_models.forcefield import pretrained
from orb_models.forcefield.inference.calculator import ORBCalculator

# Project modules
import default_config as g

# Cache for PySCF configurations to avoid reading the JSON file multiple times
_PYSCF_CONFIG_CACHE = None
_PYSCF_PROFILE_CACHE = {}
_TORCH_GPU_MEMORY_LIMIT_APPLIED = False


# Composite methods benchmarked with the Grimme vDZP orbital basis.
# These are handled through the standard PySCF/GPU4PySCF APIs, not the
# dedicated 3c driver.  The keys are normalized by _normalize_method_name().
_VDZP_METHOD_SPECS = {
    "r2scan-d4/vdzp": {
        "xc": "r2scan",
        "disp": "d4:r2scan",
    },
    "b3lyp-d4/vdzp": {
        "xc": "b3lyp",
        "disp": "d4:b3lyp",
    },
    "b97-d3bj/vdzp": {
        "xc": "b97",
        "disp": "d3bj:b97",
    },
    "wb97x-d4/vdzp": {
        # This is the general omegaB97X-D4/vDZP combination, not omegaB97X-3c.
        "xc": "wb97x-v",
        "nlc": 0,
        "disp": "d4:wb97x",
    },
}


def _normalize_method_name(value: Any) -> str:
    """Normalize a user-facing composite-method name for registry lookup."""
    return str(value or "").strip().lower().replace("_", "-").replace(" ", "")


def _is_grimme_vdzp_basis(value: Any) -> bool:
    """Return True for accepted spellings of the Grimme vDZP basis."""
    normalized = str(value or "").strip().lower().replace("_", "").replace("-", "").replace(" ", "")
    return normalized in {"vdzp", "grimmevdzp"}


def _build_vdzp_ecp(atoms):
    """Build an element-specific Grimme vDZP ECP dictionary.

    Grimme vDZP does not provide an ECP for every element (notably hydrogen).
    PySCF therefore must not receive a blanket ``ecp='Grimme vDZP'`` setting.
    Only elements for which the Basis Set Exchange entry contains ECP data are
    added to the dictionary.
    """
    from pyscf import gto

    ecp = {}
    for symbol in sorted(set(atoms.get_chemical_symbols())):
        try:
            ecp_data = gto.basis.load_ecp("Grimme vDZP", symbol)
        except Exception as exc:
            raise RuntimeError(
                f"Failed to load the Grimme vDZP ECP definition for {symbol}. "
                "Check that the installed PySCF/Basis Set Exchange data support vDZP."
            ) from exc

        if ecp_data:
            ecp[symbol] = "Grimme vDZP"

    return ecp or None


def resolve_pyscf_profile(atoms, profile: Dict[str, Any]) -> Dict[str, Any]:
    """Resolve composite aliases and molecule-dependent vDZP settings.

    The cached source profile is never modified.  This matters because the ECP
    dictionary depends on the elements present in each molecule.
    """
    resolved = dict(profile)
    method_name = _normalize_method_name(resolved.get("xc"))
    method_spec = _VDZP_METHOD_SPECS.get(method_name)

    if method_spec is not None:
        resolved.update(method_spec)
        resolved["basis"] = "Grimme vDZP"
        # A composite alias always implies the matching vDZP ECP set.  Do not
        # retain a generic def2 ECP inherited from the source profile.
        resolved["ecp"] = "auto"
        resolved["method_label"] = method_name

    if _is_grimme_vdzp_basis(resolved.get("basis")):
        resolved["basis"] = "Grimme vDZP"
        configured_ecp = resolved.get("ecp")
        auto_ecp_names = {"", "auto", "vdzp", "grimme vdzp"}
        if configured_ecp is None or str(configured_ecp).strip().lower() in auto_ecp_names:
            resolved["ecp"] = _build_vdzp_ecp(atoms)
        elif not isinstance(configured_ecp, dict):
            raise ValueError(
                "Grimme vDZP requires its matching element-specific ECPs. "
                "Set ecp to null/'auto', or provide an explicit ECP dictionary."
            )
        resolved["is_vdzp"] = True

    resolved["is_3c"] = str(resolved.get("xc", "")).lower().endswith("3c")
    resolved["is_skala"] = str(resolved.get("xc", "")).lower().startswith("skala")
    return resolved


def configure_torch_gpu_memory_limit():
    """Limit the PyTorch CUDA allocator to 50% of device memory."""
    global _TORCH_GPU_MEMORY_LIMIT_APPLIED

    if _TORCH_GPU_MEMORY_LIMIT_APPLIED or g.DEVICE != "cuda":
        return

    import torch

    if not torch.cuda.is_available():
        return

    fraction = 0.50
    device = torch.cuda.current_device()
    torch.cuda.set_per_process_memory_fraction(fraction, device=device)

    total_gib = torch.cuda.get_device_properties(device).total_memory / (1024 ** 3)
    print(
        f"[Memory] PyTorch CUDA allocator limit: {fraction:.0%} "
        f"({total_gib * fraction:.2f} GiB of {total_gib:.2f} GiB)"
    )

    _TORCH_GPU_MEMORY_LIMIT_APPLIED = True


def clear_gpu_cache():
    """Release unused PyTorch and CuPy cache blocks before a GPU calculation."""
    if g.DEVICE != "cuda":
        return

    import gc

    gc.collect()

    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except ImportError:
        pass

    try:
        import cupy

        cupy.get_default_memory_pool().free_all_blocks()
        cupy.get_default_pinned_memory_pool().free_all_blocks()
    except ImportError:
        pass

def load_pyscf_config():
    """Load PySCF configuration from a JSON file."""
    global _PYSCF_CONFIG_CACHE
    if _PYSCF_CONFIG_CACHE is None:
        default_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pyscf_config.json")
        config_path = getattr(g, "PYSCF_CONFIG_FILE", default_path)
        with open(config_path, "r", encoding="utf-8") as f:
            _PYSCF_CONFIG_CACHE = json.load(f)
    return _PYSCF_CONFIG_CACHE

def get_pyscf_profile(calc_type):
    """Retrieve the specific PySCF profile (e.g., 'pyscf_high') from the loaded config."""
    global _PYSCF_PROFILE_CACHE
    if calc_type in _PYSCF_PROFILE_CACHE:
        return _PYSCF_PROFILE_CACHE[calc_type]

    config = load_pyscf_config()
    if calc_type not in config:
        raise KeyError(f"Missing PySCF profile in config: {calc_type}")

    profile = dict(config[calc_type])
    profile["calc_type"] = calc_type
    
    profile["is_3c"] = str(profile.get("xc", "")).lower().endswith("3c")
    profile["is_skala"] = str(profile.get("xc", "")).lower().startswith("skala")
    
    _PYSCF_PROFILE_CACHE[calc_type] = profile
    return profile


def get_solvation_info(calc_type):
    """Return normalized implicit-solvation metadata for a calculator type."""
    if calc_type == "orbmol+alpb":
        return {
            "enabled": True,
            "model": "ALPB",
            "solvent": str(getattr(g, "ALPB_SOLVENT", "water")),
        }

    if calc_type in ("pyscf", "pyscf_high"):
        profile = get_pyscf_profile(calc_type)
        enabled = bool(profile.get("with_solvent", False))
        return {
            "enabled": enabled,
            "model": str(profile.get("solvent_model", "SMD")).upper() if enabled else None,
            "solvent": str(profile.get("solvent", "water")) if enabled else None,
        }

    return {"enabled": False, "model": None, "solvent": None}

def build_pyscf_method_common(atoms, base_name, profile):
    """Build the common PySCF mean-field object with standard settings."""
    from pyscf import M, lib
    from pyscf.pbc.tools.pyscf_ase import ase_atoms_to_pyscf

    # Work on a molecule-specific copy.  Composite aliases and vDZP ECPs are
    # resolved here so the cached JSON profile remains immutable.
    profile = resolve_pyscf_profile(atoms, profile)

    threads = profile.get("threads", os.environ.get("OMP_NUM_THREADS", os.cpu_count()))
    lib.num_threads(threads)
    mol = M(
        atom=ase_atoms_to_pyscf(atoms),
        basis=profile.get("basis"),
        ecp=profile.get("ecp"),
        charge=g.CHARGE,
        spin=g.MULT - 1,
        output=base_name + "_pyscf.log",
        verbose=profile.get("verbose", 4),
    )

    is_skala = profile.get("is_skala", False)

    clear_gpu_cache()

    if is_skala:
        if g.DEVICE == "cuda":
            configure_torch_gpu_memory_limit()
            from skala.gpu4pyscf import SkalaKS
        else:
            from skala.pyscf import SkalaKS

        mf = SkalaKS(mol, xc=profile["xc"])
        mf.conv_tol = profile.get("conv_tol", 6e-10)
        mf.max_cycle = profile.get("max_cycle", 400)
        if profile.get("disp"):
            mf.disp = profile["disp"]
    else:
        mf = mol.RKS(
            xc=profile["xc"],
            disp=profile.get("disp"),
            conv_tol=profile.get("conv_tol", 6e-10),
            max_cycle=profile.get("max_cycle", 400),
        )

        # Explicitly override or disable nonlocal correlation when requested.
        # This is required for combinations such as wb97x-v + D4.
        if "nlc" in profile:
            mf.nlc = profile["nlc"]

        # Use density fitting by default for the standard PySCF/GPU4PySCF path.
        # Passing no auxiliary basis lets PySCF select or generate one.
        if profile.get("with_df", True):
            auxbasis = profile.get("auxbasis")
            if auxbasis:
                mf = mf.density_fit(auxbasis=auxbasis)
            else:
                mf = mf.density_fit()

    if profile.get("with_solvent", False):
        solvent_model = str(profile.get("solvent_model", "")).upper()
        if solvent_model == "SMD":
            mf = mf.SMD()
        else:
            raise NotImplementedError(f"Unsupported solvent model: {solvent_model}")
        mf.with_solvent.solvent = profile.get("solvent", "water")
        if profile.get("eps") is not None:
            mf.with_solvent.eps = profile["eps"]

    mf.grids.level = profile.get("grids_level", 5)
    if (
        hasattr(mf, "nlcgrids")
        and profile.get("nlcgrids_level") is not None
        and profile.get("nlc") != 0
    ):
        mf.nlcgrids.level = profile["nlcgrids_level"]

    if profile.get("direct_scf_tol") is not None:
        mf.direct_scf_tol = float(profile["direct_scf_tol"])
    if profile.get("scf_level_shift") is not None:
        mf.level_shift = float(profile["scf_level_shift"])
    if "chkfile" in profile:
        mf.chkfile = profile.get("chkfile")

    if g.DEVICE == "cuda" and not is_skala:
        mf = mf.to_gpu()

    return mf

def build_pyscf_standard(atoms, base_name, profile):
    """Build a standard PySCF calculator for ASE."""
    from gpu4pyscf.tools.ase_interface import PySCF
    mf = build_pyscf_method_common(atoms, base_name, profile)
    return PySCF(method=mf)

def build_pyscf_3c(atoms, base_name, profile):
    """Build a PySCF calculator specifically for composite methods like r2SCAN-3c."""
    from pyscf_3c import PySCFCalculator, build_3c_method

    clear_gpu_cache()

    config = {}
    config["xc"] = profile["xc"]
    config["charge"] = g.CHARGE
    config["spin"] = g.MULT - 1
    config["verbose"] = profile.get("verbose", 4)
    config["output"] = base_name + "_pyscf.log"
    config["inputfile"] = [
        (ele, coord) for ele, coord in zip(atoms.get_chemical_symbols(), atoms.get_positions())
    ]
    config["with_df"] = profile.get("with_df", True)
    config["auxbasis"] = profile.get("auxbasis", "def2-universal-jkfit")
    config["with_gpu"] = (g.DEVICE == "cuda")

    if profile.get("conv_tol") is not None:
        config["scf_conv_tol"] = profile["conv_tol"]
    if profile.get("scf_level_shift") is not None:
        config["scf_level_shift"] = profile["scf_level_shift"]
    if "max_cycle" in profile:
        config["scf_max_cycle"] = profile["max_cycle"]
    if profile.get("grids_level") is not None:
        config["grids"] = {"level": profile["grids_level"]}
    if profile.get("nlcgrids_level") is not None:
        config["nlcgrids"] = {"level": profile["nlcgrids_level"]}

    if profile.get("with_solvent", False):
        config["with_solvent"] = True
        config["solvent"] = {
            "method": profile.get("solvent_model", "SMD"),
            "eps": profile.get("eps", 78.3553),
            "solvent": profile.get("solvent", "water"),
        }

    if not str(config["xc"]).lower().endswith("3c"):
        raise NotImplementedError("When a 3c profile is specified, the xc string must end with '3c'.")

    mf = build_3c_method(config)
    return PySCFCalculator(mf, xc_3c=profile["xc"])

def load_orbmol_model():
    """Load the selected OrbMol model."""
    orbmol_version = str(getattr(g, "ORBMOL_VERSION", "v2")).lower()

    if orbmol_version == "v2":
        loader = pretrained.orbmol_v2
    elif orbmol_version == "v1":
        loader = pretrained.orbmol_v1_conservative
    else:
        raise ValueError(f"Unknown ORBMOL_VERSION: {orbmol_version}")

    return loader(
        device=g.DEVICE,
        precision="float64",
    )

def make_calculator(calc_type, atoms, base_name):
    """
    Initialize and return the appropriate ASE calculator based on the calc_type.
    Supported types: 'pyscf', 'pyscf_high', 'orbmol', 'orbmol+alpb'.
    """
    # PySCF
    if calc_type in ["pyscf", "pyscf_high"]:
        profile = get_pyscf_profile(calc_type)
        if profile["is_3c"]:
            calculator = build_pyscf_3c(atoms, base_name, profile)
        else:
            calculator = build_pyscf_standard(atoms, base_name, profile)

    # orbmol
    elif calc_type == "orbmol":
        orbff, atoms_adapter = load_orbmol_model()
        calculator = ORBCalculator(orbff, atoms_adapter=atoms_adapter, device=g.DEVICE)

    # orbmol+alpb
    elif calc_type == "orbmol+alpb":
        from ase.calculators.mixing import LinearCombinationCalculator
        from dual_tblite_delta import DualTBLite
        
        orbff, atoms_adapter = load_orbmol_model()
        solvation = ("alpb", getattr(g, "ALPB_SOLVENT", "water"))
        acc = getattr(g, "TBLITE_ACCURACY", 0.02)
        calc_mlip =  ORBCalculator(orbff, atoms_adapter=atoms_adapter, device=g.DEVICE)
        
        # --- Resolve TBLite method ---
        # If 'hybrid' is passed to the calculator builder, it behaves as GFN2-xTB.
        # (The temporary switch to GFN1-xTB is handled in molscout.py during DMF)
        current_tblite_method = getattr(g, 'TBLITE_METHOD', 'GFN2-xTB')
        if current_tblite_method == "hybrid":
            current_tblite_method = "GFN2-xTB"
            
        calc_delta = DualTBLite(method=current_tblite_method, charge=g.CHARGE, multiplicity=g.MULT, solvation=solvation, accuracy=acc, verbosity=0)
        calculator = LinearCombinationCalculator([calc_mlip, calc_delta], [1, 1])

    else:
        sys.exit(f"error: incorrect calc type: {calc_type}")
        
    return calculator
