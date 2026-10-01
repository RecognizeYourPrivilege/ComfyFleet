#!/usr/bin/env bash
# Pull ComfyFleet images from GHCR and start the manager.
# The host can be Arch. The images are linux/amd64 Debian bookworm-slim.
# This does not build torch. A local image rebuild is optional (see README).
set -euo pipefail

REGISTRY="${COMFYFLEET_REGISTRY:-ghcr.io}"
OWNER="$(printf '%s' "${COMFYFLEET_GHCR_OWNER:-recognizeyourprivilege}" | tr '[:upper:]' '[:lower:]')"
INSTANCE_REPO="${REGISTRY}/${OWNER}/comfyfleet"
MANAGER_REPO="${REGISTRY}/${OWNER}/comfyfleet-manager"
LOCAL_INSTANCE_TAG="comfyfleet:phase1"
LOCAL_MANAGER_TAG="comfyfleet-manager:latest"
INSTANCE_PIN="sha256:6441c6340c7330198fd8a492b763b6c19874e7091e8ce310b3b6abfda54454ba"
MANAGER_PIN="sha256:a4204564c60cf3afc40db17b34a268cf2c1c2f8e685b4601bc1c6e4dedbc713f"
NAME="${COMFYFLEET_CONTAINER_NAME:-comfyfleet-manager}"
PORT="${COMFYFLEET_PUBLISH_PORT:-9100}"
MODE="run"
PULL_ONLY=0

usage() {
  cat <<EOF
Usage: install.sh [--compose] [--pull-only] [--public-host HOST] [--password PASS] [--port PORT] [--name NAME]

Pull prebuilt images and start the ComfyFleet manager. Does not docker-build torch.

  COMFYFLEET_PASSWORD       required unless --pull-only. Prefer the environment
                            (a --password argument is visible in the process list).
  COMFYFLEET_PUBLIC_HOST    LAN hostname or IP browsers use. Required unless --pull-only.

Default images:
  ${INSTANCE_REPO}:phase1@${INSTANCE_PIN}
  ${MANAGER_REPO}:latest@${MANAGER_PIN}

  COMFYFLEET_INSTANCE_IMAGE / COMFYFLEET_MANAGER_IMAGE replace those refs.
  COMFYFLEET_INSTANCE_DIGEST / COMFYFLEET_MANAGER_DIGEST (sha256:...) pull a different digest.

The instance image is tagged ${LOCAL_INSTANCE_TAG}. The manager image is tagged
${LOCAL_MANAGER_TAG}. The running manager gets COMFYFLEET_INSTANCE_IMAGE set to
the pulled ref so create finds that image on the host engine.

  --compose     docker compose up -d instead of docker run (still pulls both images)
  --pull-only   pull and tag, do not start the manager
  --port        host port published to container 9100 (docker run only; default 9100)
  --name        manager container name (default ${NAME})

Examples:
  COMFYFLEET_PASSWORD='...' COMFYFLEET_PUBLIC_HOST=192.168.1.20 ./install.sh
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

if [[ -n "${COMFYFLEET_INSTANCE_DIGEST:-}" ]]; then
  INSTANCE_REF="$(digest_ref "${INSTANCE_REPO}" "${COMFYFLEET_INSTANCE_DIGEST}")"
elif [[ -n "${COMFYFLEET_INSTANCE_IMAGE:-}" ]]; then
  INSTANCE_REF="${COMFYFLEET_INSTANCE_IMAGE}"
else
  INSTANCE_REF="${INSTANCE_REPO}:phase1@${INSTANCE_PIN}"
fi

if [[ -n "${COMFYFLEET_MANAGER_DIGEST:-}" ]]; then
  MANAGER_REF="$(digest_ref "${MANAGER_REPO}" "${COMFYFLEET_MANAGER_DIGEST}")"
elif [[ -n "${COMFYFLEET_MANAGER_IMAGE:-}" ]]; then
  MANAGER_REF="${COMFYFLEET_MANAGER_IMAGE}"
else
  MANAGER_REF="${MANAGER_REPO}:latest@${MANAGER_PIN}"
fi

# Create reads this name on the host engine. install always sets it.
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

pull_and_tag() {
  pull_ref "${INSTANCE_REF}"
  pull_ref "${MANAGER_REF}"
  tag_ref "${INSTANCE_REF}" "${LOCAL_INSTANCE_TAG}"
  tag_ref "${MANAGER_REF}" "${LOCAL_MANAGER_TAG}"
  if [[ -n "${COMFYFLEET_INSTANCE_DIGEST:-}" ]]; then
    tag_ref "${INSTANCE_REF}" "${INSTANCE_REPO}:phase1"
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
