"""Helpers for submitting MolScout jobs from a flat JSON configuration."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class JsonSubmissionError(ValueError):
    """Raised when an uploaded JSON configuration cannot be submitted."""


WORKFLOW_PRESETS = {
    "Lite workflow": {
        "initial_path": True,
        "ts_opt": True,
        "irc": True,
        "vib": True,
        "refine": False,
    },
    "Full workflow": {
        "initial_path": True,
        "ts_opt": True,
        "irc": True,
        "vib": True,
        "refine": True,
    },
    "IRC workflow only": {
        "initial_path": False,
        "ts_opt": True,
        "irc": True,
        "vib": False,
        "refine": False,
    },
    "VIB workflow only": {
        "initial_path": False,
        "ts_opt": False,
        "irc": False,
        "vib": True,
        "refine": False,
    },
}


PATH_DEFAULT_KEY_MAP = {
    "ORBMOL_VERSION": "orbmol_version",
    "TBLITE_METHOD": "tblite",
    "TBLITE_ACCURACY": "tblite_accuracy",
    "INIT_PATH_METHOD": "init_path_method",
    "REFINE_INPUT_ON": "refine_input_on",
    "PICK_OPTPOINTS_ON": "pick_optpoints_on",
    "SAVE_FIG_ON": "save_fig_on",
    "NMOVE": "nmove",
    "UPDATE_TEVAL": "update_teval",
    "DMF_CONVERGENCE": "dmf_convergence",
    "NEB_IMAGES": "neb_images",
    "NEB_SPRING_CONSTANT": "neb_spring_constant",
    "NEB_CLIMB": "neb_climb",
    "SCAN_TYPE": "scan_type",
    "SCAN_STEPS": "scan_steps",
    "SCAN_MF_ON": "scan_mf_on",
    "SCAN_MF_INTERVAL": "scan_mf_interval",
    "USE_SELLA_IN_OPT": "use_sella_in_opt",
    "SELLA_INTERNAL_AUTO": "sella_internal_auto",
    "SELLA_INTERNAL": "sella_internal",
    "IRC_DX_INIT": "irc_dx_init",
    "IRC_DX_MAX": "irc_dx_max",
    "IRC_DX_MIN": "irc_dx_min",
    "OPT_FMAX": "opt_fmax",
    "TSOPT_FMAX": "tsopt_fmax",
    "REFINE_CALC_TYPE": "refine_calc_type",
    "OPT_OPTPOINTS_AGAIN_ON": "opt_optpoints_again_on",
}


CONCAT_DEFAULT_KEY_MAP = {
    "ORBMOL_VERSION": "orbmol_version",
    "TBLITE_METHOD": "tblite",
    "TBLITE_ACCURACY": "tblite_accuracy",
    "SAVE_FIG_ON": "save_fig_on",
    "PICK_OPTPOINTS_ON": "pick_optpoints_on",
    "USE_SELLA_IN_OPT": "use_sella_in_opt",
    "SELLA_INTERNAL_AUTO": "sella_internal_auto",
    "SELLA_INTERNAL": "sella_internal",
    "IRC_DX_INIT": "irc_dx_init",
    "IRC_DX_MAX": "irc_dx_max",
    "IRC_DX_MIN": "irc_dx_min",
    "OPT_FMAX": "opt_fmax",
    "TSOPT_FMAX": "tsopt_fmax",
    "REFINE_CALC_TYPE": "refine_calc_type",
    "OPT_OPTPOINTS_AGAIN_ON": "opt_optpoints_again_on",
}


def parse_json_config(data: bytes) -> dict[str, Any]:
    """Parse and validate an uploaded flat MolScout configuration."""
    try:
        payload = json.loads(data.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise JsonSubmissionError("The JSON file must use UTF-8 encoding.") from exc
    except json.JSONDecodeError as exc:
        raise JsonSubmissionError(f"Invalid JSON configuration: {exc}") from exc

    if not isinstance(payload, dict):
        raise JsonSubmissionError("The configuration root must be a JSON object.")

    invalid_keys = [key for key in payload if not isinstance(key, str) or not key.isupper()]
    if invalid_keys:
        labels = ", ".join(repr(key) for key in invalid_keys)
        raise JsonSubmissionError(f"Configuration keys must be uppercase strings: {labels}")

    missing_keys = [key for key in ("CHARGE", "CALC_TYPE") if key not in payload]
    if missing_keys:
        labels = ", ".join(missing_keys)
        raise JsonSubmissionError(
            "Submit (JSON) currently expects a resolved configuration. "
            f"Missing required keys: {labels}"
        )
    if isinstance(payload["CHARGE"], bool) or not isinstance(payload["CHARGE"], int):
        raise JsonSubmissionError("CHARGE must be an integer.")
    if not isinstance(payload["CALC_TYPE"], str) or not payload["CALC_TYPE"].strip():
        raise JsonSubmissionError("CALC_TYPE must be a non-empty string.")

    boolean_keys = (
        "INIT_PATH_SEARCH_ON",
        "TSOPT_ON",
        "IRC_ON",
        "VIB_ON",
        "REFINE_ENERGY_ON",
        "PRESERVE_CSV_ON",
        "SCAN_MF_ON",
    )
    for key in boolean_keys:
        if key in payload and not isinstance(payload[key], bool):
            raise JsonSubmissionError(f"{key} must be a boolean.")
    if "MULT" in payload and (
        isinstance(payload["MULT"], bool)
        or not isinstance(payload["MULT"], int)
        or payload["MULT"] < 1
    ):
        raise JsonSubmissionError("MULT must be a positive integer.")
    if "THERMO_TEMPERATURE" in payload and (
        isinstance(payload["THERMO_TEMPERATURE"], bool)
        or not isinstance(payload["THERMO_TEMPERATURE"], (int, float))
    ):
        raise JsonSubmissionError("THERMO_TEMPERATURE must be a number.")
    if "R_CSV" in payload and not isinstance(payload["R_CSV"], str):
        raise JsonSubmissionError("R_CSV must be a string.")
    if bool(payload.get("PRESERVE_CSV_ON", False)):
        raise JsonSubmissionError(
            "Figure refresh configurations are not supported by the JSON submit page."
        )

    if bool(payload.get("SCAN_MF_ON", False)):
        if not bool(payload.get("INIT_PATH_SEARCH_ON", True)):
            raise JsonSubmissionError("SCAN_MF_ON requires INIT_PATH_SEARCH_ON=true.")
        if str(payload.get("INIT_PATH_METHOD", "DMF")).upper() != "SCAN":
            raise JsonSubmissionError("SCAN_MF_ON requires INIT_PATH_METHOD='SCAN'.")
        if str(payload.get("CALC_TYPE", "")).lower() != "pyscf":
            raise JsonSubmissionError("MF-SCAN requires CALC_TYPE='pyscf'.")
        interval = payload.get("SCAN_MF_INTERVAL", 2)
        if isinstance(interval, bool) or not isinstance(interval, int) or interval < 1:
            raise JsonSubmissionError("SCAN_MF_INTERVAL must be an integer >= 1.")
        mlip_calc_type = str(payload.get("SCAN_MF_MLIP_CALC_TYPE", "orbmol")).lower()
        if mlip_calc_type not in {"orbmol", "orbmol+alpb"}:
            raise JsonSubmissionError(
                "MF-SCAN supports SCAN_MF_MLIP_CALC_TYPE='orbmol' or 'orbmol+alpb'."
            )
        if mlip_calc_type == "orbmol+alpb" and str(payload.get("ALPB_SOLVENT", "None")) == "None":
            raise JsonSubmissionError(
                "MF-SCAN orbmol+alpb guide requires ALPB_SOLVENT to be set."
            )
    return payload


def input_mode(config: dict[str, Any]) -> str:
    """Return the required input layout for a submitted configuration."""
    if bool(config.get("INIT_PATH_SEARCH_ON", True)):
        if str(config.get("INIT_PATH_METHOD", "DMF")).upper() == "CAT":
            return "cat"
        return "reactant_product"
    return "single_input"


def normalize_runtime_config(config: dict[str, Any]) -> dict[str, Any]:
    """Return a runtime copy with workflow-inapplicable options disabled."""
    normalized = dict(config)
    method = str(normalized.get("INIT_PATH_METHOD", "DMF")).upper()
    refine_input_applicable = (
        bool(normalized.get("INIT_PATH_SEARCH_ON", True))
        and method in {"DMF", "NEB", "CAT"}
    )
    if not refine_input_applicable:
        normalized["REFINE_INPUT_ON"] = False
    if method != "SCAN":
        normalized["SCAN_MF_ON"] = False
    return normalized


def product_is_required(config: dict[str, Any]) -> bool:
    """Return whether the path search requires a product structure."""
    return str(config.get("INIT_PATH_METHOD", "DMF")).upper() != "SCAN"


def result_name(config: dict[str, Any]) -> str:
    """Return a safe result CSV filename from the submitted configuration."""
    name = Path(str(config.get("R_CSV", "result.csv"))).name
    return name if name.lower().endswith(".csv") else "result.csv"


def workflow_steps(config: dict[str, Any]) -> dict[str, bool]:
    """Translate resolved configuration flags to app workflow flags."""
    return {
        "initial_path": bool(config.get("INIT_PATH_SEARCH_ON", True)),
        "ts_opt": bool(config.get("TSOPT_ON", True)),
        "irc": bool(config.get("IRC_ON", True)),
        "vib": bool(config.get("VIB_ON", True)),
        "refine": bool(config.get("REFINE_ENERGY_ON", True)),
    }


def infer_workflow_preset(config: dict[str, Any]) -> str:
    """Return the closest GUI workflow preset for a resolved configuration."""
    steps = workflow_steps(config)
    for preset, preset_steps in WORKFLOW_PRESETS.items():
        if steps == preset_steps:
            return preset
    return "Full workflow"


def configuration_summary(config: dict[str, Any]) -> list[dict[str, str]]:
    """Build a compact summary for the JSON submit page."""
    steps = workflow_steps(config)
    enabled = [name for name, value in steps.items() if value]
    mf_scan_on = bool(config.get("SCAN_MF_ON", False))
    calculator = str(config.get("CALC_TYPE", ""))
    if mf_scan_on:
        guide = f"OrbMol {config.get('ORBMOL_VERSION', 'v2')}"
        if str(config.get("SCAN_MF_MLIP_CALC_TYPE", "orbmol")).lower() == "orbmol+alpb":
            guide += f" + ALPB ({config.get('ALPB_SOLVENT', 'water')})"
        calculator = f"{calculator} (MF-SCAN DFT anchors; {guide} guide)"
    initial_path = str(config.get("INIT_PATH_METHOD", "Disabled"))
    if mf_scan_on and initial_path.upper() == "SCAN":
        initial_path = f"SCAN / MF-SCAN (interval {int(config.get('SCAN_MF_INTERVAL', 2))})"
    return [
        {"Setting": "Calculator", "Value": calculator},
        {
            "Setting": "Charge / multiplicity",
            "Value": f"{int(config.get('CHARGE', 0))} / {int(config.get('MULT', 1))}",
        },
        {
            "Setting": "Initial path",
            "Value": initial_path if steps["initial_path"] else "Disabled",
        },
        {"Setting": "Workflow steps", "Value": ", ".join(enabled) or "None"},
        {"Setting": "Input mode", "Value": input_mode(config).replace("_", " ")},
        {"Setting": "Result CSV", "Value": result_name(config)},
    ]


def uses_pyscf(config: dict[str, Any]) -> bool:
    """Return whether the enabled workflow may use a PySCF profile."""
    primary = str(config.get("CALC_TYPE", "")).lower()
    if primary in {"pyscf", "pyscf_high"}:
        return True
    refine = str(config.get("REFINE_CALC_TYPE", "")).lower()
    return bool(config.get("REFINE_ENERGY_ON", False)) and refine in {"pyscf", "pyscf_high"}


def _method_defaults(config: dict[str, Any]) -> dict[str, object]:
    calc_type = str(config.get("CALC_TYPE", "orbmol"))
    if calc_type == "orbmol+alpb":
        method = "orbmol"
        custom = "orbmol"
        solvent = str(config.get("ALPB_SOLVENT", "water"))
    elif calc_type in {"orbmol", "pyscf", "pyscf_high"}:
        method = calc_type
        custom = calc_type
        solvent = "None"
    else:
        method = "custom"
        custom = calc_type
        solvent = "None"
    return {
        "method": method,
        "custom": custom,
        "alpb_solvent": solvent,
    }


def _copy_known_values(
    config: dict[str, Any],
    mapping: dict[str, str],
) -> dict[str, object]:
    return {
        target: config[source]
        for source, target in mapping.items()
        if source in config
    }


def gui_defaults_from_config(config: dict[str, Any]) -> tuple[str, dict[str, object]]:
    """Translate a resolved configuration to persistent GUI defaults."""
    mode = input_mode(config)
    common = {
        **_method_defaults(config),
        "charge": int(config.get("CHARGE", 0)),
        "mult": int(config.get("MULT", 1)),
        "temp": float(config.get("THERMO_TEMPERATURE", 298.15)),
        "result_name": result_name(config),
    }

    if mode == "cat":
        values = {
            **common,
            **_copy_known_values(config, CONCAT_DEFAULT_KEY_MAP),
            "do_opt": bool(config.get("REFINE_INPUT_ON", False)),
            "do_vib": bool(config.get("VIB_ON", False)),
            "do_refine": bool(config.get("REFINE_ENERGY_ON", False)),
        }
        fixed_atoms = config.get("FIXED_ATOMS")
        if isinstance(fixed_atoms, list):
            values["fixed_atoms_text"] = ",".join(str(item) for item in fixed_atoms)
        return "concat", values

    steps = workflow_steps(config)
    values = {
        **common,
        **_copy_known_values(config, PATH_DEFAULT_KEY_MAP),
        "preset": infer_workflow_preset(config),
        **{f"workflow_step_{name}": enabled for name, enabled in steps.items()},
    }

    scan_type = str(config.get("SCAN_TYPE", values.get("scan_type", "bond")))
    values["scan_type"] = scan_type
    values["scan_type_previous"] = scan_type
    scan_indices = config.get("SCAN_INDICES")
    if isinstance(scan_indices, list):
        values["scan_indices_text"] = ",".join(str(item) for item in scan_indices)
    if "SCAN_START_VAL" in config or "SCAN_END_VAL" in config:
        start_value = config.get("SCAN_START_VAL")
        values["scan_range_mode"] = "auto_to_absolute" if start_value is None else "absolute"
        values["scan_start_auto"] = start_value is None
        if start_value is not None:
            values["scan_start_val"] = float(start_value)
        if config.get("SCAN_END_VAL") is not None:
            values["scan_end_val"] = float(config["SCAN_END_VAL"])
        values["scan_step_mode"] = "steps"
    fixed_atoms = config.get("FIXED_ATOMS")
    if isinstance(fixed_atoms, list):
        values["fixed_atoms_text"] = ",".join(str(item) for item in fixed_atoms)
    return "path_search", values
