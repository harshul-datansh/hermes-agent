#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "Datansh Agent OS setup"
echo "Root: $ROOT"

if [ ! -f ".env" ]; then
  cp ".env.example" ".env"
  echo "Created .env from .env.example. Add OPENROUTER_API_KEY there for live calls."
fi

python3 --version
if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi

".venv/bin/python" -m pip install --upgrade pip
".venv/bin/python" -m pip install -r monitor/requirements.txt

if command -v uv >/dev/null 2>&1; then
  uv --version
else
  echo "uv is not installed. Hermes' official installer can install it, or install uv before editable Hermes development."
fi

echo "Setup complete."

