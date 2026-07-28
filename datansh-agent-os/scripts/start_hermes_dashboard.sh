#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
HOST="${HERMES_DASHBOARD_HOST:-127.0.0.1}"
PORT="${HERMES_DASHBOARD_PORT:-9119}"
if ! command -v hermes >/dev/null 2>&1; then
  echo "hermes command not found. This fork is cloned, but Hermes CLI is not installed on PATH. Follow Hermes developer setup or official installer, then rerun." >&2
  exit 1
fi
hermes dashboard --host "$HOST" --port "$PORT" --no-open

