#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi

.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip install -e ".[viz,server,mcp,dev]"

echo ""
echo "Готово. Активация (zsh/bash):"
echo "  source \"$ROOT/.venv/bin/activate\""
echo ""
echo "Проверка:"
echo "  python -m wisedb"
echo "  wisedb-viz --demo"
echo "  pytest"
echo "  wisedb-server"
echo ""
