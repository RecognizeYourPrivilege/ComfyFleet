#!/bin/bash
# ComfyFleet container entrypoint.
# Loads the operator-supplied workflow bind-mounted at
# /opt/comfyfleet/instance/default_workflow.json on every start.
# This image does not contain a stock, QualitySafe, or sample fallback workflow.
set -euo pipefail

WF="${COMFYFLEET_WORKFLOW_PATH:-/opt/comfyfleet/instance/default_workflow.json}"
BAKED="/opt/comfyfleet/baked_custom_nodes"
NODES="/opt/ComfyUI/custom_nodes"
STOCK="/opt/comfyfleet/stock_custom_nodes"
BOOT_ID_FILE="/tmp/comfyfleet-boot-id"

if [[ ! -f "$WF" ]]; then
  echo "comfyfleet: operator workflow missing at ${WF}." >&2
  echo "comfyfleet: refusing to start. This image has no stock or sample default workflow." >&2
  echo "comfyfleet: create the instance with: comfyfleet create --workflow /path/to/flow.json" >&2
  exit 1
fi

/opt/venv/bin/python - "$WF" <<'PY'
import json
import sys

path = sys.argv[1]
try:
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
except Exception as exc:
    print(f"comfyfleet: operator workflow is not valid JSON ({path}): {exc}", file=sys.stderr)
    sys.exit(1)
if not isinstance(data, dict):
    print(
        f"comfyfleet: operator workflow must be a JSON object ({path}). No substitute workflow will be loaded.",
        file=sys.stderr,
    )
    sys.exit(1)
PY

# New id on every container start so the UI reloads the operator file after reboot.
/opt/venv/bin/python - "$BOOT_ID_FILE" <<'PY'
import sys
import time

with open(sys.argv[1], "w", encoding="utf-8") as handle:
    handle.write(str(time.time_ns()))
PY

mkdir -p "$NODES"
if [[ -d "$STOCK" ]]; then
  shopt -s nullglob
  for item in "$STOCK"/*; do
    base="$(basename "$item")"
    if [[ ! -e "${NODES}/${base}" ]]; then
      cp -a "$item" "${NODES}/${base}"
    fi
  done
  shopt -u nullglob
fi

link_baked() {
  local name="$1"
  local src="${BAKED}/${name}"
  local dest="${NODES}/${name}"
  if [[ ! -e "$src" ]]; then
    echo "comfyfleet: baked custom node missing: ${src}" >&2
    exit 1
  fi
  if [[ -L "$dest" || ! -e "$dest" ]]; then
    ln -sfn "$src" "$dest"
  else
    echo "comfyfleet: keeping existing ${dest} (not replacing with the baked node)" >&2
  fi
}

link_baked "ComfyUI-Manager"
link_baked "ComfyUI-Pixaroma"
link_baked "ComfyUI-ComfyDock"
link_baked "comfyfleet_default_workflow"

models="/opt/ComfyUI/models"
mkdir -p \
  "${models}/checkpoints" \
  "${models}/configs" \
  "${models}/loras" \
  "${models}/vae" \
  "${models}/text_encoders" \
  "${models}/clip" \
  "${models}/diffusion_models" \
  "${models}/unet" \
  "${models}/clip_vision" \
  "${models}/style_models" \
  "${models}/embeddings" \
  "${models}/diffusers" \
  "${models}/vae_approx" \
  "${models}/controlnet" \
  "${models}/t2i_adapter" \
  "${models}/gligen" \
  "${models}/upscale_models" \
  "${models}/latent_upscale_models" \
  "${models}/hypernetworks" \
  "${models}/photomaker" \
  "${models}/classifiers" \
  "${models}/model_patches" \
  "${models}/audio_encoders" \
  "${models}/background_removal" \
  "${models}/frame_interpolation" \
  "${models}/geometry_estimation" \
  "${models}/optical_flow" \
  "${models}/detection"

echo "comfyfleet: loading operator workflow ${WF}"
# Locked first. Extra arguments come from `docker create` (the instance
# launch flags). A pasted --listen or --port is dropped so it cannot
# replace this pair. The published host port is Docker's -p mapping;
# ComfyUI itself always listens on 8188 inside the container.
comfy_args=(--listen 0.0.0.0 --port 8188)
while [[ $# -gt 0 ]]; do
  case "$1" in
    --listen|--port)
      shift
      if [[ $# -gt 0 && "$1" != --* ]]; then
        shift
      fi
      ;;
    --listen=*|--port=*)
      shift
      ;;
    *)
      comfy_args+=("$1")
      shift
      ;;
  esac
done
echo "comfyfleet: ComfyUI ${comfy_args[*]}"
cd /opt/ComfyUI
exec /opt/venv/bin/python main.py "${comfy_args[@]}"
