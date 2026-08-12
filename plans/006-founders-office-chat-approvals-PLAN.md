# 006 — Founder's Office: Chat, Hierarchy & Approvals ⭐

**Covers:** requirement #8 — *"a dashboard + chat for conversation between project
manager and founder's office. Can be a group chat, and approvals for modifications.
CFO, CEO, etc. **most important.** The hierarchy should be there — CFO can ask for CEO
approval here for tickets, if he's requiring it."*
**Depends on:** 001 (tables), 002 (org ranks), 004 (the PM agent).

> **Compatibility reset (binding):** The custom chat, hierarchy, approval database,
> and live-message implementation described below are deferred. PM-OS uses Hermes
> conversations, Kanban comments/events, configured gateway channels, and existing
> approval controls. Preserve those capability paths rather than replacing them with
> a PM-specific message runtime.
> ## ⚠ Amended by [021](021-core-architecture-gaps-and-modifications-PLAN.md) — read that first
>
> This plan implicitly assumed we would build a conversation runtime: our own message
> store, our own agent invocation, our own concurrency control. **We should not.**
>
> Hermes has no multi-participant agent chat, but it has a **gateway platform plugin
> path** that `ADDING_A_PLATFORM.md` says *"requires zero changes to core Hermes code"* —
> and it brings session binding, turn leasing (`gateway/turn_lease.py`) and event-driven
> wake (`gateway/wake.py`) with it.
>
> **What changes:** §1's model and §4's posting path. `pmo_chat.post()` writes the
> `messages` row (still our durable record and the UI's source) **and**, when `@pm` is
> present, emits a `MessageEvent` into the gateway. `profile_routing` resolves the thread
> to `pm-<slug>`; the turn lease serialises it — which also replaces the bespoke
> single-flight lock hand-waved in 004 §3.
>
> **What does not change:** the schema, the rank rules, the approval lifecycle, the UI,
> and every test below. §2, §3, §5 and §7 stand as written.
>
> See ADR-006.

**Time budget:** 2 h 30 m — the largest single block. It is the flagged priority.
**Definition of done:** CEO, CFO and the PM agent are in one project thread. The CFO
raises an approval on a ticket, decides they want the CEO on it, escalates the required
rank, the CEO approves, and the linked ticket becomes finalizable — with every step
visible in the thread and recorded in `approval_escalations`.

---

## 1. Model

```
project ──1:N── threads ──1:N── messages
                   │              └─ kind: text | system | approval_ref
                   ├── thread_members (users + 'agent:<project>/<handle>')
                   └── approvals ──1:N── approval_escalations
                          └─ optional task_id → the ticket being gated
```

Every project gets one `kind='founders_office'` thread at bootstrap, with the CEO, the
CFO/CTO, the bootstrapping user, and `agent:<project>/pm` as members.

`kind` is on `threads` from day 1 so `client` and `team` threads (P1) need no
migration. Only `founders_office` is built today.

---

## 2. The hierarchy

Ranks live in `org_roles` (seeded in 001):

| key | label | rank |
|---|---|---|
| `ceo` | CEO | 100 |
| `cfo` | CFO | 70 |
| `cto` | CTO | 70 |
| `founder_office` | Founder's Office | 50 |
| `staff` | Staff | 10 |

**CFO and CTO deliberately share rank 70.** Peers cannot approve each other's
escalations; both must go up to the CEO. That is what gives "the CFO can ask for CEO
approval" real content — if CFO could satisfy a CTO-level requirement, the hierarchy
would be decorative.

Decision rule, in one line:

> A user may decide approval `A` iff `max(rank(user)) >= A.required_rank`.

Agents hold no org rank at all (002 §4), so `approval.decide` is structurally
impossible for them. There is no row that could grant it.

---

## 3. Approvals

### Lifecycle

```
                    ┌──────────────────────────────────────┐
   raise            │                                      │  escalate
   (PM / CFO / any  ▼                                      │  (raises required_rank)
    contributor)  pending ──approve──► approved            │
                    │  │                                   │
                    │  └──reject───► rejected              │
                    │                                      │
                    └──withdraw──► withdrawn ◄─────────────┘
```

`approved` / `rejected` / `withdrawn` are terminal. Re-raising creates a **new**
approval linked to the old one — never mutate a decided row. An approval record is
evidence; overwriting it destroys the audit trail that is the point of having one.

### Raising

- A **PM agent** raises one when a ticket matches an `approvals.rules` entry in
  `.datansh/project.yaml` (003 §3), or when it judges a decision above its authority.
  `required_rank` comes from the matched rule.
- A **human** raises one from the thread composer or the ticket drawer.

```python
pmo_approval_request(
    project_id, title, detail,
    required_rank: int,
    task_id: str | None = None,
    thread_id: str | None = None,
) -> approval_id
```

Raising posts a `messages` row with `kind='approval_ref'`, `ref_id=<approval_id>`, so
the card renders inline in the conversation. The chat is the record; there is no
separate approvals silo that people forget to check.

### Escalating — the requirement-#8 centrepiece

```python
pmo_approval_escalate(approval_id, to_rank: int, reason: str, by_user: str)
```

Rules:

1. Caller must hold `approval.escalate` (rank ≥ 50).
2. `to_rank > approval.required_rank`. You can only escalate **up**. De-escalation
   would let a CFO lower a CEO-required gate to their own level, which is the whole
   control, inverted.
3. Approval must be `pending`.
4. Writes an `approval_escalations` row (from/to/by/reason), updates
   `approvals.required_rank`, posts a system message in the thread, and notifies
   everyone at or above the new rank.

UI: an **"Escalate ↑"** button on a pending approval card, visible to anyone who can
escalate but whose own rank is below the *desired* level — plus always visible to any
rank-50+ member who wants a second signature. It opens a small dialog: target role
(dropdown of ranks above current) + a reason field (required — an unexplained
escalation is noise to whoever receives it).

Concretely, the requirement's example: a CFO looking at a rank-70 spend approval on a
ticket clicks **Escalate ↑ → CEO**, types "over the quarterly cap, needs your sign-off",
and the card moves to `required_rank=100`. The CFO can no longer decide it. The CEO
gets a notification and the thread shows *"CFO escalated this to CEO: over the
quarterly cap…"*.

### Gating tickets

An approval with a `task_id` blocks `pmo_ticket_finalize` (004 §4) until `approved`.
The ticket shows an **Awaiting approval** pill (`--warning`, design.md §8) and its
`draft → todo` transition returns a structured refusal naming the approval.

On `approved`: system comment on the ticket, PM woken so it can finalize.
On `rejected`: system comment carrying `decision_note`, ticket stays `draft`, PM woken
so it can revise or close.

---

## 4. Chat

### Posting

```python
pmo_thread_post(thread_id, author, body, kind='text', ref_id=None) -> message_id
```

- Guarded by `thread.post` (contributor and up) + thread membership.
- `@pm` in a human message wakes the PM (004 §3). This is the **only** inbound
  instruction channel to the PM — requirement #2.
- `@handle` mentions of humans notify them.
- Agent posts go through `pmo_thread_post` too, so a PM reply and a human message are
  the same kind of row and render identically.

### Live updates

The forked plugin already tails a `/events` WebSocket for `task_events`. Extend it to
multiplex thread messages and approval state changes on the same socket rather than
opening a second one.

**Scope check on the socket.** 002's traps call this out and it belongs here too: the
subscribe frame carries a `project_id`, and the server validates membership before
streaming. Without it, a viewer on project A receives project B's founder's-office
conversation — which is both a scope leak (#9) and a confidentiality problem, since
this thread is where spend and scope get discussed.

Day-1 acceptable fallback if the socket work runs long: 3-second polling on the active
thread. Ship polling rather than an unscoped socket.

### Presence, typing indicators, read receipts

**Not day 1.** Say so in the UI's absence rather than half-building it.

---

## 5. Dashboard

The `/pmo` tab's section nav (per `design/design.md` §6e):

```
Board            ← the kanban (007)
Backlog          ← draft tickets awaiting finalization
My Queue         ← tickets assigned to me
Approvals        ← inbox, filtered to what I can decide, plus what I raised
Founder's Office ← the group chat  ⭐
Escalations      ← open blockers needing human input (005)
Members          ← roles + access (002)
```

### Founder's Office screen

Two-pane, following the ERP shell (design.md §4):

```
┌────────────────────────────────┬───────────────────────┐
│ page-title "Founder's Office"  │  Context rail 320px   │
│ page-sub  "{project} · 4 members"                      │
├────────────────────────────────┤  • Pending approvals  │
│                                │  • Open escalations   │
│  message list                  │  • Ticket being       │
│   ├ human message              │    discussed          │
│   ├ PM agent message           │  • Members + ranks    │
│   ├ ┌ approval card ┐          │                       │
│   │ │ title         │          │                       │
│   │ │ detail        │          │                       │
│   │ │ Needs: CFO    │          │                       │
│   │ │ [Approve][Reject][Escalate ↑] │                  │
│   │ └───────────────┘          │                       │
│   └ system: "CFO escalated to CEO: ..."                │
├────────────────────────────────┤                       │
│  composer  @mention autocomplete│                      │
└────────────────────────────────┴───────────────────────┘
```

Card details:

- **Message rows** — 28px identity chip, name, rank badge for humans (`CEO`, `CFO`),
  role label for agents (`Project Manager`), relative timestamp on the right.
  Chip colours per design.md §8.
- **Approval card** — a `.widget` inline in the stream, `--warning` left border while
  pending, `--success` when approved, `--danger` when rejected. Header shows
  `Needs: {role at required_rank}`. Buttons are disabled with a tooltip ("Requires CEO")
  when your rank is insufficient — visible-but-disabled beats hidden, because the user
  needs to know *why* they cannot act.
- **System messages** — muted, centred, no chip. Escalations, decisions, ticket
  finalizations.
- **Composer** — `@` autocomplete over thread members + this project's agent handles.
  A `/approve`-style slash affordance is P1; the button on the card is enough today.

---

## 6. API

```
GET    /api/plugins/pmo/projects/{pid}/threads
GET    /api/plugins/pmo/threads/{tid}/messages?after=<id>&limit=100
POST   /api/plugins/pmo/threads/{tid}/messages
GET    /api/plugins/pmo/projects/{pid}/approvals?status=pending
POST   /api/plugins/pmo/projects/{pid}/approvals
POST   /api/plugins/pmo/approvals/{aid}/decide      {decision, note}
POST   /api/plugins/pmo/approvals/{aid}/escalate    {to_rank, reason}
POST   /api/plugins/pmo/approvals/{aid}/withdraw
WS     /api/plugins/pmo/events?project_id=...       (multiplexed)
```

Every route guarded per 002. `/decide` additionally checks
`rank(user) >= approval.required_rank` **at decision time**, not at render time — the
required rank can be escalated between the page loading and the button being clicked,
and a stale UI must not be able to land a decision the server would now refuse.

---

## 7. Tests

```
tests/hermes_cli/test_pmo_chat.py
  test_thread_membership_required_to_read
  test_thread_membership_required_to_post
  test_at_pm_wakes_pm_agent
  test_agent_post_and_human_post_are_same_shape

tests/hermes_cli/test_pmo_approvals.py
  test_cfo_cannot_decide_ceo_rank_approval
  test_cfo_can_escalate_to_ceo                       # requirement #8 core
  test_escalation_records_from_and_to_rank
  test_cannot_de_escalate
  test_cannot_escalate_decided_approval
  test_peer_rank_cannot_decide_peer_escalation       # CFO vs CTO, both rank 70
  test_agent_can_never_decide
  test_decide_rechecks_rank_at_decision_time
  test_approved_approval_unblocks_ticket_finalize
  test_rejected_approval_keeps_ticket_draft
  test_decided_approval_is_immutable

tests/plugins/test_pmo_events_scope.py
  test_ws_subscribe_rejects_non_member_project       # the confidentiality guard
```

`test_cfo_can_escalate_to_ceo` and `test_peer_rank_cannot_decide_peer_escalation` are
the pair that proves the requirement. Write them first.

---

## P0 / P1

**P0:** founder's-office thread per project; post/read with membership guards; `@pm`
waking the PM; approvals raise/decide/escalate/withdraw with the rank rules; inline
approval cards in the chat; ticket gating on `finalize`; the escalate dialog; the
Approvals inbox; scoped live updates (socket or polling).

**P1:** client threads and team threads; presence/typing/read receipts; attachments in
chat; approval delegation ("acting CEO" while away); scheduled approval reminders;
slash commands in the composer; message editing and deletion (note: deletion in an
approval trail needs a tombstone, not a `DELETE` — think before adding it).

## Traps

- **Do not mutate a decided approval.** Re-raise as a new row linked to the old one.
  The trail is the product here.
- **Re-check rank server-side at decision time.** A card rendered before an escalation
  still shows an enabled Approve button in a stale tab.
- **Do not let de-escalation exist.** It inverts the entire control.
- **Scope the WebSocket subscribe by project membership.** This thread carries spend
  and scope discussions; an unscoped socket is the most consequential leak in the
  build.
- Hidden vs disabled buttons: disable with a reason. A CFO who cannot see the Approve
  button assumes the app is broken; one who sees "Requires CEO" reaches for Escalate.
