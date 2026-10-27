"""Shared optional PET-OMol Delta E controls for Submit and Concatenation."""

from __future__ import annotations

from collections.abc import Mapping

CROSS_BACKEND_UI_DEFAULTS: dict[str, object] = {
    "cross_pet_omol_s": False,
    "cross_pet_omol_l": False,
    "cross_precision": "float32",
    "cross_source": "auto",
}


def selected_models(values: Mapping[str, object]) -> list[str]:
    result: list[str] = []
    if values.get("cross_pet_omol_s", False) is True:
        result.append("pet-omol-s")
    if values.get("cross_pet_omol_l", False) is True:
        result.append("pet-omol-l")
    return result


def cross_backend_config_overrides(values: Mapping[str, object]) -> dict[str, object]:
    precision = values.get("cross_precision", "float32")
    source = values.get("cross_source", "auto")
    if precision not in ("float32", "float64"):
        raise ValueError("PET-OMol inference precision must be FP32 or FP64.")
    if source not in ("auto", "optpoints", "initial_path"):
        raise ValueError("PET-OMol comparison source must be auto, optpoints, or initial_path.")
    for field in ("cross_pet_omol_s", "cross_pet_omol_l"):
        if type(values.get(field, False)) is not bool:
            raise ValueError(f"{field} must be a boolean.")
    return {
        "CROSS_BACKEND_MODELS": selected_models(values),
        "CROSS_BACKEND_PRECISION": precision,
        "CROSS_BACKEND_SOURCE": source,
    }
