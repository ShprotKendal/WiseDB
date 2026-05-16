#!/usr/bin/env bash
# Shared image name helpers for docker-build.sh / docker-push.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

read_version() {
  python3 -c "import tomllib; print(tomllib.load(open('${ROOT}/pyproject.toml', 'rb'))['project']['version'])"
}

# Local tag used by compose by default, e.g. wise:2.1.0
local_image() {
  local version="${1:-$(read_version)}"
  echo "wise:${version}"
}

# Release alias used for Docker Hub marketing tags, e.g. 2.1.0 -> v2.1
release_tag() {
  local version="${1:-$(read_version)}"
  echo "v${version%.*}"
}

# Registry image, e.g. ghcr.io/you/wisedb — set WISEDB_IMAGE (without tag)
registry_image() {
  if [[ -z "${WISEDB_IMAGE:-}" ]]; then
    echo "Set WISEDB_IMAGE to push, e.g. ghcr.io/you/wisedb or docker.io/you/wisedb" >&2
    return 1
  fi
  local version="${1:-$(read_version)}"
  echo "${WISEDB_IMAGE}:${version}"
}
