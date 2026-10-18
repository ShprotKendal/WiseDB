#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi

.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip install -e ".[server,mcp,dev]"

echo ""
echo "Ready. Activate (zsh/bash):"
echo "  source \"$ROOT/.venv/bin/activate\""
echo ""
echo "Quick checks:"
echo "  pytest"
echo "  wisedb-server"
echo ""
