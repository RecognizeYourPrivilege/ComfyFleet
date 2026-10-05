# ComfyFleet cu130 instance line. Primary tag: comfyfleet:cu130.
# Peer line: Dockerfile.cu124 (comfyfleet:cu124).
# Official CPython 3.14.7 on Debian bookworm-slim, plus the NVIDIA CUDA 13.0
# runtime libraries (not the devel toolkit). Bookworm's python3 is 3.11 and
# is not installed. /opt/venv is created from this interpreter. The build
# fails unless sys.version_info[:3] is (3, 14, 7).
#
# gcc is the host C compiler Triton 3.7.1 (torch 2.13.0+cu130) needs to
# JIT-compile cuda_utils. That compile runs when the top-level kitchen
# package loads the Triton backend. Python.h comes from this image
# (/usr/local/include/python3.14). Debian python3-dev is CPython 3.11 and
# is not installed. g++ and the CUDA compiler are not installed.
# libc6-dev sits next to gcc. apt uses --no-install-recommends, which
# skips gcc's recommended libc-dev, so bookworm-slim has no crti.o.
# The llama import smoke links a temporary libcuda.so.1 with gcc -shared
# and fails with "cannot find crti.o" without that package. Triton's
# cuda_utils link needs the same objects. The cu124 line already has
# them: python3-dev installs zlib1g-dev, and zlib1g-dev depends on
# libc6-dev. python3-dev itself only recommends libc6-dev.
#
# No workflow JSON is copied into this image. The operator file is bind-mounted
# at /opt/comfyfleet/instance/default_workflow.json and the entrypoint refuses
# to start when that file is missing.
#
# Pins are duplicated in docker/PINS.txt and the README.
# llama-cpp-python==0.3.36 is baked from the abetlen cu130 index (binary
# wheel only) so JoyCaption does not source-build it. The cu124 index is
# not used in this file.
#
# RES4LYF imports cv2 while the custom node loads. The opencv-python wheel
# (not the headless build; that is the name in RES4LYF requirements.txt)
# ships a Qt xcb plugin linked to libxcb.so.1. bookworm-slim does not ship
# that library, which is the "cannot open shared object file" crash.
# libxcb1, libx11-6, libxext6, libice6, libsm6, libglib2.0-0, and libgl1
# cover that plugin plus the libGL and libglib imports cv2 itself performs.
# numpy==2.3.2 is written into torch-constraints.txt next to the torch pins.
# 2.2.6 has no cp314 manylinux wheel; 2.3.2 is the first release that does.
# torchaudio==2.11.0+cu130 is installed with torch and torchvision. There is
# no torchaudio 2.13 on the cu130 index. Pinned ComfyUI-Manager logs
# "PyTorch is not installed" when pip list is missing any of the three; it
# does not import torch. See docker/PINS.txt.

FROM python:3.14.7-slim-bookworm

# PIP_NO_CACHE_DIR applies to every pip invocation in this build, including
# a build-isolation child. The command-line cache flag is not always enough:
# an isolated sam2 build left a multi-gigabyte http-v2 cache in the layer.
# Each RUN that installs Python packages also deletes /root/.cache/pip.
ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    CUDA_VERSION=13.0.3 \
    COMFYFLEET_CUDA_TAG=cu130 \
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
        cuda-libraries-13-0=13.0.3-1 \
        cuda-cudart-13-0=13.0.96-1 \
        libcudnn9-cuda-13=9.20.0.48-1 \
        bash \
        gcc \
        libc6-dev \
        git \
        libxcb1 \
        libx11-6 \
        libxext6 \
        libice6 \
        libsm6 \
        libglib2.0-0 \
        libgl1 \
    && ln -sfn /usr/local/cuda-13.0 /usr/local/cuda \
    && apt-mark hold cuda-libraries-13-0 cuda-cudart-13-0 libcudnn9-cuda-13 \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -c 'import sys; assert sys.version_info[:3] == (3, 14, 7), sys.version'

RUN mkdir -p /opt/comfyfleet \
    && python3 -m venv /opt/venv \
    && /opt/venv/bin/python -c 'import sys; assert sys.version_info[:3] == (3, 14, 7), sys.version' \
    && pip install --no-cache-dir \
        torch==2.13.0+cu130 \
        torchvision==0.28.0+cu130 \
        torchaudio==2.11.0+cu130 \
        --index-url https://download.pytorch.org/whl/cu130 \
        --extra-index-url https://pypi.org/simple \
    && python -c 'import torch; v = torch.__version__; assert v.startswith("2.13.0") and "cu130" in v, v' \
    && python -c 'import importlib.metadata as metadata; expected = (("torch", "2.13.0+cu130"), ("torchvision", "0.28.0+cu130"), ("torchaudio", "2.11.0+cu130")); mismatches = {name: metadata.version(name) for name, pin in expected if metadata.version(name) != pin}; assert not mismatches, mismatches' \
    && pip install --no-cache-dir --only-binary=numpy numpy==2.3.2 \
    && python -c 'import numpy; assert numpy.__version__ == "2.3.2", numpy.__version__' \
    && printf '%s\n' 'torch==2.13.0+cu130' 'torchvision==0.28.0+cu130' 'torchaudio==2.11.0+cu130' 'numpy==2.3.2' > /opt/comfyfleet/torch-constraints.txt \
    && printf '%s\n' 'opencv-python-headless==99.0.0' >> /opt/comfyfleet/torch-constraints.txt \
    && printf '%s\n' 'llama-cpp-python==0.3.36' >> /opt/comfyfleet/torch-constraints.txt \
    && mkdir -p /opt/comfyfleet/wheels \
    && rm -rf /root/.cache/pip

# Manager installs run `python -m pip install <pkg>` and do not pass -c
# (pinned 14b5aaab does not pass -U either). PIP_CONSTRAINT is the same file,
# so those installs cannot replace torch, torchvision, torchaudio, numpy,
# or llama-cpp-python. PIP_ONLY_BINARY refuses a PyPI sdist for that package
# (0.3.36 on PyPI is source-only; the CUDA wheel is the abetlen index).
# PIP_FIND_LINKS holds the opencv-python-headless==99.0.0 placeholder
# (docker/opencv_headless_shim.py). That version satisfies
# albumentations' opencv-python-headless>=4.9.0.80 and is not the published
# wheel, so a git-URL or zip requirements install cannot replace cv2.
# Afterwards PIPFixer.fix_broken reads `pip list` and logs
# "PyTorch is not installed" if any of those three torch packages is absent.
ENV PIP_CONSTRAINT=/opt/comfyfleet/torch-constraints.txt \
    PIP_FIND_LINKS=/opt/comfyfleet/wheels \
    PIP_ONLY_BINARY=llama-cpp-python

# Impact requirements name opencv-python-headless. This filter drops that
# line (and a second opencv-python pin) so RES4LYF's opencv-python wheel
# stays. The placeholder pin above is what a later Manager install is
# allowed to select. The published headless wheel does not match it.
# sam2's pyproject build-system requires torch>=2.5.1. An isolated build
# downloads a second torch (PyPI, not this image's cu130 index) and the
# nvidia wheels into /root/.cache/pip. --no-build-isolation builds sam2
# against the torch already in this venv. That flag does not install build
# dependencies. The same wheel and setuptools>=70.1 pin as the cu124 line
# is installed first so bdist_wheel exists (bookworm ensurepip setuptools
# 66 has no such command; this image installs that pair either way).
# SAM2_BUILD_CUDA=0 skips the CUDA extension. The torch pin is unchanged.
COPY docker/impact_bake.py /opt/comfyfleet/impact_bake.py
COPY docker/opencv_headless_shim.py /opt/comfyfleet/opencv_headless_shim.py

# ComfyUI keeps its .git. Pinned Manager 14b5aaab reads that repo at startup
# (iter_commits for the revision count, plus the commit hash and date). The
# notice route prints "Your ComfyUI isn't git repo." when that date is still
# the 1900 default. A depth-1 ComfyUI repo would report revision 1, so this
# clone stays full and its .git is not removed. Baked custom nodes are
# fetch --depth 1 of the pinned commit, and .git is deleted in this same RUN.
# Manager's own version string is a constant in manager_core.py. It does not
# need its .git to import, and a git pull of that tree would drop the
# trusted-install patch applied later. git stays installed so Manager can
# clone other nodes onto the host custom_nodes mount.
# Pixaroma's workflow-browser examples stay inside that custom node; the
# entrypoint never uses them as the instance default.
# ComfyUI v0.37.4 requirements install comfy-kitchen==0.2.35. On CPython 3.14
# pip selects the cp312-abi3 manylinux wheel (CUDA build). The image does not
# override that with the pure-Python wheel, and it does not rewrite
# annotations: torch 2.13 accepts PEP 585 list[...] custom-op schemas.
RUN git config --global --add safe.directory '*' \
    && git clone https://github.com/Comfy-Org/ComfyUI.git /opt/ComfyUI \
    && git -C /opt/ComfyUI checkout 8ff6dc384ba5c410266b40e137799e049459d4f2 \
    && pin_git() { \
        url="$1"; dest="$2"; rev="$3"; \
        mkdir -p "$dest" \
        && git -C "$dest" init -q \
        && git -C "$dest" remote add origin "$url" \
        && git -C "$dest" fetch --depth 1 origin "$rev" \
        && git -C "$dest" checkout --detach FETCH_HEAD \
        && rm -rf "$dest/.git"; \
    } \
    && pin_git https://github.com/Comfy-Org/ComfyUI-Manager.git /opt/comfyfleet/baked_custom_nodes/ComfyUI-Manager 14b5aaab711ad1f1306d420732a923fb058c44d7 \
    && pin_git https://github.com/pixaroma/ComfyUI-Pixaroma.git /opt/comfyfleet/baked_custom_nodes/ComfyUI-Pixaroma 9259bc49557a92e3fc14796999468c723bd1ecdd \
    && pin_git https://github.com/RecognizeYourPrivilege/ComfyUI-ComfyDock.git /opt/comfyfleet/baked_custom_nodes/ComfyUI-ComfyDock 3a9ff9eba897bf2388d6c1943b01d819ba05a0c6 \
    && pin_git https://github.com/ClownsharkBatwing/RES4LYF.git /opt/comfyfleet/baked_custom_nodes/RES4LYF 3d1d69da69ee47f7647d59e1bd0967e472fccc41 \
    && pin_git https://github.com/ltdrdata/ComfyUI-Impact-Pack.git /opt/comfyfleet/baked_custom_nodes/ComfyUI-Impact-Pack 429d0159ad429e64d2b3916e6e7be9c22d025c3c \
    && pin_git https://github.com/ltdrdata/ComfyUI-Impact-Subpack.git /opt/comfyfleet/baked_custom_nodes/ComfyUI-Impact-Subpack 50c7b71a6a224734cc9b21963c6d1926816a97f1 \
    && mkdir -p /opt/comfyfleet/stock_custom_nodes \
    && cp -a /opt/ComfyUI/custom_nodes/. /opt/comfyfleet/stock_custom_nodes/ \
    && mkdir -p /opt/ComfyUI/models /opt/ComfyUI/input /opt/ComfyUI/output /opt/ComfyUI/temp /opt/comfyfleet/instance \
    && find /opt/comfyfleet/baked_custom_nodes /opt/comfyfleet/stock_custom_nodes -depth -name .git -exec rm -rf {} +

RUN pip install --no-cache-dir -c /opt/comfyfleet/torch-constraints.txt \
        -r /opt/ComfyUI/requirements.txt \
        -r /opt/comfyfleet/baked_custom_nodes/ComfyUI-Manager/requirements.txt \
        -r /opt/comfyfleet/baked_custom_nodes/ComfyUI-Pixaroma/requirements.txt \
        -r /opt/comfyfleet/baked_custom_nodes/ComfyUI-ComfyDock/requirements.txt \
        -r /opt/comfyfleet/baked_custom_nodes/RES4LYF/requirements.txt \
    && python -c 'import sys; assert sys.version_info[:3] == (3, 14, 7), sys.version' \
    && python -c 'import torch; assert "cu130" in torch.__version__, torch.__version__' \
    && python -c 'import numpy; assert numpy.__version__ == "2.3.2", numpy.__version__' \
    && python /opt/comfyfleet/opencv_headless_shim.py --wheel-dir /opt/comfyfleet/wheels \
    && pip install --no-cache-dir --no-index --find-links /opt/comfyfleet/wheels 'opencv-python-headless==99.0.0' \
    && python /opt/comfyfleet/impact_bake.py \
    && pip install --no-cache-dir -c /opt/comfyfleet/torch-constraints.txt wheel 'setuptools>=70.1' \
    && python -c 'from setuptools.command.bdist_wheel import bdist_wheel' \
    && SAM2_BUILD_CUDA=0 pip install --no-cache-dir --no-build-isolation -c /opt/comfyfleet/torch-constraints.txt -r /tmp/impact-requirements.txt \
    && python -c 'import importlib.metadata as metadata; assert metadata.version("torch") == "2.13.0+cu130", metadata.version("torch")' \
    && python -c 'import sam2' \
    && python -c 'import importlib.metadata as metadata; assert metadata.version("opencv-python-headless")=="99.0.0", metadata.version("opencv-python-headless"); assert metadata.version("opencv-python"); import cv2' \
    && touch /opt/comfyfleet/baked_custom_nodes/skip_download_model \
    && COMFYUI_PATH=/opt/ComfyUI COMFYUI_MODEL_PATH=/opt/ComfyUI/models python /opt/comfyfleet/baked_custom_nodes/ComfyUI-Impact-Pack/install.py \
    && COMFYUI_PATH=/opt/ComfyUI COMFYUI_MODEL_PATH=/opt/ComfyUI/models python /opt/comfyfleet/baked_custom_nodes/ComfyUI-Impact-Subpack/install.py \
    && rm -f /opt/comfyfleet/baked_custom_nodes/skip_download_model \
    && python /opt/comfyfleet/impact_bake.py --check-weights \
    && rm -rf /root/.cache/pip

# JoyCaption imports llama_cpp. PyPI 0.3.36 is an sdist, and this image has
# no g++. The abetlen cu130 wheel is py3-none manylinux, so CPython 3.14.7
# can install it. CXX, CC, and CMAKE_ARGS are cleared so pip cannot take a
# compile path. --only-binary=:all: fails the build if the wheel is absent.
# libggml-cuda needs libcuda.so.1 (the host driver, not in this image).
# docker/llama_import_smoke.py imports Llama with a temporary gcc stub and
# deletes that stub in the same step. It is not on the runtime linker path.
# That gcc -shared link needs crti.o from libc6-dev, installed above.
COPY docker/llama_import_smoke.py /opt/comfyfleet/llama_import_smoke.py
RUN unset CXX CC CMAKE_ARGS \
    && /opt/venv/bin/python -m pip install --no-cache-dir 'llama-cpp-python==0.3.36' \
        --only-binary=:all: \
        --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu130 \
    && /opt/venv/bin/python /opt/comfyfleet/llama_import_smoke.py \
    && rm -rf /root/.cache/pip

COPY docker/PINS.txt /opt/comfyfleet/PINS.txt
COPY docker/verify_image_pins.py /opt/comfyfleet/verify_image_pins.py

# Requirements install comfy-kitchen==0.2.35. The image build does not import
# the top-level kitchen package: that import loads the Triton backend, and
# Triton raises "0 active drivers" when the build has no NVIDIA driver.
# GPU selection at runtime does not provide a driver during docker build.
# On a GPU host the same import reaches triton/runtime/build.py and compiles
# cuda_utils (driver.c, which includes Python.h). Without gcc that step
# raises "Failed to find C compiler. Please specify via CC environment
# variable." gcc is installed above. Python.h is the 3.14 header from the
# base image. Triton ships cuda.h in its wheel; this image does not install
# nvcc or g++.
RUN python /opt/comfyfleet/verify_image_pins.py
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
COPY docker/seed_impact_config.py /opt/comfyfleet/seed_impact_config.py
COPY docker/entrypoint.sh /opt/comfyfleet/entrypoint.sh
RUN python /opt/comfyfleet/patch_manager_trusted_install.py \
    && chmod 0755 /opt/comfyfleet/entrypoint.sh

WORKDIR /opt/ComfyUI
EXPOSE 8188
ENTRYPOINT ["/opt/comfyfleet/entrypoint.sh"]
