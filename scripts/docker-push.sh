#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=scripts/docker-image.sh
source "${ROOT}/scripts/docker-image.sh"

VERSION="$(read_version)"
LOCAL="$(local_image "${VERSION}")"
REMOTE="$(registry_image "${VERSION}")"
RELEASE="$(release_tag "${VERSION}")"

"${ROOT}/scripts/docker-build.sh"

echo "Pushing ${REMOTE}, ${WISEDB_IMAGE}:${RELEASE}, and ${WISEDB_IMAGE}:latest ..."
docker push "${REMOTE}"
docker push "${WISEDB_IMAGE}:${RELEASE}"
docker push "${WISEDB_IMAGE}:latest"
echo "Done. Pull with: docker pull ${WISEDB_IMAGE}:${RELEASE}"
