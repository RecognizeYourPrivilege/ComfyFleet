# ComfyFleet Phase 1 runtime.
# Slim Debian bookworm + NVIDIA CUDA 12.4 runtime libraries (not the devel toolkit).
# gcc and python3-dev are the host C compiler and Python.h that Triton 3.2
# (torch 2.6.0+cu124's dependency) needs to JIT-compile cuda_utils. That
# compile runs when the top-level kitchen package loads the Triton backend.
# No workflow JSON is copied into this image. The operator file is bind-mounted
# at /opt/comfyfleet/instance/default_workflow.json and the entrypoint refuses
# to start when that file is missing.
#
# Pins are duplicated in docker/PINS.txt and the README.
#
# RES4LYF imports cv2 while the custom node loads. The opencv-python wheel
# (not the headless build; that is the name in RES4LYF requirements.txt)
# ships a Qt xcb plugin linked to libxcb.so.1. bookworm-slim does not ship
# that library, which is the "cannot open shared object file" crash.
# libxcb1, libx11-6, libxext6, libice6, libsm6, libglib2.0-0, and libgl1
# cover that plugin plus the libGL and libglib imports cv2 itself performs.
# numpy==2.2.6 is written into torch-constraints.txt next to the torch pins.
# torchaudio==2.6.0+cu124 is installed with torch and torchvision. Pinned
# ComfyUI-Manager logs "PyTorch is not installed" when pip list is missing
# any of the three; it does not import torch. See docker/PINS.txt.

FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    CUDA_VERSION=12.4.1 \
    NVIDIA_VISIBLE_DEVICES=all \
    NVIDIA_DRIVER_CAPABILITIES=compute,utility \
    PATH=/opt/venv/bin:/usr/local/cuda/bin:${PATH} \
    LD_LIBRARY_PATH=/usr/local/cuda/lib64:${LD_LIBRARY_PATH}

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
    && curl -fsSL -o /tmp/cuda-keyring.deb \
        https://developer.download.nvidia.com/compute/cuda/repos/debian12/x86_64/cuda-keyring_1.1-1_all.deb \
    && dpkg -i /tmp/cuda-keyring.deb \
    && rm /tmp/cuda-keyring.deb \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        cuda-libraries-12-4=12.4.1-1 \
        cuda-cudart-12-4=12.4.127-1 \
        libcudnn9-cuda-12=9.1.0.70-1 \
        bash \
        gcc \
        python3-dev \
        git \
        python3 \
        python3-venv \
        python3-pip \
        libxcb1 \
        libx11-6 \
        libxext6 \
        libice6 \
        libsm6 \
        libglib2.0-0 \
        libgl1 \
    && ln -sfn /usr/local/cuda-12.4 /usr/local/cuda \
    && apt-mark hold cuda-libraries-12-4 cuda-cudart-12-4 libcudnn9-cuda-12 \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -c 'import sys; assert sys.version_info[:2] == (3, 11), sys.version'

RUN mkdir -p /opt/comfyfleet \
    && python3 -m venv /opt/venv \
    && pip install --no-cache-dir \
        torch==2.6.0+cu124 \
        torchvision==0.21.0+cu124 \
        torchaudio==2.6.0+cu124 \
        --index-url https://download.pytorch.org/whl/cu124 \
        --extra-index-url https://pypi.org/simple \
    && python -c 'import torch; v = torch.__version__; assert v.startswith("2.6.0") and "cu124" in v, v' \
    && python -c 'import importlib.metadata as metadata; expected = (("torch", "2.6.0+cu124"), ("torchvision", "0.21.0+cu124"), ("torchaudio", "2.6.0+cu124")); mismatches = {name: metadata.version(name) for name, pin in expected if metadata.version(name) != pin}; assert not mismatches, mismatches' \
    && pip install --no-cache-dir numpy==2.2.6 \
    && python -c 'import numpy; assert numpy.__version__ == "2.2.6", numpy.__version__' \
    && printf '%s\n' 'torch==2.6.0+cu124' 'torchvision==0.21.0+cu124' 'torchaudio==2.6.0+cu124' 'numpy==2.2.6' > /opt/comfyfleet/torch-constraints.txt

# Manager installs run `python -m pip install <pkg>` and do not pass -c
# (pinned 14b5aaab does not pass -U either). PIP_CONSTRAINT is the same file,
# so those installs cannot replace torch, torchvision, torchaudio, or numpy.
# Afterwards PIPFixer.fix_broken reads `pip list` and logs
# "PyTorch is not installed" if any of those three torch packages is absent.
ENV PIP_CONSTRAINT=/opt/comfyfleet/torch-constraints.txt

# Pure-Python comfy-kitchen (eager backend). The manylinux wheel targets CUDA 13.
# Install this wheel before ComfyUI requirements so comfy-kitchen==0.2.36 is
# already satisfied and pip does not select the CUDA 13 wheel. Torch 2.6
# infer_schema still rejects this release's list[int]/list[bool] custom-op
# annotations; the rewrite below the requirements install fixes that.
ARG COMFY_KITCHEN_WHEEL=https://files.pythonhosted.org/packages/38/23/a6787aac01d7c28ae3cb07579ba839297a35e6fad66baac096916246cc7f/comfy_kitchen-0.2.36-py3-none-any.whl
RUN pip install --no-cache-dir "${COMFY_KITCHEN_WHEEL}"

# Full upstream checkouts. Pixaroma's workflow-browser examples stay inside that
# custom node; the entrypoint never uses them as the instance default.
RUN git config --global --add safe.directory '*' \
    && git clone https://github.com/Comfy-Org/ComfyUI.git /opt/ComfyUI \
    && git -C /opt/ComfyUI checkout 6b747c0428c343e1417219641db93a4fb7cb69ae \
    && git clone https://github.com/Comfy-Org/ComfyUI-Manager.git /opt/comfyfleet/baked_custom_nodes/ComfyUI-Manager \
    && git -C /opt/comfyfleet/baked_custom_nodes/ComfyUI-Manager checkout 14b5aaab711ad1f1306d420732a923fb058c44d7 \
    && git clone https://github.com/pixaroma/ComfyUI-Pixaroma.git /opt/comfyfleet/baked_custom_nodes/ComfyUI-Pixaroma \
    && git -C /opt/comfyfleet/baked_custom_nodes/ComfyUI-Pixaroma checkout 9259bc49557a92e3fc14796999468c723bd1ecdd \
    && git clone https://github.com/RecognizeYourPrivilege/ComfyUI-ComfyDock.git /opt/comfyfleet/baked_custom_nodes/ComfyUI-ComfyDock \
    && git -C /opt/comfyfleet/baked_custom_nodes/ComfyUI-ComfyDock checkout 3a9ff9eba897bf2388d6c1943b01d819ba05a0c6 \
    && git clone https://github.com/ClownsharkBatwing/RES4LYF.git /opt/comfyfleet/baked_custom_nodes/RES4LYF \
    && git -C /opt/comfyfleet/baked_custom_nodes/RES4LYF checkout 3d1d69da69ee47f7647d59e1bd0967e472fccc41 \
    && mkdir -p /opt/comfyfleet/stock_custom_nodes \
    && cp -a /opt/ComfyUI/custom_nodes/. /opt/comfyfleet/stock_custom_nodes/ \
    && mkdir -p /opt/ComfyUI/models /opt/ComfyUI/input /opt/ComfyUI/output /opt/ComfyUI/temp /opt/comfyfleet/instance

RUN pip install --no-cache-dir -c /opt/comfyfleet/torch-constraints.txt \
        -r /opt/ComfyUI/requirements.txt \
        -r /opt/comfyfleet/baked_custom_nodes/ComfyUI-Manager/requirements.txt \
        -r /opt/comfyfleet/baked_custom_nodes/ComfyUI-Pixaroma/requirements.txt \
        -r /opt/comfyfleet/baked_custom_nodes/ComfyUI-ComfyDock/requirements.txt \
        -r /opt/comfyfleet/baked_custom_nodes/RES4LYF/requirements.txt \
    && python -c 'import torch; assert "cu124" in torch.__version__, torch.__version__' \
    && python -c 'import numpy; assert numpy.__version__ == "2.2.6", numpy.__version__'

COPY docker/PINS.txt /opt/comfyfleet/PINS.txt
COPY docker/patch_comfy_kitchen_torch26.py /opt/comfyfleet/patch_comfy_kitchen_torch26.py
COPY docker/verify_image_pins.py /opt/comfyfleet/verify_image_pins.py

# Requirements leave an already-installed 0.2.36 in place. Reinstall the
# pure-Python wheel anyway so a resolver that picked the manylinux build cannot
# survive into the image, then rewrite custom-op annotations for torch 2.6.
# The patch script registers those eager custom ops through infer_schema.
# It does not load the top-level kitchen package: that import loads the Triton
# backend, and Triton raises "0 active drivers" when the build has no NVIDIA
# driver. GPU selection at runtime does not provide a driver during docker build.
# On a GPU host the same import reaches triton/runtime/build.py and compiles
# cuda_utils (driver.c, which includes Python.h). bookworm-slim has no gcc, so
# that step raised "Failed to find C compiler. Please specify via CC
# environment variable." gcc and python3-dev above are what that compile uses.
# Triton ships cuda.h in its wheel; this image does not install nvcc or g++.
RUN pip install --no-cache-dir --force-reinstall --no-deps "${COMFY_KITCHEN_WHEEL}" \
    && python /opt/comfyfleet/patch_comfy_kitchen_torch26.py \
    && python /opt/comfyfleet/verify_image_pins.py
COPY docker/comfyfleet_default_workflow /opt/comfyfleet/baked_custom_nodes/comfyfleet_default_workflow

# Fleet patch of the baked Manager. Stock is_dedicated_install_allowed
# requires allow_git_url_install / allow_pip_install AND a loopback --listen.
# The entrypoint keeps --listen 0.0.0.0 so Docker can publish the instance
# port onto the docker.sock LAN, and it exports COMFYFLEET_TRUSTED_INSTALL=1.
# This rewrite keeps the flag check and skips the loopback term only when
# that variable is 1. A public-internet Manager is a different threat model;
# the env var is the operator gate for this image. Source-only, and it sits
# after the torch and git-clone layers so a rebuild can reuse them.
COPY docker/patch_manager_trusted_install.py /opt/comfyfleet/patch_manager_trusted_install.py
COPY docker/seed_manager_config.py /opt/comfyfleet/seed_manager_config.py
COPY docker/entrypoint.sh /opt/comfyfleet/entrypoint.sh
RUN python /opt/comfyfleet/patch_manager_trusted_install.py \
    && chmod 0755 /opt/comfyfleet/entrypoint.sh

WORKDIR /opt/ComfyUI
EXPOSE 8188
ENTRYPOINT ["/opt/comfyfleet/entrypoint.sh"]
