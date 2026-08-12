# 009 — Ticket Lifecycle & Workflow

**Why this plan exists:** plans 004–006 describe *who* moves tickets and *why*. Nothing
describes *what a ticket is*. Without a written state machine and a body contract, four
agents will invent four conventions in the first hour and the board becomes unreadable
by lunch.

**Day-1 touchpoint (must do today):** adopt the **upstream** status vocabulary, the
`triage → todo` finalize gate, and the ticket body template. Everything else is day 2+.
**Full build:** 2 h (down from 3 — see §1).

> ## ⚠ Corrected 2026-08-05 — this plan originally invented a status vocabulary
>
> The first draft of §1 proposed `draft` / `in_progress` / `cancelled` and asserted
> *"`kanban_db` stores `status` as free text."* **Both claims are wrong.** Verification
> against `hermes_cli/kanban_db.py` found:
>
> ```python
> VALID_STATUSES = {"triage", "todo", "scheduled", "ready",
>                   "running", "blocked", "review", "done", "archived"}
> ```
>
> and `list_tasks` raises `ValueError` on anything outside it. The invented statuses
> would have thrown on the first board query.
>
> More importantly: **the gate this plan exists to build already exists.**
> `create_task(triage=True)` documents itself as *"a specifier/triager is expected to
> promote the task to `todo` once the spec is fleshed out."* That is requirement #5,
> upstream, with the same intent. See [`VERIFIED.md`](VERIFIED.md) V-02 and ADR-017.

---

## 1. Status model — use upstream's

| Upstream status | Claimable | Meaning | Our use |
|---|---|---|---|
| `triage` | ❌ | Spec not fleshed out; a triager must promote it | **The PM's draft state.** What `finalize` promotes out of |
| `todo` | ❌ | Has unfinished parents | Child tickets waiting on their epic |
| `scheduled` | ❌ | Deferred | unused day 1 |
| `ready` | ✅ | No parents, or all parents `done` | **What the dispatcher claims** |
| `running` | — | A run holds the claim lock | in-flight |
| `blocked` | ❌ | Waiting on a human or a dependency | #6 |
| `review` | ❌ | Awaiting a reviewer | already exists — 009 §4 needs no new state |
| `done` | — | Accepted | |
| `archived` | ❌ | Closed without doing, record retained | our "cancelled" |

Two things follow, and both *reduce* work:

1. **`finalize` is `triage → todo`/`ready`**, not a new state. Upstream's
   `create_task(..., triage=True)` and its promote pass (`todo → ready` when parents are
   done, `auto_promote_children`) are the machinery. `pmo_ticket_finalize` adds the four
   validation checks (004 §4) and calls it.
2. **The dispatcher already ignores `triage`.** It claims `ready`. So ADR-009's "our own
   dispatcher" is still right — for budget gating and concurrency caps — but it is no
   longer needed to *hide drafts*. That was already true.

### Legal transitions

```
triage ──finalize──► todo ──(parents done)──► ready ──claim──► running
   ▲                   ▲                        ▲                 │
   │                   │                        │                 ├──► review ──accept──► done
   │                   └────────unblock─────── blocked ◄──block────┤    │
   │                                              │                └────┴──► done
   │                                              │
   └── rework ◄── review --reject ────────────────┘
   │
   └── BLOCK_RECURRENCE_LIMIT reached (upstream loop-breaker) ──────┘
   │
   └── any ──► archived
```

Note the bottom-left edge: upstream's unblock-loop breaker **already routes a
repeatedly-re-blocked task to `triage`** (`BLOCK_RECURRENCE_LIMIT`, with a deliberate
comment that the counter resets only on completion, *not* on unblock — "exactly the
amnesia that let the loop run unbounded"). That is 014's circuit breaker for this case,
already built and better reasoned than the one I proposed.

```python
TRANSITIONS: dict[tuple[str, str], str] = {
    ("triage",  "todo"):     "board.dispatch",   # finalize
    ("triage",  "archived"): "task.delete",
    ("todo",    "ready"):    "_promote",         # upstream, parents done
    ("todo",    "triage"):   "board.dispatch",   # PM pulls it back
    ("ready",   "running"):  "_dispatcher",
    ("running", "blocked"):  "_agent",
    ("running", "review"):   "_agent",
    ("running", "done"):     "_agent",
    ("blocked", "ready"):    "board.dispatch",   # unblock
    ("review",  "done"):     "task.transition",
    ("review",  "triage"):   "board.dispatch",   # rework
    ("*",       "archived"): "task.delete",
}
```

A transition not in the table is refused with a structured error naming the legal
targets. One function, `pmo_workflow.transition(task, to, principal)`, is the only writer
of `tasks.status` in the PM-OS codepath. Grep for direct `UPDATE tasks SET status` after
the port; there should be exactly one.

**UI labels differ from storage.** The board shows *Draft · Queued · Ready · In progress ·
Blocked · Review · Done*, mapping to `triage · todo · ready · running · blocked · review ·
done`. Users get readable columns; the DB keeps upstream's vocabulary so every upstream
tool, CLI command and test keeps working. Put the mapping in `STRINGS` (025 §2).

### Why `review` is not optional

Requirement #4 says the PM discusses "within the team". A review step is where that
discussion actually happens on the artifact rather than in the abstract. It is also
the only mechanism that catches a worker agent confidently finishing the wrong thing —
which is the dominant failure mode of multi-agent delivery, not crashes.

`requires_review` comes from `board.review` in `project.yaml`:

```yaml
board:
  review:
    default: true
    reviewer: "qa"              # handle from the roster
    skip_labels: ["chore", "docs"]
```

---

## 2. The ticket body contract

`pmo_ticket_finalize` refuses a ticket without acceptance criteria (004 §4). Here is
what it checks for.

```markdown
## Context
Why this exists. Link the founder's-office message or parent epic.

## Acceptance
- [ ] Concrete, checkable statement
- [ ] Another one
(at least one checkbox; this is what `finalize` validates)

## Out of scope
What this ticket deliberately does not do. Prevents scope drift.

## Handoff
Files/modules likely involved. Known constraints. Who to @ when stuck.
```

Validation in `pmo_workflow.validate_body(body)`:

- `## Acceptance` present, with ≥1 `- [ ]` item → else refuse.
- Title ≤ 90 chars, imperative mood (heuristic: first word is not a gerund).
- If `## Out of scope` is absent, warn but allow — it is good practice, not a gate.

Keep the gate list short. A validator with eight rules gets worked around; one with
two gets respected.

### Labels

A closed set in `project.yaml`, because free-text labels are how approval rules
(006 §3) silently stop matching:

```yaml
board:
  labels: ["spend", "scope-change", "release", "chore", "docs", "bug", "security"]
```

`pmo_ticket_create` rejects an unknown label and lists the legal set — same
reject-with-valid-list pattern as mentions (005 §4). It is a good pattern; use it
everywhere an agent must pick from a vocabulary.

---

## 3. Priority, estimates, dates

| Field | Where | Notes |
|---|---|---|
| `priority` | `tasks.priority` (exists, INTEGER) | 0 = normal, 1 = high, 2 = urgent. Do not invent more. |
| estimate | `task_events` payload | `{"kind":"estimate","hours":4}`. Not a column — avoids touching upstream schema. |
| due date | `task_events` payload | Same. Rendered on the card when set. |

Resist adding columns to `tasks`. `000-CONTEXT.md` D2 explains why (`_REBUILD_SPECS`
will drop a drifted table). Anything PM-OS-specific and per-ticket goes in
`task_events` as a typed event, or in `pmo.db` keyed by `task_id`.

**Estimates are advisory.** Do not build burndown on day 1 — agent estimates are
close to meaningless until you have a few weeks of calibration data. Record them so
that calibration is possible later; do not display them as a commitment.

---

## 4. The rework loop

`review → draft` is the rework path, and it needs to carry *why*.

```python
pmo_review_reject(task_id, reason, reviewer)
```

1. Posts a comment `@{assignee} Rework: {reason}` — so the rejection travels on the
   only channel (#7).
2. Transitions `review → draft`.
3. Increments a rework counter in `task_events`.
4. On the **third** rework, auto-escalates to the PM with the full comment thread. A
   ticket that has bounced three times is not a worker problem; it is a specification
   problem, and the PM wrote the specification.

That auto-escalation is the highest-value line in this plan. Without it, a bad ticket
and a diligent worker burn tokens in a loop indefinitely, and it looks like progress
because the board keeps moving.

---

## 5. Idempotency

Agents retry. Retries duplicate. Three defences:

> **Corrected:** `kanban_db.create_task` already takes an **`idempotency_key`** parameter.
> Use it. Do not build the `idem_keys` table this plan originally proposed
> (`REFERENCE-data-model.md` lists it — strike it). See `VERIFIED.md` V-03.

| Operation | Defence |
|---|---|
| `pmo_ticket_create` | Pass upstream's `idempotency_key` = hash of (project, title, parent, assignee). Second call returns the first id. |
| `pmo_comment` | Hash of `(task_id, author, body)` within a 60 s window → return existing comment id. |
| `pmo_ticket_finalize` | Naturally idempotent — `draft → todo` on an already-`todo` ticket is a no-op success, not an error. |
| mention fan-out | `UNIQUE(comment_id, mentionee)` (already in the 001 schema). |

Make every PM-OS tool return the same shape on a duplicate as on the original. An agent
that gets an error on retry will "fix" it by changing the title, and then you have two
tickets that are 95% the same.

---

## 6. Agent roster archetypes

Requirement #6 mentions "developers". Ship four archetypes as bootstrap templates in
`.datansh/agents/`; a project keeps what it needs.

| Handle | Role | Toolset | Notes |
|---|---|---|---|
| `pm` | Project Manager | orchestrator | Always present, exactly one (004) |
| `dev-N` | Engineer | coding | Scale by adding `dev-2`, `dev-3` |
| `qa` | Reviewer | review (read + comment, no write to source) | Owns `review → done` |
| `research` | Researcher | read + web | No repo write access at all |

`qa` having no source-write toolset is deliberate: a reviewer that can fix what it
finds stops reporting and starts patching, and the review signal disappears.

---

## 7. Board hygiene

Automatic, run by the PM on its scheduled tick (004 P1):

- Ticket in `in_progress` with no heartbeat for `2 × max_runtime` → reclaim (upstream
  `kanban_db` already has reclaim; wire it).
- Ticket in `blocked` past `escalation.blocked_timeout_minutes` → escalate (005 §6).
- Ticket in `draft` for > 7 days → PM prompted to finalize or cancel.
- `review` older than 48 h → nudge the reviewer via comment.

Each of these is three lines and each prevents a specific way boards rot.

---

## 8. Tests

```
tests/hermes_cli/test_pmo_workflow.py
  test_illegal_transition_refused_with_legal_targets
  test_only_pm_can_finalize
  test_dispatcher_only_claims_todo
  test_cancelled_preserves_comments
  test_status_written_through_single_function     # grep-style guard
  test_validate_body_requires_acceptance_checkbox
  test_unknown_label_rejected_with_valid_list
  test_third_rework_escalates_to_pm

tests/hermes_cli/test_pmo_idempotency.py
  test_duplicate_create_returns_same_id
  test_duplicate_comment_within_window_is_noop
  test_finalize_is_idempotent
```

## Traps

- Letting `status` be written from more than one place. Within a week there will be
  five, and the transition table becomes decorative.
- Adding columns to `tasks`. Use `task_events` or `pmo.db`.
- A validator with too many rules. Two gates that hold beat eight that get bypassed.
- Displaying agent estimates as commitments before you have calibration data.
