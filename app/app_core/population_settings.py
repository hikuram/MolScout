"""Job-level PySCF population-analysis settings shared by Submit workflows.

UI field names are independent of the core's uppercase config keys. Keep the
mapping here so ordinary path jobs and concatenation jobs cannot diverge.
"""

from __future__ import annotations

from collections.abc import Mapping

POPULATION_METHODS = ("mulliken", "mbis", "hirshfeld")
POPULATION_UI_DEFAULTS: dict[str, object] = {
    "population_mulliken": True,
    "population_mbis": False,
    "population_hirshfeld": False,
    "population_export_mode": "compatible",
    "population_grid_level": 5,
    "population_hirshfeld_xc": "PBE",
}


def selected_population_methods(values: Mapping[str, object]) -> list[str]:
    """Return selected methods in a stable order for JSON reproducibility."""
    return [
        name
        for name in POPULATION_METHODS
        if values.get(f"population_{name}", POPULATION_UI_DEFAULTS[f"population_{name}"])
        is True
    ]


def population_config_overrides(values: Mapping[str, object]) -> dict[str, object]:
    """Convert GUI values into the existing MolScout core configuration contract."""
    mode = values.get("population_export_mode", "compatible")
    if mode not in ("compatible", "fast"):
        raise ValueError("Population export mode must be 'compatible' or 'fast'.")

    grid_level = values.get("population_grid_level", 5)
    if isinstance(grid_level, bool) or not isinstance(grid_level, int) or not 0 <= grid_level <= 9:
        raise ValueError("Population grid level must be an integer from 0 to 9.")

    hirshfeld_xc = values.get("population_hirshfeld_xc", "PBE")
    if not isinstance(hirshfeld_xc, str) or not hirshfeld_xc.strip():
        raise ValueError("Hirshfeld reference XC must be a non-empty string.")

    return {
        "POPULATION_ANALYSES": selected_population_methods(values),
        "POPULATION_FAST_EXPORT": mode == "fast",
        "POPULATION_GRID_LEVEL": grid_level,
        "POPULATION_HIRSHFELD_XC": hirshfeld_xc.strip(),
    }
