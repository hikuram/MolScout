FROM nvidia/cuda:13.3.1-runtime-ubuntu24.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PIP_BREAK_SYSTEM_PACKAGES=1

ARG MOLSCOUT_REPO=https://github.com/hikuram/MolScout.git
ARG MOLSCOUT_REF=app-ja

# System libraries needed by Python wheels and cyipopt.
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

# Optional Skala functional backend. Enable only when using skala-* PySCF XC settings.
# RUN pip3 install --no-cache-dir --break-system-packages skala

# Prepare font and plotting caches during image build.
RUN fc-cache -f -v \
    && mkdir -p /root/.cache/matplotlib \
    && python3 -c "import matplotlib.pyplot"

# Prefetch OrbMol v1/v2 models on CPU so image builds do not require a GPU.
# The shared cache is made writable/readable for root and non-root container users.
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
