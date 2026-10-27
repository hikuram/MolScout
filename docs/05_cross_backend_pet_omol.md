# Optional PET-OMol S/L energy-profile cross-check (v0.4.6)

This option adds **secondary single-point evaluations** to a finished MolScout
Primary MLIP workflow. It never changes the Primary calculator, optimization,
TS search, IRC, or existing `result.csv` values. Both PET models are **OFF** by
default in **Submit > 6. Extra Workflow Options** and **New Concatenation >
5. Extra Workflow Options**.

## Configuration

```json
{
  "CROSS_BACKEND_MODELS": ["pet-omol-s", "pet-omol-l"],
  "CROSS_BACKEND_PRECISION": "float32",
  "CROSS_BACKEND_SOURCE": "auto"
}
```

- Models: PET-OMol-S/L, pinned to **checkpoint version 1.0.0**, evaluated with
  **UPET 0.3.1** via `upet.ase.UPETCalculator(checkpoint_path=...)`.
- Precision: `float32` (PyTorch matmul precision **highest**, default) or
  `float64` (slower; useful to test numerical sensitivity of Delta E).
- Source: `auto` prefers evaluated `optpoints/optpoints.traj` and
  `optpoints/result_optpoints.csv` when present, otherwise uses the Primary
  input/path trajectory and its matching CSV. Explicit `optpoints` or
  `initial_path` selection is also available. It does not run new structure
  optimizations or reconstruct energies from individual XYZ files.
- Only **Primary OrbMol / OrbMol+ALPB** Delta E series are eligible. MF-SCAN
  mixed DFT anchor trajectories, figure refresh, and CAT batches that optimize
  frame geometries are excluded, to avoid mismatched structures and energies.
- The trajectory must match the CSV's frame count and `# image` IDs, and all
  frames must have the same elements in the same order. Missing Primary
  energies or a missing reference frame cause the option to skip safely.

## Output

`cross_backend.csv` (separate from and non-destructive to Primary outputs):

- One row per `model` and `# image`, including `source_traj`, `source_csv`,
  `series`, and `previous_#image` (for optpoints).
- `primary_energy_eV`, `primary_deltaE_kcalmol`: **existing** Primary values.
- `energy_raw_eV`: PET model's raw total energy, recorded for provenance,
  **not** for absolute-energy comparisons against another model.
- `deltaE_kcalmol`: `(PET E(frame) - PET E(frame 0)) * EV_TO_KCAL_MOL`.
  The reference is always frame 0 and never silently changes after failure.
- `deltaE_minus_primary_kcalmol`: only populated for a vacuum Primary.
  A Primary `orbmol+alpb` includes an xTB solvation-delta correction while PET
  is vacuum-only; that column is blank in this case and the basis difference
  is annotated in the CSV. No implicit solvent correction is added to PET.
- `charge`, `multiplicity`, `precision`, `checkpoint_version`, elapsed time,
  per-frame `status` and `error` for reproducibility and debugging.

For electron-state conditioning, each detached ASE `Atoms` snapshot receives
`atoms.info['charge']` and `atoms.info['spin']` (multiplicity), including the
`spin_multiplicity` alias. The original calculator is never deep-copied or
replaced. All frames are evaluated for PET-OMol-S **before** loading PET-OMol-L.
PyTorch cache is cleared between models, but actual GPU memory must be checked.

If a secondary model fails (e.g. missing checkpoint, CUDA OOM), the failure is
recorded without marking the already-finished Primary job as failed. If the
reference frame has no PET energy, its Delta E remains missing for the *whole*
model even when later frames succeed.

## Docker / offline execution

`requirements.txt` pins `upet==0.3.1`. The Dockerfile keeps the NGC Torch
version constrained when installing other packages. By default it downloads
PET-OMol-S/L v1.0.0 checkpoints during the image build (with
SHA-256 verification against the published Hugging Face artifacts) under:

```
/opt/MolScout/.cache/upet_models/models/pet-omol-s-v1.0.0.ckpt
/opt/MolScout/.cache/upet_models/models/pet-omol-l-v1.0.0.ckpt
```

The L checkpoint increases the image by about 1 GB. To disable build-time
prefetch, use `--build-arg PREFETCH_UPET_CHECKPOINTS=0` and provide the model
files yourself before selecting a model. You can override the directory with
`MOLSCOUT_UPET_CHECKPOINT_DIR`. Runtime execution stays compatible with
`HF_HUB_OFFLINE=1` and **does not** query the remote checkpoint registry.

Before enabling long reaction paths, run a two-frame neutral-water smoke check
in the rebuilt container:

```bash
python3 /opt/MolScout/scripts/check_pet_omol.py --models pet-omol-s pet-omol-l
```

Use `--precision float64` for an optional numerical-sensitivity check. This
smoke test confirms basic inference and a finite Delta E, not predictive
accuracy for chemical reactions, charged species, or open-shell systems.

The package dependencies and CUDA compatibility must be checked during an
actual NGC Docker build and GPU validation. CPU tests use an injected fake
Calculator and therefore **do not** establish numerical equivalence or GPU
compatibility with PET-OMol-S/L.

References:
- UPET 0.3.1 API: https://lab-cosmo.github.io/upet/latest/api.html
- UPET model catalog: https://lab-cosmo.github.io/upet/latest/models.html
- UPET ASE usage: https://lab-cosmo.github.io/upet/latest/usage/ase.html
