# Reference — User Journeys

The plans are system-centric: tables, guards, watchers. This page is the other view —
what a **day** looks like for each person and each agent. It exists because reading it
surfaces missing UI that no schema review catches.

Each journey names the screens it needs. If a screen is not in 007 or 022, that is a gap.

---

## 1. CEO — five minutes, twice a day

**Wants:** to know nothing is stuck on them, and to be the last word on what matters.

```
Login  →  Portfolio (022 §2)
          "Needs you: 2" on Acme, 0 elsewhere
          │
          ▼
       Approvals inbox, "To decide" tab
          ap_3f21  Upgrade billing plan · escalated by CFO · "over quarterly cap"
          │  reads the thread context inline
          ▼
       Approve, with a note
          │
          ▼
       Founder's Office (skim)  — PM posted a status digest this morning
```

**Screens:** Portfolio · Approvals · Founder's Office.
**The number that matters:** *Needs you*. If it is zero across the portfolio, the CEO
closes the tab. That is the intended steady state, and the product should be honest
about it rather than manufacturing engagement.

**Gap this surfaces:** the approval card must carry enough context to decide **without
opening the ticket**. If the CEO has to navigate to the board to understand a spend
approval, the flow fails at the busiest person in the company. → 006 §5, card detail.

---

## 2. CFO — governs spend, escalates what exceeds their authority

**Wants:** to keep delivery moving within budget, and to know when to pull in the CEO.

```
Login  →  Portfolio → Spend (012 §5)
          MTD $142 / $500. Cost per ticket $10.10. Cache hit 94%.
          │
          ▼
       Approvals: ap_3f21 "Upgrade billing plan", needs rank 70 — theirs
          │  reads it; it's above the quarterly cap they agreed with the CEO
          ▼
       Escalate ↑ → CEO,  reason: "over the quarterly cap, needs your sign-off"
          │  their own Approve button greys out, tooltip "Requires CEO"
          ▼
       Founder's Office: system message posts the escalation
          │
          ▼
       @pm  "what's the run rate if we add a second engineer?"
          │  PM replies in-thread with numbers
```

**Screens:** Portfolio · Spend · Approvals · Founder's Office.

**This is requirement #8's centrepiece** and the demo (023 §2) is seeded so it is visible
on first load.

**Gap this surfaces:** the CFO needs *spend attributable to a decision*, not just to a
project — "what did that feature cost?" → 012 §6 attribution, and the top-10-expensive-
tickets table.

---

## 3. Delivery lead (project_admin, no org rank) — lives here

**Wants:** the project to move; to unblock people; to know what the agents are doing.

```
Morning
  Portfolio → Acme → Overview (022 §3)
    "Needs attention": 1 blocked 42m, 1 escalation overdue 3h
    │
    ▼
  Escalation: @dev-1 blocked on an SSO redirect URI
    │  it needs an external account change — the PM couldn't solve it
    ▼
  Answers in Founder's Office; PM relays to the ticket, unblocks the worker
    │
    ▼
  Board: reviews 3 draft tickets the PM shaped overnight
    one has vague acceptance criteria → comments "@pm tighten AC on t9c02"
    │
    ▼
  Members: adds a new engineer as `contributor`   (inline role select, 002 §6)

Afternoon
  Activity (013 §5): a run has been going 40 minutes → inspects → terminates
  Timeline on the failed ticket → reads the transcript → files a knowledge note
```

**Screens:** Overview · Escalations · Founder's Office · Board · Ticket drawer ·
Members · Activity · Timeline.

This is the heaviest user and the one whose journey touches almost every screen. If a
screen is not in this journey or the CEO/CFO ones, ask whether it needs to exist.

**Gap this surfaces:** they need to comment on a `draft` ticket **before** it is
finalized. If the drawer is read-only in `draft`, the review loop happens in chat and is
lost. → 009 §1, `comment.write` must be legal on `draft`.

---

## 4. The PM agent — event-driven, never idle-polling

```
WAKE: @pm in Founder's Office, "we need SSO before the 15th, budget is tight"
  │  context assembled (019 §5): trigger, board summary, roster load, recent decisions
  ├─ DISCUSS  → posts 2 clarifying questions + a recommendation
  │
WAKE: CFO answers
  ├─ DECIDE   → pmo_decide("Auth0 over self-hosted Keycloak", rationale, alternatives)
  ├─ creates epic → pmo_ticket_decompose → 4 children, status=draft
  ├─ assigns from roster, checks `touches` overlap (011 §4)
  ├─ one child carries label `spend` → approval rule matches at rank 70
  │     → pmo_approval_request; that ticket cannot finalize yet
  └─ pmo_ticket_finalize(other 3) → todo
  │
WAKE: @pm on ticket t8a3f1 — "@pm Blocked: redirect URI not registered"
  ├─ tries: comments "@dev-1 try the staging tenant"
  │
WAKE: still blocked, retry limit reached
  └─ pmo_escalate → escalations row + Founder's Office system message
  │
WAKE: human answered in the thread
  └─ posts "@dev-1 {resolution}" on the ticket, pmo_unblock
```

**Notice what it never does:** poll, send an email, read another project, decide its own
approval, or receive an instruction from anywhere but the founder's-office thread. Each
of those is structurally impossible (ADR-005), not merely discouraged.

---

## 5. Worker agent — one ticket at a time

```
Dispatcher claims t8a3f2 (status=todo, assignee=dev-1, budget OK, WIP under cap)
  │  worktree created: pmo/acme/t8a3f2-add-sso-callback   (011 §2)
  ├─ context: ticket, acceptance criteria, comment thread, conventions,
  │           previous run summary if this is a retry  (019 §5)
  ├─ works inside project folders only; .env refused by scope.deny (015 §1)
  ├─ heartbeats
  ├─ hits an unknown → pmo_comment("@pm which tenant should staging use?")
  │     ...waits. The PM answers. Resumes.
  └─ done → status=review (requires_review) → @qa notified
```

**The only thing it can say to anyone is a ticket comment.** No messaging tool exists in
its toolset.

---

## 6. Reviewer agent

```
WAKE: mention on t8a3f2, status=review
  ├─ context: the ticket, the acceptance criteria, and THE DIFF — not the repo
  ├─ checks each acceptance checkbox against the diff
  └─ accept  → status=done, merge/PR per board.auto_merge (011 §3)
     reject  → pmo_review_reject(reason) → comment "@dev-1 Rework: …" → draft
               third rejection auto-escalates to the PM (009 §4)
```

`qa` has no source-write toolset. A reviewer that can fix what it finds stops reporting
and starts patching, and the review signal disappears.

---

## 7. Client contact (P1, plan 010)

```
Magic link → one thread, one project. Sees only this conversation.
  │  posts "the login page is broken on Safari"
  ▼
scan_for_threats → wrapped in an untrusted envelope
  ▼
PM's CLIENT-mode turn (pm-acme-client profile, ADR-007)
  │  toolset: pmo_thread_post + pmo_approval_request. Nothing else.
  └─ drafts a summary for the founder's office
  ▼
A human posts that summary into the founder's thread — and THAT is the instruction (#2)
  ▼
Reply to the client requires human send (010 §4)
```

The client can never instruct the PM. The path from a client message to work is always
through a human in the founder's office.

---

## 8. Journey → screen matrix

| Screen | CEO | CFO | Lead | Plan |
|---|---|---|---|---|
| Portfolio | ●●● | ●●● | ●● | 022 |
| Project overview | ● | ●● | ●●● | 022 |
| Board | | ● | ●●● | 007 |
| Ticket drawer + comments | | ● | ●●● | 005, 007 |
| Founder's Office | ●● | ●●● | ●●● | 006 |
| Approvals | ●●● | ●●● | ● | 006 |
| Escalations | ● | ● | ●●● | 005 |
| Spend | ● | ●●● | ● | 012 |
| Members | | | ●● | 002 |
| Activity | | | ●● | 013 |
| Timeline | | | ●● | 013 |
| Decisions | ● | ● | ●● | 020 |
| Audit | | ● | ●● | 013 |

Nothing in the plan set renders a screen that no journey visits — and no journey needs a
screen the plans do not describe. That is the check this page exists to make.

---

## 9. What these journeys demand that the plans nearly missed

| Finding | Fix | Plan |
|---|---|---|
| CEO must decide without opening the ticket | approval card carries full context inline | 006 §5 |
| Lead must comment on `draft` tickets | `comment.write` legal in `draft` | 009 §1 |
| CFO wants cost per *decision*, not just per project | link `decisions` ↔ `task_runs` cost | 012 §6, 020 §3 |
| Everyone lands somewhere after login | Portfolio is the default route | 022 §2 |
| "Nothing needs me" must be a *fast* answer | *Needs you* computed server-side, cached | 022 §4 |
