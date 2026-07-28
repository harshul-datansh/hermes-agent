# Demo Script

1. Open Datansh Mission Control at `http://127.0.0.1:8501`.
2. Start Hermes dashboard at `http://127.0.0.1:9119` after the Hermes CLI is installed/on PATH.
3. Run `powershell -ExecutionPolicy Bypass -File scripts/run_demo.ps1`.
4. Watch the role rows move through queued, running, and success.
5. Open `demo/final-output.md` and the latest folder under `demo/runs/`.
6. Show the memory updates in `datansh-brain/04-decision-log.md`, `05-learning-log.md`, and `06-risk-register.md`.
7. Run `powershell -ExecutionPolicy Bypass -File scripts/audit_free_model_config.ps1` to show free-only model guardrails.

CEO summary:

```text
I have set up the first Datansh Agent OS POC locally using Hermes Agent as the base.

The demo has:
- a Datansh-specific local agent brain
- a master orchestrator
- specialist project agents for PM, developer, research, review, and memory
- OpenRouter free-model configuration for cheap initial validation
- a local visual monitor to see each agent's status and outputs
- generated project execution artifacts from one Datansh task

This validates the direction of owning our agent layer instead of only wrapping hosted chatbot agents.

The next step is to benchmark reliability: free OpenRouter models vs stronger API models vs on-prem candidates, and then decide whether we continue extending Hermes directly or build a Datansh orchestration layer inspired by Hermes.
```
