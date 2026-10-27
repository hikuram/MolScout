#!/usr/bin/env python3
"""GPU/CPU smoke check for MolScout's optional pinned PET-OMol evaluator.

Example (inside the MolScout image):
    python3 /opt/MolScout/scripts/check_pet_omol.py --models pet-omol-s pet-omol-l

This runs two fixed neutral-water single points at one electron state; it is an
API/runtime smoke check, NOT a reference-DFT accuracy or convergence benchmark.
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", nargs="+", default=["pet-omol-s"],
                        choices=["pet-omol-s", "pet-omol-l"])
    parser.add_argument("--precision", choices=["float32", "float64"], default="float32")
    parser.add_argument("--device", choices=["cuda", "cpu"], default="cuda")
    args = parser.parse_args()

    from ase import Atoms
    from cross_backend import _default_calculator_factory, _release_calculator

    print(f"UPET PET-OMol smoke check: {args.device}, {args.precision}")
    frame0 = Atoms("OH2", positions=[(0, 0, 0), (0.756, 0, 0.586), (-0.756, 0, 0.586)])
    frame1 = frame0.copy()
    frame1.positions[1, 0] += 0.05
    frames = (frame0, frame1)
    for model in args.models:
        calc = None
        try:
            start = perf_counter()
            calc = _default_calculator_factory(model, args.precision, args.device)
            values: list[float] = []
            for frame in frames:
                probe = frame.copy()
                probe.info.update(charge=0, spin=1, spin_multiplicity=1)
                probe.calc = calc
                try:
                    energy = float(probe.get_potential_energy())
                finally:
                    probe.calc = None
                if not math.isfinite(energy):
                    raise ValueError(f"Nonfinite PET energy for {model}: {energy}")
                values.append(energy)
            delta = (values[1] - values[0]) * 23.0605
            print(f"{model}: E0={values[0]:.8f} eV, "
                  f"E1={values[1]:.8f} eV, deltaE={delta:+.6f} kcal/mol, "
                  f"elapsed={perf_counter() - start:.2f} s")
        finally:
            calc = None
            _release_calculator(args.device)
    print("Smoke check complete. No numerical accuracy claim is implied.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
