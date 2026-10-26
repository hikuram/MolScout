"""Regression checks for Submit/Concatenation population settings."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "app"))
from app_core.population_settings import (  # noqa: E402
    POPULATION_UI_DEFAULTS,
    population_config_overrides,
    selected_population_methods,
)


def test_defaults_match_core_and_preserve_legacy_export():
    assert population_config_overrides(POPULATION_UI_DEFAULTS) == {
        "POPULATION_ANALYSES": ["mulliken"],
        "POPULATION_FAST_EXPORT": False,
        "POPULATION_GRID_LEVEL": 5,
        "POPULATION_HIRSHFELD_XC": "PBE",
    }


def test_fast_output_can_include_multiple_partitioning_methods():
    settings = {
        **POPULATION_UI_DEFAULTS,
        "population_mbis": True,
        "population_hirshfeld": True,
        "population_export_mode": "fast",
        "population_grid_level": 3,
        "population_hirshfeld_xc": " PBE ",
    }
    assert population_config_overrides(settings) == {
        "POPULATION_ANALYSES": ["mulliken", "mbis", "hirshfeld"],
        "POPULATION_FAST_EXPORT": True,
        "POPULATION_GRID_LEVEL": 3,
        "POPULATION_HIRSHFELD_XC": "PBE",
    }


def test_direct_mulliken_can_be_disabled_without_losing_other_methods():
    settings = {**POPULATION_UI_DEFAULTS, "population_mulliken": False, "population_mbis": True}
    assert selected_population_methods(settings) == ["mbis"]
    assert population_config_overrides(settings)["POPULATION_ANALYSES"] == ["mbis"]


def test_no_new_analyses_is_permitted_for_legacy_compatible_mode():
    settings = {**POPULATION_UI_DEFAULTS, "population_mulliken": False}
    assert population_config_overrides(settings)["POPULATION_ANALYSES"] == []
    assert population_config_overrides(settings)["POPULATION_FAST_EXPORT"] is False


@pytest.mark.parametrize("field,value", [
    ("population_export_mode", "old"),
    ("population_grid_level", -1),
    ("population_grid_level", 10),
    ("population_grid_level", 3.5),
    ("population_grid_level", True),
    ("population_hirshfeld_xc", " "),
])
def test_invalid_settings_are_rejected_before_job_submission(field, value):
    with pytest.raises(ValueError):
        population_config_overrides({**POPULATION_UI_DEFAULTS, field: value})


def test_both_submit_forms_have_population_controls_and_config_wiring():
    source = (PROJECT_ROOT / "app" / "app_ui" / "views.py").read_text(encoding="utf-8")
    nodes = ast.parse(source)
    functions = {node.name: node for node in nodes.body if isinstance(node, ast.FunctionDef)}
    for name in ("render_job_submission", "render_concat_submission"):
        function = functions[name]
        called = {
            node.func.id for node in ast.walk(function)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assert "render_population_output_settings" in called
        assert "population_config_overrides" in called
        assert "save_submit_defaults" in called
    assert "POPULATION_UI_DEFAULTS" in source
    assert '5. Output Settings' in source


def test_streamlit_population_form_reads_existing_state_without_switching_defaults():
    """Exercise the actual shared form helper with a lightweight Streamlit stub."""
    source = (PROJECT_ROOT / "app" / "app_ui" / "views.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    target = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "render_population_output_settings")

    class FakeStreamlit:
        def __init__(self):
            self.session_state = {
                "job_population_mulliken": True,
                "job_population_mbis": True,
                "job_population_hirshfeld": False,
                "job_population_export_mode": "fast",
                "job_population_grid_level": 3,
                "job_population_hirshfeld_xc": "PBE0",
            }

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def expander(self, *args, **kwargs):
            return self

        def columns(self, spec):
            return [self] * (spec if isinstance(spec, int) else len(spec))

        def caption(self, *_):
            pass

        def markdown(self, *_):
            pass

        def checkbox(self, label, *, key, **kwargs):
            return self.session_state.get(key, kwargs.get("value", False))

        def radio(self, label, options, *, key, **kwargs):
            return self.session_state.get(key, options[kwargs.get("index", 0)])

        def number_input(self, label, *, key, **kwargs):
            return self.session_state.get(key, kwargs.get("value", 0))

        def text_input(self, label, *, key, **kwargs):
            return self.session_state.get(key, kwargs.get("value", ""))

    fake_st = FakeStreamlit()
    namespace = {
        "st": fake_st,
        "t": lambda value: value,
        "POPULATION_UI_DEFAULTS": POPULATION_UI_DEFAULTS,
        "widget_default_kwargs": lambda key, **defaults: {} if key in fake_st.session_state else defaults,
    }
    module = ast.fix_missing_locations(ast.Module(body=[target], type_ignores=[]))
    exec(compile(module, "views.py", "exec"), namespace)
    values = namespace["render_population_output_settings"]("job")
    assert population_config_overrides(values) == {
        "POPULATION_ANALYSES": ["mulliken", "mbis"],
        "POPULATION_FAST_EXPORT": True,
        "POPULATION_GRID_LEVEL": 3,
        "POPULATION_HIRSHFELD_XC": "PBE0",
    }


def test_both_jobs_unpack_population_overrides_into_job_config():
    source = (PROJECT_ROOT / "app" / "app_ui" / "views.py").read_text(encoding="utf-8")
    functions = {n.name: n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef)}
    for name in ("render_job_submission", "render_concat_submission"):
        function = functions[name]
        override_dicts = [
            node.value
            for node in ast.walk(function)
            if isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Dict)
            and any(isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name)
                    and target.value.id == "job" and isinstance(target.slice, ast.Constant)
                    and target.slice.value == "config_overrides" for target in node.targets)
        ]
        assert len(override_dicts) == 1
        assert any(key is None and isinstance(value, ast.Name) and value.id == "population_overrides"
                   for key, value in zip(override_dicts[0].keys, override_dicts[0].values))
