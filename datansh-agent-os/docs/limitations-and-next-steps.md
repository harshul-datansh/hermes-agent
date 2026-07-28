# Limitations And Next Steps

## Current Limitations

- The Datansh wrapper is intentionally thin and does not yet wire every role to Hermes-native subagents.
- Live OpenRouter calls require the user to configure `OPENROUTER_API_KEY` in `.env` or `~/.hermes/.env`.
- A real Datansh GitHub fork remote still needs to be attached because GitHub CLI is not installed locally.
- The Hermes dashboard was documented but not launched because `hermes` is not currently on PATH.
- Free OpenRouter models are for early validation only. They can be rate-limited, unavailable, logged by providers, and weaker at tool calling.
- Native Windows is fine for scripts and Streamlit, but Hermes TUI/chat pane is more reliable in WSL2.
- The monitor reads local JSONL events and does not yet stream directly from Hermes REST session events.

## Next-Week Roadmap

| Day | Focus | Outcome |
| --- | --- | --- |
| 1 | Stabilize Hermes install and fork branch | Repeatable setup on Datansh machines |
| 2 | Connect one real repo read-only | Repo-inspection plan with file-level context |
| 3 | Sandbox patch proposal | Developer agent proposes changes on a human-approved branch |
| 4 | Stronger memory retrieval | Markdown first, SQLite index next, vector DB later if needed |
| 5 | Benchmark models | Compare OpenRouter free, approved paid, OpenAI/Claude, and local models |
| 6 | Framework comparison | Score Hermes vs LangGraph, Agno, CrewAI |
| 7 | Recommendation | Continue Hermes, build Datansh orchestration layer, or switch framework |

## Decision Criteria

Score future options on local ownership, open-source license, memory model, subagent support, tool calling, observability, deployment path, model-provider flexibility, on-prem support, learning curve, maintainability, community health, and lock-in risk.
