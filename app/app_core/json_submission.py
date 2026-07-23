"""Helpers for submitting MolScout jobs from a flat JSON configuration."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class JsonSubmissionError(ValueError):
    """Raised when an uploaded JSON configuration cannot be submitted."""


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
            "Figure refresh configurations are not supported by the minimal JSON submit page."
        )
    return payload


def input_mode(config: dict[str, Any]) -> str:
    """Return the required input layout for a submitted configuration."""
    if bool(config.get("INIT_PATH_SEARCH_ON", True)):
        if str(config.get("INIT_PATH_METHOD", "DMF")).upper() == "CAT":
            return "cat"
        return "reactant_product"
    return "single_input"


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
