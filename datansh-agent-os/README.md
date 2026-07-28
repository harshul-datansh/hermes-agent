# Datansh Agent OS

Datansh Agent OS is a local one-day POC for Datansh-owned internal project agents. It lives inside a Datansh branch/fork of Hermes Agent and adds Datansh-specific prompts, memory files, scripts, free-model guardrails, demo outputs, and a local visual monitor called Datansh Mission Control.

This POC is not a production autonomous coding system, not a promise that free models are good enough for production, and not a replacement for senior engineering review. It proves that Datansh can own the prompts, memory, workflows, monitoring, and model-routing layer while keeping the model provider swappable.

## Fast Path

1. Copy `.env.example` to `.env` and add `OPENROUTER_API_KEY` when you want live model calls.
2. Run `powershell -ExecutionPolicy Bypass -File scripts/setup_environment.ps1`.
3. Run `powershell -ExecutionPolicy Bypass -File scripts/discover_openrouter_free_models.ps1`.
4. Run `powershell -ExecutionPolicy Bypass -File scripts/audit_free_model_config.ps1`.
5. Start Hermes dashboard with `powershell -ExecutionPolicy Bypass -File scripts/start_hermes_dashboard.ps1`.
6. Start Datansh Mission Control with `powershell -ExecutionPolicy Bypass -File scripts/start_monitor.ps1`.
7. Run the demo with `powershell -ExecutionPolicy Bypass -File scripts/run_demo.ps1`.

WSL2/Linux equivalents are in the `.sh` scripts.

## Prerequisites

- Git
- Python 3.11, 3.12, or 3.13
- Node/npm for Hermes dashboard frontend build
- Docker Desktop or WSL2 recommended for the Hermes TUI/chat pane on Windows
- OpenRouter API key for live free-model calls
- A GitHub fork URL for pushing Datansh's Hermes changes

## Configuration

All Datansh POC environment variables are listed in `.env.example`. Put real values in a local `.env` at the repo root. Scripts load that file first and never require secrets in tracked files.

For the remote fork, set `DATANSH_HERMES_FORK_URL` in `.env` after creating the GitHub fork, then run `scripts/attach_fork_remote.ps1`. This local checkout already uses `upstream` for `https://github.com/NousResearch/hermes-agent`.

Hermes itself stores secrets in `~/.hermes/.env` and non-secret settings in `~/.hermes/config.yaml`. Use:

```powershell
hermes config set OPENROUTER_API_KEY <your-openrouter-key>
hermes model
```

For this POC, keep models free-only: `openrouter/free` or explicit model IDs ending in `:free`.

## What Is Included

- `hermes-agent/`: local upstream Hermes Agent checkout.
- `datansh-brain/`: local markdown memory vault.
- `datansh-agents/`: role prompts for six Datansh agents.
- `scripts/`: setup, OpenRouter discovery, config audit, demo runner, launch helpers.
- `monitor/`: Streamlit visual monitor reading structured events.
- `demo/`: default input and generated outputs.
- `docs/`: architecture, setup report, model summaries, demo script, limitations, screenshots folder.

## Running The Demo

The demo accepts the default task in `demo/input.md`, routes it through role-specific agents, writes individual outputs under `demo/runs/<run-id>/`, updates selected brain files, writes `demo/final-output.md`, and streams role status to `monitor/events.jsonl`.

If `OPENROUTER_API_KEY` is configured, the runner calls OpenRouter with the free-only model configured by `DATANSH_DEFAULT_MODEL`. If the key is blank, the runner uses deterministic offline POC responses and labels the run accordingly.

## Resetting Demo Logs

Delete `monitor/events.jsonl` and the ignored `demo/runs/` directory when you want a clean monitor.

## Known Limitations

- GitHub CLI is not installed on this machine, so the remote GitHub fork must be created in GitHub and attached with `DATANSH_HERMES_FORK_URL`.
- Native Windows can run the scripts and monitor, but Hermes embedded TUI is better in WSL2 because the chat pane needs POSIX PTY behavior.
- Free OpenRouter models can be rate-limited, slow, unavailable, or weaker at tool use.
- The current Datansh wrapper proves orchestration and observability without modifying Hermes core. Hermes-native subagents/profiles should be wired in after the first demo is stable.
