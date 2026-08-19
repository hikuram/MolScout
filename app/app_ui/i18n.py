"""Fixed-language UI support for MolScout."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import streamlit as st

LANGUAGE_SESSION_KEY = "_molscout_ui_language"
SUPPORTED_LANGUAGES = {"en", "ja"}
_LOCALE_DIR = Path(__file__).resolve().parent / "locales"


@lru_cache(maxsize=1)
def _load_ja_translations() -> dict[str, str]:
    """Load Japanese UI strings only when the Japanese launcher needs them."""
    return json.loads((_LOCALE_DIR / "ja.json").read_text(encoding="utf-8"))


def set_language(language: str) -> str:
    """Set the fixed UI language for the current Streamlit session."""
    normalized = str(language).strip().lower()
    if normalized not in SUPPORTED_LANGUAGES:
        normalized = "en"
    st.session_state[LANGUAGE_SESSION_KEY] = normalized
    return normalized


def get_language() -> str:
    """Return the current UI language, defaulting to English."""
    language = str(st.session_state.get(LANGUAGE_SESSION_KEY, "en")).lower()
    return language if language in SUPPORTED_LANGUAGES else "en"


def t(english: str) -> str:
    """Translate an English UI string for the fixed session language."""
    if get_language() == "ja":
        return str(_load_ja_translations().get(english, english))
    return english


def tf(english_template: str, **values: object) -> str:
    """Translate an English format template and substitute named values."""
    return t(english_template).format(**values)
