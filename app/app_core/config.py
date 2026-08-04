"""Static app configuration."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from .paths import CORE_DIR


def _load_core_default(name: str, fallback):
    spec = importlib.util.spec_from_file_location("core_default_config", CORE_DIR / "default_config.py")
    if spec is None or spec.loader is None:
        return fallback
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception:
        return fallback
    return getattr(module, name, fallback)

WORKFLOW_LABELS = {
    "Lite workflow": {
        "script": "molscout.py",
        "help": "Run path search, TS optimization, IRC, VIB, and final plots.",
        "input_mode": "reactant_product",
    },
    "Full workflow": {
        "script": "molscout.py",
        "help": "Run path search, TS optimization, IRC, VIB, energy refine (DFT), and final plots.",
        "input_mode": "reactant_product",
    },
    "IRC workflow only": {
        "script": "molscout.py",
        "help": "Run TS optimization and IRC from an existing trajectory or coordinate file.",
        "input_mode": "single_input",
    },
    "VIB workflow only": {
        "script": "molscout.py",
        "help": "Run vibrational analysis from an existing trajectory or coordinate file.",
        "input_mode": "single_input",
    },
    "Figure refresh only": {
        "script": "molscout.py",
        "help": "Regenerate figures from an existing trajectory and result CSV.",
        "input_mode": "single_input_with_result",
    },
}

METHOD_OPTIONS = ["orbmol", "orbmol+alpb", "pyscf", "pyscf_high", "custom"]
ORBMOL_VERSION_OPTIONS = ["v2", "v1"]
DEFAULT_ORBMOL_VERSION = str(_load_core_default("ORBMOL_VERSION", "v2"))
DEFAULT_ALPB_SOLVENT = str(_load_core_default("ALPB_SOLVENT", "water"))
DEFAULT_TBLITE_ACCURACY = float(_load_core_default("TBLITE_ACCURACY", 0.02))
PACKAGE_CHECKS = [
    {
        "package": "ase",
        "imports": ["ase"],
        "distributions": ["ase"],
        "label": "core geometry / trajectory handling",
    },
    {
        "package": "streamlit",
        "imports": ["streamlit"],
        "distributions": ["streamlit"],
        "label": "GUI runtime",
    },
    {
        "package": "pydmf",
        "imports": ["dmf"],
        "distributions": ["pydmf"],
        "label": "DMF initial path search",
    },
    {
        "package": "sella",
        "imports": ["sella"],
        "distributions": ["sella"],
        "label": "TS optimization / IRC",
    },
    {
        "package": "orb-models",
        "imports": ["orb_models"],
        "distributions": ["orb-models"],
        "label": "OrbMol backend",
    },
    {
        "package": "pyscf",
        "imports": ["pyscf"],
        "distributions": ["pyscf"],
        "label": "PySCF backend",
    },
    {
        "package": "tblite",
        "imports": ["tblite"],
        "distributions": ["tblite"],
        "label": "xTB / ALPB backend",
    },
    {
        "package": "torch",
        "imports": ["torch"],
        "distributions": ["torch"],
        "label": "OrbMol / Skala CUDA memory handling",
    },
    {
        "package": "gpu4pyscf",
        "imports": ["gpu4pyscf"],
        "distributions": ["gpu4pyscf", "gpu4pyscf-cuda12x", "gpu4pyscf-cuda13x"],
        "label": "GPU acceleration for PySCF backends",
    },
    {
        "package": "cupy",
        "imports": ["cupy"],
        "distributions": ["cupy", "cupy-cuda12x", "cupy-cuda13x"],
        "label": "GPU array runtime / memory pool control",
    },
    {
        "package": "cupytensor",
        "imports": ["cupytensor", "cutensor", "cupy.cuda.cutensor"],
        "distributions": ["cupytensor", "cutensor", "cutensor-cu12", "cutensor-cu13"],
        "label": "cuTENSOR bindings used by CuPy/GPU backends",
    },
    {
        "package": "libxc",
        "imports": ["pyscf.dft.libxc", "pylibxc"],
        "distributions": ["pylibxc", "libxc"],
        "label": "exchange-correlation functional support",
    },
    {
        "package": "skala",
        "imports": ["skala"],
        "distributions": ["skala"],
        "label": "optional Skala functional backend",
        "required": False,
    },
]
SAMPLE_INPUT_ROOT = CORE_DIR / "sample_input"
RESULT_CANDIDATES = [
    "result.csv",
    "molscout.log",
    "timing.log",
    "suggestions.txt",
    "output.json",
    "orbitals.molden",
]
LOG_EXTENSIONS = {".log"}
JSON_EXTENSIONS = {".json"}
XYZ_EXTENSIONS = {".xyz"}
TEXT_EXTENSIONS = {".log", ".txt", ".json", ".xyz", ".csv", ".out", ".err", ".dat"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg"}
TABLE_EXTENSIONS = {".csv"}
INPUT_EXTENSIONS = {".traj", ".xyz"}
OUTPUT_CATEGORY_DIRS = {
    ".log": "Logs",
    ".json": "Json",
    ".xyz": "XYZ",
    ".csv": "Tables",
}
