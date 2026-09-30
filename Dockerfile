# ComfyFleet Phase 1 runtime.
# Slim Debian bookworm + NVIDIA CUDA 12.4 runtime libraries (not the devel toolkit).
# No workflow JSON is copied into this image. The operator file is bind-mounted
# at /opt/comfyfleet/instance/default_workflow.json and the entrypoint refuses
# to start when that file is missing.
#
# Pins are duplicated in docker/PINS.txt and the README.

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
        git \
        python3 \
        python3-venv \
        python3-pip \
    && ln -sfn /usr/local/cuda-12.4 /usr/local/cuda \
    && apt-mark hold cuda-libraries-12-4 cuda-cudart-12-4 libcudnn9-cuda-12 \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -c 'import sys; assert sys.version_info[:2] == (3, 11), sys.version'

RUN mkdir -p /opt/comfyfleet \
    && python3 -m venv /opt/venv \
    && pip install --no-cache-dir \
        torch==2.6.0+cu124 \
        torchvision==0.21.0+cu124 \
        --index-url https://download.pytorch.org/whl/cu124 \
        --extra-index-url https://pypi.org/simple \
    && python -c 'import torch; v = torch.__version__; assert v.startswith("2.6.0") and "cu124" in v, v' \
    && printf '%s\n' 'torch==2.6.0+cu124' 'torchvision==0.21.0+cu124' > /opt/comfyfleet/torch-constraints.txt

# Pure-Python comfy-kitchen (eager backend). The manylinux wheel targets CUDA 13.
RUN pip install --no-cache-dir \
    https://files.pythonhosted.org/packages/38/23/a6787aac01d7c28ae3cb07579ba839297a35e6fad66baac096916246cc7f/comfy_kitchen-0.2.36-py3-none-any.whl

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
    && mkdir -p /opt/comfyfleet/stock_custom_nodes \
    && cp -a /opt/ComfyUI/custom_nodes/. /opt/comfyfleet/stock_custom_nodes/ \
    && mkdir -p /opt/ComfyUI/models /opt/ComfyUI/input /opt/ComfyUI/output /opt/ComfyUI/temp /opt/comfyfleet/instance

RUN pip install --no-cache-dir -c /opt/comfyfleet/torch-constraints.txt \
        -r /opt/ComfyUI/requirements.txt \
        -r /opt/comfyfleet/baked_custom_nodes/ComfyUI-Manager/requirements.txt \
        -r /opt/comfyfleet/baked_custom_nodes/ComfyUI-Pixaroma/requirements.txt \
        -r /opt/comfyfleet/baked_custom_nodes/ComfyUI-ComfyDock/requirements.txt \
    && python -c 'import torch; assert "cu124" in torch.__version__, torch.__version__'

COPY docker/PINS.txt /opt/comfyfleet/PINS.txt
COPY docker/comfyfleet_default_workflow /opt/comfyfleet/baked_custom_nodes/comfyfleet_default_workflow
COPY docker/entrypoint.sh /opt/comfyfleet/entrypoint.sh

RUN chmod 0755 /opt/comfyfleet/entrypoint.sh

WORKDIR /opt/ComfyUI
EXPOSE 8188
ENTRYPOINT ["/opt/comfyfleet/entrypoint.sh"]
