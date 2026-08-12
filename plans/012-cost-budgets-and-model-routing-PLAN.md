# 012 — Cost, Budgets & Model Routing

**Why this plan exists:** requirement #8 puts a **CFO** in the founder's office with
approval authority. A CFO whose only lever is approving tickets is decorative. The
thing a CFO actually needs to govern in a multi-agent delivery system is **spend** —
and a PM that can spawn N workers, each of which can loop, is a spend generator.

Nothing in plans 001–008 records what a run costs.

**Day-1 touchpoint:** record token counts and cost per run into `task_runs.metadata`
(the column already exists). You cannot backfill this. Everything else here can be
built later on top of that one record.
**Full build:** 2 h.

---

## 1. What upstream already gives you

- `hermes_cli/model_cost_guard.py` — `expensive_model_warning(...)`, pricing lookup from
  model info, `Decimal` money formatting.
- `hermes insights` — *"Analyze session history to show token usage, costs, tool
  patterns, and activity trends"*.
- `hermes_cli/nous_billing.py`, `nous_subscription.py` — provider-side billing.
- `task_runs.metadata` — a free TEXT column, already there, already written on run end.

So pricing and per-session analysis exist. What is missing is the join: **run → ticket →
project → budget.** That join is the whole plan.

---

## 2. Day-1 touchpoint: capture the session id; the usage may already exist

> **Corrected (VERIFIED V-23).** `hermes_state.py` already has a **`session_model_usage`**
> table — *"per-model billing rollup"* — and `sessions` carries ~45 columns including cost
> accounting. So the day-1 job may be a **join, not a write**.
>
> What is unrecoverable either way is the **session id at spawn time** (013 §3 already
> requires it). Store it in `task_runs.metadata`, then read `session_model_usage`.
> Check U-7 first: does it break out **cached** input tokens? If not, fall back to writing
> the block below. Cache-hit rate is the number that explains the bill (§5) and it cannot
> be reconstructed later.

If a fallback write is needed, on run completion put into `task_runs.metadata`:

```json
{
  "usage": {
    "model": "anthropic/claude-opus-4.6",
    "input_tokens": 184203,
    "cached_input_tokens": 151002,
    "output_tokens": 8811,
    "cost_usd": "0.9142",
    "provider": "anthropic",
    "turns": 14,
    "tool_calls": 61,
    "wall_seconds": 412
  }
}
```

Use `Decimal` for money and serialise as a string — floats accumulating over thousands
of runs produce a total that disagrees with the invoice, and that disagreement destroys
trust in the whole dashboard.

Record `cached_input_tokens` separately. Prompt caching is the largest single lever on
cost in an agent system, and you cannot measure a cache-hit rate you never recorded.

**This is the irreversible bit.** Every other section can be added in a week; a month of
runs with no usage data is a month you cannot analyse.

---

## 3. Budgets

`project.yaml`:

```yaml
budget:
  currency: "USD"
  monthly_cap: 500.00
  per_ticket_soft_cap: 5.00
  per_ticket_hard_cap: 15.00
  per_run_max_turns: 40
  on_soft_cap: "warn"          # warn | block
  on_hard_cap: "block"         # block | escalate
  alert_at_percent: [50, 80, 95]
```

Enforcement points:

| Cap | Checked | Action on breach |
|---|---|---|
| `per_run_max_turns` | each turn | End the run, `outcome='gave_up'`, comment `@pm` |
| `per_ticket_soft_cap` | run end | Comment on the ticket, flag the card |
| `per_ticket_hard_cap` | each turn | Stop the run, ticket → `blocked`, escalate to PM |
| `monthly_cap` × alert % | run end | System message in the founder's office thread |
| `monthly_cap` reached | **before promotion** | `pmo_ticket_finalize` refuses; nothing becomes `ready` |

The last row is the one that matters. A cap that warns but keeps spending is not a cap.

> **Corrected (ADR-017).** This originally said "the dispatcher stops claiming". We keep
> the upstream dispatcher, and the right gate is one step earlier: **promotion**. Refusing
> to promote costs nothing; refusing to claim wastes a tick and leaves a `ready` ticket
> that looks available and isn't.

### Exclude `moa` from PM-OS profiles

Cron already does this — its toolset resolution *"explicitly excludes `moa`,
`homeassistant`, and `rl` toolsets by default… a direct guard against surprise cost,
referencing a previously-real incident (a $4.63 unattended cron run)"* (V-29).

MoA independently has a known billing leak: in-flight advisor HTTP calls are **not
cancellable on interrupt** — they keep running and billing, reconciled after the fact.
For an unattended fleet that is exactly the wrong property. Exclude it, and enable
`ToolCallGuardrailController`'s `hard_stop_enabled` (default **False** — warns only,
V-30).

### Escalation on hard cap

`on_hard_cap: escalate` raises an approval (006) at the CFO's rank with the ticket's
cost history attached. The CFO either approves an overage or cancels the ticket. This
is the concrete answer to "what does the CFO actually do in this product."

---

## 4. Model routing

The PM does planning and discussion; workers do bounded execution; the reviewer reads
diffs. These have genuinely different capability requirements, and defaulting all three
to the largest model is where most of the money goes.

```yaml
models:
  pm:        "anthropic/claude-opus-4.6"     # planning, judgment, client-facing
  worker:    "anthropic/claude-sonnet-4.6"   # bounded, well-specified execution
  reviewer:  "anthropic/claude-opus-4.6"     # catching subtle wrongness
  summariser:"anthropic/claude-haiku-4-5-20251001"  # digests, titles, labels
  escalation_override: "anthropic/claude-opus-4.6"  # any agent, when blocked twice
```

`escalation_override` is worth the complexity: a worker that has failed twice on the
same ticket is exactly where a stronger model earns its cost, and it is a small
fraction of runs.

Hermes already has per-profile model config and a fallback chain
(`hermes_cli/fallback_config.py`) — set these through the profile, do not build a
parallel routing layer.

### Guardrail

Wire `model_cost_guard.expensive_model_warning(...)` into `pmo project bootstrap` so
someone configuring `worker: opus` across ten agents sees the warning at setup rather
than on the invoice.

---

## 5. The CFO's dashboard

A **Spend** screen in the PM-OS section nav, visible to rank ≥ 50.

Following `design/design.md` §5 and the `dataviz` conventions:

- Metric row (`.metric` tiles): month-to-date spend, % of cap, cost per completed
  ticket, cache-hit rate.
- A line chart, month to date, spend by day, split by agent kind (`--chart-1`
  pm / `--chart-2` worker / `--chart-3` reviewer). Use the `--chart-*` tokens; they
  exist for this.
- A `.tbl` of the top 10 most expensive tickets, click-through to the ticket.
- A `.tbl` by agent handle: runs, tokens, cost, completion rate, average reworks.

**Cost per completed ticket** is the number to lead with. Total spend is not
interpretable on its own — a month where spend doubled and throughput tripled is a good
month, and a raw total hides that.

Cache-hit rate deserves a tile because it is the most actionable single number: a low
rate usually means the context is being rebuilt each turn, which is a fixable bug
rather than an inherent cost.

---

## 6. Attribution

Every cost needs to reach a project, or the CFO view is a single meaningless total.

```
task_runs.id → tasks.id → tasks.project_id → projects
```

For PM turns that are not attached to a ticket (thread discussion, triage), record a
synthetic run row with `task_id = NULL` and `project_id` in metadata. Otherwise the
PM's discussion cost — which is not small — vanishes from the books, and the numbers
quietly stop reconciling with the provider invoice.

Reconciliation check in `hermes pmo doctor`: sum of recorded `cost_usd` for the month vs
`hermes insights` total. A drift over 5% means attribution is leaking somewhere.

---

## 7. Tests

```
tests/hermes_cli/test_pmo_cost.py
  test_usage_recorded_on_run_end
  test_cost_uses_decimal_not_float
  test_cached_tokens_recorded_separately
  test_pm_thread_turn_gets_synthetic_run_row
  test_hard_cap_blocks_mid_run
  test_monthly_cap_stops_claiming_not_running
  test_soft_cap_warns_only
  test_hard_cap_escalate_raises_cfo_approval
  test_cost_attributes_to_project
```

## P0 (day-1 touchpoint) / P1

**Day 1:** usage + cost written to `task_runs.metadata` on every run end, including
synthetic rows for PM thread turns. Nothing else.

**P1:** budget caps and enforcement; the Spend screen; model routing config;
`escalation_override`; reconciliation in `doctor`; per-agent efficiency table.

## Traps

- Floats for money. Use `Decimal`, store as string.
- Forgetting PM discussion turns. They are a large share of spend and the easiest to
  lose.
- Enforcing `monthly_cap` at run time instead of claim time — you pay for the run and
  still don't get the ticket.
- Reporting total spend without throughput. It reads as bad news in a good month and
  the CFO stops trusting the screen.
- Not recording `cached_input_tokens` from day 1. It is the number that explains the
  bill, and it cannot be reconstructed.
