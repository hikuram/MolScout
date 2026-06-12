"""
ase_calculators.py
Utility functions for setting up ASE calculators (PySCF, OrbMol, etc.).
"""

import os
import sys
import json
import cupy
from orb_models.forcefield import pretrained
from orb_models.forcefield.inference.calculator import ORBCalculator

# Project modules
import default_config as g

# Cache for PySCF configurations to avoid reading the JSON file multiple times
_PYSCF_CONFIG_CACHE = None
_PYSCF_PROFILE_CACHE = {}

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
    profile["is_3c"] = str(profile.get("xc", "")).endswith("3c")
    _PYSCF_PROFILE_CACHE[calc_type] = profile
    return profile

def build_pyscf_method_common(atoms, base_name, profile):
    """Build the common PySCF mean-field object (RKS) with standard settings."""
    from pyscf import M, lib
    from pyscf.pbc.tools.pyscf_ase import ase_atoms_to_pyscf

    threads = profile.get("threads", os.environ.get("OMP_NUM_THREADS", os.cpu_count()))
    lib.num_threads(threads)
    mol = M(
        atom=ase_atoms_to_pyscf(atoms),
        basis=profile.get("basis"),
        ecp=profile.get("ecp"),
        charge=g.CHARGE,
        spin=g.MULT - 1,
        output=base_name + '_pyscf.log',
        verbose=profile.get("verbose", 4)
    )
    mf = mol.RKS(
        xc=profile["xc"],
        disp=profile.get("disp"),
        conv_tol=profile.get("conv_tol", 6e-10),
        max_cycle=profile.get("max_cycle", 400)
    )

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
    if hasattr(mf, "nlcgrids") and profile.get("nlcgrids_level") is not None:
        mf.nlcgrids.level = profile.get("nlcgrids_level")

    if g.DEVICE == "cuda":
        cupy.get_default_memory_pool().free_all_blocks()
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

    if not str(config["xc"]).endswith("3c"):
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
