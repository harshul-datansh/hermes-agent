# 022 — Project-Level View & Portfolio

**Why this plan exists:** requirement #2 opens with *"should have a project level view"*
before it says anything about the PM agent. I mapped that whole requirement to 004 and
006 — the PM and the chat — and never built the view itself. There is a board, a chat and
an approvals inbox; there is no screen that answers *"how is this project going?"* and no
screen that answers *"how are all my projects going?"*

The ERP has exactly this and calls it the Manager Dashboard (`design/design.md` §6a).
That is the model.

**Day-1 touchpoint:** none — this is pure read-side over data the other plans already
write. It is the cheapest high-visibility plan in the set, which is why it is worth
doing early on day 2.
**Full build:** 2 h.

---

## 1. Two levels

| View | Audience | Question |
|---|---|---|
| **Portfolio** | founder's office (CEO, CFO, CTO) | Which of my projects need me? |
| **Project overview** | PM's humans, project_admin | How is *this* project going, and what is stuck? |

Both are read-only aggregations. No new tables — everything derives from `tasks`,
`task_runs`, `approvals`, `escalations`, `decisions`, `messages`.

---

## 2. Portfolio

The landing screen after login. One row per project **you are a member of** (server-side
filtered — 003), plus every project you can approve in by rank (006 §2), because a CEO
needs to see a project they govern but do not work in.

Following the ERP's Projects table (design.md §6b) with delivery columns:

```
PROJECT      STATUS    PROGRESS       BLOCKED  NEEDS YOU  SPEND MTD   LAST ACTIVITY
Acme Portal  ● Active  ███████░░ 14/22    1        2 ⚠      $142      12m ago
Beta CRM     ● Active  ███░░░░░░  4/18    0        0        $38       3h ago
Gamma        ○ Paused  ██████░░░ 11/17    3 ⚠      1 ⚠      $0        2d ago
```

- **Needs you** is the only column people will actually scan. It counts approvals at or
  below your rank plus escalations you can resolve. If it is zero across the board, the
  founder's office has nothing to do — which is the intended steady state.
- **Status** derives, it is not stored: `Paused` when 014's pause is set, `At risk` when
  there is an overdue escalation or a budget breach, otherwise `Active`.
- **Progress** is `done / (total − cancelled)`. Say so in a tooltip; an unexplained
  fraction invites people to infer a schedule that does not exist.
- Sorted by **Needs you** desc, then last activity. Not alphabetically.

Above the table, a `.metric` row across all visible projects: projects active, tickets in
flight, blocked, approvals awaiting you, MTD spend.

---

## 3. Project overview

The default tab when you open a project — before Board. Board is where you work;
overview is where you orient.

```
Acme Portal                                          [Open board]  [Founder's Office]
Client · Acme Corp   ·   PM: @pm   ·   4 agents   ·   Active

┌ metrics ────────────────────────────────────────────────────────────────┐
│  22 Tickets   14 Done   2 In progress   1 Blocked   2 Awaiting approval  │
│  $142 MTD     $10.10 per ticket        94% cache hit                      │
└──────────────────────────────────────────────────────────────────────────┘

┌ Needs attention ────────────────────────────┐ ┌ Recent decisions ────────┐
│ ⚠ t8a3f1 Blocked 42m — @dev-1               │ │ Auth0 over Keycloak      │
│   "redirect URI not registered"             │ │   @pm · 2d               │
│ ⚠ ap_3f21 Awaiting CFO — spend              │ │ Postgres 16              │
│ ⚠ es_77b  Escalation overdue 3h             │ │   CEO · 5d               │
└─────────────────────────────────────────────┘ └──────────────────────────┘

┌ Flow (last 14 days) ────────────────────────┐ ┌ Team ────────────────────┐
│  cumulative flow, done/in-progress/blocked  │ │ @dev-1  1 active  8 done │
│                                             │ │ @qa     idle      6 rev  │
│                                             │ │ @research idle    2 done │
└─────────────────────────────────────────────┘ └──────────────────────────┘

┌ Activity ───────────────────────────────────────────────────────────────┐
│ 12m  @dev-1 blocked t8a3f1                                               │
│ 34m  @pm finalized 4 tickets                                             │
│  2h  CFO escalated ap_3f21 to CEO                                        │
└──────────────────────────────────────────────────────────────────────────┘
```

**Needs attention is the point of the screen.** It is the ERP's "Upcoming actions" widget
(design.md §6a): title + muted descriptor on the left, status pill on the right, and it
is the first thing below the metrics. Everything on it is actionable and links straight
to the thing.

The flow chart uses the `--chart-*` tokens (design.md §2a) and the `dataviz` conventions.
Cumulative flow, not burndown — burndown implies a committed scope and a date, and 009 §3
is explicit that agent estimates are not commitments yet.

---

## 4. Endpoints

```
GET /api/plugins/pmo/portfolio                      → per-project rollup, membership+rank filtered
GET /api/plugins/pmo/projects/{pid}/overview        → the whole screen in one response
GET /api/plugins/pmo/projects/{pid}/flow?days=14    → the chart series
```

One response per screen. A dashboard that fires nine requests to render is slow on a cold
cache and impossible to reason about when a number is wrong.

Cache the rollup for 30 s (in-process). These are aggregate queries over the whole board
and the portfolio is the most-loaded screen in the product; recomputing per request is how
024's scale problems start.

---

## 5. Where the numbers come from

| Number | Source | Plan |
|---|---|---|
| ticket counts by status | `tasks` grouped | 009 |
| blocked + duration | `tasks` + `task_events` | 005 |
| awaiting approval | `approvals` where pending | 006 |
| needs you | `approvals.required_rank ≤ your rank` + `escalations` open | 006, 016 |
| spend MTD, cost/ticket, cache hit | `task_runs.metadata` | 012 |
| agent load | `task_runs` active + `tasks.assignee` | 011 |
| recent decisions | `decisions` where active | 020 |
| activity | merged `task_events` + `messages` + `approvals` | 013 §2 |

Every one of these already exists as a day-1 or hardening write. **That is why this plan
has no day-1 touchpoint** — it is entirely derived. It is also why it should be built
early: it is the screen that makes the rest of the system's data visible, and therefore
the screen that tells you whether the writes are correct.

---

## 6. Tests

```
tests/plugins/test_pmo_portfolio.py
  test_portfolio_lists_only_member_projects
  test_portfolio_includes_projects_you_can_approve_in    # rank, not membership
  test_needs_you_counts_only_what_you_can_act_on
  test_sorted_by_needs_you_then_activity
  test_overview_is_one_request
  test_rollup_cached_briefly
  test_progress_excludes_cancelled
  test_paused_project_shows_paused_status
```

`test_portfolio_includes_projects_you_can_approve_in` encodes 002 §2a's two-axis rule at
the UI level: rank grants visibility for governance, membership grants write access. A
CEO seeing only projects they are a member of would be a bug, not a safety feature.

## P0 / P1

**P0 (day 2, first thing):** portfolio table; project overview with metrics and *Needs
attention*; the two endpoints.

**P1:** the flow chart; the activity feed; the 30 s cache; per-agent load table;
portfolio-level spend rollup.

## Traps

- Building this before the writes it reads exist. It will look finished and be empty.
- Nine requests per screen.
- Burndown instead of cumulative flow.
- Sorting the portfolio alphabetically. The list exists to surface exceptions.
- Showing a progress fraction with no explanation of the denominator.
