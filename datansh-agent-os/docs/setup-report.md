# Datansh Agent OS Setup Report

Prepared: 2026-07-28

## Environment

| Item | Detected |
| --- | --- |
| OS | Windows NT 10.0.26100 |
| Shell | Windows PowerShell 5.1 |
| Git | Available |
| Python | 3.12.6 |
| uv | Not installed at first check |
| Node/npm | Available |
| Docker | Available |

Native Windows can run this POC's scripts and monitor. WSL2 is still recommended for Hermes TUI/chat behavior because the embedded chat pane uses PTY functionality.

## Workspace Layout

The fork is the only thing in the workspace root, and the Datansh layer lives inside it:

```text
datansh_multi_agents/
└── hermes-agent/            Datansh fork of NousResearch/hermes-agent
    └── datansh-agent-os/    Datansh brain, agents, scripts, monitor, demo, docs
```

An earlier setup left a partial duplicate of `datansh-agent-os/` in the workspace root alongside an empty git scaffold. Both were removed; the copy inside the fork was the tracked, complete one. Run scripts from inside `hermes-agent/datansh-agent-os/` — all paths resolve relative to that directory, not to the workspace root.

## Hermes Fork Checkout

| Field | Value |
| --- | --- |
| Upstream | `https://github.com/NousResearch/hermes-agent` |
| Path | repository root |
| Datansh branch | `codex/datansh-agent-os` |
| Commit | `86091aa74` |
| Remote fork | `https://github.com/harshul-datansh/hermes-agent.git` |
| Published branch | `codex/datansh-agent-os` |

Verified current docs and repository state:

- Hermes is open source and advertises persistent memory, skills, session search, subagents/delegation, and multiple providers.
- Hermes supports OpenRouter and uses `OPENROUTER_API_KEY`.
- Hermes stores secrets in `~/.hermes/.env` and non-secret settings in `~/.hermes/config.yaml`.
- `hermes model` is the interactive model configuration path.
- `hermes dashboard` starts a localhost dashboard at `http://127.0.0.1:9119` by default.
- Dashboard dependencies are the `web` extra; PTY behavior is best in WSL2 on Windows.

## Commands Used

```powershell
git clone/fetch https://github.com/NousResearch/hermes-agent.git hermes-agent
git -C hermes-agent remote rename origin upstream
git -C hermes-agent switch -c codex/datansh-agent-os
git -C hermes-agent rev-parse HEAD
python --version
node --version
npm --version
docker --version
```

## Configuration

All Datansh POC environment variables are in `.env.example`. Copy it to `.env` and configure values there.

Hermes secrets should be configured with:

```powershell
hermes config set OPENROUTER_API_KEY <your-openrouter-key>
hermes model
```

Free-only guardrails:

- Allowed: `openrouter/free`
- Allowed: explicit OpenRouter model IDs ending in `:free`
- Blocked: `openrouter/auto`
- Blocked: paid model names or fallbacks

## Local URLs

| Service | URL |
| --- | --- |
| Hermes Dashboard | `http://127.0.0.1:9119` when `hermes` is installed/on PATH |
| Datansh Mission Control | `http://127.0.0.1:8501` verified HTTP 200 |

## Demo

Run:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/run_demo.ps1
```

Outputs:

- `demo/final-output.md`
- `demo/runs/2026-07-28T16-43-32/`
- `monitor/events.jsonl`
- memory updates in `datansh-brain/`

Latest verified run:

| Check | Result |
| --- | --- |
| Helper venv | Created at `.venv/` |
| Monitor dependencies | Installed |
| Free model discovery | 14 live zero-cost candidates found |
| Free model audit | Passed |
| Demo workflow | Passed in offline POC mode because no real OpenRouter key is configured |
| Role outputs | 7 markdown outputs plus `run-metadata.json` |
| Brain update | Decision, learning, risk, and project-index files appended |
| Mission Control | Running locally and responding on `http://127.0.0.1:8501` |
| Hermes dashboard | Not launched; `hermes` command is not on PATH in this shell |

## Current Status

This fork now contains the Datansh POC layer under `datansh-agent-os/`. If no OpenRouter key is configured, the demo runs in offline POC mode and documents that limitation in the generated metadata.

## Fork Remote

The Datansh fork is attached as `origin`, the NousResearch repository is preserved as `upstream`, and the Datansh implementation is published on `codex/datansh-agent-os`. The local `.env` keeps the fork URL in the same central configuration file as the runtime settings.
