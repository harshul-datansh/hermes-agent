# Per-Role Model Assignments

Testing phase constraint: every model used must be genuinely zero-cost on OpenRouter
(`pricing.prompt == "0"` and `pricing.completion == "0"`), not just cheap. This was
sourced by pulling `https://openrouter.ai/api/v1/models` live on 2026-07-29 and
filtering to that exact condition — 17 models matched, 2 of which are audio-only
(`google/lyria-3-*`) and were dropped as non-text. The full ranked candidate list is
in `openrouter-free-models-2026-07-29.json` and the discovery script that produced it
is `scripts/discover_openrouter_free_models.py`.

Two names that came up in conversation are not usable here:

- `deepseek/deepseek-v4-flash` exists on OpenRouter but is **not free** — it prices at
  `$0.14` / `$0.28` per million prompt/completion tokens. Cheap, not zero-cost.
- "big pickle" does not match any of the 367 models currently on OpenRouter, by id or
  by name. If this is a stealth/cloaked-model codename from another source, it wasn't
  live under that name as of this pull — send the exact id and it can be checked.

## Assignment table

| Role | Primary | Fallback | Why |
| --- | --- | --- | --- |
| Master Orchestrator | `nvidia/nemotron-3-ultra-550b-a55b:free` | `nvidia/nemotron-3-super-120b-a12b:free` | 1M context holds every upstream role's output without clipping; highest benchmarked intelligence (37.8), coding (49.3), and agentic (27.4) indices in the free pool; described by NVIDIA as a reasoning-and-orchestration model. |
| Product/Project Manager | `google/gemma-4-31b-it:free` | `google/gemma-4-26b-a4b-it:free` | General-purpose instruction-tuned model, not a coding specialist — matches writing acceptance criteria and milestones in plain, verifiable language. |
| Researcher | `nvidia/nemotron-3-super-120b-a12b:free` | `inclusionai/ling-3.0-flash:free` | Generalist reasoning model, 262K context for source material, supports `response_format`/`structured_outputs` for citing sources cleanly. |
| Applied AI Engineer | `inclusionai/ling-3.0-flash:free` | `nvidia/nemotron-3-super-120b-a12b:free` | Purpose-built for "token efficiency and production-scale agentic inference" — closest philosophical match to a role that has to reason about retrieval, latency, and cost tradeoffs. |
| Developer | `poolside/laguna-s-2.1:free` | `poolside/laguna-xs-2.1:free` | Dedicated coding-agent model, 70.2% on Terminal-Bench 2.1. Fallback is the same lineage's smaller sibling — needed because Laguna S already showed a live 429 rate-limit in the first smoke test. |
| Reviewer/QA | `cohere/north-mini-code:free` | `poolside/laguna-xs-2.1:free` | A second coding model from a different lineage than the Developer's model, so the reviewer doesn't inherit the same training blind spots as the code it's reviewing. |
| Memory Curator | `nvidia/nemotron-nano-9b-v2:free` | `google/gemma-4-26b-a4b-it:free` | Small and fast, and — unlike most models in this pool — supports `response_format`/`structured_outputs`. Targets the `no_markdown_sections` rejection this role already hit once in testing. |

Runner behavior (`scripts/run_demo.py`): each role calls its primary model first. If
that call fails at the provider level (`mode == "live_failed"` — rate limit, timeout,
HTTP error), the runner retries once with the role's fallback model before falling
back further to the existing offline POC output. A missing `OPENROUTER_API_KEY`
(`mode == "offline"`) skips straight to the offline path, since no model call is
possible either way. The models actually used per role for a given run are recorded
in `run-metadata.json` under `"models"` (a role → model-id map, not a single string).

## Deliberately excluded from the pool

- `nvidia/nemotron-3.5-content-safety:free` — a 4B moderation/guardrail classifier,
  not a generation model. It is the leading suspect for the reproducible 17-byte
  `User Safety: safe` fragment seen when earlier runs used the random
  `openrouter/free` router (documented in `docs/limitations-and-next-steps.md`).
- `openrouter/free` (the random free-model router) — no longer used per-role now that
  every role has an explicit assignment. Keeping the router in the loop means any role
  could silently land on the content-safety model above.

## Re-verifying this list

Free models on OpenRouter rotate — providers add, remove, and rate-limit them without
notice. Re-run discovery before trusting this table on a new day:

```bash
python scripts/discover_openrouter_free_models.py
```

This writes a fresh `docs/openrouter-free-models-<date>.json` and rewrites
`docs/openrouter-free-models-summary.md`. If a model in the table above no longer
appears in that live pull, treat the assignment as stale and re-pick from the current
list rather than assuming the id still resolves.
