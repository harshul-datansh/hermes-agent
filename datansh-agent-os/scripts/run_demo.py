from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

from datansh_common import (
    ROOT,
    RUNS_DIR,
    append_event,
    append_text,
    brain_context,
    call_openrouter,
    get_env,
    is_free_model,
    read_text,
    role_prompt,
    run_id,
    write_json,
    write_text,
)

ROLE_SEQUENCE = [
    ("Master Orchestrator", "master-orchestrator.md", "01-master-route.md", "Create the routing plan and quality bar."),
    ("Product/Project Manager", "project-manager.md", "02-project-manager.md", "Convert the task into milestones and acceptance criteria."),
    ("Researcher", "researcher.md", "03-researcher.md", "Identify current facts, unknowns, assumptions, and research risks."),
    ("Developer", "developer.md", "04-developer.md", "Create the technical implementation plan and verification steps."),
    ("Reviewer/QA", "reviewer-qa.md", "05-reviewer-qa.md", "Review the plan for gaps, tests, risks, and demo readiness."),
    ("Memory Curator", "memory-curator.md", "06-memory-curator.md", "Identify durable memory updates and redactions."),
    ("Master Orchestrator", "master-orchestrator.md", "07-final-master-report.md", "Synthesize the final Datansh execution plan."),
]


def offline_output(agent: str, task: str, previous: dict[str, str]) -> str:
    if agent == "Product/Project Manager":
        return """# Product/Project Manager Output

## Problem Framing
Datansh needs a local internal agent workflow that can inspect software projects, plan feature work, review its own plan, and retain durable learnings in a company-owned brain.

## Milestones
1. Keep Hermes Agent as the forked runtime base.
2. Add Datansh-owned brain, role prompts, wrapper scripts, and event logging.
3. Run a multi-role demo on a Datansh project task.
4. Show role status and artifacts in Mission Control.
5. Audit that OpenRouter configuration is free-only.

## Acceptance Criteria
- Every role writes an individual markdown output.
- A final master report is generated.
- Memory logs are appended without secrets.
- The monitor shows role status, timeline, and output previews.
- Model configuration contains only `openrouter/free` or `:free` IDs.

## Dependencies
OpenRouter key for live model calls, Hermes CLI/dashboard for runtime validation, Streamlit for visual monitoring.

## Open Questions
- Which Datansh GitHub org/user should own the remote fork?
- Which real Datansh repo should be the first read-only inspection target?
"""
    if agent == "Researcher":
        return """# Researcher Output

## Verified Facts
- Hermes Agent documents local configuration under `~/.hermes/`, with secrets in `.env` and settings in `config.yaml`.
- Hermes documents OpenRouter configuration through `OPENROUTER_API_KEY` and model/provider selection.
- OpenRouter exposes a public `/api/v1/models` endpoint with pricing metadata.
- The Hermes dashboard runs locally on `127.0.0.1:9119` by default.

## Assumptions
- Native Windows can run the Datansh wrapper, but WSL2 is better for Hermes TUI/chat behavior.
- The first POC should avoid modifying Hermes core until Hermes-native profiles/subagents are validated.

## Useful Docs Or APIs
- Hermes installation, model configuration, provider, dashboard, memory, and skills docs.
- OpenRouter models API.

## Risks From Uncertainty
Free models may be rate-limited, removed, or weak at tool calling. Hermes internals may change quickly.

## Recommended Follow-Up Research
Benchmark the top discovered free models against Datansh repo-inspection tasks and compare against approved stronger models later.
"""
    if agent == "Developer":
        return """# Developer Output

## Technical Approach
Create Datansh-owned files inside the Hermes fork under `datansh-agent-os/`. Use a thin orchestration script first, then graduate to Hermes-native profiles/subagents after the demo is stable.

## File/Module Plan
- `datansh-agent-os/datansh-brain/`: persistent markdown memory.
- `datansh-agent-os/datansh-agents/`: role prompts.
- `datansh-agent-os/scripts/`: setup, model discovery, audit, demo runner, launch helpers.
- `datansh-agent-os/monitor/`: Streamlit Mission Control.
- `datansh-agent-os/docs/`: report, architecture, demo script, limitations.

## Implementation Steps
1. Configure `.env` from `.env.example`.
2. Discover live free OpenRouter models.
3. Audit all local model config for paid fallbacks.
4. Run role workflow and write artifacts.
5. Start Mission Control to inspect structured events.
6. Optionally start Hermes dashboard from the fork checkout.

## Verification Commands
```powershell
python datansh-agent-os/scripts/discover_openrouter_free_models.py
python datansh-agent-os/scripts/audit_free_model_config.py
python datansh-agent-os/scripts/run_demo.py
streamlit run datansh-agent-os/monitor/app.py
```

## Rollback Plan
Datansh changes are isolated to the `codex/datansh-agent-os` branch and mostly under `datansh-agent-os/`.
"""
    if agent == "Reviewer/QA":
        return """# Reviewer/QA Output

## Findings
- The fork needs a real Datansh remote `origin`; this local machine does not have GitHub CLI installed.
- Live LLM validation depends on `OPENROUTER_API_KEY`; offline mode proves orchestration but not model quality.
- Hermes-native subagent integration remains a next step.

## Missing Tests
- Add unit tests for free-model audit parsing.
- Add a fixture-based test for Mission Control event aggregation.
- Run live model smoke tests once the key is configured.

## Demo Risks
- Free models may fail during the demo due to capacity.
- Dashboard may require WSL2 for embedded chat.

## Security/Privacy Checks
- Keep secrets in `.env` or `~/.hermes/.env`.
- Confirm no real OpenRouter keys are present before pushing.
- Keep dashboard bound to localhost.

## Go/No-Go Recommendation
Go for a CEO POC if presented honestly as local orchestration, memory, monitoring, and free-model validation rather than production autonomy.
"""
    if agent == "Memory Curator":
        return """# Memory Curator Output

## Decisions To Append
- Use a Hermes Agent fork as the base for Datansh Agent OS.
- Keep Datansh customizations isolated under `datansh-agent-os/` for the POC.
- Use OpenRouter free-only models during initial validation.

## Learnings To Append
- A thin wrapper can prove role routing, observability, and memory before changing Hermes core.
- Mission Control can read JSONL events and show role state without a cloud dependency.

## Risks To Append
- Free model reliability and tool use are unproven.
- Remote GitHub fork setup still needs Datansh org/user credentials.

## Project-Index Updates
- Datansh Agent OS now has a local brain, role prompts, event log, monitor, and demo workflow.

## Redactions Or Skipped Memory
No secrets or private customer data should be stored.
"""
    return f"""# {agent} Output

## Route Plan
Run PM, Researcher, Developer, Reviewer/QA, and Memory Curator in sequence. Use their outputs to produce a final practical execution plan for Datansh Agent OS.

## Quality Checks
- PM must define acceptance criteria.
- Developer must name files, scripts, and verification commands.
- Reviewer must identify realistic risks.
- Memory Curator must append durable, non-secret learnings.

## Final Synthesized Plan
Build the Datansh customization layer inside the Hermes fork, keep env variables in one template, use OpenRouter free-only guardrails, run a local multi-agent demo, and show role status in Mission Control.

## Confidence And Risk Summary
Confidence is medium-high for the POC mechanics and medium for live model reliability until OpenRouter smoke tests run with a real key.

## Recommended Next Actions
1. Create the remote Datansh fork on GitHub and add it as `origin`.
2. Configure `.env` and `~/.hermes/.env`.
3. Run live free-model discovery and smoke tests.
4. Benchmark against one real Datansh repository in read-only mode.
"""


def build_prompt(agent: str, role_file: str, task: str, previous: dict[str, str]) -> str:
    previous_text = "\n\n".join(f"## {name}\n{content}" for name, content in previous.items())
    return f"""{role_prompt(role_file)}

# Datansh Brain Context
{brain_context()}

# User Task
{task}

# Previous Agent Outputs
{previous_text or "None yet."}

# Current Assignment
Act as {agent}. Produce the required sections in markdown. Keep the output practical and Datansh-specific.
"""


def update_memory(run: str, curator_output: str) -> None:
    date = datetime.now().strftime("%Y-%m-%d")
    append_text(
        ROOT / "datansh-brain" / "04-decision-log.md",
        f"| {date} | {run} | Use Hermes Agent fork as Datansh Agent OS base | Fastest POC path with memory, skills, dashboard, providers | Datansh Agent OS | Attach real Datansh remote fork |\n",
    )
    append_text(
        ROOT / "datansh-brain" / "05-learning-log.md",
        f"| {date} | {run} | Thin wrapper proves orchestration, monitoring, and memory before core changes | Early agent workflow demos | demo run |\n",
    )
    append_text(
        ROOT / "datansh-brain" / "06-risk-register.md",
        "| Remote GitHub fork not attached in local checkout | Medium | Medium | Add Datansh fork URL as origin after GitHub fork is created | Open |\n",
    )
    append_text(
        ROOT / "datansh-brain" / "01-project-index.md",
        f"\n- {date} `{run}`: Demo workflow generated role outputs, final report, structured events, and memory updates.\n",
    )


def main() -> int:
    model = get_env("DATANSH_DEFAULT_MODEL", "openrouter/free")
    if not is_free_model(model):
        raise SystemExit(f"Refusing non-free model for POC: {model}")

    run = run_id()
    run_dir = RUNS_DIR / run
    run_dir.mkdir(parents=True, exist_ok=True)
    task = read_text(ROOT / "demo" / "input.md")
    write_text(run_dir / "00-input.md", task)

    append_event(run, "System", "task_received", "running", "Run Datansh demo task", "Demo task received", str(run_dir / "00-input.md"), model)

    previous: dict[str, str] = {}
    outputs: list[str] = []
    started_at = datetime.now().isoformat(timespec="seconds")
    live_modes: list[str] = []

    for agent, role_file, filename, assignment in ROLE_SEQUENCE:
        output_path = run_dir / filename
        append_event(run, agent, "agent_started", "running", assignment, f"{agent} started", str(output_path), model)
        prompt = build_prompt(agent, role_file, task, previous)
        append_event(run, agent, "model_call_started", "running", assignment, "Model call started", str(output_path), model)
        content, metadata = call_openrouter(prompt, model)
        live_modes.append(metadata.get("mode", "unknown"))
        if not content:
            content = offline_output(agent, task, previous)
            metadata["fallback"] = "offline_poc_output"
        write_text(output_path, content)
        previous[agent if filename != "07-final-master-report.md" else "Final Master Report"] = content
        outputs.append(str(output_path.relative_to(ROOT)))
        append_event(run, agent, "agent_completed", "success", assignment, f"{agent} completed", str(output_path), model, metadata)
        time.sleep(0.2)

    final = previous["Final Master Report"]
    write_text(ROOT / "demo" / "final-output.md", final)
    update_memory(run, previous.get("Memory Curator", ""))
    append_event(run, "Memory Curator", "memory_updated", "success", "Append durable brain entries", "Brain memory files updated", str(ROOT / "datansh-brain"), model)

    metadata = {
        "run_id": run,
        "started_at": started_at,
        "ended_at": datetime.now().isoformat(timespec="seconds"),
        "provider": get_env("DATANSH_LLM_PROVIDER", "openrouter"),
        "models": [model],
        "agents": [item[0] for item in ROLE_SEQUENCE],
        "status": "success",
        "modes": live_modes,
        "outputs": outputs,
    }
    write_json(run_dir / "run-metadata.json", metadata)
    append_event(run, "System", "run_completed", "success", "Complete Datansh Agent OS demo", "Run completed", str(ROOT / "demo" / "final-output.md"), model, metadata)
    print(f"Demo run completed: {run}")
    print(f"Final output: {ROOT / 'demo' / 'final-output.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
