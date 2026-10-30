"""Shared optional PET-OMol Delta E controls for Submit and Concatenation."""

from __future__ import annotations

from collections.abc import Mapping

CROSS_BACKEND_UI_DEFAULTS: dict[str, object] = {
    "cross_model": "none",
    "cross_precision": "float32",
    "cross_source": "auto",
}


def selected_models(values: Mapping[str, object]) -> list[str]:
    model = values.get("cross_model", "none")
    if model not in ("none", "pet-omol-s", "pet-omol-l"):
        raise ValueError("PET-OMol evaluation model must be None, PET-OMol-S, or PET-OMol-L.")
    return [] if model == "none" else [model]


def cross_backend_config_overrides(values: Mapping[str, object]) -> dict[str, object]:
    precision = values.get("cross_precision", "float32")
    source = values.get("cross_source", "auto")
    if precision not in ("float32", "float64"):
        raise ValueError("PET-OMol inference precision must be FP32 or FP64.")
    if source not in ("auto", "optpoints", "initial_path", "both"):
        raise ValueError("PET-OMol comparison source must be auto, optpoints, initial_path, or both.")
    return {
        "CROSS_BACKEND_MODELS": selected_models(values),
        "CROSS_BACKEND_PRECISION": precision,
        "CROSS_BACKEND_SOURCE": source,
    }
