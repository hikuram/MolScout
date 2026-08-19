# Environment and Installation: MolScout

[日本語](ja/04_environment.md)

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

Launch the default English app inside the container:

```bash
streamlit run /opt/MolScout/app/streamlit_app.py --server.address 0.0.0.0
```

Launch the Japanese-assisted app with:

```bash
streamlit run /opt/MolScout/app/streamlit_app_ja.py --server.address 0.0.0.0
```

For command-line jobs, run `core/molscout.py` directly from the same container environment.

## 2. Manual pip setup

Manual installation can be used for development or syntax-level checks:

```bash
pip install -r requirements.txt
pip install streamlit "chemiscope[streamlit]"
```

This path does not guarantee identical behavior for CUDA, tblite, gpu4pyscf, or compiled optimizer dependencies. Confirm backend initialization in the job log before using manually installed environments for production calculations.

## 3. Verified package versions

The current verification environment used the following package versions.

| Package | Version |
|---|---:|
| ase | 3.28.0 |
| matplotlib | 3.10.9 |
| numpy | 2.4.6 |
| orb-models | 0.7.0 |
| pandas | 3.0.3 |
| pillow | 12.2.0 |
| pydmf | 1.2.1 |
| pyscf | 2.13.0 |
| rmsd | 1.6.5 |
| scipy | 1.17.1 |
| seaborn | 0.13.2 |
| sella | 0.0.1.dev386+g21c6dc7bf |
| streamlit | 1.58.0 |
| tblite | 0.6.0 |
| torch | 2.12.0 |
