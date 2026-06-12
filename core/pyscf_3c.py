import os
from types import MethodType
from typing import Any, Dict, Optional

import numpy as np
from ase import Atoms, units
from ase.calculators.calculator import Calculator, all_changes
from pyscf import dft, gto, lib

# Partially adapted from AM3GroupHub/redox_benchmark
# Source: https://github.com/AM3GroupHub/redox_benchmark

def build_method(config: Dict[str, Any]):
    """Build a generic PySCF KS method from a config dictionary."""
    xc = config.get("xc", "B3LYP")
    basis = config.get("basis", "def2-SVP")
    ecp = config.get("ecp", None)
    nlc = config.get("nlc", "")
    disp = config.get("disp", None)
    verbose = config.get("verbose", 4)
    scf_conv_tol = config.get("scf_conv_tol", 1e-8)
    direct_scf_tol = config.get("direct_scf_tol", 1e-8)
    scf_max_cycle = config.get("scf_max_cycle", 50)
    with_df = config.get("with_df", True)
    auxbasis = config.get("auxbasis", "def2-universal-jkfit")
    with_gpu = config.get("with_gpu", True)

    grids = config.get("grids", {})
    nlcgrids = config.get("nlcgrids", {})

    with_solvent = config.get("with_solvent", False)
    solvent = config.get("solvent", {"method": "SMD", "solvent": "water"})

    max_memory = config.get("max_memory", None)
    if max_memory is not None:
        max_memory *= 1024

    threads = config.get("threads", os.environ.get("OMP_NUM_THREADS", os.cpu_count()))
    lib.num_threads(threads)

    atom = config.get("inputfile", "mol.xyz")
    charge = config.get("charge", 0)
    spin = config.get("spin", 0)
    output = config.get("output", "pyscf.log")

    mol = gto.M(
        atom=atom,
        basis=basis,
        ecp=ecp,
        max_memory=max_memory,
        verbose=verbose,
        charge=charge,
        spin=spin,
        output=output,
    )
    mol.build()

    mf = dft.KS(mol, xc=xc)
    mf.nlc = nlc
    mf.disp = disp

    if "atom_grid" in grids:
        mf.grids.atom_grid = grids["atom_grid"]
    if "level" in grids:
        mf.grids.level = grids["level"]

    nlc_enabled = False
    try:
        nlc_enabled = bool(mf._numint.libxc.is_nlc(mf.xc))
    except Exception:
        nlc_enabled = False
    if nlc_enabled or nlc not in (None, ""):
        if "atom_grid" in nlcgrids:
            mf.nlcgrids.atom_grid = nlcgrids["atom_grid"]
        if "level" in nlcgrids:
            mf.nlcgrids.level = nlcgrids["level"]

    if with_df:
        mf = mf.density_fit(auxbasis=auxbasis)

    if with_gpu:
        try:
            import cupy

            cupy.get_default_memory_pool().free_all_blocks()
            mf = mf.to_gpu()
        except ImportError:
            print("GPU support is not available. Proceeding with CPU.")

    if with_solvent:
        method = str(solvent.get("method", "SMD")).upper()
        if method == "SMD":
            mf = mf.SMD()
            if "solvent" in solvent:
                mf.with_solvent.solvent = solvent["solvent"]
            if solvent.get("eps") is not None:
                mf.with_solvent.eps = solvent["eps"]
        else:
            raise ValueError(f"Solvation method {solvent['method']} not recognized.")

    mf.direct_scf_tol = float(direct_scf_tol)
    mf.chkfile = None
    mf.conv_tol = float(scf_conv_tol)
    mf.max_cycle = scf_max_cycle
    return mf


def build_3c_method(config: Dict[str, Any]):
    """Build a PySCF method for 3c functionals such as r2scan-3c or b97-3c."""
    xc = str(config.get("xc", "B97-3c"))
    if not xc.lower().endswith("3c"):
        raise ValueError("The xc functional must be a 3c method, e.g., B97-3c.")

    from gpu4pyscf.drivers.dft_3c_driver import gen_disp_fun, parse_3c

    pyscf_xc, nlc, basis, ecp, (xc_disp, _disp), xc_gcp = parse_3c(xc.lower())

    cfg = dict(config)
    cfg["xc"] = pyscf_xc
    cfg["nlc"] = nlc
    cfg["basis"] = basis
    cfg["ecp"] = ecp

    mf = build_method(cfg)
    mf.get_dispersion = MethodType(gen_disp_fun(xc_disp, xc_gcp), mf)
    mf.do_disp = lambda: True
    return mf


def get_gradient_method(mf, xc_3c: Optional[str] = None):
    """Return a gradient method, including 3c dispersion corrections when needed."""
    if xc_3c is not None:
        if not str(xc_3c).lower().endswith("3c"):
            raise ValueError("The xc functional must be a 3c method, e.g., B97-3c.")
        from gpu4pyscf.drivers.dft_3c_driver import gen_disp_grad_fun, parse_3c

        _, _, _, _, (xc_disp, _disp), xc_gcp = parse_3c(str(xc_3c).lower())
        grad = mf.nuc_grad_method()
        grad.get_dispersion = MethodType(gen_disp_grad_fun(xc_disp, xc_gcp), grad)
        return grad

    return mf.nuc_grad_method()


def get_Hessian_method(mf, xc_3c: Optional[str] = None):
    """Return a Hessian method, including 3c dispersion corrections when needed."""
    if xc_3c is not None:
        if not str(xc_3c).lower().endswith("3c"):
            raise ValueError("The xc functional must be a 3c method, e.g., B97-3c.")
        from gpu4pyscf.drivers.dft_3c_driver import gen_disp_hess_fun, parse_3c

        _, _, _, _, (xc_disp, _disp), xc_gcp = parse_3c(str(xc_3c).lower())
        hess = mf.Hessian()
        hess.get_dispersion = MethodType(gen_disp_hess_fun(xc_disp, xc_gcp), hess)
        hess.auxbasis_response = 2
        return hess

    hess = mf.Hessian()
    hess.auxbasis_response = 2
    return hess


class PySCFCalculator(Calculator):
    """ASE calculator backed by a PySCF mean-field object."""

    implemented_properties = ["energy", "forces"]
    default_parameters: Dict[str, Any] = {}

    def __init__(self, method, xc_3c: Optional[str] = None, **kwargs):
        self.method = method
        self.xc_3c = xc_3c
        self.g_scanner = get_gradient_method(self.method, xc_3c=xc_3c).as_scanner()
        super().__init__(**kwargs)

    def set(self, **kwargs):
        changed_parameters = super().set(**kwargs)
        if changed_parameters:
            self.reset()
        return changed_parameters

    def calculate(self, atoms: Atoms = None, properties=None, system_changes=all_changes):
        if properties is None:
            properties = self.implemented_properties

        super().calculate(atoms, properties, system_changes)

        mol = self.method.mol
        positions = atoms.get_positions()
        atomic_numbers = atoms.get_atomic_numbers()
        ref_atomic_numbers = np.array([gto.charge(x) for x in mol.elements])

        if np.array_equal(ref_atomic_numbers, atomic_numbers):
            geom = positions
        else:
            geom = list(zip(atomic_numbers, positions))

        mol.set_geom_(geom, unit="Angstrom")
        energy, gradients = self.g_scanner(mol)

        self.results["energy"] = energy * units.Hartree
        self.results["forces"] = -gradients * (units.Hartree / units.Bohr)
