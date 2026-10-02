#!/usr/bin/env bash
# Pull ComfyFleet images from GHCR and start the manager.
# The host can be Arch. The images are linux/amd64 Debian bookworm-slim.
# This does not build torch. A local image rebuild is optional (see README).
# This script starts the manager only. Instance containers are created later
# with docker create --shm-size 8g (Compose form: shm_size: '8g').
# Local tags: comfyfleet:cu130 (default, host CUDA 13.0) and comfyfleet:cu124
# (host CUDA 12.4). --cuda-tag both pulls and tags each line. The manager
# default stays cu130 unless this install already selected the cu124 image.
# Changing an instance's line is a recreate, not a restart.
set -euo pipefail

REGISTRY="${COMFYFLEET_REGISTRY:-ghcr.io}"
OWNER="$(printf '%s' "${COMFYFLEET_GHCR_OWNER:-recognizeyourprivilege}" | tr '[:upper:]' '[:lower:]')"
INSTANCE_REPO="${REGISTRY}/${OWNER}/comfyfleet"
MANAGER_REPO="${REGISTRY}/${OWNER}/comfyfleet-manager"
LOCAL_MANAGER_TAG="comfyfleet-manager:latest"
# Published primary-tag digests. Aliases (:latest → cu130, :phase1 → cu124)
# are the same images and are not pinned separately.
CU130_PIN="sha256:2032db1691256959cd619108376a7227f68d54e0135073dd941f1dc8d41d032e"
CU124_PIN="sha256:d2a5e55fcc348c0550e3d2e918bf36579a5a092d498070d63391cebffad71b76"
MANAGER_PIN="sha256:a26f075d8b31c44cbd080de0557ebe29a6617c2f848b5b244f461b6ac42b2cb8"
CUDA_TAG=""
CUDA_TAG_EXPLICIT=0
NAME="${COMFYFLEET_CONTAINER_NAME:-comfyfleet-manager}"
PORT="${COMFYFLEET_PUBLISH_PORT:-9100}"
MODE="run"
PULL_ONLY=0

usage() {
  cat <<EOF
Usage: install.sh [--cuda-tag cu130|cu124|both] [--compose] [--pull-only] [--public-host HOST] [--password PASS] [--port PORT] [--name NAME]

Pull prebuilt images and start the ComfyFleet manager. Does not docker-build torch.

  COMFYFLEET_PASSWORD       required unless --pull-only. Prefer the environment
                            (a --password argument is visible in the process list).
  COMFYFLEET_PUBLIC_HOST    LAN hostname or IP browsers use. Required unless --pull-only.
  COMFYFLEET_CUDA_TAG       cu130, cu124, or both. Same as --cuda-tag. The flag wins.
                            both pulls each instance line. The manager still
                            gets cu130 unless COMFYFLEET_INSTANCE_IMAGE is
                            already a cu124 ref.

CUDA line (pick the one that matches the host NVIDIA driver major):
  cu130   host driver CUDA 13.0. Default when this prompt is skipped
          (stdin is not a terminal, or the choice is left empty).
          Does not pull cu124.
          ${INSTANCE_REPO}:cu130@${CU130_PIN}
  cu124   host driver CUDA 12.4.
          ${INSTANCE_REPO}:cu124@${CU124_PIN}
          A wrong line can fail when an instance starts.
  both    pull and tag cu130 and cu124. Manager COMFYFLEET_CUDA_TAG stays
          cu130 unless COMFYFLEET_INSTANCE_IMAGE is already a cu124 ref.
          Create can pick either line because both images are local.

Default images when the line is cu130:
  ${INSTANCE_REPO}:cu130@${CU130_PIN}
  ${MANAGER_REPO}:latest@${MANAGER_PIN}

  COMFYFLEET_INSTANCE_IMAGE / COMFYFLEET_MANAGER_IMAGE replace those refs.
  COMFYFLEET_INSTANCE_DIGEST / COMFYFLEET_MANAGER_DIGEST (sha256:...) pull a different digest.

The instance image is tagged comfyfleet:<cuda tag>. The manager image is tagged
${LOCAL_MANAGER_TAG}. The running manager gets COMFYFLEET_INSTANCE_IMAGE and
COMFYFLEET_CUDA_TAG set to the chosen line so create finds that image on the
host engine. both tags each line (comfyfleet:cu130 and comfyfleet:latest,
comfyfleet:cu124 and comfyfleet:phase1) and sets the manager default to cu130
unless this install already selected the cu124 image. Create can still pick
the other line; that image must be pulled too (both does that pull). Changing
an existing instance's line is a recreate, not a restart.

  --cuda-tag    cu130 (host CUDA 13.0, default), cu124 (host CUDA 12.4), or both
  --compose     docker compose up -d instead of docker run (still pulls both images)
  --pull-only   pull and tag, do not start the manager
  --port        host port published to container 9100 (docker run only; default 9100)
  --name        manager container name (default ${NAME})

Examples:
  COMFYFLEET_PASSWORD='...' COMFYFLEET_PUBLIC_HOST=192.168.1.20 ./install.sh
  COMFYFLEET_PASSWORD='...' COMFYFLEET_PUBLIC_HOST=192.168.1.20 ./install.sh --cuda-tag cu124
  COMFYFLEET_PASSWORD='...' COMFYFLEET_PUBLIC_HOST=192.168.1.20 ./install.sh --cuda-tag both
  COMFYFLEET_PASSWORD='...' COMFYFLEET_PUBLIC_HOST=192.168.1.20 ./install.sh --compose
  curl -fsSL https://raw.githubusercontent.com/RecognizeYourPrivilege/ComfyFleet/main/install.sh \\
    | COMFYFLEET_PASSWORD='...' COMFYFLEET_PUBLIC_HOST=192.168.1.20 bash
  curl -fsSL https://raw.githubusercontent.com/RecognizeYourPrivilege/ComfyFleet/main/install.sh \\
    | COMFYFLEET_PASSWORD='...' COMFYFLEET_PUBLIC_HOST=192.168.1.20 bash -s -- --compose
EOF
}

die() {
  echo "comfyfleet: $*" >&2
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --cuda-tag)
      [[ $# -ge 2 ]] || die "--cuda-tag needs cu130 or cu124, or both."
      CUDA_TAG=$2
      CUDA_TAG_EXPLICIT=1
      shift 2
      ;;
    --compose)
      MODE="compose"
      shift
      ;;
    --pull-only)
      PULL_ONLY=1
      shift
      ;;
    --public-host)
      [[ $# -ge 2 ]] || die "--public-host needs a value."
      COMFYFLEET_PUBLIC_HOST=$2
      shift 2
      ;;
    --password)
      [[ $# -ge 2 ]] || die "--password needs a value."
      COMFYFLEET_PASSWORD=$2
      shift 2
      ;;
    --port)
      [[ $# -ge 2 ]] || die "--port needs a value."
      PORT=$2
      shift 2
      ;;
    --name)
      [[ $# -ge 2 ]] || die "--name needs a value."
      NAME=$2
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      usage >&2
      die "unknown argument: $1"
      ;;
  esac
done

if [[ "${MODE}" == "compose" && "${PORT}" != "9100" ]]; then
  die "compose.yaml publishes 9100:9100. Omit --port or use docker run."
fi
if [[ "${MODE}" == "compose" && "${NAME}" != "comfyfleet-manager" ]]; then
  die "compose uses container name comfyfleet-manager. Omit --name or use docker run."
fi

digest_ref() {
  local repo="$1"
  local digest="$2"
  if [[ "${digest}" != sha256:* ]]; then
    digest="sha256:${digest}"
  fi
  printf '%s@%s\n' "${repo}" "${digest}"
}

# A ref that still carries the published cu130 digest is that line, even when
# the tag says phase1. phase1 without that digest is the cu124 alias.
image_line() {
  local ref="$1"
  if [[ -z "${ref}" ]]; then
    printf '%s\n' ""
    return
  fi
  if [[ "${ref}" == *"${CU130_PIN}"* ]]; then
    printf '%s\n' "cu130"
    return
  fi
  case "${ref}" in
    *:cu124|*:cu124@*|*:phase1|*:phase1@*) printf '%s\n' "cu124" ;;
    *:cu130|*:cu130@*|*:latest|*:latest@*) printf '%s\n' "cu130" ;;
    *) printf '%s\n' "" ;;
  esac
}

default_line_ref() {
  local tag="$1"
  if [[ "${tag}" == "cu130" ]]; then
    printf '%s\n' "${INSTANCE_REPO}:cu130@${CU130_PIN}"
  elif [[ -n "${CU124_PIN}" ]]; then
    printf '%s\n' "${INSTANCE_REPO}:cu124@${CU124_PIN}"
  else
    printf '%s\n' "${INSTANCE_REPO}:cu124"
  fi
}

choose_cuda_tag() {
  if [[ -z "${CUDA_TAG}" && -n "${COMFYFLEET_CUDA_TAG:-}" ]]; then
    CUDA_TAG="${COMFYFLEET_CUDA_TAG}"
    CUDA_TAG_EXPLICIT=1
  fi
  if [[ -z "${CUDA_TAG}" ]]; then
    if [[ -t 0 ]]; then
      echo "Instance CUDA line (match the host NVIDIA driver major):"
      echo "  cu130  host driver CUDA 13.0 (default)"
      echo "  cu124  host driver CUDA 12.4"
      echo "  both   pull cu130 and cu124 (manager default cu130)"
      read -r -p "CUDA tag [cu130]: " CUDA_TAG
    fi
    if [[ -z "${CUDA_TAG}" ]]; then
      CUDA_TAG="cu130"
    fi
  fi
  if [[ "${CUDA_TAG}" != "cu130" && "${CUDA_TAG}" != "cu124" && "${CUDA_TAG}" != "both" ]]; then
    die "CUDA tag must be cu130 or cu124, or both (host driver CUDA 13.0 or CUDA 12.4; both pulls each line), got ${CUDA_TAG}."
  fi
}

choose_cuda_tag

# both pulls each line. The manager default stays cu130 unless this install
# already pointed COMFYFLEET_INSTANCE_IMAGE at the cu124 line. A digest pin
# stays on cu130, the same default line install uses when the prompt is skipped.
MANAGER_CUDA_TAG="${CUDA_TAG}"
if [[ "${CUDA_TAG}" == "both" ]]; then
  MANAGER_CUDA_TAG="cu130"
  if [[ -z "${COMFYFLEET_INSTANCE_DIGEST:-}" && "$(image_line "${COMFYFLEET_INSTANCE_IMAGE:-}")" == "cu124" ]]; then
    MANAGER_CUDA_TAG="cu124"
  fi
fi

if [[ -n "${COMFYFLEET_INSTANCE_DIGEST:-}" ]]; then
  INSTANCE_REF="$(digest_ref "${INSTANCE_REPO}" "${COMFYFLEET_INSTANCE_DIGEST}")"
elif [[ -n "${COMFYFLEET_INSTANCE_IMAGE:-}" && "${CUDA_TAG_EXPLICIT}" -eq 0 ]]; then
  INSTANCE_REF="${COMFYFLEET_INSTANCE_IMAGE}"
elif [[ -n "${COMFYFLEET_INSTANCE_IMAGE:-}" && "${COMFYFLEET_INSTANCE_IMAGE}" == *":${MANAGER_CUDA_TAG}"* ]]; then
  INSTANCE_REF="${COMFYFLEET_INSTANCE_IMAGE}"
elif [[ -n "${COMFYFLEET_INSTANCE_IMAGE:-}" && "$(image_line "${COMFYFLEET_INSTANCE_IMAGE}")" == "${MANAGER_CUDA_TAG}" ]]; then
  INSTANCE_REF="${COMFYFLEET_INSTANCE_IMAGE}"
elif [[ "${MANAGER_CUDA_TAG}" == "cu130" ]]; then
  INSTANCE_REF="${INSTANCE_REPO}:cu130@${CU130_PIN}"
elif [[ -n "${CU124_PIN}" ]]; then
  INSTANCE_REF="${INSTANCE_REPO}:cu124@${CU124_PIN}"
else
  INSTANCE_REF="${INSTANCE_REPO}:cu124"
fi

if [[ -n "${COMFYFLEET_MANAGER_DIGEST:-}" ]]; then
  MANAGER_REF="$(digest_ref "${MANAGER_REPO}" "${COMFYFLEET_MANAGER_DIGEST}")"
elif [[ -n "${COMFYFLEET_MANAGER_IMAGE:-}" ]]; then
  MANAGER_REF="${COMFYFLEET_MANAGER_IMAGE}"
else
  MANAGER_REF="${MANAGER_REPO}:latest@${MANAGER_PIN}"
fi

# Create reads this name on the host engine. install always sets it.
# both never reaches the manager: COMFYFLEET_CUDA_TAG is cu130 or cu124.
LOCAL_INSTANCE_TAG="comfyfleet:${MANAGER_CUDA_TAG}"
export COMFYFLEET_CUDA_TAG="${MANAGER_CUDA_TAG}"
export COMFYFLEET_INSTANCE_IMAGE="${INSTANCE_REF}"
export COMFYFLEET_MANAGER_IMAGE="${MANAGER_REF}"

require_docker() {
  if ! command -v docker >/dev/null 2>&1; then
    die "docker is not on PATH."
  fi
  if ! docker info >/dev/null 2>&1; then
    die "docker info failed. Start the Docker daemon and retry."
  fi
}

pull_ref() {
  local ref="$1"
  if ! docker pull "${ref}"; then
    echo "comfyfleet: docker pull failed for ${ref}." >&2
    echo "comfyfleet: images are pushed by .github/workflows/publish-images.yml on main." >&2
    echo "comfyfleet: until that run succeeds and the GHCR package is public, pull fails (denied or not found)." >&2
    echo "comfyfleet: a local rebuild is optional. See the Development section of the README and scripts/publish-images.sh." >&2
    return 1
  fi
}

tag_ref() {
  local ref="$1"
  local alias="$2"
  if [[ "${ref}" == "${alias}" ]]; then
    return 0
  fi
  docker tag "${ref}" "${alias}"
}

pull_tag_line() {
  local tag="$1"
  local ref="$2"
  pull_ref "${ref}"
  tag_ref "${ref}" "comfyfleet:${tag}"
  if [[ "${tag}" == "cu130" ]]; then
    tag_ref "${ref}" "comfyfleet:latest"
  else
    tag_ref "${ref}" "comfyfleet:phase1"
  fi
}

pull_and_tag_both() {
  local cu130_ref cu124_ref
  if [[ "${MANAGER_CUDA_TAG}" == "cu130" ]]; then
    cu130_ref="${INSTANCE_REF}"
    cu124_ref="$(default_line_ref cu124)"
  else
    cu124_ref="${INSTANCE_REF}"
    cu130_ref="$(default_line_ref cu130)"
  fi
  pull_tag_line cu130 "${cu130_ref}"
  pull_tag_line cu124 "${cu124_ref}"
  pull_ref "${MANAGER_REF}"
  tag_ref "${MANAGER_REF}" "${LOCAL_MANAGER_TAG}"
  if [[ -n "${COMFYFLEET_INSTANCE_DIGEST:-}" ]]; then
    tag_ref "${INSTANCE_REF}" "${INSTANCE_REPO}:${MANAGER_CUDA_TAG}"
  fi
  if [[ -n "${COMFYFLEET_MANAGER_DIGEST:-}" ]]; then
    tag_ref "${MANAGER_REF}" "${MANAGER_REPO}:latest"
  fi
  echo "comfyfleet: instance ${cu130_ref} (also comfyfleet:cu130)"
  echo "comfyfleet: instance ${cu124_ref} (also comfyfleet:cu124)"
  echo "comfyfleet: manager ${MANAGER_REF} (also ${LOCAL_MANAGER_TAG})"
  echo "comfyfleet: manager default ${COMFYFLEET_CUDA_TAG}"
}

pull_and_tag() {
  if [[ "${CUDA_TAG}" == "both" ]]; then
    pull_and_tag_both
    return
  fi
  pull_ref "${INSTANCE_REF}"
  pull_ref "${MANAGER_REF}"
  tag_ref "${INSTANCE_REF}" "${LOCAL_INSTANCE_TAG}"
  tag_ref "${MANAGER_REF}" "${LOCAL_MANAGER_TAG}"
  if [[ "${CUDA_TAG}" == "cu130" ]]; then
    tag_ref "${INSTANCE_REF}" "comfyfleet:latest"
  else
    tag_ref "${INSTANCE_REF}" "comfyfleet:phase1"
  fi
  if [[ -n "${COMFYFLEET_INSTANCE_DIGEST:-}" ]]; then
    tag_ref "${INSTANCE_REF}" "${INSTANCE_REPO}:${CUDA_TAG}"
  fi
  if [[ -n "${COMFYFLEET_MANAGER_DIGEST:-}" ]]; then
    tag_ref "${MANAGER_REF}" "${MANAGER_REPO}:latest"
  fi
  echo "comfyfleet: instance ${INSTANCE_REF} (also ${LOCAL_INSTANCE_TAG})"
  echo "comfyfleet: manager ${MANAGER_REF} (also ${LOCAL_MANAGER_TAG})"
}

require_password() {
  if [[ -z "${COMFYFLEET_PASSWORD:-}" || -z "${COMFYFLEET_PASSWORD//[[:space:]]/}" ]]; then
    if [[ -t 0 ]]; then
      read -r -s -p "COMFYFLEET_PASSWORD: " COMFYFLEET_PASSWORD
      echo
    fi
  fi
  if [[ -z "${COMFYFLEET_PASSWORD:-}" || -z "${COMFYFLEET_PASSWORD//[[:space:]]/}" ]]; then
    die "COMFYFLEET_PASSWORD is required and must be non-empty. There is no open-LAN fallback."
  fi
  if [[ "${COMFYFLEET_PASSWORD}" == *$'\n'* ]]; then
    die "COMFYFLEET_PASSWORD must be a single line."
  fi
  export COMFYFLEET_PASSWORD
}

require_public_host() {
  if [[ -z "${COMFYFLEET_PUBLIC_HOST:-}" || -z "${COMFYFLEET_PUBLIC_HOST//[[:space:]]/}" ]]; then
    if [[ -t 0 ]]; then
      read -r -p "COMFYFLEET_PUBLIC_HOST (LAN address browsers use): " COMFYFLEET_PUBLIC_HOST
    fi
  fi
  local host="${COMFYFLEET_PUBLIC_HOST:-}"
  if [[ -z "${host//[[:space:]]/}" ]]; then
    die "COMFYFLEET_PUBLIC_HOST is required. Example: 192.168.1.20"
  fi
  if ! public_host_ok "${host}"; then
    die "COMFYFLEET_PUBLIC_HOST is not a usable hostname or IP (${host})."
  fi
  export COMFYFLEET_PUBLIC_HOST="${host}"
}

public_host_ok() {
  local host="$1"
  [[ -n "${host}" && ${#host} -le 253 ]] || return 1
  [[ "${host}" != "0.0.0.0" && "${host}" != "::" && "${host}" != "[::]" && "${host}" != "*" ]] || return 1
  [[ "${host}" != *[[:space:]]* && "${host}" != */* && "${host}" != *\\* ]] || return 1
  [[ "${host}" != *@* && "${host}" != *'#'* && "${host}" != *'?'* ]] || return 1
  [[ "${host}" != *'"'* && "${host}" != *\'* ]] || return 1
  return 0
}

remove_manager() {
  if docker container inspect "${NAME}" >/dev/null 2>&1; then
    echo "comfyfleet: replacing container ${NAME}. Workflow instances are left in place."
    docker rm -f "${NAME}" >/dev/null
  fi
}

script_dir() {
  if [[ -n "${BASH_SOURCE[0]:-}" && -f "${BASH_SOURCE[0]}" ]]; then
    (cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
  fi
}

write_compose() {
  local dest="$1"
  cat >"${dest}" <<'EOF'
# Generated by install.sh. Same contract as compose.yaml in the repo.
# Instance containers are not this service. The manager creates them with
# docker create --shm-size 8g (Compose form: shm_size: '8g').
services:
  manager:
    image: ${COMFYFLEET_MANAGER_IMAGE:?}
    container_name: comfyfleet-manager
    ports:
      - "9100:9100"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - /home:/home
    environment:
      COMFYFLEET_PASSWORD: ${COMFYFLEET_PASSWORD:?Set COMFYFLEET_PASSWORD}
      COMFYFLEET_PUBLIC_HOST: ${COMFYFLEET_PUBLIC_HOST:?Set COMFYFLEET_PUBLIC_HOST}
      COMFYFLEET_INSTANCE_IMAGE: ${COMFYFLEET_INSTANCE_IMAGE:?}
      COMFYFLEET_CUDA_TAG: ${COMFYFLEET_CUDA_TAG:?}
      COMFYFLEET_BIND_HOST: 0.0.0.0
      COMFYFLEET_BIND_PORT: "9100"
    gpus: all
    restart: unless-stopped
EOF
}

start_run() {
  if [[ ! -S /var/run/docker.sock ]]; then
    echo "comfyfleet: /var/run/docker.sock is missing. The UI can still start; create needs the host socket." >&2
  fi
  remove_manager
  if ! docker run -d --name "${NAME}" \
    --restart unless-stopped \
    --gpus all \
    -p "${PORT}:9100" \
    -v /var/run/docker.sock:/var/run/docker.sock \
    -v /home:/home \
    -e "COMFYFLEET_PASSWORD=${COMFYFLEET_PASSWORD}" \
    -e "COMFYFLEET_PUBLIC_HOST=${COMFYFLEET_PUBLIC_HOST}" \
    -e "COMFYFLEET_INSTANCE_IMAGE=${COMFYFLEET_INSTANCE_IMAGE}" \
    -e "COMFYFLEET_CUDA_TAG=${COMFYFLEET_CUDA_TAG}" \
    -e COMFYFLEET_BIND_HOST=0.0.0.0 \
    -e COMFYFLEET_BIND_PORT=9100 \
    "${MANAGER_REF}"
  then
    echo "comfyfleet: docker run failed. --gpus all needs the NVIDIA Container Toolkit, and nvidia-smi must work on the host." >&2
    exit 1
  fi
  echo "comfyfleet: manager ${NAME} is starting."
  echo "comfyfleet: open http://${COMFYFLEET_PUBLIC_HOST}:${PORT}/"
}

start_compose() {
  if ! docker compose version >/dev/null 2>&1; then
    die "docker compose is not available. Re-run without --compose."
  fi
  local dir compose_file generated=0
  dir="$(script_dir || true)"
  if [[ -n "${dir}" && -f "${dir}/compose.yaml" ]]; then
    compose_file="${dir}/compose.yaml"
  else
    compose_file="$(mktemp)"
    generated=1
    write_compose "${compose_file}"
  fi
  remove_manager
  if ! docker compose -f "${compose_file}" up -d; then
    if [[ "${generated}" -eq 1 ]]; then
      rm -f "${compose_file}"
    fi
    echo "comfyfleet: docker compose up failed. gpus: all needs the NVIDIA Container Toolkit." >&2
    exit 1
  fi
  if [[ "${generated}" -eq 1 ]]; then
    rm -f "${compose_file}"
  fi
  echo "comfyfleet: manager is starting (compose, container ${NAME})."
  echo "comfyfleet: open http://${COMFYFLEET_PUBLIC_HOST}:9100/"
}

if [[ "${PULL_ONLY}" -eq 0 ]]; then
  require_password
  require_public_host
fi

require_docker
pull_and_tag

if [[ "${PULL_ONLY}" -eq 1 ]]; then
  echo "comfyfleet: pull-only done. Manager was not started."
  exit 0
fi

if [[ "${MODE}" == "compose" ]]; then
  start_compose
else
  start_run
fi
