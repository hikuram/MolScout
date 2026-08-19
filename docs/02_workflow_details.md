# Workflow Details: MolScout

[日本語](ja/02_workflow_details.md)

MolScout controls reaction-path generation and follow-up molecular analysis through explicit workflow flags. The application wrapper sets those flags for each job, and `core/molscout.py` runs the selected stages.

## 1. Initial path search

The initial-path stage is mainly controlled by `INIT_PATH_SEARCH_ON` and `INIT_PATH_METHOD`.

- `DMF` generates an initial path from reactant/product structures through FB-ENM interpolation and DirectMaxFlux optimization.
- `NEB` uses ASE NEB and writes the final path and optimization history.
- `SCAN` performs constrained optimization along a specified bond, angle, or dihedral coordinate.
- `CAT` concatenates user-provided trajectories or coordinate files for batch-oriented processing.

The generated path is written as `init_path.traj`, converted to coordinate files where needed, and summarized in the result CSV.

## 2. Peak extraction

After a path is available, MolScout evaluates the energy profile and extracts local maxima as transition-state candidates. Endpoints and selected frames are also retained for optional optimization, vibrational analysis, and plotting.

## 3. TS optimization and IRC

When enabled, transition-state optimization is performed with Sella-based routines. The workflow includes safeguards for coordinate-system issues in highly symmetric or nearly linear structures and can fall back to Cartesian coordinates when internal-coordinate setup is unreliable.

IRC calculations use the `AdaptiveIRC` extension. Step sizes are adjusted dynamically, and rollback is used when convergence failures indicate that the path has become unstable.

## 4. Vibrational analysis and thermochemistry

Vibrational analysis calculates thermal corrections and free-energy terms for selected structures. Low-frequency artifacts are handled by applying a floor correction, and small imaginary modes can be treated as numerical noise rather than chemically meaningful transition-state modes.

The thermochemistry temperature and related options can be overridden per app job without editing `core/default_config.py`.

## 5. Energy refinement and export

Optional refinement stages can evaluate selected structures with higher-level settings. PySCF-based runs can export molecular orbital information, Mulliken charges, dipole moments, JSON summaries, and Molden files when the selected backend supports them.

## 6. Finalization

At the end of a run, MolScout updates the result CSV, writes log and timing information, records suggested follow-up inspections, and generates figures when enabled. The app validates the expected output files before marking a queued job as complete.
