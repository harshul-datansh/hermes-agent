# 014 — Resilience, Failure Modes & Recovery

> **Compatibility replacement (implemented 2026-08-05).** PM-OS has no parallel
> watcher/reclaim database or restart service. `hermes pmo doctor` projects existing
> project bindings, profiles, conversation tasks, worktrees, claims, retries, budgets,
> reconciliation, native diagnostics, and compatibility checks. Its optional restart
> drain is bounded and delegates to native per-task reclaim and the existing PMO
> mention-delivery edge. The read-only default changes nothing. Any older `pmo.db`,
> new cursor table, or second recovery-runtime proposal below is superseded.

**Why this plan exists:** plans 001–008 describe the happy path. In a multi-agent system
the unhappy paths are not edge cases — a worker crashing, a model API 529-ing, or the
PM looping are ordinary Tuesday events. Every one of them currently ends with a ticket
stuck in a state nothing will move it out of.

**Day-1 touchpoint:** rely on the upstream reclaim/heartbeat machinery instead of
routing around it, and make sure every PM-OS-created run sets `max_runtime_seconds`.
An unbounded run is the failure that costs money while producing nothing.
**Full build:** 2 h.

---

## 1. What upstream already handles

`kanban_db` and its tests already cover a surprising amount. Read these before writing
anything:

| Concern | Where |
|---|---|
| Claim locks + expiry | `tasks.claim_lock`, `task_runs.claim_lock/claim_expires` |
| Heartbeats | `task_runs.last_heartbeat_at`, `kanban_heartbeat` tool |
| Runtime caps | `task_runs.max_runtime_seconds` |
| Crash outcomes | `task_runs.outcome ∈ {crashed, timed_out, spawn_failed, gave_up, reclaimed}` |
| Reclaim | `POST /tasks/{id}/reclaim`, `test_kanban_reclaim_claim_lock_guard.py` |
| Dispatch locking | `test_kanban_dispatch_lock.py`, `test_kanban_init_lock_bounded.py` |
| SQLite busy retry | `test_kanban_write_txn_busy_retry.py` |
| Signal handling | `test_signal_handler_kanban_worker.py` |
| Stop control | `agent/kanban_stop.py` |

**The design instruction: do not build a parallel recovery mechanism.** The failure
mode of this plan is a second, subtly different reclaim path racing the first.

---

## 2. Failure matrix

| # | Failure | Detection | Recovery | Owner |
|---|---|---|---|---|
| 1 | Worker process crashes | heartbeat stale > `2 × interval` | upstream reclaim → `todo`, run `outcome='crashed'` | dispatcher |
| 2 | Worker hangs (no crash, no progress) | `max_runtime_seconds` exceeded | terminate, `outcome='timed_out'`, retry once, then `blocked` | dispatcher |
| 3 | Worker loops (progress, no convergence) | `per_run_max_turns` (012) | end run, `outcome='gave_up'`, comment `@pm` | run loop |
| 4 | PM turn fails mid-decomposition | children created, epic still `draft` | idempotent create (009 §5) makes retry safe; PM re-runs | watcher |
| 5 | PM loops (wakes on own writes) | wake rate > N/min for one profile | circuit breaker (§3) | watcher |
| 6 | Model API down / rate-limited | `FailoverReason` (see below) | **per-class recovery**, not one path | run loop |
| 7 | SQLite `database is locked` | `sqlite3.OperationalError` | upstream busy-retry with backoff | store layer |
| 8 | Two dispatchers running | duplicate claims | claim lock is authoritative; second loses | upstream |
| 9 | Gateway/watcher dies | mentions undelivered, PM never wakes | watchdog + `delivered_at IS NULL` backlog drains on restart | service |
| 10 | Merge conflict | rebase fails | `pmo_block` → PM (011 §4) | worker |
| 11 | Disk full (worktrees, transcripts) | write failure | `doctor` warns at 85%; `gc` reclaims | operator |
| 12 | Corrupt `project.yaml` | validation fails | **hard error**, project won't load (003 §3) | operator |
| 13 | Human never answers an escalation | escalation open > SLA | reminder, then re-notify at higher rank (016) | watcher |
| 14 | Approval raised, approver on leave | same as 13 | rank escalation is already the mechanism (006 §3) | human |
| 15 | Background-review fork races the next turn | none — daemon thread, no `.join()` | don't assume turn N's review finished before N+1; disable the loop on workers (004) | design |
| 16 | Iteration budget exhausted | `_turn_exit_reason = "budget_exhausted"` | distinct `task_runs.outcome`, **not** a crash. Mirror `_budget_grace_call`: one extra call so the run still writes its summary | run loop |

Rows 3, 5 and 13 are the ones with no upstream equivalent. Build those; wire the rest.

### Row 6 in detail — consume `FailoverReason`, don't re-derive it (V-41)

`agent/error_classifier.py` already unifies vendor error shapes into one enum, and each
`ClassifiedError` carries `retryable`, `should_compress`, `should_rotate_credential`,
`should_fallback` *"so the retry loop never has to re-derive intent from a raw error."*
Map ticket behaviour onto it rather than inventing a second taxonomy:

| `FailoverReason` | Ticket behaviour |
|---|---|
| `context_overflow` / `payload_too_large` | compress and retry; if it recurs, the ticket is too big → back to `triage` for the PM to split |
| `rate_limit` (user key) | back off, retry within the run budget |
| `upstream_rate_limit` (aggregator) | rotate credential, then retry — **a different path**, and conflating the two is why this distinction exists |
| `overloaded` / `server_error` / `timeout` | fallback chain, then `blocked` (`block_kind='transient'`) |
| `auth` / `auth_permanent` / `billing` | **stop the project**, not the ticket. Escalate to founder's office — no amount of retrying fixes an unpaid invoice |
| `content_policy_blocked` | **never retry.** `blocked` with `block_kind='capability'` → straight to a human (005 §6) |
| `model_not_found` | config error → `pmo doctor`, block the ticket |
| `thinking_signature` | see V-40 — an injected-message bug on our side, not a provider fault |

**Caveat:** Bedrock has its **own** parallel classifier and does not feed this taxonomy
(`05` §6). A project routed to Bedrock gets no hints — treat every Bedrock error as
`unknown` and lean on the run budget instead.

---

## 3. Circuit breakers

Three, each guarding a different runaway:

```yaml
resilience:
  pm_wake_rate_limit: 12          # wakes per project per hour
  worker_retry_limit: 2           # retries per ticket before `blocked`
  consecutive_failure_limit: 3    # per agent handle, then quarantine
```

**PM wake rate.** Exceeding it stops waking the PM, posts a system message in the
founder's-office thread ("PM paused: wake rate exceeded, N pending items"), and requires
a human to resume. A looping PM is expensive and produces a board full of near-duplicate
tickets that take longer to clean up than the loop took to create.

**Agent quarantine.** Three consecutive failed runs for one handle → mark
`agent_identities.active = 0`, stop assigning to it, comment `@pm`. Usually a broken
profile config or a bad model endpoint, and it is better to lose one agent than to have
it fail every ticket it touches.

**Retry budget.** Per ticket, not per agent. A ticket that fails twice goes `blocked`
with the run summaries attached — that is a specification problem for the PM, not a
retry problem.

All three write to `audit` (013).

---

## 4. Restart semantics

Every PM-OS process must survive a restart mid-flight. The rule: **all state is in
SQLite, nothing important is in memory.**

| Process | On restart |
|---|---|
| Dispatcher | Reclaim expired claims, resume claiming |
| Watcher | Resume from persisted cursors (`messages.id`, `task_events.id`, `mentions.delivered_at IS NULL`) |
| Dashboard | Stateless; sessions live in `pmo.db` |

Cursors go in `project_meta` or a small `pmo_cursors` table — **not** in a file next to
the process. `datansh-agent-os/.state/router-cursor.json` in the sibling checkout is the
pattern to avoid: a JSON cursor file drifts from the DB it points into, and after a
crash you cannot tell which is right.

Startup drain, in order: expired claims → undelivered mentions → unprocessed thread
messages. Bound each pass; a watcher that comes up and immediately wakes forty agents
because it drained a weekend's backlog is its own incident.

---

## 5. Degraded modes

Explicit, named, and visible in the UI — a system that silently half-works is worse
than one that says what it cannot do.

| Mode | Trigger | Behaviour | Banner |
|---|---|---|---|
| **Read-only** | DB unwritable, disk full | Board renders, all writes 503 | `--danger` |
| **No-dispatch** | budget cap, or operator pause | Board and chat work; no claims | `--warning` |
| **No-live** | WebSocket down | Fall back to polling | muted, in-header |
| **Agents paused** | circuit breaker | Humans work normally; agents idle | `--warning` |

`hermes pmo pause --project acme --reason "..."` and `hermes pmo resume`. Operators need
a stop button that is not "kill the process", and the reason should show up in the
founder's-office thread so nobody wonders why the board went quiet.

---

## 6. Backups

`~/.hermes/pmo.db` holds the approval trail, the chat, and the role assignments. It is
the one file whose loss is unrecoverable — tickets can be re-created, a decision record
cannot.

- `hermes pmo backup` → `VACUUM INTO` a timestamped copy. Use `VACUUM INTO`, not a file
  copy: copying a WAL-mode SQLite file under load produces a corrupt backup that
  restores cleanly right up until the moment you need it.
- Nightly via the existing cron subsystem (`hermes_cli/cron.py` — already present).
- Keep 14 daily + 8 weekly.
- `hermes pmo restore --from <file> --dry-run` reports what would change. Test the
  restore path once, on day 2, before you need it.

`hermes_cli/backup.py` exists upstream — check whether it can be extended before adding
a second backup mechanism.

---

## 7. Tests

```
tests/hermes_cli/test_pmo_resilience.py
  test_crashed_run_reclaims_to_todo
  test_timeout_retries_once_then_blocks
  test_turn_limit_ends_run_with_gave_up
  test_pm_wake_rate_limit_pauses_and_notifies
  test_agent_quarantined_after_consecutive_failures
  test_ticket_retry_budget_is_per_ticket
  test_provider_error_falls_back_then_blocks

tests/hermes_cli/test_pmo_restart.py
  test_cursors_persist_in_db_not_files
  test_startup_drains_undelivered_mentions
  test_startup_drain_is_bounded
  test_expired_claims_reclaimed_on_start

tests/hermes_cli/test_pmo_backup.py
  test_backup_uses_vacuum_into
  test_restore_dry_run_reports_changes
```

## P0 (day-1 touchpoint) / P1

**Day 1:** `max_runtime_seconds` set on every PM-OS run; heartbeats wired; cursors in
the DB rather than files; no parallel reclaim path.

**P1:** the three circuit breakers; degraded modes + banners; `pause`/`resume`; backup
and restore; the startup drain bounding; escalation SLA reminders.

## Traps

- Building a second reclaim mechanism. Race it against upstream's and both lose.
- Cursors in JSON files. After a crash you cannot tell whether the file or the DB is
  authoritative.
- Unbounded startup drain. Coming back from an outage should not wake every agent at
  once.
- `cp` on a WAL-mode SQLite file. Use `VACUUM INTO`.
- Retry budgets per agent rather than per ticket. A bad ticket then burns the whole
  roster's budget one agent at a time.
