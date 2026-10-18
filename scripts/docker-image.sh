#!/usr/bin/env bash
# Shared image name helpers for docker-build.sh / docker-push.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

read_version() {
  python3 -c "import tomllib; print(tomllib.load(open('${ROOT}/pyproject.toml', 'rb'))['project']['version'])"
}

# Local tag, e.g. wise:3.0.0
local_image() {
  local version="${1:-$(read_version)}"
  echo "wise:${version}"
}

# Release tag used for Docker Hub, e.g. 3.0.0 -> v3.0.0
release_tag() {
  local version="${1:-$(read_version)}"
  echo "v${version}"
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
