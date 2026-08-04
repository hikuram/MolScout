# Environment and Installation: MolScout

MolScout should be installed through the provided Dockerfile for routine use. The calculation environment depends on Python packages with compiled extensions, CUDA-aware libraries, and external numerical libraries. Using one image definition reduces differences between local workstations, shared servers, and batch-style execution environments.

## 1. Recommended Docker workflow

Build the image from the repository root:

```bash
docker build -t molscout .
```

Run an interactive container with GPU access:

```bash
docker run --gpus all -it --rm -p 8501:8501 molscout
```

Launch the app inside the container:

```bash
streamlit run /opt/MolScout/app/streamlit_app.py --server.address 0.0.0.0
```

For command-line jobs, run `core/molscout.py` directly from the same container environment.

## 2. Manual pip setup

Manual installation can be used for development or syntax-level checks:

```bash
pip install -r requirements.txt
pip install -r app/requirements.txt
```

This path does not guarantee identical behavior for CUDA, tblite, gpu4pyscf, or compiled optimizer dependencies. Confirm backend initialization in the job log before using manually installed environments for production calculations.

## 3. Verified package versions

The current verification environment used the following package versions.

| Package | Version |
|---|---:|
| Python | 3.12 |
| ase | 3.28.0 |
| sella | 0.0.1.dev386+g21c6dc7bf |
| pydmf | 1.2.1 |
| pyscf | 2.13.0 |
| orb-models | 0.7.0 |
| tblite | 0.6.0 |
| torch | 2.12.0 |
