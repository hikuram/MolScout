# Copilot Instructions for MolScout

MolScout is a Python-based computational chemistry toolkit for reaction-path exploration, transition-state optimization, IRC calculations, vibrational analysis, and optional high-level refinement.

## Repository structure

- `app/`: Streamlit UI, queue management, session handling, monitoring, cleanup, and archive helpers.
- `core/`: scientific workflow modules, calculator backends, trajectory utilities, plotting, PySCF export, default configuration, and sample inputs.
- `docs/`: architecture, workflow, and calculator documentation.

## Main entry point

Use `core/molscout.py` as the maintained workflow entry point. Stage-specific behavior should be controlled through `core/default_config.py` or, preferably, through `app_core.workflow_runner` from the Streamlit app.

Example full workflow:

```bash
python core/molscout.py -d <directory> -c <charge> -m orbmol -r reactant.xyz -p product.xyz
```

Example app-wrapper workflow:

```bash
PYTHONPATH=app:core python -m app_core.workflow_runner \
  --workflow "IRC workflow only" \
  --directory <directory> \
  --charge <charge> \
  --method orbmol \
  --input input.traj \
  --ts-opt \
  --irc
```

## Development notes

- Preserve the core scientific behavior unless a change explicitly targets numerical or workflow logic.
- Keep `app/` orchestration separate from `core/` scientific routines.
- Avoid reintroducing duplicate standalone workflow scripts for IRC-only, VIB-only, or figure-refresh modes.
- Prefer app-wrapper overrides over editing `core/default_config.py` during queued app execution.
- Keep Python comments and identifiers in ASCII unless existing domain terminology requires otherwise.
