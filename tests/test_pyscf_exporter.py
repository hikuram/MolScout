"""Pure-Python regression tests for the v1.9.0 population export path.

These tests deliberately stub PySCF/GPU4PySCF: they verify orchestration and
JSON contracts without pretending to validate GPU numerical correctness.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import numpy as np
import pytest


@pytest.fixture
def exporter(monkeypatch):
    calls = []
    pyscf = ModuleType("pyscf")
    tools = ModuleType("pyscf.tools")
    molden = ModuleType("pyscf.tools.molden")

    def header(mol, f):
        f.write("[Molden Format]\n")

    def orbital_coeff(mol, f, mo_coeff, spin="Alpha", ene=None, occ=None):
        if spin == "Alpha":
            f.write("[MO]\n")
        f.write(f"Spin={spin}\n")

    molden.header = header
    molden.orbital_coeff = orbital_coeff
    tools.molden = molden
    pyscf.tools = tools
    utils = ModuleType("utils")
    utils.log = lambda *a, **kw: calls.append((a, kw))
    for name, mod in (("pyscf", pyscf), ("pyscf.tools", tools),
                      ("pyscf.tools.molden", molden), ("utils", utils)):
        monkeypatch.setitem(sys.modules, name, mod)

    source = Path(__file__).resolve().parents[1] / "core" / "pyscf_exporter.py"
    spec = importlib.util.spec_from_file_location("pyscf_exporter_test_instance", source)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeMol:
    natm = 2
    charge = 0
    spin = 0
    nelectron = 2
    basis = {"H": "sto-3g"}
    ecp = {}
    pseudo = None
    _ecpbas = ()
    symmetry = False

    def atom_symbol(self, i):
        return "H"

    def atom_coords(self, unit="ANG"):
        return np.array([[0, 0, 0], [0, 0, 0.74]])


class FakeCPU:
    def __init__(self, mol, events):
        self.mol = mol
        self.events = events
        self.mo_energy = np.array([-0.6, 0.2])
        self.mo_occ = np.array([2.0, 0.0])
        self.mo_coeff = np.eye(2)
        self.e_tot = -1.1
        self.converged = True
        self.xc = "PBE"
        self.scf_summary = {"e1": -1.2}

    def mulliken_meta(self, verbose=0):
        self.events.append("meta_lowdin_cpu")
        return np.array([0.94, 1.06]), np.array([0.12, -0.12])

    def dip_moment(self, verbose=0):
        self.events.append("dipole_cpu")
        return np.array([0.0, 0.0, 0.6])


class FakeGPU:
    def __init__(self, events=None, mol=None):
        self.events = [] if events is None else events
        self.mol = mol if mol is not None else FakeMol()
        self.mo_energy = np.array([-0.6, 0.2])
        self.mo_occ = np.array([2.0, 0.0])
        self.mo_coeff = np.eye(2)
        self.e_tot = -1.1
        self.converged = True
        self.xc = "PBE"
        self.scf_summary = {"e1": -1.2}

    def mulliken_pop(self, verbose=0):
        self.events.append("mulliken_source")
        return np.array([0.88, 1.12]), np.array([0.24, -0.24])

    def to_cpu(self):
        self.events.append("to_cpu")
        return FakeCPU(self.mol, self.events)

    def make_rdm1(self):
        self.events.append("make_rdm1")
        return np.eye(2)


def make_atoms(mf):
    return SimpleNamespace(calc=SimpleNamespace(method=mf))


def test_direct_mulliken_before_cpu_preserves_legacy_fields(exporter, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    gpu = FakeGPU()
    result = exporter.export_pyscf_single_point(make_atoms(gpu), prefix="regression")
    assert gpu.events == ["mulliken_source", "to_cpu", "meta_lowdin_cpu", "dipole_cpu"]
    assert result["mulliken_raw_charge"] == pytest.approx([0.24, -0.24])
    assert result["mulliken_charge"] == pytest.approx([0.12, -0.12])
    assert result["mulliken_scheme"].startswith("meta_lowdin")
    assert result["dip_moment"] == pytest.approx([0, 0, 0.6])
    assert result["mulliken_raw_charge_residual"] == pytest.approx(0)
    assert (tmp_path / "regression.molden").exists()
    saved = json.loads((tmp_path / "regression_pyscf.json").read_text())
    assert saved["mulliken_raw_charge"] == result["mulliken_raw_charge"]


def test_optional_ecp_partition_is_skipped(exporter, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    mol = FakeMol()
    mol._ecpbas = (1,)
    result = exporter.export_pyscf_single_point(
        make_atoms(FakeGPU(mol=mol)), population_analyses=["mulliken", "mbis", "hirshfeld"]
    )
    assert "ECP" in result["mbis_error"]
    assert "ECP" in result["hirshfeld_error"]
    assert "mulliken_raw_charge" in result


def test_optional_gpu_partition_multipoles(exporter, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    gpu4pyscf = ModuleType("gpu4pyscf")
    dft = ModuleType("gpu4pyscf.dft")
    grids_module = ModuleType("gpu4pyscf.dft.gen_grid")
    qmmm = ModuleType("gpu4pyscf.qmmm")
    mbis_module = ModuleType("gpu4pyscf.qmmm.mbis")
    hirshfeld_module = ModuleType("gpu4pyscf.qmmm.hirshfeld")
    captured = []

    class Grids:
        def __init__(self, mol):
            self.level = 0

    def multipoles():
        return (
            np.array([0.21, -0.21]),
            np.zeros((2, 3)),
            np.zeros((2, 3, 3)),
            np.zeros((2, 3, 3, 3)),
            np.ones(2),
            2 * np.ones(2),
            3 * np.ones(2),
        )

    def mbis(mol, grids, dm):
        captured.append(("mbis", grids.level))
        return multipoles()

    def hirshfeld(mol, grids, dm, xc="PBE"):
        captured.append(("hirshfeld", grids.level, xc))
        return multipoles()

    grids_module.Grids = Grids
    mbis_module.mbis = mbis
    hirshfeld_module.hirshfeld = hirshfeld
    for name, mod in (("gpu4pyscf", gpu4pyscf), ("gpu4pyscf.dft", dft),
                      ("gpu4pyscf.dft.gen_grid", grids_module),
                      ("gpu4pyscf.qmmm", qmmm),
                      ("gpu4pyscf.qmmm.mbis", mbis_module),
                      ("gpu4pyscf.qmmm.hirshfeld", hirshfeld_module)):
        monkeypatch.setitem(sys.modules, name, mod)

    gpu = FakeGPU()
    result = exporter.export_pyscf_single_point(
        make_atoms(gpu), prefix="pop", population_analyses=["mulliken", "mbis", "hirshfeld"],
        population_grid_level=4, hirshfeld_xc="PBE",
    )
    assert gpu.events[:4] == ["mulliken_source", "make_rdm1", "to_cpu", "meta_lowdin_cpu"]
    assert captured == [("mbis", 4), ("hirshfeld", 4, "PBE")]
    assert result["mbis_charge_sum"] == pytest.approx(0)
    assert result["hirshfeld_r4_moment"] == [3., 3.]
    assert result["hirshfeld_reference_xc"] == "PBE"
    assert result["population_analyses_requested"] == ["mulliken", "mbis", "hirshfeld"]


def test_reject_unknown_scheme_without_calculation(exporter, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    gpu = FakeGPU()
    with pytest.raises(ValueError, match="Unknown population"):
        exporter.export_pyscf_single_point(make_atoms(gpu), population_analyses=["npa"])
    assert not gpu.events


def test_missing_direct_mulliken_keeps_legacy_charge(exporter, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    gpu = FakeGPU()
    gpu.mulliken_pop = None
    result = exporter.export_pyscf_single_point(make_atoms(gpu), prefix="fallback")
    assert "mulliken_raw_error" in result
    assert result["mulliken_charge"] == pytest.approx([0.12, -0.12])
    assert (tmp_path / "fallback_pyscf.json").exists()


def test_ghost_atom_partition_is_skipped(exporter, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    mol = FakeMol()
    mol.atom_charges = lambda: np.array([1.0, 0.0])
    result = exporter.export_pyscf_single_point(
        make_atoms(FakeGPU(mol=mol)), population_analyses=["mbis", "hirshfeld"]
    )
    assert "ghost atoms" in result["mbis_error"]
    assert "ghost atoms" in result["hirshfeld_error"]


def test_negative_charge_mbis_warning(exporter, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    mol = FakeMol()
    mol.charge = -1
    result = exporter.export_pyscf_single_point(
        make_atoms(FakeGPU(mol=mol)), population_analyses=["mbis"]
    )
    assert "negative charge" in result["mbis_warning"]


def test_open_shell_uhf_molden_two_spin_channels(exporter, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    gpu = FakeGPU()

    class OpenShellCPU(FakeCPU):
        nelec = (1, 0)

        def __init__(self, mol, events):
            super().__init__(mol, events)
            self.mo_energy = np.array([[-0.7, 0.3], [-0.4, 0.5]])
            self.mo_occ = np.array([[1., 0.], [0., 0.]])
            self.mo_coeff = np.stack((np.eye(2), np.eye(2)))

    gpu.mo_energy = np.array([[-0.7, 0.3], [-0.4, 0.5]])
    gpu.mo_occ = np.array([[1., 0.], [0., 0.]])
    gpu.mo_coeff = np.stack((np.eye(2), np.eye(2)))
    gpu.to_cpu = lambda: OpenShellCPU(gpu.mol, gpu.events)
    result = exporter.export_pyscf_single_point(make_atoms(gpu), prefix="spin")
    contents = (tmp_path / "spin.molden").read_text()
    assert contents.count("[MO]") == 1
    assert "Spin=Alpha" in contents and "Spin=Beta" in contents
    assert result["na"] == 1 and result["nb"] == 0


def test_fast_export_omits_full_cpu_conversion_and_molden(exporter, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    gpu = FakeGPU()
    gpu.dip_moment = lambda verbose=0: np.array([0., 0., 0.6])
    result = exporter.export_pyscf_single_point(
        make_atoms(gpu), prefix="fast", fast_export=True,
    )
    assert gpu.events == ["mulliken_source"]
    assert result["population_export_mode"] == "fast"
    assert result["mulliken_raw_charge"] == pytest.approx([0.24, -0.24])
    assert "mulliken_charge" not in result
    assert result["dip_moment"] == pytest.approx([0., 0., 0.6])
    assert not (tmp_path / "fast.molden").exists()
    assert (tmp_path / "fast_pyscf.json").exists()


def test_reject_non_boolean_fast_flag(exporter):
    with pytest.raises(ValueError, match="fast_export must be a boolean"):
        exporter.export_pyscf_single_point(make_atoms(FakeGPU()), fast_export="yes")
