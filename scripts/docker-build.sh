#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=scripts/docker-image.sh
source "${ROOT}/scripts/docker-image.sh"

VERSION="$(read_version)"
LOCAL="$(local_image "${VERSION}")"
RELEASE="$(release_tag "${VERSION}")"

tags=( -t "${LOCAL}" -t "wise:${RELEASE}" -t "wise:latest" )

if [[ -n "${WISEDB_IMAGE:-}" ]]; then
  REMOTE="$(registry_image "${VERSION}")"
  tags+=( -t "${REMOTE}" -t "${WISEDB_IMAGE}:${RELEASE}" -t "${WISEDB_IMAGE}:latest" )
fi

docker build \
  --build-arg "VERSION=${VERSION}" \
  "${tags[@]}" \
  "${ROOT}"

echo "Built ${LOCAL}"
echo "Also tagged wise:${RELEASE} and wise:latest"
[[ -n "${WISEDB_IMAGE:-}" ]] && echo "Also tagged ${REMOTE}, ${WISEDB_IMAGE}:${RELEASE}, and ${WISEDB_IMAGE}:latest"
