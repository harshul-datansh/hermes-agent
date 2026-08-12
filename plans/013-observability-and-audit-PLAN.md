# 013 — Observability & Audit

**Why this plan exists:** when the PM assigns a ticket to the wrong agent, or a worker
marks something done that isn't, someone has to answer *"why did it do that?"* — and
the answer must not require SSH access and a SQLite client. Plans 001–008 write plenty
of data (`task_events`, `task_runs`, `audit`, `messages`) and provide no way to read it.

**Day-1 touchpoint:** write the `audit` row on every authorization decision (already in
002 §2d) and make `task_events` carry enough payload to reconstruct a timeline. Both are
write-side; the readers can come later, but only if the writes happened.
**Full build:** 2 h.

---

## 1. The question this must answer

> A ticket went from "PM created it" to "worker marked it done and it was wrong."
> Show me every step, who did it, what they saw, and what it cost.

One screen. If answering that takes three screens and a join, nobody will do it, and
the system becomes unaccountable in exactly the way that makes people stop trusting
agents.

---

## 2. The ticket timeline

A **Timeline** tab in the ticket drawer, merging four sources into one chronological
list:

| Source | Rows |
|---|---|
| `task_events` (kanban.db) | created, status changes, blocked, mention, dispatch |
| `task_comments` (kanban.db) | every comment, with `@` mentions resolved |
| `task_runs` (kanban.db) | run start/end, outcome, summary, usage (012) |
| `approvals` + `approval_escalations` (pmo.db) | gates, decisions, rank escalations |

Rendered as a vertical rail (`--line` 1px, 8px dots coloured by kind), newest last.
Each entry: actor chip, action, relative time, and an expander.

Expanders are what make it useful:

- **Run** → outcome, duration, turns, tokens, cost, and a link to the full transcript.
- **Status change** → from → to, actor, and the capability that permitted it.
- **Approval** → who raised, required rank, escalation chain, decision + note.
- **Comment** → the raw body including mentions.

Build this as one backend endpoint that does the merge server-side:

```
GET /api/plugins/pmo/tasks/{task_id}/timeline
```

Merging in the client means four round trips and a sort bug. Do it once, in Python,
guarded by `task.read`.

---

## 3. Run transcripts

`task_runs` records outcome and summary but the actual conversation is a Hermes session.
Hermes already has `hermes_cli/session_export.py`, `session_export_md.py`,
`session_export_html.py` — wire, do not rebuild.

- Store the session id in `task_runs.metadata` at spawn time. Without this the link is
  unrecoverable later.
- `GET /runs/{run_id}/transcript` renders the exported markdown, guarded by `task.read`.
- **Redact before rendering.** `tests/tools/test_kanban_redaction.py` exists upstream —
  find the redaction helper it exercises and apply it. Transcripts contain tool output,
  which contains file contents, which contains secrets.

Retention: transcripts are large. `observability.transcript_retention_days` (default 30),
after which the summary survives and the transcript is dropped. Say so in the UI when
a transcript has aged out, rather than showing an empty page.

---

## 4. Audit log

The `audit` table (001) is written on every `require()` decision (002 §2d), allowed and
denied. The denied rows are the valuable ones.

Viewer: an **Audit** screen for `project_admin` and rank ≥ 70.

- `.tbl`: `WHEN | ACTOR | ACTION | SUBJECT | PROJECT | RESULT`.
- Filters: actor, action prefix, allowed/denied, date range.
- Denied rows tinted `--danger` at 12% (design.md §5 pill conventions).

### Per-tool-call instrumentation — adopt the atlas's field set (V-39)

`17-tool-catalog-and-capability-resolution.md` §7 specifies a better record than my first
draft. Use it verbatim:

```text
tool_name · toolset
origin            = model | harness | plugin | child | background_review | curator
execution_lane    = inline | registry | tool_search | mcp | delegated
availability      = available | unavailable | unknown
scope_decision    = allowed | denied
approval_decision = auto | user | denied | not_applicable
started_at / completed_at
result_class      = success | tool_error | policy_error | timeout | cancelled
side_effect_class = none | filesystem | process | network | memory | skill | orchestration
```

Their rationale is the point: *"Without these fields, a dashboard that says 'tool call
failed' cannot distinguish an invalid model name from a missing prerequisite, a
child-scope refusal, a dangerous-command denial, or a handler exception."*

Two fields carry most of the value for us:

- **`execution_lane`** makes V-33's three lanes visible. It is how you *prove*
  `delegate_task` is unreachable (ADR-018) rather than merely absent from a toolset.
- **`origin`** distinguishes a call the model made from one the harness, a child, or the
  background-review fork made — the last of which writes skills unattended (V-34) and
  would otherwise be invisible in the timeline.

**Record the attempt, not only the dispatch.** From `14` §5: *"an out-of-scope call can be
rejected before a normal dispatch hook runs, so 'no dispatch log' does not necessarily
mean the model never emitted the call."* Hook the executor, not `registry.dispatch`. The
atlas lists "which tool calls are counted when rejected before dispatch" as an unresolved
question (`15` §5 #3) — assume the answer is "none" until measured.

Add these non-authz events to `audit` too — they are the ones that get asked about:

- role changes (who granted whom what, and when)
- approval decisions and rank escalations
- project config writes (`project.yaml` diffs)
- client message flags (010 §3)
- budget cap breaches (012)

Volume: an `audit` row per API call adds up. Sample the `allowed` reads (1 in 20) but
**never** sample denials or any write. A denial you didn't record is the one someone
will ask about.

---

## 5. Live system view

An **Activity** screen answering "what is happening right now":

- Metric row: active runs, queued `todo` tickets, blocked tickets, open escalations,
  today's spend.
- Active runs table: ticket, agent, model, elapsed, turns so far, last heartbeat,
  **Terminate** button (the forked plugin already has
  `POST /runs/{run_id}/terminate` — keep it).
- Stale-heartbeat rows flagged `--warning`; upstream reclaim handles them, but a human
  wants to see it happening.

`GET /workers/active` and `GET /diagnostics` already exist in the fork. This screen is
mostly presentation over endpoints you inherit.

---

## 6. Health and metrics

`hermes pmo doctor` (001 §4) grows into the standing health check:

```
Schema         ✅ pmo.db v1, kanban.db v?, projects.db v?
Orphans        ⚠️  2 tickets reference project p_9f2a (missing)
Handles        ✅ 4 agent identities, all profiles exist
Threads        ✅ 3 projects, 3 founders_office threads
Scope          ✅ all project_folders exist and are readable
Boards         ✅ every project has a board_slug and the board exists
Worktrees      ⚠️  1 stale worktree (7d), run `hermes pmo gc`
Budget         ✅ $142.11 MTD / $500 cap (28%)
Reconciliation ✅ recorded cost within 1.2% of `hermes insights`
Unguarded      ✅ 0 routes without a capability guard
```

That last line runs the 002 §2e coverage check as an operational command, not only a
test. It is worth having in both places — a route added by a hotfix bypasses CI more
often than you would like.

### Metrics worth tracking from day 1

| Metric | Why |
|---|---|
| Ticket cycle time (`todo` → `done`) | The headline throughput number |
| Rework rate (reworks ÷ completed) | Specification quality, from the PM |
| Escalation rate | How often the system needs a human |
| Blocker MTTR | Whether escalations get answered |
| Cost per completed ticket | The CFO's number (012 §5) |
| Cache-hit rate | The cheapest cost lever (012 §2) |
| Conflict rate | Whether `max_concurrent_workers` is set too high (011 §5) |

All derivable from data the day-1 writes already produce. Do not add tables for them;
compute on read.

---

## 7. Logging

- Structured JSON to `~/.hermes/logs/pmo.log`, one line per event, always carrying
  `project_id`, `actor`, `task_id`, `run_id` when known. A log line without
  `project_id` cannot be triaged.
- Use `hermes_logging` (repo root) — do not configure a second logging stack.
- Never log message bodies, comment bodies, or tool output at INFO. Those are the
  fields most likely to contain client data and secrets. Log ids and lengths; the
  content is retrievable through the audited endpoints.

---

## 8. Tests

```
tests/plugins/test_pmo_timeline.py
  test_timeline_merges_four_sources_in_order
  test_timeline_requires_task_read
  test_timeline_excludes_other_projects

tests/hermes_cli/test_pmo_audit.py
  test_denied_decisions_always_recorded
  test_allowed_reads_may_be_sampled
  test_writes_never_sampled
  test_role_change_recorded
  test_audit_viewer_requires_rank

tests/hermes_cli/test_pmo_doctor.py
  test_doctor_detects_orphan_project_reference
  test_doctor_detects_missing_profile_for_handle
  test_doctor_reports_unguarded_routes
```

## P0 (day-1 touchpoint) / P1

**Day 1:** `audit` written on every authz decision; `task_events` payloads rich enough
to rebuild a timeline; session id stored in `task_runs.metadata` at spawn.

**P1:** the Timeline tab; transcript rendering with redaction; the Audit screen; the
Activity screen; the full `doctor`; metric computation.

## Traps

- Not storing the session id at spawn. The transcript link is unrecoverable afterwards.
- Rendering transcripts unredacted. They contain tool output, which contains secrets.
- Sampling denials. Those are the rows that get asked about.
- Merging the timeline client-side. Four fetches and a sort bug.
- Logging message bodies at INFO. It is the quiet way client data ends up in a log file
  that outlives its retention policy.
