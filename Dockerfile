ARG NGC_PYTORCH_TAG=26.07-py3
FROM nvcr.io/nvidia/pytorch:${NGC_PYTORCH_TAG}

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_BREAK_SYSTEM_PACKAGES=1 \
    CUDA_HOME=/usr/local/cuda \
    TORCH_CUDA_ARCH_LIST="12.0" \
    CUPY_ACCELERATORS="cutensor,cub"

ARG GPU4PYSCF_REF=v1.8.0
ARG MOLSCOUT_REF=app-ja

RUN echo "ttf-mscorefonts-installer msttcorefonts/accepted-mscorefonts-eula select true" \
        | debconf-set-selections \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        cmake \
        cython3 \
        fontconfig \
        gfortran \
        git \
        libblas-dev \
        liblapack-dev \
        pkg-config \
        python3-dev \
        python3-tk \
        coinor-libipopt1v5 \
        coinor-libipopt-dev \
        ttf-mscorefonts-installer \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/requirements.txt

# PyTorch and its CUDA libraries are provided by the NGC base image.
RUN python3 -m pip install --no-cache-dir \
        -r /tmp/requirements.txt \
    && python3 -c \
        "import torch; print(f'torch={torch.__version__}, cuda={torch.version.cuda}')"

# CUDA libraries used by GPU4PySCF.
RUN python3 -m pip install --no-cache-dir \
        "cupy-cuda13x==13.6.0" \
        "cutensor-cu13==2.3.*"

# Make the cuTENSOR wheel library available to the system linker.
RUN SITE_PACKAGES="$(python3 -c \
        'import site; print(site.getsitepackages()[0])')" \
    && CUTENSOR_SO="$(find "${SITE_PACKAGES}" \
        -name 'libcutensor.so*' -print -quit)" \
    && test -n "${CUTENSOR_SO}" \
    && dirname "${CUTENSOR_SO}" > /etc/ld.so.conf.d/cutensor.conf \
    && ldconfig

# Build GPU4PySCF for GeForce RTX 50-series Blackwell GPUs.
RUN git clone --depth 1 --branch "${GPU4PYSCF_REF}" \
        https://github.com/pyscf/gpu4pyscf.git /opt/gpu4pyscf \
    && cmake -S /opt/gpu4pyscf/gpu4pyscf/lib \
        -B /opt/gpu4pyscf/build \
        -DCMAKE_BUILD_TYPE=Release \
        -DCUDA_ARCHITECTURES="120-real" \
        -DBUILD_LIBXC=ON \
    && cmake --build /opt/gpu4pyscf/build --parallel 4

# Optional Skala exchange-correlation backend.
# RUN python3 -m pip install --no-cache-dir skala

# Optional Streamlit interface.
RUN python3 -m pip install --no-cache-dir \
        streamlit \
        "chemiscope[streamlit]"

RUN git clone --depth 1 --branch "${MOLSCOUT_REF}" \
        https://github.com/hikuram/MolScout.git /opt/MolScout

ENV PYTHONPATH="/opt/gpu4pyscf:/opt/MolScout/core:${PYTHONPATH}" \
    HF_HOME=/opt/MolScout/.cache/huggingface

# Cache fonts and OrbMol models for offline execution.
RUN fc-cache -f \
    && mkdir -p /root/.cache/matplotlib \
    && python3 -c "import matplotlib.pyplot" \
    && python3 -c \
        "from orb_models.forcefield import pretrained; pretrained.orbmol_v2(device='cpu', precision='float64')" \
    && python3 -c \
        "from orb_models.forcefield import pretrained; pretrained.orbmol_v1_conservative(device='cpu', precision='float64')"

ENV HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    HF_HUB_ETAG_TIMEOUT=1 \
    HF_HUB_DOWNLOAD_TIMEOUT=1

WORKDIR /workspace

CMD ["/bin/bash"]
