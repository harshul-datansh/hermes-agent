# 005 — Agent Communication (`@` on tickets) & Escalation

**Covers:** #7 (agents communicate on task comments using `@`, and **no other channel**
— mimicking Jira), #6 (agent blocker → PM → escalate to developers/founder's office).
**Depends on:** 001, 003, 004.
**Time budget:** 1 h 30 m.
**Definition of done:** worker `@pm`s a blocker on a ticket; the PM wakes, tries, fails,
escalates; a human answers in the founder's-office thread; the answer lands back on the
ticket as a comment and the worker resumes — with no message having travelled through
any channel other than `task_comments` and `messages`.

---

## 1. Why this is cheap

`task_comments(id, task_id, author, body, created_at)` already exists in `kanban.db`,
the kanban plugin already has `POST /tasks/{id}/comments`, and `tools/kanban_tools.py`
already has `kanban_comment`. Requirement #7 is a **parser plus a router** on top of an
existing table — not a messaging system.

The hard part is making task comments the durable, auditable default while preserving
Hermes' broader communication and agent capabilities.

---

## 2. Preferred coordination path and narrow safeguards (#7)

**Plan update:** task comments and `@` mentions are the PM workflow record, not a
blanket capability-removal rule. PM-OS profiles preserve the standard Hermes tool base.
Use existing approval, workspace, credential, and profile boundaries for a specific
evidenced risk instead of excluding every messaging/platform tool.

Three enforcement points, in decreasing order of strength:

1. **Rejected: toolset allowlist.** PM-OS agent profiles must not receive a blanket tool allowlist that
   removes messaging/platform tools. The following historical sentences are retained for
   audit only and are not implementation instructions. Preserve the normal Hermes tool
   base and test comment-workflow behavior instead of tool absence.
   contains no messaging/platform tool. Not a denylist — an allowlist, so a tool added
   upstream next month is excluded by default. This is the guarantee.
2. **Delegation guard.** `tools/kanban_tools.py` already has
   `_reject_delegated_child_mutation(...)`. Keep it on `pmo_comment` so a sub-agent
   spawned by a worker cannot post as its parent.
3. **Soul/skill instruction.** Make task comments the default for project work, while
   keeping normal Hermes capabilities available through existing safety controls.

Test the task-comment audit trail and escalation flow, plus capability parity for skills,
memory, delegation, messaging, and the normal Hermes tool base.

---

## 3. Mention grammar

```
@handle          -> an agent or human in THIS project
@pm              -> the project manager (reserved handle)
@founders-office -> reserved; escalates (see §6)
```

```python
MENTION_RE = re.compile(r"(?<![\w@])@([a-z0-9][a-z0-9._-]{1,31})\b")
```

Notes on the regex, each earned:

- `(?<![\w@])` stops `user@example.com` and `@@` from producing a mention. Email
  addresses in comments are common and a mis-parse pages the wrong agent.
- Lowercase only, matching the project-slug convention in `projects_db._SLUG_RE`.
- Trailing `.` / `-` are allowed inside but `\b` handles sentence-final punctuation.

Fenced code blocks and inline code are stripped before matching. An `@` inside a code
sample is not a mention, and agents paste code constantly.

---

## 4. Resolution and rejection

`hermes_cli/pmo_mentions.py`:

```python
def parse(body: str) -> list[str]:
    """Extract handles, code-stripped, deduped, order preserved."""

def resolve(scope: ProjectScope, handles: list[str]) -> tuple[list[Recipient], list[str]]:
    """Return (resolved, unknown). Only ever queries within scope.project_id."""

def post_comment(scope, task_id, author, body) -> int:
    """Validate, insert into kanban.db task_comments, fan out mentions rows."""
```

`post_comment` behaviour:

1. Assert `task.project_id == scope.project_id`. Otherwise `ScopeViolation`.
2. Parse handles.
3. Resolve against `agent_identities` **and** `project_members` for this project only.
4. If any handle is unknown → **reject the whole comment** with:
   ```json
   { "error": "unknown_mention",
     "unknown": ["dev-7"],
     "valid_handles": ["pm", "dev-1", "qa", "harshul"] }
   ```
   Rejecting rather than silently dropping is the right call: a silently-dropped
   `@dev-7` is an agent waiting forever for a reply that was never routed. The error
   carries the valid list so the agent self-corrects on the next attempt.
5. Insert the comment, then insert one `mentions` row per recipient
   (`UNIQUE(comment_id, mentionee)` makes the fan-out idempotent).
6. Insert a `task_events` row `kind='mention'` so the existing kanban watchers and the
   live-updates WebSocket pick it up for free.

Because `agent_identities` is keyed `(project_id, handle)`, a handle from another
project resolves to nothing and lands in `unknown`. Cross-project messaging is
**unnameable**, not merely forbidden (see 003 §2c).

---

## 5. Delivery

`pmo_watchers.py` (built in 004) tails `mentions WHERE delivered_at IS NULL`:

- Group pending mentions by mentionee.
- For an **agent** mentionee: wake its profile with a context block —
  ```
  You were mentioned on ticket {id} "{title}" by {author}:

  > {comment body}

  Ticket status: {status}. Assignee: {assignee}.
  Reply with pmo_comment(task_id="{id}", body="...").
  ```
- For a **human** mentionee: a dashboard notification (bell) + an unread badge on the
  ticket. No email on day 1.
- Stamp `delivered_at` **after** the wake is enqueued, in the same transaction as the
  enqueue where possible. If you stamp before, a crash loses the mention permanently;
  stamping after can double-deliver, which is recoverable.

Ordering: deliver oldest-first per recipient so a conversation reads in order.

---

## 6. Blockers and escalation (#6)

> **Corrected 2026-08-05 — upstream already types blockers.** `kanban_db` defines:
>
> ```python
> VALID_BLOCK_KINDS = {"dependency", "needs_input", "capability", "transient"}
> ```
>
> with the semantics we need already written down: `needs_input` and `capability` are
> *"truly blocked"* — they go to `blocked` **for a human** — while `dependency` and
> `transient` are machine-resolvable. That is requirement #6's taxonomy, upstream.
>
> **`pmo_block` must pass a `block_kind`.** The escalation ladder below then keys off it:
> `transient` → retry, `dependency` → link and wait, `needs_input` / `capability` →
> escalate to the PM immediately rather than after a retry budget. A worker blocked on a
> missing credential should not burn two retries first.
>
> Upstream also ships `BLOCK_RECURRENCE_LIMIT` — a loop-breaker that routes a
> repeatedly-re-blocked task to `triage` instead of back to `blocked`, with a counter that
> resets only on completion. Wire it; do not rebuild it (ADR-012). See `VERIFIED.md` V-04.

### The ladder

```
worker hits a blocker
   │ pmo_block(task_id, reason)
   ├─► task.status = 'blocked'
   ├─► auto-comment: "@pm Blocked: {reason}"
   └─► task_events kind='blocked'
        │
        ▼
   PM wakes (watcher)
   ├─ can resolve? ──► pmo_comment("@dev-1 {answer}") + pmo_unblock(task) ──► worker resumes
   └─ cannot, after `escalation.pm_retry_limit` attempts
        │ pmo_escalate(task_id, reason)
        ▼
   escalations row (level='founders_office', status='open')
   ├─► message into the project's founder's-office thread, kind='system', ref_id=task
   ├─► ticket gets an "Escalated" pill + --warning left border (design.md §8)
   └─► dashboard: Escalations inbox
        │
        ▼
   human replies in the thread
   ├─► escalations.status='answered', resolution=<text>
   ├─► PM wakes, posts the answer to the ticket as "@dev-1 {resolution}"
   └─► pmo_unblock → worker resumes
```

### Why the answer round-trips through the PM

A human could comment on the ticket directly — and they can, that still works. But the
*escalation resolution* path goes back through the PM deliberately: the PM is the only
orchestrator (#4), it needs to know the blocker cleared to keep its plan coherent, and
routing through it means one place decides whether the answer also changes the ticket's
scope. A human answer that silently unblocks behind the PM's back produces a PM working
from a stale model of its own project.

### Retry counting

`escalations.pm_retry_limit` is counted per `(task_id, blocker reason hash)`, not per
task. A task blocked twice for different reasons gets two fresh budgets. Store the
count in `task_events` payload rather than a new column.

### Timeout escalation

`escalation.blocked_timeout_minutes` (default 30): a task sitting in `blocked` with no
PM activity escalates automatically. This is the safety net for the PM itself failing
— without it, a crashed PM turn means a silently stalled project.

---

## 7. UI

### Ticket drawer — comment thread

- Comments render newest-last, with a 28px identity chip per author.
  Chip colours per `design/design.md` §8: PM `--brand-50`/`--brand`, worker
  `--surface-sunken`/`--ink-2`, human `--brand-100`/`--brand-700`.
- `@handle` renders as an inline token: `--brand-50` bg, `--brand-600` text,
  `--r-sm`, weight 500. Hover shows the role label.
- Composer with `@` autocomplete — typing `@` opens a filtered list of **this
  project's** handles. The autocomplete list is itself the scope boundary made visible
  to the user, which is worth more than a paragraph of documentation.
- System comments (auto-`@pm` on block, escalation resolutions) render with a muted
  left rule and no chip, so machine-generated lines don't read as agent authorship.

### Blocked / escalated affordances

- `blocked` column and pill: `--danger` (design.md §8).
- Escalated ticket: `--warning` pill + a 3px `--warning` left border on the card.
- An **Escalations** entry in the section nav with a count badge, listing open
  escalations across the projects you're a member of.

---

## 8. Tests

```
tests/hermes_cli/test_pmo_mentions.py
  test_parse_ignores_email_addresses
  test_parse_ignores_fenced_code_blocks
  test_parse_ignores_inline_code
  test_parse_dedupes_and_preserves_order
  test_resolve_scoped_to_project
  test_unknown_mention_rejects_whole_comment_with_valid_list
  test_cross_project_handle_is_unknown            # requirement #9 at the comms layer
  test_fanout_is_idempotent_on_retry

tests/hermes_cli/test_pmo_escalation.py
  test_block_autocomments_at_pm
  test_pm_resolution_unblocks_worker
  test_escalate_after_retry_limit
  test_escalation_posts_to_founders_thread
  test_human_answer_routes_back_through_pm
  test_blocked_timeout_auto_escalates

tests/tools/test_pmo_comms_isolation.py
  test_worker_toolset_excludes_messaging_tools     # the #7 guarantee
  test_pmo_comment_is_only_emit_path
```

---

## P0 / P1

**P0:** mention parse + scoped resolve + reject-with-valid-list; `mentions` fan-out;
watcher delivery to agents; `pmo_block` → auto `@pm`; PM resolve/unblock;
`pmo_escalate` → thread + escalations row; human answer round-trip; capability-parity
 tests; `@` autocomplete in the composer.

**P1:** human notification beyond the in-app bell (email/Slack — note that any such
bridge is *outbound only* and must never become an inbound instruction channel, which
would break #2 and #7); mention read-receipts; threaded replies inside a ticket;
per-handle mention digests.

## Traps

- Parsing `@` out of code blocks. Agents paste decorators, npm scopes (`@types/node`),
  and email addresses constantly. Strip code first, and keep the negative lookbehind.
- Stamping `delivered_at` before the wake is enqueued. A crash in between loses the
  mention with no trace and presents as "the agent ignored me".
- Letting the PM's own auto-comment wake the PM. Filter by author in the watcher.
- An outbound Slack/email bridge quietly growing an inbound path. If you build the P1
  notification bridge, make it structurally one-directional.
