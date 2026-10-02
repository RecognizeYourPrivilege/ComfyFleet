#!/usr/bin/env bash
# Push ComfyFleet images to GHCR with docker buildx.
# No GPU: the instance Dockerfile does not import comfy_kitchen during build.
# Torch and CUDA layers are large. Use a machine with tens of GB free.
# GitHub Actions (.github/workflows/publish-images.yml) is the primary path.
# This script is the fallback when a hosted runner runs out of disk or time.
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
cd "${root}"

usage() {
  cat <<'EOF'
Usage: scripts/publish-images.sh

Build Dockerfile (cu130), Dockerfile.cu124, and Dockerfile.manager for linux/amd64 and push to GHCR.

Login first. The password is a PAT, not your GitHub password
(classic write:packages, or fine-grained Packages read and write):

  echo "$GHCR_TOKEN" | docker login ghcr.io -u YOUR_GITHUB_USERNAME --password-stdin

Environment:
  COMFYFLEET_GHCR_OWNER   default recognizeyourprivilege (lowercased)
  COMFYFLEET_REGISTRY     default ghcr.io

Tags:
  ghcr.io/<owner>/comfyfleet:cu130
  ghcr.io/<owner>/comfyfleet:latest          (alias of cu130)
  ghcr.io/<owner>/comfyfleet:<git sha>       (cu130)
  ghcr.io/<owner>/comfyfleet:<git sha>-cu130
  ghcr.io/<owner>/comfyfleet:cu124
  ghcr.io/<owner>/comfyfleet:phase1          (alias of cu124)
  ghcr.io/<owner>/comfyfleet:<git sha>-cu124
  ghcr.io/<owner>/comfyfleet-manager:latest
  ghcr.io/<owner>/comfyfleet-manager:<git sha>

Prints digests after the push. Record them in the README.
GHCR rejects a layer over 10 GB and an upload that takes longer than about 10 minutes.
Re-running the script reuses layers that already uploaded.
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi
if [[ $# -gt 0 ]]; then
  usage >&2
  echo "comfyfleet-publish: unknown argument: $1" >&2
  exit 2
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "comfyfleet-publish: docker is not on PATH." >&2
  exit 1
fi
if ! docker buildx version >/dev/null 2>&1; then
  echo "comfyfleet-publish: docker buildx is required." >&2
  exit 1
fi
if ! command -v git >/dev/null 2>&1; then
  echo "comfyfleet-publish: git is required to read the commit SHA." >&2
  exit 1
fi

owner="$(printf '%s' "${COMFYFLEET_GHCR_OWNER:-recognizeyourprivilege}" | tr '[:upper:]' '[:lower:]')"
registry="${COMFYFLEET_REGISTRY:-ghcr.io}"
sha="$(git rev-parse HEAD)"
source_url="${COMFYFLEET_SOURCE_URL:-https://github.com/RecognizeYourPrivilege/ComfyFleet}"
instance="${registry}/${owner}/comfyfleet"
manager="${registry}/${owner}/comfyfleet-manager"

echo "comfyfleet-publish: linux/amd64, no GPU. Instance context is ${root}."
echo "comfyfleet-publish: this can take a long time. Torch wheels and CUDA libraries need a lot of disk."
echo "comfyfleet-publish: instance ${instance}:cu130 ${instance}:latest ${instance}:${sha} ${instance}:${sha}-cu130"
echo "comfyfleet-publish: instance ${instance}:cu124 ${instance}:phase1 ${instance}:${sha}-cu124"
echo "comfyfleet-publish: manager ${manager}:latest ${manager}:${sha}"

build_push() {
  local file="$1"
  shift
  docker buildx build \
    --platform linux/amd64 \
    --pull \
    --push \
    --provenance=false \
    --sbom=false \
    --label "org.opencontainers.image.source=${source_url}" \
    --label "org.opencontainers.image.revision=${sha}" \
    -f "${file}" \
    "$@" \
    "${root}"
}

build_push "${root}/Dockerfile" \
  -t "${instance}:cu130" \
  -t "${instance}:latest" \
  -t "${instance}:${sha}" \
  -t "${instance}:${sha}-cu130"

build_push "${root}/Dockerfile.cu124" \
  -t "${instance}:cu124" \
  -t "${instance}:phase1" \
  -t "${instance}:${sha}-cu124"

build_push "${root}/Dockerfile.manager" \
  -t "${manager}:latest" \
  -t "${manager}:${sha}"

print_digest() {
  local role="$1"
  local repo="$2"
  local tag="$3"
  local digest=""
  if digest="$(docker buildx imagetools inspect --format '{{.Manifest.Digest}}' "${repo}:${tag}" 2>/dev/null)" \
    && [[ "${digest}" == sha256:* ]]; then
    echo "${role} ${repo}@${digest}"
    return 0
  fi
  echo "comfyfleet-publish: could not format the ${role} digest. Full inspect:" >&2
  docker buildx imagetools inspect "${repo}:${tag}" || true
  return 1
}

echo "comfyfleet-publish: digests (copy into the README; not committed by this script)"
failed=0
print_digest instance-cu130 "${instance}" "cu130" || failed=1
print_digest instance-cu124 "${instance}" "cu124" || failed=1
print_digest manager "${manager}" "${sha}" || failed=1
echo "comfyfleet-publish: a personal-account package is private until a human sets it to public."
echo "comfyfleet-publish: Actions pushes with GITHUB_TOKEN link the package to the repo. This CLI push relies on the source label."
if [[ "${failed}" -ne 0 ]]; then
  echo "comfyfleet-publish: images were pushed. Copy the sha256 from the inspect output above into the README." >&2
  exit 1
fi
