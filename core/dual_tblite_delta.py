# SPDX-License-Identifier: LGPL-3.0-or-later
"""
Dual TBLite ASE calculator with optional solvation->gas restart transfer.

This calculator evaluates two tblite singlepoints per ASE call:
1) solvated (e.g., ALPB/GBSA/etc.)
2) gas-phase (no solvation)

It can return either:
- delta: (E_solv - E_gas, F_solv - F_gas)  [default]
- solv:  (E_solv, F_solv)
- gas:   (E_gas,  F_gas)

It also stores component results in the results dictionary:
- solv_energy, solv_forces
- gas_energy, gas_forces
- delta_energy, delta_forces

Notes
-----
- Uses tblite.interface.Calculator directly, so wavefunction/restart transfer is easy.
- By default, the gas-phase calculation is seeded from the solvated Result container
  using singlepoint(res_s, copy=True). This keeps the solvated Result intact.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np

try:
    import ase
    import ase.calculators.calculator as ase_calc
    from ase.atoms import Atoms
    from ase.units import Bohr, Hartree, kB
except ModuleNotFoundError as e:
    raise ModuleNotFoundError("This module requires ASE installed") from e

from tblite.interface import Calculator as TBLiteAPI


SolvationSpec = Optional[Tuple[Any, ...]]


class DualTBLite(ase_calc.Calculator):
    """
    ASE calculator performing solvated and gas-phase tblite calculations.

    Parameters (subset mirrors tblite.ase.TBLite)
    --------------------------------------------
    method: str
        "GFN2-xTB" (default), "GFN1-xTB", "IPEA1-xTB"
    charge: Optional[float]
        Total charge. If None, uses sum(atoms.get_initial_charges()).
    multiplicity: Optional[int]
        Total multiplicity. If None, uses sum(atoms.get_initial_magnetic_moments()) to infer uhf.
    accuracy: float
    guess: str
        "sad", "eeq", "eeqbc"
    max_iterations: int
    mixer_damping: float
    electronic_temperature: float
        Kelvin.
    electric_field: Optional[np.ndarray]
        Vector in V/Ang.
    spin_polarization: Optional[float]
        Scaling factor.
    solvation: Optional[tuple]
        Same convention as tblite.ase.TBLite:
          ("alpb", solvent_name, solution_state_optional)
          ("gbsa", solvent_name, solution_state_optional)
          ("cpcm", epsilon)
          ("gbe", epsilon, born_kernel)
          ("gb",  epsilon, born_kernel)
    mode: str
        "delta" (default), "solv", "gas"
    seed_gas_from: str
        "solv" (default) seeds gas from solvated Result each step.
        "gas"  seeds gas from previous gas Result (standard restart).
        "none" does not seed gas.
    cache_api: bool
        Keep underlying API objects between calls.
    verbosity: int
    """

    implemented_properties = [
        "energy",
        "forces",
        "delta_energy",
        "delta_forces",
        "solv_energy",
        "solv_forces",
        "gas_energy",
        "gas_forces",
        "dipole",
        "charges",
    ]

    default_parameters: Dict[str, Any] = {
        "method": "GFN2-xTB",
        "charge": None,
        "multiplicity": None,
        "accuracy": 1.0,
        "guess": "sad",
        "max_iterations": 250,
        "mixer_damping": 0.4,
        "electronic_temperature": 300.0,
        "electric_field": None,
        "spin_polarization": None,
        "solvation": ("alpb", "water"),
        "mode": "delta",
        "seed_gas_from": "solv",
        "cache_api": True,
        "verbosity": 1,
    }

    _xtb_solv: Optional[TBLiteAPI] = None
    _xtb_gas: Optional[TBLiteAPI] = None
    _res_solv: Any = None
    _res_gas: Any = None

    def __init__(self, atoms: Optional[Atoms] = None, **kwargs: Any):
        super().__init__(atoms=atoms, **kwargs)

    def set(self, **kwargs: Any) -> dict:
        _update_parameters(kwargs)
        changed = super().set(**kwargs)
        if changed:
            self.reset()

        critical = {"method", "electric_field", "spin_polarization", "solvation"}
        if critical.intersection(changed):
            self._xtb_solv = None
            self._xtb_gas = None
            self._res_solv = None
            self._res_gas = None

        # Minor updates can be pushed into existing API calculators.
        for api in (self._xtb_solv, self._xtb_gas):
            if api is None:
                continue
            if "accuracy" in changed:
                api.set("accuracy", self.parameters.accuracy)
            if "electronic_temperature" in changed:
                api.set("temperature", self.parameters.electronic_temperature * kB / Hartree)
            if "max_iterations" in changed:
                api.set("max-iter", self.parameters.max_iterations)
            if "guess" in changed:
                api.set("guess", {"sad": 0, "eeq": 1, "eeqbc": 2}[self.parameters.guess])
            if "mixer_damping" in changed:
                api.set("mixer-damping", self.parameters.mixer_damping)
            if "verbosity" in changed:
                api.set("verbosity", self.parameters.verbosity)

        if ("charge" in changed or "multiplicity" in changed) and self.atoms is not None:
            for api in (self._xtb_solv, self._xtb_gas):
                if api is not None:
                    api.update(
                        charge=_get_charge(self.atoms, self.parameters),
                        uhf=_get_uhf(self.atoms, self.parameters),
                    )

        return changed

    def reset(self) -> None:
        super().reset()
        if not self.parameters.cache_api:
            self._xtb_solv = None
            self._xtb_gas = None
            self._res_solv = None
            self._res_gas = None

    def _check_api_calculators(self, system_changes: List[str]) -> None:
        # Same policy as tblite.ase.TBLite: allow update for geometry/charge/magmoms
        reset_changes = list(system_changes)
        for ch in system_changes:
            if ch in ("positions", "cell", "initial_charges", "initial_magmoms"):
                if ch in reset_changes:
                    reset_changes.remove(ch)

        if reset_changes:
            self._xtb_solv = None
            self._xtb_gas = None
            self._res_solv = None
            self._res_gas = None
            return

        if system_changes and (self._xtb_solv is not None or self._xtb_gas is not None):
            try:
                cell = self.atoms.cell
                for api in (self._xtb_solv, self._xtb_gas):
                    if api is None:
                        continue
                    api.update(
                        positions=self.atoms.positions / Bohr,
                        lattice=cell / Bohr,
                        charge=_get_charge(self.atoms, self.parameters),
                        uhf=_get_uhf(self.atoms, self.parameters),
                    )
            except RuntimeError:
                self._xtb_solv = None
                self._xtb_gas = None
                self._res_solv = None
                self._res_gas = None

    def calculate(
        self,
        atoms: Optional[Atoms] = None,
        properties: Optional[List[str]] = None,
        system_changes: List[str] = ase_calc.all_changes,
    ) -> None:
        if not properties:
            properties = ["energy", "forces"]
        super().calculate(atoms, properties, system_changes)

        self._check_api_calculators(system_changes)

        if self._xtb_solv is None:
            self._xtb_solv = _create_api_calculator(self.atoms, self.parameters, solvation=self.parameters.solvation)
        if self._xtb_gas is None:
            self._xtb_gas = _create_api_calculator(self.atoms, self.parameters, solvation=None)

        # 1) Solvated calculation (updates self._res_solv in-place)
        try:
            self._res_solv = self._xtb_solv.singlepoint(self._res_solv)
        except RuntimeError as e:
            raise ase_calc.CalculationFailed(f"Solvated tblite failed: {e}") from e

        # 2) Gas calculation, with optional restart transfer
        seed_mode = str(self.parameters.seed_gas_from).lower()
        seed_res = None
        if seed_mode == "solv":
            seed_res = self._res_solv
        elif seed_mode == "gas":
            seed_res = self._res_gas
        elif seed_mode == "none":
            seed_res = None
        else:
            raise ase_calc.InputError("seed_gas_from must be one of: 'solv', 'gas', 'none'")

        try:
            # copy=True ensures we do not overwrite the seed container (especially solvated)
            self._res_gas = self._xtb_gas.singlepoint(seed_res, copy=True)
        except RuntimeError as e:
            raise ase_calc.CalculationFailed(f"Gas-phase tblite failed: {e}") from e

        # Convert units to ASE conventions
        e_s = float(self._res_solv["energy"]) * Hartree
        e_g = float(self._res_gas["energy"]) * Hartree
        f_s = -np.asarray(self._res_solv["gradient"], dtype=float) * Hartree / Bohr
        f_g = -np.asarray(self._res_gas["gradient"], dtype=float) * Hartree / Bohr

        de = e_s - e_g
        df = f_s - f_g

        # Provide component outputs for logging/debugging
        self.results["solv_energy"] = e_s
        self.results["solv_forces"] = f_s
        self.results["gas_energy"] = e_g
        self.results["gas_forces"] = f_g
        self.results["delta_energy"] = de
        self.results["delta_forces"] = df

        # Provide some common partitioned properties from solvated calculation (optional)
        try:
            self.results["charges"] = np.asarray(self._res_solv["charges"], dtype=float)
        except Exception:
            pass
        try:
            # res["dipole"] in tblite is in e*Bohr, ase expects Debye via tblite.ase uses Bohr directly
            # We keep the same convention as tblite.ase: dipole * Bohr gives e*Ang; ASE's get_dipole_moment
            # simply returns the stored value. Users should interpret consistently.
            self.results["dipole"] = np.asarray(self._res_solv["dipole"], dtype=float) * Bohr
        except Exception:
            pass

        # Select what this calculator returns as the primary energy/forces
        mode = str(self.parameters.mode).lower()
        if mode == "delta":
            self.results["energy"] = de
            self.results["free_energy"] = de
            self.results["forces"] = df
        elif mode == "solv":
            self.results["energy"] = e_s
            self.results["free_energy"] = e_s
            self.results["forces"] = f_s
        elif mode == "gas":
            self.results["energy"] = e_g
            self.results["free_energy"] = e_g
            self.results["forces"] = f_g
        else:
            raise ase_calc.InputError("mode must be one of: 'delta', 'solv', 'gas'")


def _create_api_calculator(
    atoms: Atoms,
    parameters: ase_calc.Parameters,
    solvation: SolvationSpec,
) -> TBLiteAPI:
    try:
        cell = atoms.cell
        periodic = atoms.pbc
        charge = _get_charge(atoms, parameters)
        uhf = _get_uhf(atoms, parameters)

        calc = TBLiteAPI(
            parameters.method,
            atoms.numbers,
            atoms.positions / Bohr,
            charge,
            uhf,
            cell / Bohr,
            periodic,
        )
        calc.set("accuracy", parameters.accuracy)
        calc.set("temperature", parameters.electronic_temperature * kB / Hartree)
        calc.set("max-iter", parameters.max_iterations)
        calc.set("guess", {"sad": 0, "eeq": 1, "eeqbc": 2}[parameters.guess])
        calc.set("mixer-damping", parameters.mixer_damping)
        calc.set("verbosity", parameters.verbosity)

        if parameters.electric_field is not None:
            calc.add("electric-field", np.asarray(parameters.electric_field, dtype=float) * Bohr / Hartree)
        if parameters.spin_polarization is not None:
            calc.add("spin-polarization", float(parameters.spin_polarization))

        if solvation is not None:
            solvation_model, *solvation_args = solvation
            if isinstance(solvation_args, (tuple, list)):
                calc.add(f"{solvation_model}-solvation", *solvation_args)
            else:
                calc.add(f"{solvation_model}-solvation", solvation_args)
    except RuntimeError as e:
        raise ase_calc.InputError(str(e)) from e

    return calc


def _get_charge(atoms: Atoms, parameters: ase_calc.Parameters) -> float:
    if parameters.charge is None:
        return float(atoms.get_initial_charges().sum())
    return float(parameters.charge)


def _get_uhf(atoms: Atoms, parameters: ase_calc.Parameters) -> int:
    if parameters.multiplicity is None:
        return int(round(float(atoms.get_initial_magnetic_moments().sum())))
    return int(parameters.multiplicity) - 1


def _update_parameters(parameters: Dict[str, Any]) -> None:
    # Backward compatible alias: initial_guess -> guess
    if "initial_guess" in parameters and "guess" not in parameters:
        parameters["guess"] = parameters.pop("initial_guess")
    if "guess" in parameters:
        guess = str(parameters["guess"]).lower()
        if guess not in ("sad", "eeq", "eeqbc"):
            raise ase_calc.InputError("guess must be one of: sad, eeq, eeqbc")
        parameters["guess"] = guess
