# Architecture Decision Log

Append-only. One entry per decision that would be expensive to reverse or that someone
will otherwise re-litigate. **Never edit a decided entry** — supersede it with a new one
and set the old to `Superseded by ADR-NNN`.

This mirrors the rule 020 §3 applies to the PM agent's own `decisions` table. The history
of what was believed and when is the product.

Status: `Accepted` · `Superseded` · `Reversed` · `Proposed`

---

## ADR-001 — Fork the kanban plugin rather than modify it
**Accepted** · 2026-08-05 · plan 001, 018

Upstream (`NousResearch/hermes-agent`) is very active. Every line changed inside
`plugins/kanban/` is a merge conflict forever.

**Decision:** copy `plugins/kanban/` → `plugins/pmo/`. Leave the original byte-identical.
CI asserts `git diff --stat -- plugins/kanban` is empty.

**Cost:** the fork drifts; `plugins/pmo/UPSTREAM.md` tracks provenance and skipped fixes.
**Alternative rejected:** modify in place — cheaper today, unmergeable within a month.

---

## ADR-002 — Separate `pmo.db` rather than extending `kanban.db`
**Accepted** · 2026-08-05 · plan 001

`kanban_db.py` owns `SCHEMA_SQL` and `_REBUILD_SPECS`, which **`DROP TABLE`** a table
whose columns have drifted. Adding our columns there is a data-loss footgun on the next
upstream merge.

**Decision:** three databases. `kanban.db` and `projects.db` stay upstream's, read-only
for us. `~/.hermes/pmo.db` holds everything PM-OS-specific. Cross-DB references by id,
resolved in the service layer.

**Cost:** no cross-DB joins; two queries where one would do.

---

## ADR-003 — Project == board == folder set
**Accepted** · 2026-08-05 · plan 003

`projects.board_slug`, `project_folders` and `tasks.project_id` already exist upstream.

**Decision:** one project maps to exactly one board and one folder set. That single
mapping satisfies #3 (per-project PM), #5 (tickets on the project board) and #9 (folder
scope) with no new primitive.

---

## ADR-004 — Two authorization axes: org rank and project role
**Accepted** · 2026-08-05 · plan 002, 006

Requirement #8 needs a hierarchy (CFO escalating to CEO); requirement #1 needs read/write
scoping. One flat role list cannot answer both.

**Decision:** **org rank** answers "senior enough to approve?"; **project role** answers
"can write in this project?". A CEO with no membership in `acme` cannot edit its tickets
but can decide approvals raised there.

**Consequence:** CFO and CTO share rank 70 deliberately — peers cannot approve each
other, both must escalate to CEO. Without that, the hierarchy is decorative.

---

## ADR-005 — Enforcement lives in toolset allowlists, not prompts
**Accepted, amended 2026-08-05** · plans 004, 005, 010 · see VERIFIED V-20

Requirements #2 and #7 are absolute ("always", "no other way"). A prompt is a request.

**Decision:** every "the agent must not X" is implemented as X being absent from the
agent's toolset. Tests assert on the **resolved** toolset, not on soul text.

**Amendment (V-20).** A named toolset is **not a closed set**.
`toolsets.get_toolset(name, include_registry=True)` unions the static list with the
registry at call time — that is how plugins and MCP servers add tools into an existing
toolset without editing `toolsets.py`. So the allowlist is a guarantee only if we close it:

1. Dedicated toolset names (`pmo-orchestrator`, `pmo-worker`, `pmo-reviewer`,
   `pmo-client`) that nothing else registers into.
2. **Assert the resolved tool list at turn start**, refuse the turn on mismatch. The
   runtime twin of the test 004 §8 specifies.
3. Gate `pmo_tools` on an env marker, following `kanban_tools` — which has *"zero schema
   footprint for any session that isn't inside a dispatcher-spawned kanban task."*

**Consequence:** agents hold no org rank, so `approval.decide` is not merely denied — no
row could grant it. And "absent from the profile" is now precisely "absent from the
*resolved* set, checked at turn start."

---

## ADR-006 — PM-OS threads are a Hermes **gateway platform**, not a bespoke runtime
**Accepted** · 2026-08-05 · plan 021 GAP 1 — *supersedes the implicit design in plan 006*

Requirement #8's group chat has no equivalent in Hermes: it has 1:1 sessions and platform
bridges. Plan 006 implicitly assumed we would build a conversation runtime.

**Decision:** register PM-OS as a gateway platform plugin
(`plugins/platforms/pmo/`). Threads become chats on platform `pmo`;
`gateway/profile_routing.py` routes thread → PM profile; `gateway/turn_lease.py` serialises
turns; `gateway/wake.py` handles event-driven wakes. `ADDING_A_PLATFORM.md`: *"This
requires zero changes to core Hermes code."*

**Enabled by:** requirement #7. Because agents talk only on tickets, a thread never needs
more than one agent — which is exactly the shape `profile_routing` supports. The
constraint that looked limiting is what makes it fit.

**Alternative rejected:** own runtime. `turn_lease.py`'s docstring documents a concurrency
bug (#64934) involving interleaved transcript flushes and a permanent `user;user` wedge.
Not a bug worth rediscovering.

**Consequence:** our `messages` table remains the durable product record and UI source;
the gateway session is the agent's own memory. Two views, different lifetimes.

---

## ADR-007 — Per-turn toolset variation via a second profile
**Accepted** · 2026-08-05 · plan 021 GAP 2 — *amends plan 010*

Hermes binds toolsets per profile. Plan 010 needs the PM to hold a restricted toolset on a
client-thread turn, as a boundary rather than a check.

**Decision:** two profiles per PM — `pm-<slug>` (full) and `pm-<slug>-client`
(restricted) — selected by `profile_routes` on `thread_id` (specificity 14).

**Alternative rejected:** a guard inside each tool. That is enforcement *inside* the
boundary, and it fails open for any tool added later.

---

## ADR-008 — Core Hermes dashboard pages stay single-tenant admin surfaces
**Accepted** · 2026-08-05 · plan 021 GAP 4

Once PM-OS is multi-user, a CFO has a dashboard login. Core pages (Env, Files, Config,
MCP, Skills, Sessions) expose the whole machine.

**Decision:** PM-OS is the multi-user surface; core pages are admin-only, enforced at
deployment (reverse proxy or tab gating). Guarding our routes while `/api/env` stays open
to any authenticated session is the same hole with more steps.

---

## ADR-009 — Our own dispatcher, gated on `draft`
**Superseded by ADR-017** · 2026-08-05

Rested on two claims that verification overturned: that `draft` was a new concept
(it is `triage`, V-02) and that the core dispatcher would need patching to ignore it
(it claims only `ready`, V-19). Retained for the record.

---

## ADR-017 — Keep the upstream dispatcher; gate at **promotion**, not claim
**Accepted** · 2026-08-05 · supersedes ADR-009 · VERIFIED V-19, V-02

The upstream dispatcher already claims only `status='ready'`, so `triage` and `todo` are
invisible to it and the finalize gate needs no dispatcher change. Its CAS claim —

```sql
UPDATE tasks SET status='running', claim_lock=?, claim_expires=?
WHERE id=? AND status='ready' AND claim_lock IS NULL
```

— is described by the reverse-engineering atlas as *"a real, working example of the
lease/CAS pattern done well… degrades safely by construction,"* with an incident-tuned
reclaim guard (#23025: never steal from a live same-host PID unless its heartbeat is stale
by an hour) and `kanban.failure_limit` auto-blocking.

**Decision:** use it. PM-OS controls **promotion** (`triage → todo → ready`), which the PM
already owns. Budget caps (012 §3) and `max_concurrent_workers` (011 §5) are enforced by
refusing to promote.

**Why promotion is the better gate anyway:** refusing to promote costs nothing; refusing
to claim wastes a dispatcher tick and leaves a `ready` ticket that looks available and
isn't.

**Consequence:** ADR-012's "build no parallel path" now covers the dispatcher — where it
should have applied from the start. One fewer subsystem to write, test and keep in sync.

---

## ADR-018 — PM-OS workers are dispatcher-spawned, never `delegate_task`-spawned
**Accepted** · 2026-08-05 · VERIFIED V-31

`_build_child_agent` constructs delegated children with `skip_context_files=True,
skip_memory=True` and a fresh scoped prompt capped at 32,000 chars.

**Decision:** the PM never delegates ticket work via `delegate_task`. Work reaches a worker
only by being promoted to `ready` and claimed by the dispatcher, which spawns
`hermes -p <profile>` with a profile-scoped `HERMES_HOME`.

**Why:** a delegated child would lose `.datansh/context.md` (019 §4) and project memory
(020) — the two things that make a worker competent on *this* project. It would also lose
the board-pinned isolation that `HERMES_KANBAN_BOARD` provides (V-22).

**Corroboration:** `tools/kanban_tools.py` already carries
`_reject_delegated_child_mutation` — upstream reached the same conclusion.

**Recorded so nobody "optimises" the PM into delegating directly.** It looks faster and it
silently removes the project from the agent's context.

---

## ADR-010 — Never read "active" state
**Accepted** · 2026-08-05 · plan 021 GAP 5, plan 003

Hermes scopes by profile; "active board" and "active profile" are process-global.

**Decision:** every call passes explicit scope. No active board, no active profile, no
active project. One profile per (project, agent role), so profile-global state becomes
project-scoped for free — including memory (020 §1).

**Enforced by:** a CI grep for `connect_closing()` without `board=`.

---

## ADR-011 — Client channel is evidence, not instruction
**Accepted** · 2026-08-05 · plan 010

Requirement #4 puts the PM in conversation with end clients. A client message is untrusted
text reaching an orchestrator with tool access.

**Decision:** founder's office = instruction (trusted). Client = evidence (untrusted).
`@pm` in a client thread creates a triage item, never an instruction. Enforced by
ADR-007's restricted profile plus an untrusted-data envelope and
`tools/threat_patterns.scan_for_threats()`.

**Also:** `client_contacts` is a separate table from `users`, so no row could grant a
client an org rank.

---

## ADR-012 — Reuse upstream resilience machinery; build no parallel path
**Accepted** · 2026-08-05 · plan 014

`kanban_db` already has claim locks, expiry, heartbeats, runtime caps, reclaim, busy-retry
and signal handling, all tested.

**Decision:** wire them. The failure mode of this plan is a second, subtly different
reclaim path racing the first.

**We build only what has no upstream equivalent:** the three circuit breakers (PM wake
rate, agent quarantine, per-ticket retry budget) and the escalation SLA ladder.

---

## ADR-013 — Notifications are egress-only
**Accepted** · 2026-08-05 · plan 016

Requirements #2 and #7 say the PM's inbound is the founder's office and agents talk only
on tickets. A reply-to-act notification bridge silently reintroduces a second inbound
channel.

**Decision:** notifications project state that already exists and carry a deep link back
into the dashboard. **The delivery module has no write access to `messages` or
`task_comments`**, so the shortcut is unavailable to a future contributor in a hurry.

---

## ADR-014 — Frozen-snapshot context; volatile data never in the cached prefix
**Accepted** · 2026-08-05 · plan 019

`tools/memory_tool.py` documents the pattern: memory is injected as a frozen snapshot at
session start; mid-session writes hit disk but not the prompt, preserving the prefix cache.

**Decision:** adopt it for all of layers 1–5 (identity, soul, project context, memory,
roster). One timestamp in the prefix roughly triples the bill.

**Enforced by:** `test_no_volatile_data_in_cacheable_prefix` — build the prefix twice a
second apart, assert byte equality. And 012's cache-hit tile makes a regression visible.

---

## ADR-015 — Decisions are append-only
**Accepted** · 2026-08-05 · plans 006, 020

Approvals, `decisions`, and this file.

**Decision:** never mutate a decided record. Reversal inserts a new row and marks the old
`superseded`. Applies to `approvals` (a decided approval is immutable; re-raise instead),
`decisions` (020 §3) and ADRs here.

---

## ADR-016 — English-only on day 1, strings centralised
**Accepted** · 2026-08-05 · plan 021 GAP 8, plan 025

Hermes ships 20 locales; our no-build plugin ships English. That is a regression against
the host, recorded rather than discovered later.

**Decision:** English-only, with every user-facing string in one `STRINGS` object and a
`t()` accessor from the first commit. Extraction becomes mechanical once 007 P1's Vite
build lands. A half-populated locale file is worse than English-only.

---

## ADR-019 — PM-OS toolsets are leaf sets, asserted at both schema and execution
**Accepted** · 2026-08-05 · VERIFIED V-37, V-38 · tightens ADR-005

`toolsets.py` resolves `includes` recursively, supports registry-only toolsets **and
aliases**, and unions the registry at call time. Meanwhile our PM is gateway-facing
(ADR-006), and Hermes ships 20+ broad `hermes-*` composites.

The atlas's own security checklist (`17` §8) names the failure: *"Verify gateway-facing
tools do not inherit local-terminal capability through an overly broad composite."*

**Decision:** `pmo-orchestrator` / `pmo-worker` / `pmo-reviewer` / `pmo-client` are leaf
sets — no `includes`, and nothing includes them. Tests assert the transitive closure in
both directions, and assert the PM's resolved set by **tool name** (aliases and registry
unions bypass toolset-level checks).

**And assert at two gates, not one.** `17` §4 lists seven; *"the same tool can appear in a
schema but still be refused at execution time — schema exposure and action authorization
are separate decisions."* Check what the model was **told** it can do and what will
actually **run**. Checking one leaves a gap in either direction.

**Why it matters:** a shell is a communication channel. If the PM inherits `terminal`,
requirement #7 is false and nothing in the product would show it.

---

## ADR-020 — The platform adapter is append-only
**Accepted** · 2026-08-05 · VERIFIED V-40 · constrains ADR-006

`anthropic_adapter.py` carries a tracked failure mode where *"message
editing/stripping/merging invalidates an extended-thinking signature and causes an
outright HTTP 400."* `prompt_caching.py` re-applies cache markers after failover because
*"caching state is treated as fragile and re-derivable, not a stable invariant."*

**Decision:** the PM-OS adapter appends events and never edits, merges or strips an
existing message. The gateway owns transcript shape; we supply an event.

**Why it needs recording:** the failure is a **400, not a degradation** — it will present
as a provider outage and be debugged in the wrong place. The `user;user` alternation wedge
that motivated `turn_lease.py` is the same class of problem
(`14` §6: transcript shape is a reliability boundary).

---

## Template

## ADR-021 — Preserve Hermes; PM-OS is additive and budgeted
**Accepted** · 2026-08-05 · plan 026

The reverse-engineering atlas shows that Hermes already owns the agent loop, tool and
approval protocol, skills, memory, context compression, delegation, Kanban, gateway,
MCP, and plugin seams. The earlier PM-OS plans proposed parallel implementations and
broad capability removal, which would turn a newer Hermes fork into a divergent product.

**Decision:** PM-OS extends existing Hermes surfaces and preserves normal agent
capabilities by default. Target 5–10% changed production files; existing Hermes core and
Kanban files are read-only by default. A deviation needs an ADR with measured impact,
failed extension alternatives, rollback, and capability-parity tests.

**Supersedes where incompatible:** earlier directions to create a parallel
runtime/database, apply blanket toolset allowlists, or disable skills, memory,
delegation, messaging, or standard tools as PM-OS defaults. The ADR-001 Kanban copy is
retained as the merge-isolation boundary for PM-OS work.

---

```markdown
## ADR-0NN — <one-line decision>
**Proposed|Accepted|Superseded by ADR-0MM** · YYYY-MM-DD · plan NNN

<The forces. What made this a choice rather than an obvious step.>

**Decision:** <what we do>

**Alternative rejected:** <the strongest one, and why>

**Cost / consequence:** <what this makes harder>
```
