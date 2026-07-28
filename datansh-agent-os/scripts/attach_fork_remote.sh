#!/usr/bin/env bash
set -euo pipefail
DATANSH_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_PATH="$DATANSH_ROOT/.env"

if [ ! -f "$ENV_PATH" ]; then
  echo "Missing $ENV_PATH. Copy .env.example to .env and set DATANSH_HERMES_FORK_URL." >&2
  exit 1
fi

FORK_URL="$(grep -E '^[[:space:]]*DATANSH_HERMES_FORK_URL[[:space:]]*=' "$ENV_PATH" | tail -n1 | cut -d= -f2- | tr -d '"' | tr -d "'" | xargs)"
if [ -z "$FORK_URL" ]; then
  echo "DATANSH_HERMES_FORK_URL is empty in $ENV_PATH." >&2
  exit 1
fi

cd "$REPO_ROOT"
if git remote get-url origin >/dev/null 2>&1; then
  git remote set-url origin "$FORK_URL"
else
  git remote add origin "$FORK_URL"
fi

if ! git remote get-url upstream >/dev/null 2>&1; then
  git remote add upstream https://github.com/NousResearch/hermes-agent.git
fi

git remote -v

