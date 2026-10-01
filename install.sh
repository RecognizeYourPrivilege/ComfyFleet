#!/usr/bin/env bash
# Install ComfyFleet and start the manager.
# The host can be Arch or another Linux. The images are linux/amd64 Debian bookworm-slim.
# Prompts are read from /dev/tty, so `./install.sh` and `curl | bash` both work.
# Enter downloads the pinned GHCR images. Choosing a local build runs docker build
# for the instance and manager Dockerfiles. That build installs PyTorch inside
# the instance image. This script does not install PyTorch on the host.
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
NONINTERACTIVE=0
IMAGE_SOURCE=""
BUILD_ROOT=""
BUILD_ROOT_CLONED=0
SOURCE_URL="${COMFYFLEET_SOURCE_URL:-https://github.com/RecognizeYourPrivilege/ComfyFleet.git}"

usage() {
  cat <<EOF
Usage: install.sh [--compose] [--pull-only] [--pull] [--build] [--non-interactive]
                  [--public-host HOST] [--password PASS] [--port PORT] [--name NAME]

Install ComfyFleet and start the manager.

On a terminal, the script asks:
  1. Web UI password, typed twice. Characters are not shown. A mismatch asks again.
  2. This computer's IP address or hostname on your local network.
  3. Where the images come from:
       1  Download prebuilt images (press Enter). Fast. Uses the digest pins below.
       2  Build both images on this computer. Slow. Needs disk space and a network
          connection. Docker installs PyTorch inside the instance image.

Answers are read from /dev/tty, so both of these work:
  curl -fsSL ... -o install.sh && chmod +x install.sh && ./install.sh
  curl -fsSL ... | bash

No prompts when --non-interactive is set, or when /dev/tty cannot be opened
(no terminal). Password and LAN host are then required in the environment or
as flags, and the prebuilt images are used unless --build is set.

  COMFYFLEET_PASSWORD       required unless --pull-only. Prefer the environment
                            (a --password argument is visible in the process list).
  COMFYFLEET_PUBLIC_HOST    LAN hostname or IP browsers use. Required unless --pull-only.

Default images (choice 1 / --pull):
  ${INSTANCE_REPO}:phase1@${INSTANCE_PIN}
  ${MANAGER_REPO}:latest@${MANAGER_PIN}

  COMFYFLEET_INSTANCE_IMAGE / COMFYFLEET_MANAGER_IMAGE replace those refs.
  COMFYFLEET_INSTANCE_DIGEST / COMFYFLEET_MANAGER_DIGEST (sha256:...) pull a different digest.

The instance image is tagged ${LOCAL_INSTANCE_TAG}. The manager image is tagged
${LOCAL_MANAGER_TAG}. A prebuilt install sets COMFYFLEET_INSTANCE_IMAGE to the
pulled ref. A local build sets it to ${LOCAL_INSTANCE_TAG}.

  --pull        download the prebuilt images (the Enter default)
  --build       docker build both Dockerfiles, then start with the local tags
  --non-interactive
                never prompt
  --compose     docker compose up -d instead of docker run
  --pull-only   pull and tag the prebuilt images, do not start the manager
  --port        host port published to container 9100 (docker run only; default 9100)
  --name        manager container name (default ${NAME})

  --build looks for Dockerfile and Dockerfile.manager next to this script.
  If they are not there, it clones ${SOURCE_URL} (override with COMFYFLEET_SOURCE_URL).

Examples:
  curl -fsSL https://raw.githubusercontent.com/RecognizeYourPrivilege/ComfyFleet/main/install.sh -o install.sh && chmod +x install.sh && ./install.sh
  curl -fsSL https://raw.githubusercontent.com/RecognizeYourPrivilege/ComfyFleet/main/install.sh | bash
  COMFYFLEET_PASSWORD='...' COMFYFLEET_PUBLIC_HOST=192.168.1.20 ./install.sh --non-interactive
  COMFYFLEET_PASSWORD='...' COMFYFLEET_PUBLIC_HOST=192.168.1.20 ./install.sh --build
  COMFYFLEET_PASSWORD='...' COMFYFLEET_PUBLIC_HOST=192.168.1.20 ./install.sh --compose
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
    --pull)
      if [[ "${IMAGE_SOURCE}" == "build" ]]; then
        die "--pull and --build cannot be used together."
      fi
      IMAGE_SOURCE="pull"
      shift
      ;;
    --build)
      if [[ "${IMAGE_SOURCE}" == "pull" ]]; then
        die "--pull and --build cannot be used together."
      fi
      IMAGE_SOURCE="build"
      shift
      ;;
    --non-interactive)
      NONINTERACTIVE=1
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

if [[ "${PULL_ONLY}" -eq 1 && "${IMAGE_SOURCE}" == "build" ]]; then
  die "--build cannot be combined with --pull-only."
fi
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

# curl | bash feeds the script on stdin. Prompts use the terminal instead.
can_prompt() {
  [[ "${NONINTERACTIVE}" -eq 0 ]] || return 1
  [[ -e /dev/tty && -r /dev/tty && -w /dev/tty ]] || return 1
  if ! (: </dev/tty) >/dev/null 2>&1; then
    return 1
  fi
  return 0
}

say_tty() {
  printf '%s\n' "$*" >/dev/tty
}

read_tty_line() {
  local prompt="$1"
  local silent="${2:-}"
  printf '%s' "${prompt}" >/dev/tty
  if [[ "${silent}" == "silent" ]]; then
    # -s turns off echo. A failed read must not trip set -e before we return.
    IFS= read -r -s REPLY </dev/tty || return $?
    printf '\n' >/dev/tty
  else
    IFS= read -r REPLY </dev/tty || return $?
  fi
}

trim() {
  local s="$1"
  s="${s#"${s%%[![:space:]]*}"}"
  s="${s%"${s##*[![:space:]]}"}"
  printf '%s\n' "${s}"
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

password_missing() {
  [[ -z "${COMFYFLEET_PASSWORD:-}" || -z "${COMFYFLEET_PASSWORD//[[:space:]]/}" ]]
}

prompt_password() {
  local first="" second=""
  say_tty ""
  say_tty "Choose a password for the ComfyFleet web page."
  say_tty "You will type this password in the browser to sign in."
  say_tty "The characters are hidden while you type."
  while true; do
    if ! read_tty_line "Web UI password: " silent; then
      die "could not read a password from the terminal."
    fi
    first="${REPLY}"
    if ! read_tty_line "Type the same password again: " silent; then
      die "could not read a password from the terminal."
    fi
    second="${REPLY}"
    if [[ -z "${first}" || -z "${first//[[:space:]]/}" ]]; then
      say_tty "That password is empty. Try again."
      continue
    fi
    if [[ "${first}" == *$'\n'* || "${second}" == *$'\n'* ]]; then
      say_tty "The password must be a single line. Try again."
      continue
    fi
    if [[ "${first}" != "${second}" ]]; then
      say_tty "Those passwords did not match. Try again."
      continue
    fi
    COMFYFLEET_PASSWORD="${first}"
    unset first second
    REPLY=""
    break
  done
}

require_password() {
  if password_missing; then
    if can_prompt; then
      prompt_password
    fi
  fi
  if password_missing; then
    die "COMFYFLEET_PASSWORD is required and must be non-empty. There is no open-LAN fallback."
  fi
  if [[ "${COMFYFLEET_PASSWORD}" == *$'\n'* ]]; then
    die "COMFYFLEET_PASSWORD must be a single line."
  fi
  export COMFYFLEET_PASSWORD
}

suggest_lan_host() {
  local candidate=""
  if command -v ip >/dev/null 2>&1; then
    candidate="$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for (i = 1; i <= NF; i++) if ($i == "src") { print $(i + 1); exit }}' || true)"
  fi
  if [[ -z "${candidate}" ]] && command -v hostname >/dev/null 2>&1; then
    candidate="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
  fi
  if [[ -n "${candidate}" ]] && public_host_ok "${candidate}"; then
    printf '%s\n' "${candidate}"
    return 0
  fi
  return 1
}

prompt_public_host() {
  local host="" suggestion="" prompt
  suggestion="$(suggest_lan_host || true)"
  say_tty ""
  say_tty "What address should other computers use to open ComfyFleet?"
  say_tty "This is the IP address of this machine on your local network"
  say_tty "(your home or office Wi-Fi or Ethernet), for example 192.168.1.20."
  say_tty "Phones and other computers on that network open http://THAT-ADDRESS:9100/"
  say_tty "Use the address those devices already use to reach this computer. 0.0.0.0 will not work."
  say_tty "127.0.0.1 works only in a browser on this same computer."
  if [[ -n "${suggestion}" ]]; then
    say_tty "Press Enter to use ${suggestion}."
  fi
  while true; do
    if [[ -n "${suggestion}" ]]; then
      prompt="Device IP or LAN hostname [${suggestion}]: "
    else
      prompt="Device IP or LAN hostname: "
    fi
    if ! read_tty_line "${prompt}"; then
      die "could not read an address from the terminal."
    fi
    host="$(trim "${REPLY}")"
    if [[ -z "${host}" && -n "${suggestion}" ]]; then
      host="${suggestion}"
    fi
    if [[ -z "${host}" ]]; then
      say_tty "Enter this computer's address on your local network, for example 192.168.1.20."
      continue
    fi
    if ! public_host_ok "${host}"; then
      say_tty "That address cannot be used (${host})."
      say_tty "Enter this computer's address on your local network, for example 192.168.1.20."
      continue
    fi
    COMFYFLEET_PUBLIC_HOST="${host}"
    REPLY=""
    break
  done
}

require_public_host() {
  local host
  if [[ -z "${COMFYFLEET_PUBLIC_HOST:-}" || -z "${COMFYFLEET_PUBLIC_HOST//[[:space:]]/}" ]]; then
    if can_prompt; then
      prompt_public_host
    fi
  fi
  host="$(trim "${COMFYFLEET_PUBLIC_HOST:-}")"
  if [[ -z "${host}" ]]; then
    die "COMFYFLEET_PUBLIC_HOST is required. Example: 192.168.1.20"
  fi
  if ! public_host_ok "${host}"; then
    die "COMFYFLEET_PUBLIC_HOST is not a usable hostname or IP (${host})."
  fi
  export COMFYFLEET_PUBLIC_HOST="${host}"
}

choose_image_source() {
  local choice
  if [[ -n "${IMAGE_SOURCE}" ]]; then
    return 0
  fi
  if ! can_prompt; then
    IMAGE_SOURCE="pull"
    return 0
  fi
  say_tty ""
  say_tty "How should this computer get the ComfyFleet images?"
  say_tty ""
  say_tty "  1) Download prebuilt images (recommended)."
  say_tty "     This is the fast option. PyTorch is already inside the image."
  say_tty ""
  say_tty "  2) Build the images on this computer."
  say_tty "     This is slow. It needs a lot of free disk space and a network connection."
  say_tty "     Docker installs PyTorch inside the instance image."
  say_tty "     The build stays inside Docker."
  say_tty ""
  while true; do
    if ! read_tty_line "Type 1 or 2, or press Enter for 1: "; then
      die "could not read a choice from the terminal."
    fi
    choice="$(trim "${REPLY}")"
    case "${choice}" in
      ""|1)
        IMAGE_SOURCE="pull"
        return 0
        ;;
      2)
        IMAGE_SOURCE="build"
        return 0
        ;;
      *)
        say_tty "Please type 1 or 2."
        ;;
    esac
  done
}

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
    echo "comfyfleet: a local image build is the other install choice. Re-run and choose 2, or pass --build." >&2
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
  export COMFYFLEET_INSTANCE_IMAGE="${INSTANCE_REF}"
  export COMFYFLEET_MANAGER_IMAGE="${MANAGER_REF}"
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

script_dir() {
  if [[ -n "${BASH_SOURCE[0]:-}" && -f "${BASH_SOURCE[0]}" ]]; then
    (cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
  fi
}

prepare_build_root() {
  local dir
  dir="$(script_dir || true)"
  if [[ -n "${dir}" && -f "${dir}/Dockerfile" && -f "${dir}/Dockerfile.manager" ]]; then
    BUILD_ROOT="${dir}"
    BUILD_ROOT_CLONED=0
    return 0
  fi
  if ! command -v git >/dev/null 2>&1; then
    die "local build needs Dockerfile and Dockerfile.manager next to install.sh. git is not on PATH, so the source was not downloaded."
  fi
  BUILD_ROOT="$(mktemp -d)"
  BUILD_ROOT_CLONED=1
  echo "comfyfleet: Dockerfile was not found next to install.sh. Downloading the source to build the images."
  if ! git clone --depth 1 --branch main "${SOURCE_URL}" "${BUILD_ROOT}"; then
    rm -rf "${BUILD_ROOT}"
    BUILD_ROOT=""
    BUILD_ROOT_CLONED=0
    die "could not download the ComfyFleet source for a local build."
  fi
  if [[ ! -f "${BUILD_ROOT}/Dockerfile" || ! -f "${BUILD_ROOT}/Dockerfile.manager" ]]; then
    die "the downloaded source is missing Dockerfile or Dockerfile.manager (${BUILD_ROOT})."
  fi
}

local_build() {
  prepare_build_root
  echo "comfyfleet: building the instance image and the manager image in ${BUILD_ROOT}."
  echo "comfyfleet: PyTorch is installed inside the instance image by the Dockerfile."
  echo "comfyfleet: the build stays inside Docker. This is slow and needs free disk and a network connection."
  if ! docker build -t "${LOCAL_INSTANCE_TAG}" "${BUILD_ROOT}"; then
    echo "comfyfleet: source is at ${BUILD_ROOT}." >&2
    die "docker build of the instance image failed."
  fi
  if ! docker build -f "${BUILD_ROOT}/Dockerfile.manager" -t "${LOCAL_MANAGER_TAG}" "${BUILD_ROOT}"; then
    echo "comfyfleet: source is at ${BUILD_ROOT}." >&2
    die "docker build of the manager image failed."
  fi
  INSTANCE_REF="${LOCAL_INSTANCE_TAG}"
  MANAGER_REF="${LOCAL_MANAGER_TAG}"
  export COMFYFLEET_INSTANCE_IMAGE="${LOCAL_INSTANCE_TAG}"
  export COMFYFLEET_MANAGER_IMAGE="${LOCAL_MANAGER_TAG}"
  if [[ "${BUILD_ROOT_CLONED}" -eq 1 && -n "${BUILD_ROOT}" && "${BUILD_ROOT}" != "/" ]]; then
    rm -rf "${BUILD_ROOT}"
    BUILD_ROOT=""
    BUILD_ROOT_CLONED=0
  fi
  echo "comfyfleet: instance ${LOCAL_INSTANCE_TAG}"
  echo "comfyfleet: manager ${LOCAL_MANAGER_TAG}"
}

remove_manager() {
  if docker container inspect "${NAME}" >/dev/null 2>&1; then
    echo "comfyfleet: replacing container ${NAME}. Workflow instances are left in place."
    docker rm -f "${NAME}" >/dev/null
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
  choose_image_source
else
  IMAGE_SOURCE="pull"
fi

require_docker

if [[ "${IMAGE_SOURCE}" == "build" ]]; then
  echo "comfyfleet: local build selected."
  local_build
else
  echo "comfyfleet: pulling prebuilt images."
  pull_and_tag
fi

if [[ "${PULL_ONLY}" -eq 1 ]]; then
  echo "comfyfleet: pull-only done. Manager was not started."
  exit 0
fi

if [[ "${MODE}" == "compose" ]]; then
  start_compose
else
  start_run
fi
