# 019 — Context Engineering

**Why this plan exists:** this is an LLM system, and **nothing in plans 001–018 says what
goes into an agent's context window.** Plan 004 says "wake the PM with a context block"
and shows five lines of prose. That single decision — what the PM sees when it wakes —
determines whether it decomposes well, whether it repeats itself, and what it costs.
It is the highest-leverage unspecified thing in the build.

**Day-1 touchpoint:** assemble the wake context through **one function** per agent kind,
not inline at each call site. Once three call sites build context by string-concatenation
you cannot change the format, measure it, or cache it.
**Full build:** 3 h.

---

## 1. What Hermes already gives you

Read these before writing anything — the assembly machinery exists and is sophisticated:

| Module | What it does |
|---|---|
| `agent/prompt_builder.py` | *"System prompt assembly — identity, platform hints, skills index, context files."* Finds `.hermes.md` up the tree, strips frontmatter, enforces a **dynamic char budget** via `_get_context_file_max_chars(context_length)`, records truncation warnings |
| `agent/turn_context.py` | `build_turn_context()`, compression preflight, idle compaction, and `consume_gateway_turn_context_notes()` — **the seam for injecting per-turn notes** |
| `agent/system_prompt.py` | The identity layer |
| `agent/memory_manager.py`, `memory_provider.py` | Memory injection |
| `agent/subdirectory_hints.py` | Directory-aware hints |
| `tools/memory_tool.py` | The **frozen snapshot pattern** — see §3 |

`consume_gateway_turn_context_notes` is the important discovery: there is already a
supported way to attach per-turn context without rebuilding the system prompt. **Use it
for mention/thread/blocker context.** Do not prepend to the user message and do not
rebuild the system prompt per wake.

---

## 2. The layer cake

Ordered from most stable to most volatile. **This order is not cosmetic** — it is what
makes prompt caching work (§3).

```
┌─ 1. Hermes identity + platform + tool schemas          [stable across all sessions]
├─ 2. Agent soul (.datansh/agents/<handle>.md)           [stable per agent]
├─ 3. Project context (.datansh/context.md)              [stable per project, §4]
├─ 4. Memory snapshot (MEMORY.md, frozen at start)       [stable per session]
├─ 5. Roster + board vocabulary (handles, labels, cols)  [stable per session]
├─ 6. Ticket / thread the turn is about                  [per turn]
├─ 7. Turn notes: the mention, the blocker, the message  [per turn, via the seam]
└─ 8. Conversation history                               [grows]
```

Layers 1–5 are the **cacheable prefix**. Everything volatile goes at 6–8. Getting one
volatile item into layer 3 — a timestamp, a ticket count, "current time is…" — silently
destroys the cache for the whole session and roughly triples the bill. 012 §5 makes
cache-hit rate a dashboard tile precisely so this is visible when it happens.

---

## 3. The frozen-snapshot rule

`tools/memory_tool.py` documents it explicitly, and it is worth quoting because it is a
constraint the rest of this plan is built around:

> *"Both are injected into the system prompt as a frozen snapshot at session start.
> Mid-session writes update files on disk immediately (durable) but do NOT change the
> system prompt — this preserves the prefix cache for the entire session."*

Adopt the same discipline for everything in layers 2–5. A PM that learns something at
turn 3 writes it to memory; it takes effect at the **next** wake, not this one. That is
a feature: it keeps the session cheap and it keeps behaviour stable within a turn.

**This is a house principle, not a preference.** `01-architecture-overview.md` §6 lists
*"Per-conversation prompt caching is sacred — never mutate past context mid-conversation"*
as design principle #1, with compression as the one documented exception.

**And the mechanism already exists — copy it (V-49).** A skill's full body is injected via
`_build_skill_message()` as **a user-turn message, not a system-prompt mutation**, and
`03` §2 states the reason plainly: *"this is what keeps it compatible with the 'prompt
caching is sacred' rule: loading a skill mid-conversation never invalidates the cached
prefix."*

So per-turn context — the mention, the blocker, the thread message — goes in as user-turn
content through `consume_gateway_turn_context_notes`, exactly as a skill body does. Not
our invention: the pattern this repo already uses for the same problem.

One caveat from `05` §4: cache markers are *"fragile and re-derivable, not a stable
invariant"* — `strip_anthropic_cache_control()` re-applies them after a mid-turn failover
(#72626). So a cache miss after a failover is expected behaviour, not a regression in our
prefix. Do not chase it.

---

## 4. Project context — `.datansh/context.md`

The shared brief every agent on a project sees. Hermes already discovers `.hermes.md`
up the directory tree (`prompt_builder._find_hermes_md`); mirror that mechanism rather
than inventing a second one.

```markdown
# Acme Portal

## What this is
One paragraph. What the product does, who uses it.

## Stack
Language, framework, database, deployment. Be specific — this is what stops an agent
proposing the wrong library.

## Conventions
Test framework and how to run it. Lint/format command. Commit message style.
Branch naming (see 011). Directory layout.

## Boundaries
What agents must never touch. Which directories are generated. Which files need
human review regardless of ticket size.

## Current focus
The one or two things in flight. Updated by the PM (020).
```

Budget: 4,000 characters. `prompt_builder` already truncates context files against a
dynamic budget and records a warning — surface those warnings in `hermes pmo doctor`
rather than letting a silently-truncated context file degrade every agent on the project.

**Keep `.hermes.md` for Hermes-level guidance and `.datansh/context.md` for
project-delivery guidance.** Two files, two audiences; do not merge them.

---

## 5. Per-kind context builders

One function per agent kind. No inline assembly anywhere.

```python
# hermes_cli/pmo_context.py
def build_pm_wake_context(scope, trigger: WakeTrigger) -> TurnNotes: ...
def build_worker_task_context(scope, task, run) -> TurnNotes: ...
def build_reviewer_context(scope, task, diff) -> TurnNotes: ...
def build_pm_client_context(scope, thread, message) -> TurnNotes: ...   # 010
```

### PM wake

```
You are waking because: {trigger.kind}

── Trigger ──────────────────────────────────────────
{thread message | mention | blocked event, verbatim}

── Board summary ────────────────────────────────────
draft 3 · todo 5 · in_progress 2 · blocked 1 · review 0 · done 14
Blocked: t8a3f1 "Add SSO callback" — @dev-1, 42m, "redirect URI not registered"
Awaiting approval: t9c02 "Upgrade billing plan" — needs CFO

── Open threads ─────────────────────────────────────
Founder's office: 3 messages since your last turn (below)

── Your roster ──────────────────────────────────────
@dev-1 Backend engineer (1 in progress) · @qa QA reviewer (idle)

── Recent decisions ─────────────────────────────────
{last 5 from the decision log, 020}
```

Design rules, each one earned:

- **Summarise the board, never dump it.** A 200-ticket board is 40k tokens and the PM
  needs six numbers and the exceptions. Ticket detail arrives on demand via
  `pmo_task_show`.
- **Exceptions in full, normal state as counts.** Blocked and awaiting-approval get a
  line each; `todo` gets a number.
- **Name the trigger explicitly.** An agent that does not know why it woke will re-read
  everything and re-plan from scratch.
- **Include roster load.** Otherwise the PM assigns four tickets to the busiest agent.

### Worker task

The ticket body, the comment thread, the acceptance criteria, the project conventions,
the worktree path and branch, and — critically — **the previous run's summary if this is
a retry**. A worker that retries without knowing why the last attempt failed repeats it.

### Reviewer

The ticket, the acceptance criteria, and **the diff** — not the repository. A reviewer
given the whole repo reviews the repo.

---

## 6. Budgets

```yaml
context:
  project_context_max_chars: 4000
  board_summary_max_tickets: 12      # exceptions listed; rest as counts
  comment_thread_max: 30             # then summarise the older ones
  thread_history_max: 40
  previous_run_summary_max_chars: 1200
  total_wake_budget_tokens: 12000    # excluding the cached prefix
```

Enforce with a hard truncation and a **visible marker** (`… 47 older comments omitted`).
Silent truncation is the worst option: the agent behaves as if it saw everything and
neither it nor you can tell what it missed.

Log the assembled size per wake into `task_runs.metadata` alongside usage (012). Context
size and cost are the same graph, and a context builder that quietly grows is the most
common cause of a bill that doubles without a feature landing.

---

## 7. Compression

Hermes already compresses history against `model.context_length` (documented in
`cli-config.yaml.example`) and `turn_context.py` has preflight/idle-compaction logic.
Do not add a second compression path.

What PM-OS adds is **structured checkpointing**: at the end of every run, the agent
writes a ≤200-word summary into `task_runs.summary` (the column exists). That summary,
not the raw transcript, is what a retry or a reviewer sees. Cheap, deterministic, and it
survives compression.

> Note: `trajectory_compressor.py` at the repo root is for **training-data
> post-processing**, not live context. Do not wire it into the runtime — the name
> invites exactly that mistake.

---

## 8. Tests

Assert on the context the model was *given*, not only on what it did. 017 §2's
`FakeModel.calls()` exists for this.

```
tests/hermes_cli/test_pmo_context.py
  test_wake_context_names_the_trigger
  test_board_summarised_not_dumped_at_scale
  test_blocked_tickets_listed_in_full
  test_retry_includes_previous_run_summary
  test_roster_load_included
  test_truncation_is_marked_not_silent
  test_context_size_recorded_in_run_metadata
  test_client_turn_context_excludes_internal_threads    # 010 §5
  test_no_volatile_data_in_cacheable_prefix             # the cost guard
```

`test_no_volatile_data_in_cacheable_prefix` is the one that protects the bill: build the
prefix twice, one second apart, assert byte equality.

## P0 (day-1 touchpoint) / P1

**Day 1:** `pmo_context.py` with the four builders; wake context via
`consume_gateway_turn_context_notes`, not string-prepending; trigger named explicitly;
board summarised.

**P1:** `.datansh/context.md`; budgets and marked truncation; context size in run
metadata; structured checkpoint summaries; the prefix-stability test.

## Traps

- Inline context assembly at each call site. After three sites the format is frozen by
  accident.
- Anything volatile in layers 1–5. One timestamp destroys the session's cache.
- Dumping the board. It is the default and it is wrong at any real size.
- Silent truncation.
- Wiring `trajectory_compressor.py` into the runtime.
- Not telling a retrying agent why the last attempt failed.
