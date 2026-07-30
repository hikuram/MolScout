FROM nvidia/cuda:13.3.1-runtime-ubuntu24.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PIP_BREAK_SYSTEM_PACKAGES=1

ARG MOLSCOUT_REPO=https://github.com/hikuram/MolScout.git
ARG MOLSCOUT_REF=app-ja

# System libraries needed by Python wheels, gpu4pyscf, and cyipopt.
RUN echo "ttf-mscorefonts-installer msttcorefonts/accepted-mscorefonts-eula select true" | debconf-set-selections \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
    build-essential \
    ca-certificates \
    cython3 \
    fontconfig \
    gfortran \
    git \
    libblas-dev \
    libhdf5-dev \
    liblapack-dev \
    pkg-config \
    python3 \
    python3-dev \
    python3-pip \
    python3-setuptools \
    python3-tk \
    python3-wheel \
    coinor-libipopt1v5 \
    coinor-libipopt-dev \
    ttf-mscorefonts-installer \
    && rm -rf /var/lib/apt/lists/*

# Clean repository checkout.
RUN git clone --depth 1 --branch "${MOLSCOUT_REF}" "${MOLSCOUT_REPO}" /opt/MolScout

WORKDIR /opt/MolScout

ENV PYTHONPATH="/opt/MolScout/core:${PYTHONPATH}"

RUN pip3 install --no-cache-dir --break-system-packages -r requirements.txt

# GPU4PySCF stack for CUDA 13.x.
# Keep CuPy/cuTENSOR pinned because gpu4pyscf is sensitive to this pairing.
RUN pip3 install --no-cache-dir --break-system-packages \
    cupy-cuda13x==13.4.1 \
    "cutensor-cu13==2.2.*" \
    gpu4pyscf-cuda13x

# Optional Skala functional backend. Enable only when using skala-* PySCF XC settings.
# RUN pip3 install --no-cache-dir --break-system-packages skala

# Quick import check for GPU4PySCF/CuPy/cuTENSOR. This does not require a GPU.
RUN python3 - <<'PY'
import cupy
import gpu4pyscf
import importlib

importlib.import_module("gpu4pyscf.lib.cutensor")
print("GPU4PySCF/CuPy/cuTENSOR imports OK")
cupy.show_config()
PY

# Prepare font and plotting caches during image build.
RUN fc-cache -f -v \
    && mkdir -p /root/.cache/matplotlib \
    && python3 -c "import matplotlib.pyplot"

# Prefetch OrbMol v1/v2 models on CPU so image builds do not require a GPU.
ENV HF_HOME=/opt/MolScout/.cache/huggingface

RUN python3 - <<'PY'
from orb_models.forcefield import pretrained

loaders = [
    ("orbmol_v2", pretrained.orbmol_v2),
    ("orbmol_v1_conservative", pretrained.orbmol_v1_conservative),
]

for name, loader in loaders:
    print(f"Prefetching {name}...")
    model, atoms_adapter = loader(device="cpu", precision="float64")
    del model, atoms_adapter

print("OrbMol model prefetch complete.")
PY

# Runtime must use the prefetched model cache instead of probing Hugging Face Hub.
ENV HF_HUB_OFFLINE=1
ENV TRANSFORMERS_OFFLINE=1
ENV HF_HUB_ETAG_TIMEOUT=1
ENV HF_HUB_DOWNLOAD_TIMEOUT=1

WORKDIR /workspace

CMD ["/bin/bash"]
