# Limitations And Next Steps

## Current Limitations

- The Datansh wrapper is intentionally thin and does not yet wire every role to Hermes-native subagents.
- Live OpenRouter calls require the user to configure `OPENROUTER_API_KEY` in `.env` or `~/.hermes/.env`.
- A real Datansh GitHub fork remote still needs to be attached because GitHub CLI is not installed locally.
- The Hermes dashboard was documented but not launched because `hermes` is not currently on PATH.
- Free OpenRouter models are for early validation only. They can be rate-limited, unavailable, logged by providers, and weaker at tool calling.
- Free models invent specifics under pressure. Observed on a live run of the reference task: the Applied AI Engineer role produced confident model pricing attributed to a date, cited a nonexistent free-tier SLA, and budgeted sub-500ms p95 for a generation call — a figure that is not physically plausible. The role prompts explicitly forbid this, and the model did it anyway. Treat every number in a free-model run as unverified. This is the single strongest argument for benchmarking against a production-grade model before anyone acts on agent output.
- The final synthesis call fails reproducibly on the free model. On two consecutive live runs the closing Master Orchestrator returned the 17-character string `User Safety: safe` instead of a report — apparently a moderation-classifier artifact rather than a completion, likely triggered by a large prompt dense with security vocabulary (prompt injection, PII, adversarial input) carried up from the earlier roles. The runner now detects this and substitutes the deterministic offline report, so `demo/final-output.md` stays coherent, but in live free-model mode the headline artifact is effectively always the fallback. This is the first thing to re-test on a production-grade model.
- Chaining roles makes one bad output everyone's problem. Before the guardrails existed, a single runaway role produced 400KB of repeated text that blew the context window of every downstream role. Output validation and per-role context clipping now contain this, but the underlying fragility is inherent to a fixed sequential chain.
- Role prompts encode Datansh stack defaults, but nothing yet verifies agent output against a real repository. Agents are told to inspect before editing; in this POC there is no repository attached to inspect, so stack-specific claims are plausible rather than confirmed.
- Native Windows is fine for scripts and Streamlit, but Hermes TUI/chat pane is more reliable in WSL2.
- The monitor reads local JSONL events and does not yet stream directly from Hermes REST session events.
- Roles run in a fixed sequence. The Master Orchestrator writes a route plan, but the runner does not act on it, so a backend-only task still pays for the Applied AI Engineer role. Dynamic routing is a next step.

## Next-Week Roadmap

| Day | Focus | Outcome |
| --- | --- | --- |
| 1 | Stabilize Hermes install and fork branch | Repeatable setup on Datansh machines |
| 2 | Connect one real Spring Boot and one real Next.js repo read-only | Repo-inspection plan with file-level context, and confirmation that house defaults match reality |
| 3 | Sandbox patch proposal | Developer agent proposes changes on a human-approved branch |
| 4 | Stronger memory retrieval | Markdown first, SQLite index next, vector DB later if needed |
| 5 | Benchmark models | Compare OpenRouter free, approved paid, OpenAI/Claude, and local models — grade specifically on whether invented numbers disappear |
| 6 | Framework comparison | Score Hermes vs LangGraph, Agno, CrewAI |
| 7 | Recommendation | Continue Hermes, build Datansh orchestration layer, or switch framework |

## Decision Criteria

Score future options on local ownership, open-source license, memory model, subagent support, tool calling, observability, deployment path, model-provider flexibility, on-prem support, learning curve, maintainability, community health, and lock-in risk.
