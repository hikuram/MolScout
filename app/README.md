# MolScout Streamlit App

[日本語](README.ja.md)

The `app/` directory contains the Streamlit front end for submitting MolScout jobs, managing the queue, monitoring runs, stopping jobs, and creating archives. The application is intended for shared remote use, with a single worker executing queued calculations sequentially.

## Execution model

- Session, job, queue, and application-state metadata is stored in PostgreSQL.
- Inputs, calculation results, and logs for each user session are stored under `data/sessions/`.
- Each job is stored under `jobs/<job_id>/` inside its session.
- The shared queue worker runs one job at a time to keep GPU / CPU utilization predictable.
- Runtime state is recorded separately from the process exit code, reducing the chance that a normally exited process is misclassified as a scientifically complete job.
- Individual jobs can be deleted from the UI. Time-based cleanup is disabled during the PostgreSQL migration.

## Application scope

- Full, IRC-only, VIB-only, figure-refresh, and concatenation/batch workflows can be submitted from one UI.
- Workflow-stage toggles and calculation settings can be configured per job.
- New structure uploads, bundled sample inputs, and reuse of existing session files are supported.
- Queue status, job logs, server resources, and NVIDIA GPU status can be inspected.
- Running jobs can be stopped, queued jobs can be deleted, and queue order can be changed within a session.
- A single job directory or an entire session can be downloaded as a ZIP archive.
- Selected artifact metadata is registered in PostgreSQL and can be searched across sessions.
- Missing/unregistered files and orphaned directories can be diagnosed by comparing database records with the filesystem.

## Directory layout

- `app/streamlit_app.py`: default English launcher
- `app/streamlit_app_ja.py`: Japanese-assisted launcher
- `app/app_main.py`: shared application/navigation implementation
- `app/app_pages/`: Queue, Submit, Results, Chemiscope, Data, PySCF, and About page modules
- `app/app_ui/`: shared Streamlit UI helpers, localization helper, and reusable view functions
- `app/app_ui/locales/ja.json`: Japanese-assisted UI strings; English strings remain canonical in Python
- `app/app_core/`: database, queue, session, artifact catalog, monitoring, archive, and workflow helpers
- `data/sessions/`: working directories for each session
- `data/queue/`: worker PID
- `data/logs/`: queue worker logs
- `data/archives/`: generated ZIP archives
- PostgreSQL: session, job, shared queue, application-state, and artifact-catalog metadata

## Language model

MolScout uses one common application implementation with two thin launchers. The language is fixed when Streamlit starts; there is no runtime language switch.

- `streamlit_app.py`: English, the canonical/default UI
- `streamlit_app_ja.py`: Japanese-assisted UI that preserves the current mixed English/Japanese localization level
- Scientific terms, method names, configuration keys, workflow names, and many technical labels remain in English in the Japanese-assisted UI.

## Page layout

- `Queue`: view the shared queue and selected-session overview.
- `Submit`: submit reaction-path searches and file-concatenation jobs.
- `Results`: inspect session jobs, logs, result files, and ZIP downloads.
- `Chemiscope`: visualize `.traj` / `.xyz` / `.extxyz` files for selected jobs.
- `Data`: search artifacts across sessions, rescan metadata, and diagnose DB/filesystem consistency.
- `PySCF`: edit PySCF settings for the selected session.
- `About`: show the application overview and page guide.

Session creation/selection, monitoring, environment checks, sample lists, and the worker log are available from the shared sidebar. Cleanup controls are disabled during the PostgreSQL migration. The application title panel is kept on the About page rather than the routine operation pages.

## Recommended launch method

In the standard environment, start PostgreSQL and the default English app from the repository root with Compose:

```bash
podman compose up -d --build
```

The persistent PostgreSQL volume is `molscout_postgres`, and calculation files are bind-mounted from `./data`.

For local development, install dependencies manually and launch Streamlit from the repository root:

```bash
pip install -r requirements.txt
pip install streamlit "chemiscope[streamlit]"
streamlit run app/streamlit_app.py
```

For the Japanese-assisted UI:

```bash
streamlit run app/streamlit_app_ja.py
```

## Notes

- The application does not edit source files under `core/`.
- Scientific default settings are read from `core/default_config.py`.
- Per-job overrides are applied by `app_core.workflow_runner` before `core/molscout.py` is executed.
- Built-in sample reactant/product pairs are read from `core/sample_input/`.
- Figure-refresh jobs require both a trajectory (`.traj` or `.xyz`) and an existing result CSV.
- If the installed Streamlit version supports fragments, the monitoring panel refreshes every 5 seconds.
- The Chemiscope page requires `chemiscope[streamlit]`. The Dockerfile installs Streamlit and Chemiscope explicitly.

## Artifact catalog

- Cataloged artifacts include `.traj`, structures, CSV/TSV, figures, log/text, JSON/YAML/TOML, selected scientific data, and ZIP files.
- File contents are not stored in PostgreSQL. The catalog records the path relative to `data/`, type, role, size, modified time, and metadata derived from manifests.
- `*.runtime.json`, PID/lock/exit files, and legacy `session.json` / `job.json` are excluded from the catalog.
- When a job enters a terminal state (`completed`, `failed`, or `cancelled`), its job directory is scanned automatically. Catalog errors do not change the calculation status and are recorded in job metadata as `artifact_catalog_error`.
- Existing data can be indexed with `python scripts/index_artifacts.py`; use `--dry-run` to inspect counts only.
- Maintenance actions on the Data page only refresh the catalog and run diagnostics; they do not delete files or database records or perform automatic repairs.

## SCAN GUI notes

- SCAN settings support quick presets and relative ranges in addition to the existing absolute `bond` / `angle` / `dihedral` inputs.
- Dihedral twists use 4 atoms, angle wag/bend uses 3 atoms, and bond stretch/compression uses 2 atoms.
- For `current -> current + delta` and `current + start delta -> current + end delta`, the GUI reads the current value from the reactant XYZ and converts it to `SCAN_START_VAL` / `SCAN_END_VAL` / `SCAN_STEPS` when the job is submitted.
- Step-size input is converted to a number of divisions to match the core `SCAN_STEPS` convention. For example, a -360 -> +360 deg scan with 10 deg spacing uses 72 divisions and therefore 73 points.
- `MF-SCAN` is available as a mode inside SCAN rather than as a separate initial-path method. It performs OrbMol constrained optimization at every SCAN point and then, at DFT anchor points, immediately performs a PySCF constrained optimization. The DFT-optimized anchor geometry is propagated into the next SCAN step.
- When `MF-SCAN` is enabled, the primary calculation level is fixed to `pyscf`; OrbMol version and optional ALPB solvent settings remain visible for the MLIP guide, while the normal Method selection is preserved for use after MF-SCAN is disabled. The first and last SCAN points are always DFT anchors, and the intermediate anchor interval is configurable.
- `init_path.traj` / `init_path.xyz` contain DFT anchors only. `mfscan_trace.csv` records every SCAN step together with MLIP/DFT convergence, timing, energy, and output-frame mapping.

## Constraints integration policy

- At this stage, SCAN-coordinate constraints and `FIXED_ATOMS` remain separate settings, combining the existing core `FixInternals` scan and `FixAtoms` behavior.
- A later stage can represent bond / angle / dihedral scans and fixed atoms through one constraints list, with unified preview, conflict detection, and persistence.
- Priority validation cases include duplicate constraints on the same internal coordinate, complete fixation of scan atoms, non-positive bond distances, angles near 0/180 deg, and failure to obtain the current coordinate for a relative scan.
