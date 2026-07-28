#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PY=".venv/bin/python"
if [ ! -x "$PY" ]; then PY="python3"; fi
HOST="${DATANSH_MONITOR_HOST:-127.0.0.1}"
PORT="${DATANSH_MONITOR_PORT:-8501}"
"$PY" -m streamlit run monitor/app.py --server.address "$HOST" --server.port "$PORT"

