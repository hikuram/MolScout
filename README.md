# MolScout

MolScout is a Python toolkit for automated reaction-path exploration and follow-up molecular calculations. It coordinates initial path generation, transition-state optimization, intrinsic reaction coordinate calculations, vibrational analysis, thermochemical evaluation, and optional high-level energy refinement as a single workflow.

This repository is centered on a shared Streamlit application and the scientific workflow code under `core/`. Earlier standalone workflow scripts have been consolidated; workflow-stage selection is now controlled by the application wrapper and `core/default_config.py`.

## Main capabilities

- Initial path generation by DMF, NEB, SCAN, or concatenated input trajectories.
- Transition-state optimization and adaptive IRC calculations using ASE/Sella-based routines.
- Vibrational analysis and thermochemistry with safeguards for low-frequency and numerical artifacts.
- Calculator backends for OrbMol, xTB/ALPB delta correction, PySCF, and gpu4pyscf-assisted runs.
- File-based result handoff through `.traj`, `.xyz`, `.csv`, log, figure, JSON, and Molden outputs.
- Shared Streamlit queue for running jobs on a common workstation or remote server.

## Repository layout

```text
MolScout/
|-- app/                 # Streamlit UI, queue, sessions, archives, and monitoring
|-- core/                # Scientific workflow modules and bundled sample inputs
|-- docs/                # Architecture, workflow, backend, and environment notes
|-- requirements.txt     # Python dependencies for non-Docker setup
`-- Dockerfile           # Recommended environment definition
```

## Installation

Using the provided Dockerfile is recommended for routine use. The calculation stack includes packages with CUDA, compiled-library, and optimizer dependencies, so Docker gives the most reproducible starting point for app and command-line use.

Build the image from the repository root:

```bash
docker build -t molscout .
```

Start an interactive container with GPU access:

```bash
docker run --gpus all -it --rm -p 8501:8501 molscout
```

Inside the container, launch the Streamlit app when needed:

```bash
streamlit run /opt/MolScout/app/streamlit_app.py --server.address 0.0.0.0
```

A direct pip setup is still possible for development or lightweight checks, but backend behavior may differ by platform:

```bash
pip install -r requirements.txt
pip install -r app/requirements.txt
```

## Verified environment

The following package versions were used for the current verification environment.

| Package | Version |
|---|---:|
| ase | 3.28.0 |
| sella | 0.0.1.dev386+g21c6dc7bf |
| pydmf | 1.2.1 |
| pyscf | 2.13.0 |
| orb-models | 0.7.0 |
| tblite | 0.6.0 |
| torch | 2.12.0 |

## Running the Streamlit app

From the Docker image:

```bash
streamlit run /opt/MolScout/app/streamlit_app.py --server.address 0.0.0.0
```

From a local checkout with dependencies already installed:

```bash
streamlit run app/streamlit_app.py
```

The app writes session data, queued jobs, logs, and generated archives under `app/data/`. Scientific defaults remain in `core/default_config.py`; per-job overrides are applied by `app_core.workflow_runner` without modifying source files under `core/`.

## Running the core workflow directly

A full reactant/product workflow can be launched directly when command-line operation is preferred:

```bash
python core/molscout.py -d <dest_dir> -c <charge> -m orbmol -r reactant.xyz -p product.xyz
```

For stage-specific runs such as IRC-only, VIB-only, or figure refresh, use the Streamlit app or the app wrapper so that the relevant workflow flags are set consistently:

```bash
PYTHONPATH=app:core python -m app_core.workflow_runner \
  --job-json app/data/sessions/<session>/jobs/<job>/job.json
```

## Documentation

- `docs/01_architecture.md`: Repository structure and data flow.
- `docs/02_workflow_details.md`: Stage-level workflow behavior.
- `docs/03_calculators.md`: Calculator backend notes.
- `docs/04_environment.md`: Installation policy and verified package versions.

## License

This project is distributed under the GPL-3.0 license. See `LICENSE` for details.

## Acknowledgments

Parts of the workflow design were informed by ColabReaction and redox_benchmark. Respect the licenses and citation requirements of those projects and all third-party dependencies used in production calculations.
