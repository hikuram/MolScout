"""Pure-CPU contract tests for optional PET-OMol Delta E comparisons.

Fake ASE frames and UPET calculators verify metadata propagation, alignment,
reference handling, and failure isolation.  No GPU numerical claim is made.
"""

from __future__ import annotations

import ast
import importlib.util
import math
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from app_core.cross_backend_settings import (  # noqa: E402
    CROSS_BACKEND_UI_DEFAULTS,
    cross_backend_config_overrides,
)


@pytest.fixture
def cross_module(monkeypatch):
    utils = ModuleType("utils")
    recorded = []
    utils.log = lambda *args: recorded.append(args)
    monkeypatch.setitem(sys.modules, "utils", utils)
    source = ROOT / "core" / "cross_backend.py"
    spec = importlib.util.spec_from_file_location("molscout_cross_backend_under_test", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module._test_messages = recorded
    return module


class AtomsStub:
    def __init__(self, index, numbers=(1, 8, 1), source_calc=None):
        self.index = index
        self.numbers = tuple(numbers)
        self.info = {"charge": 777, "spin": 222, "spin_multiplicity": 444}
        self.calc = source_calc

    def get_atomic_numbers(self):
        return self.numbers

    def copy(self):
        copy = AtomsStub(self.index, self.numbers)
        copy.info = dict(self.info)
        # Mirrors ASE.Atoms.copy(): no calculator is transferred.
        return copy

    def get_potential_energy(self):
        if self.calc is None:
            raise AssertionError("No calculator installed")
        return self.calc.energy(self)


class FakePET:
    def __init__(self, model, events, fail_index=None):
        self.model = model
        self.events = events
        self.fail_index = fail_index

    def energy(self, atoms):
        self.events.append(("energy", self.model, atoms.index, dict(atoms.info)))
        if atoms.index == self.fail_index:
            raise RuntimeError("Simulated CUDA failure")
        return -102.0 + atoms.index * (0.30 if self.model.endswith("-s") else 0.35)


def config(**overrides):
    values = {
        "CALC_TYPE": "orbmol",
        "ORBMOL_VERSION": "v2",
        "CROSS_BACKEND_MODELS": ["pet-omol-s", "pet-omol-l"],
        "CROSS_BACKEND_PRECISION": "float32",
        "CROSS_BACKEND_SOURCE": "auto",
        "INIT_PATH_METHOD": "SCAN",
        "SCAN_MF_ON": False,
        "PICK_OPTPOINTS_ON": False,
        "REFINE_INPUT_ON": False,
        "PRESERVE_CSV_ON": False,
        "R_CSV": "result.csv",
        "I_TRAJ": "init_path.traj",
        "CHARGE": -1,
        "MULT": 2,
        "DEVICE": "cpu",
        "EV_TO_KCAL_MOL": 23.0605,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def data(tmp_path, *, path="result.csv", n=3, source_indexes=None):
    csv_path = tmp_path / path
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    primary_energies = [-10.0 + i * 0.1 for i in range(n)]
    primary_delta = [(e - primary_energies[0]) * 23.0605 for e in primary_energies]
    pd.DataFrame({
        "# image": list(range(n)) if source_indexes is None else source_indexes,
        "energy [eV]": primary_energies,
        "Delta E vs. reactant [kcal/mol]": primary_delta,
    }).to_csv(csv_path, index=False)
    return csv_path


def source_files(tmp_path, *, optpoints=False):
    (tmp_path / "init_path.traj").touch()
    data(tmp_path)
    if optpoints:
        (tmp_path / "optpoints").mkdir()
        (tmp_path / "optpoints/optpoints.traj").touch()
        data(tmp_path, path="optpoints/result_optpoints.csv")


def make_factory(events, *, failure=None, load_error=None):
    def factory(model, precision, device):
        events.append(("load", model, precision, device))
        if model == load_error:
            raise FileNotFoundError("checkpoint unavailable")
        return FakePET(model, events, fail_index=0 if model == failure else None)
    return factory


def run(module, tmp_path, cfg, events, *, frames=None, **factory_kwargs):
    if frames is None:
        frames = [AtomsStub(i, source_calc=object()) for i in range(3)]
    return module.compare_primary_with_pet(
        cfg,
        calculator_factory=make_factory(events, **factory_kwargs),
        snapshot_reader=lambda path: frames,
    )


def test_no_models_returns_without_loading_source(cross_module, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert cross_module.compare_primary_with_pet(config(CROSS_BACKEND_MODELS=[])) is None
    assert not (tmp_path / "cross_backend.csv").exists()


def test_full_comparison_is_model_major_preserves_primary_and_inputs(cross_module, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    source_files(tmp_path)
    original = (tmp_path / "result.csv").read_bytes()
    events = []
    frames = [AtomsStub(i, source_calc=object()) for i in range(3)]
    output = run(cross_module, tmp_path, config(), events, frames=frames)
    assert output.name == "cross_backend.csv"
    assert (tmp_path / "result.csv").read_bytes() == original
    df = pd.read_csv(output)
    assert list(df["model"]) == ["pet-omol-s"] * 3 + ["pet-omol-l"] * 3
    assert list(df["# image"]) == [0, 1, 2] * 2
    assert df["status"].eq("ok").all()
    for model, increment in [("pet-omol-s", .30), ("pet-omol-l", .35)]:
        part = df[df["model"] == model]
        assert part["deltaE_kcalmol"].tolist() == pytest.approx(
            [0., increment * 23.0605, 2 * increment * 23.0605]
        )
        assert part["deltaE_minus_primary_kcalmol"].notna().all()
    assert [event[:2] for event in events if event[0] == "load"] == [
        ("load", "pet-omol-s"), ("load", "pet-omol-l")
    ]
    assert [e[2] for e in events if e[0] == "energy"] == [0, 1, 2, 0, 1, 2]
    for event in events:
        if event[0] == "energy":
            assert event[3]["charge"] == -1
            assert event[3]["spin"] == 2
            assert event[3]["spin_multiplicity"] == 2
    assert all(frame.calc is not None for frame in frames)
    assert all(frame.info["charge"] == 777 for frame in frames)


def test_explicit_fp64_and_auto_prefers_optpoints(cross_module, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    source_files(tmp_path, optpoints=True)
    pd.DataFrame({
        "# image": [0, 1, 2],
        "previous_#image": [1, 7, 12],
        "energy [eV]": [-12.0, -11.5, -11.0],
        "Delta E vs. reactant [kcal/mol]": [0, 0.5 * 23.0605, 23.0605],
    }).to_csv(tmp_path / "optpoints/result_optpoints.csv", index=False)
    events = []
    cfg = config(PICK_OPTPOINTS_ON=True, CROSS_BACKEND_PRECISION="float64", CROSS_BACKEND_MODELS=["pet-omol-l"])
    output = run(cross_module, tmp_path, cfg, events)
    df = pd.read_csv(output)
    assert df["source_traj"].eq("optpoints/optpoints.traj").all()
    assert df["series"].eq("optpoints").all()
    assert df["previous_#image"].tolist() == [1, 7, 12]
    assert events[0] == ("load", "pet-omol-l", "float64", "cpu")


def test_initial_path_explicit_with_optpoints_uses_original_csv(cross_module, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    source_files(tmp_path, optpoints=True)
    cfg = config(PICK_OPTPOINTS_ON=True, ORIG_R_CSV="result.csv", R_CSV="optpoints/result_optpoints.csv", CROSS_BACKEND_SOURCE="initial_path")
    events = []
    result = run(cross_module, tmp_path, cfg, events)
    df = pd.read_csv(result)
    assert df["source_csv"].eq("result.csv").all()
    assert df["series"].eq("initial_path").all()


def test_alpb_primary_is_explicitly_distinguished(cross_module, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    source_files(tmp_path)
    events = []
    output = run(cross_module, tmp_path, config(CALC_TYPE="orbmol+alpb"), events)
    df = pd.read_csv(output)
    assert df["primary_energy_basis"].eq("OrbMol + ALPB effective").all()
    assert df["pet_energy_basis"].str.contains("vacuum").all()
    assert df["deltaE_minus_primary_kcalmol"].isna().all()


def test_failed_reference_does_not_rebase(cross_module, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    source_files(tmp_path)
    output = run(cross_module, tmp_path, config(), [], failure="pet-omol-s")
    df = pd.read_csv(output)
    s = df[df.model == "pet-omol-s"]
    assert s["deltaE_kcalmol"].isna().all()
    assert s["status"].eq("error").all()
    assert s["energy_raw_eV"].iloc[1:].notna().all()
    assert df[df.model == "pet-omol-l"]["deltaE_kcalmol"].notna().all()


def test_failed_checkpoint_keeps_primary_and_writes_status(cross_module, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    source_files(tmp_path)
    output = run(cross_module, tmp_path, config(), [], load_error="pet-omol-l")
    df = pd.read_csv(output)
    l = df[df.model == "pet-omol-l"]
    assert l["status"].eq("error").all()
    assert l["error"].str.contains("checkpoint unavailable").all()
    assert df[df.model == "pet-omol-s"]["status"].eq("ok").all()


@pytest.mark.parametrize("overrides", [
    {"CALC_TYPE": "pyscf"},
    {"SCAN_MF_ON": True},
    {"INIT_PATH_METHOD": "CAT", "REFINE_INPUT_ON": True},
    {"PRESERVE_CSV_ON": True},
])
def test_ineligible_primary_is_rejected(cross_module, tmp_path, monkeypatch, overrides):
    monkeypatch.chdir(tmp_path)
    source_files(tmp_path)
    with pytest.raises(cross_module.CrossBackendConfigError):
        run(cross_module, tmp_path, config(**overrides), [])


def test_bad_csv_frame_order_is_rejected(cross_module, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    source_files(tmp_path)
    data(tmp_path, source_indexes=[0, 2, 1])
    with pytest.raises(cross_module.CrossBackendConfigError, match="frame IDs"):
        run(cross_module, tmp_path, config(), [])


def test_incompatible_atom_compositions_are_rejected(cross_module, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    source_files(tmp_path)
    frames = [AtomsStub(0), AtomsStub(1, numbers=(1, 1, 1)), AtomsStub(2)]
    with pytest.raises(cross_module.CrossBackendConfigError, match="identities and order"):
        run(cross_module, tmp_path, config(), [], frames=frames)


def test_source_optpoints_must_exist(cross_module, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    source_files(tmp_path)
    with pytest.raises(cross_module.CrossBackendConfigError, match="not available"):
        run(cross_module, tmp_path, config(CROSS_BACKEND_SOURCE="optpoints"), [])


@pytest.mark.parametrize("models,precision,source", [
    (["pet-mad-s"], "float32", "auto"),
    (["pet-omol-s", "pet-omol-s"], "float32", "auto"),
    (["pet-omol-s"], "fp32", "auto"),
    (["pet-omol-l"], "float64", "figure"),
    ("pet-omol-s", "float32", "auto"),
])
def test_invalid_core_settings_are_rejected(cross_module, models, precision, source):
    with pytest.raises(cross_module.CrossBackendConfigError):
        cross_module.validate_settings(models, precision, source)


def test_ui_defaults_are_off_and_forwarded_as_config():
    assert cross_backend_config_overrides(CROSS_BACKEND_UI_DEFAULTS) == {
        "CROSS_BACKEND_MODELS": [],
        "CROSS_BACKEND_PRECISION": "float32",
        "CROSS_BACKEND_SOURCE": "auto",
    }
    selected = {
        **CROSS_BACKEND_UI_DEFAULTS,
        "cross_pet_omol_s": True,
        "cross_pet_omol_l": True,
        "cross_precision": "float64",
        "cross_source": "initial_path",
    }
    assert cross_backend_config_overrides(selected) == {
        "CROSS_BACKEND_MODELS": ["pet-omol-s", "pet-omol-l"],
        "CROSS_BACKEND_PRECISION": "float64",
        "CROSS_BACKEND_SOURCE": "initial_path",
    }


def test_gui_and_core_are_wired_for_both_submit_flows():
    tree = ast.parse((ROOT / "app/app_ui/views.py").read_text())
    functions = {item.name: item for item in tree.body if isinstance(item, ast.FunctionDef)}
    for name in ("render_job_submission", "render_concat_submission"):
        called = {
            node.func.id for node in ast.walk(functions[name])
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assert "render_cross_backend_settings" in called
        assert "cross_backend_config_overrides" in called
        assert "save_submit_defaults" in called
        assert "selected_cross_backend_models" in called
        assignments = [node.value for node in ast.walk(functions[name])
                       if isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict)
                       and any(isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name)
                       and target.value.id == 'job' for target in node.targets)]
        assert any(any(k is None and isinstance(v, ast.Name) and v.id == "cross_backend_overrides"
                       for k, v in zip(d.keys, d.values)) for d in assignments)


def test_primary_finalize_runs_before_secondary_option():
    text = (ROOT / "core/molscout.py").read_text()
    marker = text.index("    # Finish the Primary workflow")
    trailing = text[marker:]
    assert trailing.index("finalize_run()") < trailing.index("compare_primary_with_pet(g)")


def test_docker_prefetches_both_pinned_offline_checkpoints():
    docker = (ROOT / "Dockerfile").read_text()
    assert 'PREFETCH_UPET_CHECKPOINTS=1' in docker
    assert "('s', 'l')" in docker
    assert "v1.0.0.ckpt" in docker
    assert "MOLSCOUT_UPET_CHECKPOINT_DIR" in docker
    assert "HF_HUB_OFFLINE=1" in docker


def test_offline_pinned_calculator_factory_passes_dtype_and_checkpoint(
    cross_module, tmp_path, monkeypatch,
):
    checkpoint_dir = tmp_path / "checkpoints"
    checkpoint_dir.mkdir()
    (checkpoint_dir / "pet-omol-l-v1.0.0.ckpt").touch()
    monkeypatch.setenv("MOLSCOUT_UPET_CHECKPOINT_DIR", str(checkpoint_dir))

    calls = []
    mock_torch = ModuleType("torch")
    mock_torch.float32 = object()
    mock_torch.float64 = object()
    mock_torch.set_float32_matmul_precision = lambda mode: calls.append(("matmul", mode))
    mock_upet = ModuleType("upet")
    mock_upet_ase = ModuleType("upet.ase")

    class StubCalculator:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    mock_upet_ase.UPETCalculator = StubCalculator
    mock_upet.ase = mock_upet_ase
    monkeypatch.setitem(sys.modules, "torch", mock_torch)
    monkeypatch.setitem(sys.modules, "upet", mock_upet)
    monkeypatch.setitem(sys.modules, "upet.ase", mock_upet_ase)

    calc = cross_module._default_calculator_factory("pet-omol-l", "float64", "cuda")
    assert calc.kwargs == {
        "checkpoint_path": str(checkpoint_dir / "pet-omol-l-v1.0.0.ckpt"),
        "dtype": mock_torch.float64,
        "device": "cuda",
        "non_conservative": False,
        "check_consistency": False,
    }
    assert calls == [("matmul", "highest")]
    with pytest.raises(FileNotFoundError):
        cross_module._default_calculator_factory("pet-omol-s", "float32", "cuda")
