# Calculator Backends: MolScout

[日本語](ja/03_calculators.md)

MolScout uses ASE-compatible calculators to evaluate energies, forces, and related molecular properties. Backend selection is controlled by `CALC_TYPE` and supporting settings in `core/default_config.py` or app-provided overrides.

## 1. OrbMol and MLIP backends

`orbmol` uses Orbital Materials models through `orb_models` for fast potential-energy-surface exploration. This backend is suitable for initial path search, preliminary optimization, and workflows where throughput is important.

`orbmol+alpb` combines the MLIP gas-phase energy with an xTB solvation delta correction. The delta term is evaluated through tblite-based calculations, and the implementation reuses intermediate results where possible to reduce repeated SCF work.

## 2. xTB and ALPB handling

The tblite path supports solvent-correction workflows and method switching for more robust early-stage path generation. In hybrid mode, the workflow can use a more stable xTB setting during initial path generation and then restore the production setting for downstream evaluations.

## 3. PySCF and gpu4pyscf

PySCF backends are used for electronic-structure calculations and final refinement. When CUDA support and gpu4pyscf are available, eligible calculations can be offloaded to the GPU.

The `core/pyscf_3c.py` helper provides support for composite functionals such as `r2scan-3c` and `b97-3c`, including dispersion-related gradients and Hessians required by the ASE interface.

## 4. PySCF export

`core/pyscf_exporter.py` extracts selected electronic-structure information after supported PySCF jobs. Exports can include orbital energies, HOMO/LUMO values, Mulliken charges, dipole moments, JSON summaries, and Molden files for downstream inspection.

## 5. Configuration notes

Backend behavior depends on installed packages, hardware, and numerical settings. Before production use, confirm the selected backend, charge, multiplicity, solvent setting, and CUDA availability in the job log and `molscout.log`.
