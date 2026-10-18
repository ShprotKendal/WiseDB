#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=scripts/docker-image.sh
source "${ROOT}/scripts/docker-image.sh"

VERSION="$(read_version)"
LOCAL="$(local_image "${VERSION}")"
RELEASE="$(release_tag "${VERSION}")"

# По умолчанию amd64 — для x86-хостов (Intel/AMD). На Apple Silicon: linux/amd64 для удалённого
# сервера или linux/arm64 для локального Mac. Несколько платформ: WISEDB_DOCKER_PLATFORM=linux/amd64,linux/arm64
PLATFORM="${WISEDB_DOCKER_PLATFORM:-linux/amd64}"

tags=( -t "${LOCAL}" -t "wise:${RELEASE}" -t "wise:latest" )

if [[ -n "${WISEDB_IMAGE:-}" ]]; then
  REMOTE="$(registry_image "${VERSION}")"
  tags+=( -t "${REMOTE}" -t "${WISEDB_IMAGE}:${RELEASE}" -t "${WISEDB_IMAGE}:latest" )
fi

if [[ "${PLATFORM}" == *","* ]]; then
  if ! docker buildx version >/dev/null 2>&1; then
    echo "Multi-platform build requires docker buildx (WISEDB_DOCKER_PLATFORM=${PLATFORM})" >&2
    exit 1
  fi
  push_flag=()
  if [[ "${WISEDB_DOCKER_PUSH:-}" == "1" ]]; then
    push_flag=( --push )
  fi
  docker buildx build \
    --platform "${PLATFORM}" \
    --build-arg "VERSION=${VERSION}" \
    "${push_flag[@]}" \
    "${tags[@]}" \
    "${ROOT}"
else
  docker build \
    --platform "${PLATFORM}" \
    --build-arg "VERSION=${VERSION}" \
    "${tags[@]}" \
    "${ROOT}"
fi

echo "Built ${LOCAL} (platform=${PLATFORM})"
echo "Also tagged wise:${RELEASE} and wise:latest"
[[ -n "${WISEDB_IMAGE:-}" ]] && echo "Also tagged ${REMOTE}, ${WISEDB_IMAGE}:${RELEASE}, and ${WISEDB_IMAGE}:latest"
