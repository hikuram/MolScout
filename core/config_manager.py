"""Shared helpers for loading, applying, and saving MolScout settings."""

from __future__ import annotations

import json
from pathlib import Path
from types import ModuleType
from typing import Any, Mapping


class ConfigError(ValueError):
    """Raised when a MolScout configuration file is invalid."""


def config_to_dict(config_module: ModuleType) -> dict[str, Any]:
    """Return the public uppercase settings currently stored in a module."""
    exporter = getattr(config_module, "as_dict", None)
    if callable(exporter):
        return dict(exporter())
    return {
        key: value
        for key, value in vars(config_module).items()
        if key.isupper() and not key.startswith("_")
    }


def load_config(path: str | Path) -> dict[str, Any]:
    """Load a flat JSON configuration mapping from disk."""
    config_path = Path(path)
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(f"Could not read configuration file: {config_path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Invalid JSON configuration file: {config_path}: {exc}") from exc

    if not isinstance(payload, dict):
        raise ConfigError("The configuration root must be a JSON object.")

    invalid_keys = [key for key in payload if not isinstance(key, str) or not key.isupper()]
    if invalid_keys:
        raise ConfigError(
            "Configuration keys must be uppercase strings: "
            + ", ".join(repr(key) for key in invalid_keys)
        )
    return payload


def apply_config(
    config_module: ModuleType,
    overrides: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Apply one override layer and return the resulting configuration."""
    if overrides:
        for key, value in overrides.items():
            if not isinstance(key, str) or not key.isupper():
                raise ConfigError(f"Invalid configuration key: {key!r}")
            setattr(config_module, key, value)
    return config_to_dict(config_module)


def apply_config_file(
    config_module: ModuleType,
    path: str | Path | None,
) -> dict[str, Any]:
    """Apply a JSON configuration file when a path is provided."""
    if path is None:
        return config_to_dict(config_module)
    return apply_config(config_module, load_config(path))


def save_config(config: Mapping[str, Any], path: str | Path) -> Path:
    """Save a resolved configuration as formatted JSON."""
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(dict(config), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return output_path
