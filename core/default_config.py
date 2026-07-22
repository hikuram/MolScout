# Workflow flags
INIT_PATH_METHOD: str = "DMF" # Options: "DMF", "NEB", "SCAN"
INIT_PATH_SEARCH_ON: bool = True
INIT_RECALC_MODE_ON: bool = False
REFINE_INPUT_ON: bool = True
USE_SELLA_IN_OPT: bool = False
TSOPT_ON: bool = True
IRC_ON: bool = True
PICK_OPTPOINTS_ON: bool = True
OPT_OPTPOINTS_AGAIN_ON: bool = False
VIB_ON: bool = True
REFINE_ENERGY_ON: bool = True
OTHER_JOBS_EXAMPLE_ON: bool = False
CALC_RMSD_ON: bool = True
WRITE_SUGGESTIONS_ON: bool = True
SUGGESTIONS: list = []
SAVE_FIG_ON: bool = True
PRESERVE_CSV_ON: bool = False
CURRENT_DIR: str = "."
TIME_LOG_NAME: str = "timing.log"

# Calculation settings
CALC_TYPE: str = "orbmol" # "orbmol", "orbmol+alpb", "pyscf", "pyscf_high"
REFINE_CALC_TYPE: str = "pyscf_high"
DEVICE: str = "cuda" # "cuda" or "cpu"

# OrbMol settings
ORBMOL_VERSION: str = "v2" # "v1", "v2"

# TBLite settings for MLIP + xTB solvation-delta correction
# Set to "hybrid" to use GFN1-xTB during DMF and GFN2-xTB for everything else.
TBLITE_METHOD: str = "hybrid" # "hybrid", "GFN1-xTB", "GFN2-xTB"
TBLITE_ACCURACY: float = 0.02
ALPB_SOLVENT: str = "water"

# Model settings
MULT: int = 1
THERMO_TEMPERATURE: float = 298.15
THERMO_ATOMOSPHERE: float = 101325.0
# Optional diagnostic columns written during vibrational analysis.
# Supported values: "energy_LL_vib [kcal/mol]", "thermal_corr_G [kcal/mol]"
THERMO_EXTRA_CSV_COLUMNS: list = []

# Module-specific settings
# -DMF
NMOVE: int = 40
UPDATE_TEVAL: bool = False
DMF_CONVERGENCE: str = "tight"
# NEB Settings
NEB_IMAGES: int = 10
NEB_SPRING_CONSTANT: float = 0.1
NEB_CLIMB: bool = True
# SCAN (Elongation/Torsion) Settings
SCAN_TYPE: str = "bond"  # Options: "bond", "angle", "dihedral"
SCAN_INDICES: list = [0, 1]  # Atom indices (0-indexed). e.g., [0, 1] for bond, [0, 1, 2, 3] for dihedral
SCAN_START_VAL: float = None # If None, the initial value of the reactant geometry is used
SCAN_END_VAL: float = 2.0    # Target value (Angstrom for bond, degree for angle/dihedral)
SCAN_STEPS: int = 10
# -Sella
SELLA_INTERNAL_AUTO: bool = True
SELLA_INTERNAL: bool = True
SELLA_INTERNAL_MIN_ATOMS: int = 16
IRC_DX_INIT: float = 0.06
IRC_DX_MAX: float = 0.12
IRC_DX_MIN: float = 0.02
# -Sella & ASE Optimization
OPT_FMAX: float = 0.01     # Convergence criterion for standard optimization
TSOPT_FMAX: float = 4e-4   # Convergence criterion for TS optimization
FIXED_ATOMS: list = []     # Fixed atoms constraints (list of integer indices, e.g., [0, 1, 2])

# Physical constants
EV_TO_KCAL_MOL: float = 23.0605
EV_TO_HARTREE: float = 1 / 27.2114  # approx. 0.0367493
