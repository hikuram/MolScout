# MolScout Streamlit App

The `app/` directory contains a Streamlit front end for submitting MolScout jobs, managing the shared queue, monitoring the server, stopping jobs, and building downloadable archives. It is designed for shared remote use: one worker processes queued calculations in order.

## Execution model

- Each user session is stored under `app/data/sessions/`.
- Each job is stored in `jobs/<job_id>/` inside its session folder.
- The shared queue worker runs one job at a time so GPU and CPU usage stay predictable.
- Runtime state is recorded separately from the process exit code, which helps avoid treating a clean process exit as a scientifically complete job.
- Sessions are retained for 30 days by default and can be cleaned up from the UI.

## What the app covers

- Submit full, IRC-only, VIB-only, figure-refresh, and concatenation/batch workflows from one UI.
- Configure workflow-stage toggles and calculation settings per job.
- Use newly uploaded structures, bundled sample inputs, or existing files from the selected session.
- Inspect queue status, job logs, server resources, and NVIDIA GPU status.
- Stop running jobs, delete queued jobs, and reorder jobs within a session.
- Download a single job directory, selected jobs, merged CSV outputs, or an entire session as ZIP archives.

## Directory layout

- `app/streamlit_app.py`: Main Streamlit entry point and top navigation.
- `app/app_pages/`: Queue, Submit, Results, Chemiscope, PySCF, and About page modules.
- `app/app_ui/`: Shared Streamlit UI helpers and reusable view functions.
- `app/app_core/`: Queue, session, monitoring, cleanup, archive, workflow, and system helper modules.
- `app/data/sessions/`: Per-session working directories.
- `app/data/queue/`: Shared queue state and worker PID files.
- `app/data/logs/`: Queue worker logs.
- `app/data/archives/`: Generated ZIP archives.

## Page layout

- `Queue`: Shows the shared queue and the selected session overview.
- `Submit`: Submits reaction-path and file-concatenation jobs.
- `Results`: Shows session jobs, logs, result files, and ZIP downloads.
- `Chemiscope`: Visualizes `.traj`, `.xyz`, and `.extxyz` files from the selected session with chemiscope.
- `PySCF`: Edits PySCF settings for the selected session.
- `About`: Shows the application overview and page guide.

Session creation and selection, monitoring, environment checks, sample listings, worker logs, and cleanup are available from the shared sidebar on every page. Cleanup runs from a confirmation dialog. The application title panel is only shown on the About page to keep operational pages compact.

## Recommended execution method

For the standard environment, use the repository Dockerfile. Build the image from the repository root:

```bash
docker build -t molscout .
```

Start the app from the container:

```bash
docker run --gpus all -it --rm -p 8501:8501 molscout \
  streamlit run /opt/MolScout/app/streamlit_app.py --server.address 0.0.0.0
```

For local development only, you can install dependencies manually and launch Streamlit from the repository root:

```bash
pip install -r requirements.txt
pip install -r app/requirements.txt
streamlit run app/streamlit_app.py
```

## Notes

- The app does not edit source files under `core/`.
- Scientific defaults are read from `core/default_config.py`.
- Per-job overrides are applied by `app_core.workflow_runner` before `core/molscout.py` is executed.
- Built-in sample reactant/product pairs are read from `core/sample_input/`.
- Figure-refresh jobs require both a trajectory (`.traj` or `.xyz`) and an existing result CSV.
- If the installed Streamlit version supports fragments, the monitoring panel refreshes every 5 seconds.
- The Chemiscope page requires `chemiscope[streamlit]`. App-specific optional dependencies are collected in `app/requirements.txt`.

## SCAN GUI notes

- SCAN settings support quick presets and relative ranges in addition to the conventional absolute `bond` / `angle` / `dihedral` inputs.
- Dihedral twist presets use 4 atoms, angle wag/bend presets use 3 atoms, and bond stretch/compression presets use 2 atoms.
- `current -> current + delta` and `current + start delta -> current + end delta` read the current value from the reactant XYZ in the GUI and convert it to `SCAN_START_VAL`, `SCAN_END_VAL`, and `SCAN_STEPS` at job submission.
- Step-size mode is converted to the number of divisions expected by the core `SCAN_STEPS` setting. For example, -360 -> +360 deg with a 10 deg step becomes 72 divisions and 73 points.

## Constraints integration policy

- At this stage, SCAN coordinate constraints and `FIXED_ATOMS` are treated as independent settings. The existing core `FixInternals` scan is combined with `FixAtoms`.
- In the next stage, bond/angle/dihedral scans and fixed atoms should be represented as a shared constraints list, with unified preview, conflict detection, and serialization.
- Priority validation targets are duplicate constraints on the same internal coordinate, fully fixed scan atoms, non-positive bond distances, angles near 0 or 180 deg, and failure to read the current value for a relative scan.
