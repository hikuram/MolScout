# MolScout

[日本語](README.ja.md)

MolScout is a Python toolkit for automating reaction-path searches and follow-up molecular calculations. It treats initial-path generation, transition-state optimization, intrinsic reaction coordinate calculations, vibrational analysis, thermochemical evaluation, and optional high-level energy refinement as a connected workflow.

This repository is centered on a Streamlit application for shared execution and the `core/` directory containing the scientific workflow. The earlier standalone workflow variants have been consolidated; workflow-stage selection is controlled by the application wrapper and `core/default_config.py`.

## Key features

- Initial-path generation with DMF, NEB, SCAN, or concatenated trajectories
- Multi-fidelity SCAN (MF-SCAN) with OrbMol-guided intermediate steps and periodic PySCF DFT anchors
- Transition-state optimization and adaptive IRC calculations with ASE / Sella
- Vibrational analysis and thermochemical evaluation with handling for low-frequency and numerical artifacts
- Calculator backends for OrbMol, xTB/ALPB delta correction, PySCF, and gpu4pyscf-assisted calculations
- File-based result handoff through `.traj`, `.xyz`, `.csv`, logs, figures, and related outputs
- A Streamlit queue designed for shared workstations or remote servers
- Cross-session artifact search and filesystem consistency diagnostics through a PostgreSQL artifact catalog

## Repository layout

```text
MolScout/
|-- app/                 # Streamlit UI, queue, session, and archive components
|-- core/                # Scientific workflow modules and sample inputs
|-- docs/                # Architecture, workflow, backend, and environment notes
|-- scripts/             # Metadata migration and artifact-catalog utilities
|-- requirements.txt     # Python dependencies for non-Docker environments
`-- Dockerfile           # Recommended environment definition
```

## Installation

For routine use, the provided Dockerfile is recommended. The calculation environment includes packages that depend on CUDA, compiled libraries, and optimizer backends. Using the Docker image reduces differences between the application and command-line workflows.

Build the image from the repository root:

```bash
docker build -t molscout .
```

Run an interactive container with GPU access:

```bash
docker run --gpus all -it --rm -p 8501:8501 molscout
```

Launch the default English Streamlit app inside the container:

```bash
streamlit run /opt/MolScout/app/streamlit_app.py --server.address 0.0.0.0
```

To launch the Japanese-assisted UI instead:

```bash
streamlit run /opt/MolScout/app/streamlit_app_ja.py --server.address 0.0.0.0
```

Direct setup with pip is also possible for development or lightweight checks, but backend behavior can depend on the platform.

```bash
pip install -r requirements.txt
```

## Verified environment

The current verification environment used the following package versions.

| package | version |
|---|---|
| ase | 3.29.0 |
| streamlit | 1.60.0 |
| pydmf | 1.2.2 |
| sella | 2.5.0 |
| orb-models | 0.7.0 |
| pyscf | 2.14.0 |
| tblite | 0.7.0 |
| torch | 2.13.0a0+9186a08b2c.nv26.7.59513937 |
| gpu4pyscf | 1.8.0 |
| cupy | 13.6.0 |
| cupytensor | 2.3.1 |

## Launching the Streamlit app

PostgreSQL and the Streamlit app can be started together with Compose:

```bash
podman compose up -d --build
```

After the initial verification, set `POSTGRES_PASSWORD` in `.env` and do not use the default password in the Compose file for production operation.

Management metadata for sessions, jobs, the queue, and application state is stored in PostgreSQL. Input structures, trajectories, calculation results, logs, generated archives, and other file payloads are stored under `data/` at the project root. Scientific defaults remain in `core/default_config.py`, and per-job overrides are applied by `app_core.workflow_runner`. The application does not rewrite source files under `core/`.

When launching directly from a local checkout, configure `PGHOST`, `PGDATABASE`, `PGUSER`, and `PGPASSWORD` and provide an accessible PostgreSQL instance.

### Cataloging existing artifacts

Artifact files remain under `data/`; only metadata needed for search is registered in the PostgreSQL `artifacts` table. Existing data can be indexed with the single-purpose script below.

```bash
podman compose exec molscout \
  python /opt/MolScout/scripts/index_artifacts.py --dry-run

podman compose exec molscout \
  python /opt/MolScout/scripts/index_artifacts.py
```

New jobs are indexed automatically during finalization. The Streamlit `Data` page provides cross-session search, manual rescanning, and diagnostics for missing files, unregistered files, and orphaned directories. Rescanning and diagnostics do not delete or move files.

## Running the core workflow directly

For command-line operation, the reactant/product workflow can be launched directly:

```bash
python core/molscout.py -d <dest_dir> -c <charge> -m orbmol -r reactant.xyz -p product.xyz
```

For stage-specific runs such as IRC-only, VIB-only, or figure refresh, using the Streamlit app or the application wrapper is recommended so workflow flags remain internally consistent.

```bash
PYTHONPATH=app:core python -m app_core.workflow_runner \
  --workflow "VIB workflow only" \
  --directory <dest_dir> \
  --charge <charge> \
  --method orbmol \
  --input input.traj \
  --vib
```

## Documentation

- `docs/01_architecture.md`: repository structure and data flow
- `docs/02_workflow_details.md`: behavior of individual workflow stages
- `docs/03_calculators.md`: calculator-backend notes
- `docs/04_environment.md`: installation policy and verified package versions

Japanese versions are available under `docs/ja/`. The Colab example is available in English as `colab_notebook_example.ipynb` and in Japanese-assisted form as `colab_notebook_example_ja.ipynb`.

## License

This project is distributed under the GPL-3.0 license. See `LICENSE` for details.

## Acknowledgments

Parts of the workflow design were informed by ColabReaction and redox_benchmark. For third-party projects and dependencies used in production calculations, review their licenses and citation requirements.
