#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PY=".venv/bin/python"
if [ ! -x "$PY" ]; then PY="python3"; fi
"$PY" scripts/configure_openrouter_free.py

