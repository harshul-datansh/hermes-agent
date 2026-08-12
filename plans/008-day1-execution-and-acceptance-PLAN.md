# 008 — Day-1 Execution & Acceptance

**Read this before writing any code.** It holds the schedule, the cut lines, the one
end-to-end scenario that decides whether the day succeeded, and — in **§9** — the ~40
minutes of hardening-track work that must happen today because it cannot be backfilled.

---

## 1. Schedule

Ten working blocks. Budgets are deliberately tight; the slack is in §3.

| # | Block | Plan | Budget | Output |
|---|---|---|---|---|
| 1 | Fork + scaffold + test fixtures | 001, 017 | 1:05 | `/pmo` tab renders, `pmo.db` exists, `doctor` green, `FakeModel` + `pmo_project` fixtures exist |
| 2 | Auth provider + `pmo_authz` | 002 | 1:15 | password login works, `can()`/`require()` exist, route-coverage test written **and failing** |
| 3 | Scope + project config + `scope.deny` | 003, 015 | 1:25 | `path_allowed` passes its five tests, deny rules **enforced**, `project.yaml` loads, `pmo project bootstrap` end-to-end |
| 4 | Guard the 44 routes | 002 | 0:45 | route-coverage test **passes** |
| 5 | PM agent + tickets | 004 | 1:45 | `@pm` in a thread wakes the PM; it writes `draft` tickets; `finalize` → `todo`; a worker claims one |
| 6 | Mentions + escalation | 005 | 1:15 | `@` parse/resolve/reject, block → `@pm` → escalate → thread |
| 7 | **Chat + approvals** | 006 | 2:15 | the ⭐ block — thread, cards, CFO→CEO escalation, ticket gating |
| 8 | Frontend styling + shell | 007 | 1:00 | stylesheet, `.pmo-root`, nav, project switcher |
| 9 | Frontend screens | 007 | 1:15 | board + drawer + approvals + chat wired to real APIs |
| 10 | Acceptance run + fixes | 008 | 0:45 | §4 scenario passes end-to-end |

Total ≈ 12 h 45 m, including the §9 touchpoints. That is a long day. §3 exists because
it will not all fit.

### Parallelism

If you can run more than one agent:

- **Track A (backend):** blocks 1 → 2 → 3 → 4 → 5 → 6 → 7
- **Track B (frontend):** block 8 can start immediately after block 1 against mocked
  responses; block 9 follows each backend block as its API lands.

Block 8 against mocks is genuinely independent — the stylesheet and shell depend on
`design/design.md`, not on any API.

---

## 2. Order dependencies that actually bind

```
001 ──┬─► 002 ──┬─► 004 ──┬─► 005
      │         │         └─► 006 ⭐
      └─► 003 ──┘
      └─► 007 (styling only) ──► 007 (screens, follows each API)
```

Two hard edges:

- **004 needs 003.** The PM is defined per project; without `ProjectScope` you will
  write a global PM and then rewrite it.
- **006 needs 002's ranks.** Approvals are rank arithmetic. Building the chat first and
  bolting ranks on later means reworking every decide path.

Everything else is soft.

---

## 3. Cut lines — apply in this order when you fall behind

1. **Cut 007 P1 entirely.** Backlog / My Queue / Escalations become board filters, not
   screens. Saves ~40 min, costs almost nothing.
2. **Cut the WebSocket, ship 3-second polling** (006 §4). Saves ~30 min. Poll the
   active thread and the board. Nobody notices at demo scale.
3. **Cut `kanban_decompose` integration.** The PM writes tickets directly instead of
   epic→children. Saves ~30 min, costs the impressive part of the demo — take this
   only if you must.
4. **Cut worker-agent execution.** Tickets get finalized and sit in `todo` unclaimed;
   demonstrate the board and the chat, not the code being written. Saves ~45 min.
5. **Cut `scope.deny` glob enforcement** (003 P1) — but **document it as unimplemented**.
   Shipping a config key that silently does nothing is worse than not shipping it.

### Never cut

- **002 in any part.** A permission system that is 80% wired looks like it works and
  is not. If auth cannot be finished, ship with the dashboard bound to `127.0.0.1`
  only and say so plainly in the README.
- **003 §2b** (explicit board slug everywhere). One missed `board=` is a cross-project
  read, and it will not show up in testing.
- **006's rank rules.** They are requirement #8, which was flagged as the most
  important item.
- **The route-coverage test** (002 §2e). It is the only thing that will tell you an
  unguarded route shipped.
- **Anything in §9.** Those are doors that close. Cutting one saves minutes today and
  costs days later.

---

## 4. Acceptance scenario

One narrative. If it runs clean, the day succeeded.

### Setup

```bash
hermes pmo init
hermes pmo user add --email ceo@datansh.com  --name "CEO"  --role ceo
hermes pmo user add --email cfo@datansh.com  --name "CFO"  --role cfo
hermes pmo user add --email pmo@datansh.com  --name "Dev Lead" --role staff
hermes pmo project bootstrap --slug acme --name "Acme Portal" --path C:\work\acme
hermes dashboard
```

### Run

1. **Login.** Sign in as CFO. `/pmo` shows only `acme`. ✅ #1
2. **Scope.** Bootstrap a second project `beta` with only the CEO as a member. As CFO,
   `beta` is absent from the switcher, and `GET /api/plugins/pmo/board?project_id=<beta>`
   returns **403**. ✅ #1, #9
3. **Instruct.** In Founder's Office, CFO posts:
   *"@pm we need SSO on the Acme portal before the 15th. Budget is tight."* ✅ #2, #8
4. **Discuss.** The PM replies in the thread with two clarifying questions and a
   recommendation. ✅ #4
5. **Decide + delegate.** CFO answers. The PM creates an epic and four child tickets in
   **Draft**, assigns them across `@dev-1` and `@qa` from the project roster. ✅ #4, #5
6. **Approval.** One ticket carries the `spend` label, so `approvals.rules` matches at
   rank 70. The PM raises an approval; it renders as a card in the thread; the ticket
   shows **Awaiting approval** and will not finalize. ✅ #8
7. **Escalate.** ⭐ The CFO clicks **Escalate ↑ → CEO**, reason "over the quarterly cap".
   `required_rank` → 100. The CFO's Approve button goes disabled with tooltip
   "Requires CEO". A system message appears in the thread. ✅ **#8 — the flagged
   requirement**
8. **Approve.** Sign in as CEO, approve. Ticket clears the gate. The PM finalizes all
   four: **Draft → To Do**. ✅ #5
9. **Pick up.** `@dev-1`'s worker claims its ticket, moves to **In Progress**, and works
   only inside `C:\work\acme`. Attempting to read `C:\work\beta\README.md` raises
   `ScopeViolation`. ✅ #9
10. **Blocker.** `@dev-1` calls `pmo_block` — "the SSO provider's redirect URI isn't
    registered". Ticket → **Blocked**, auto-comment `@pm Blocked: …`. ✅ #6, #7
11. **PM attempts, then escalates.** The PM comments `@dev-1` with a suggestion; it
    fails; after `pm_retry_limit` it calls `pmo_escalate`. The thread gets a system
    message and Escalations shows it. ✅ #6
12. **Human resolves.** CEO answers in the thread. The PM posts the answer to the
    ticket as `@dev-1 …` and unblocks. The worker resumes and completes. ✅ #6
13. **Channel discipline.** Grep the run transcripts: every agent-to-agent line is a
    `task_comments` row. No agent invoked a messaging tool — because none is in its
    toolset. ✅ #7
14. **Config.** Edit `C:\work\acme\.datansh\project.yaml` to add `@dev-2`. Reload. The
    handle appears in `@` autocomplete for `acme` and **not** for `beta`. ✅ #3, #10
15. **No user config.** `GET /api/plugins/pmo/me/config` → 404. No `user_*config*`
    table exists. ✅ #11
16. **Design.** Screenshot `/pmo` beside `erp.datansh.com/tasks/board`. Same nav width,
    density, pills, shadows, type. Toggle dark mode on both. ✅ #13

### Requirement coverage

| # | Verified at step |
|---|---|
| 1 | 1, 2 |
| 2 | 3 |
| 3 | 14 |
| 4 | 4, 5 |
| 5 | 5, 8 |
| 6 | 10, 11, 12 |
| 7 | 10, 13 |
| 8 | 3, 6, **7**, 8 |
| 9 | 2, 9, 14 |
| 10 | 14 |
| 11 | 15 |
| 12 | throughout (the whole UI is the fork) |
| 13 | 16 |

---

## 5. Test suite gate

```bash
python -m pytest tests/hermes_cli/test_pmo_*.py \
                 tests/plugins/test_pmo_*.py \
                 tests/tools/test_pmo_*.py -q
```

The five that must pass before you call it done — each is the only proof of its
requirement:

| Test | Proves |
|---|---|
| `test_every_pmo_route_is_guarded` | #1 — no unguarded route shipped |
| `test_cfo_can_escalate_to_ceo` | #8 — the hierarchy works |
| `test_cross_project_handle_is_unknown` | #9 — scope holds at the comms layer |
| `test_pm_toolset_excludes_messaging_tools` | #2 + #7 — one channel, structurally |
| `test_draft_tickets_are_not_dispatchable` | #5 — finalization means something |

---

## 6. Commit discipline

One commit per block, message prefixed with the plan number:

```
001: fork kanban plugin as pmo, add pmo_db schema
002: datansh auth provider + capability matrix
002: guard all pmo routes, add coverage test
...
```

Branch `feat/pm-os-day1` off `main`. Do not merge to `main` until §4 passes. Do not
push without asking.

At the end of the day, whatever the outcome, write `plans/DAY1-OUTCOME.md`: what
shipped, what was cut, what the cut items cost, and where the next day starts. Be
accurate about the cuts — a plan that claims more than it built is worse than one that
built less.

---

## 7. Known risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Guarding 44 forked routes takes longer than 45 min | High | The coverage test tells you exactly which remain. Guard in bulk with a decorator, then refine. |
| PM agent loops (wakes on its own writes) | High | Filter the watcher by author from the first line. Cap tokens per turn. |
| `kanban_decompose` produces 30 children | Medium | Cap at `board.max_children` (12) before the first run. |
| WebSocket scope leak | Medium | Ship polling if the scoped subscribe isn't done. |
| Fork's DnD breaks on restyle | Medium | Restyle with classes only; don't touch layout `position`/`transform` in the DnD path. |
| Windows path comparison bug in `path_allowed` | Medium | Five tests in 003 §5, written before the implementation. |
| Day runs long | **Certain** | §3, applied in order, without negotiating with yourself. |

---

## 8. What day 2 starts with

1. Whatever §3 cut — restore in reverse order.
2. **017** (testing) and **018** (upstream merge discipline). Both before more features:
   they are what make everything after them safe to build and cheap to maintain.
3. Then the hardening track in the order given in [`README.md`](README.md):
   `009` → `011` → `014` → `013` → `012` → `016` → `015` → `010`.

Requirement #4 is the one only partly delivered on day 1 — the PM discusses with the
founder's office and the team, but not with clients. That is **010**, and it is last in
the order deliberately: the client channel is the largest new trust surface in the
product and should land on a system that is already observable and recoverable.

---

## 9. Day-1 touchpoints demanded by the hardening track

Track B (009–018) is not day-1 work. But each of those plans needs **one small
write-side thing done today**, because it cannot be backfilled. Together these add
roughly **40 minutes** and they are the highest-leverage 40 minutes in the day.

| From | Touchpoint | Why it can't wait |
|---|---|---|
| 009 | `draft` status + the finalize gate + the ticket body template | Already in 004. Ratified here — without `draft`, "finalize" means nothing |
| 010 | `threads.kind='client'` exists; client-shaped content passes `scan_for_threats()` | Retrofitting a trust boundary after agents have been reading the content is far more expensive |
| 011 | `pmo_ticket_create` sets `workspace_kind='worktree'` + deterministic branch | The default is `scratch`. Two agents in one tree is silent mutual corruption |
| 012 | Usage + `cost_usd` (Decimal-as-string) into `task_runs.metadata`, incl. synthetic rows for PM thread turns | A month of runs with no usage data is a month you cannot analyse. Unrecoverable |
| 013 | `audit` row on every authz decision; session id stored in `task_runs.metadata` at spawn | The transcript link is unrecoverable afterwards |
| 014 | `max_runtime_seconds` on every run; cursors in the DB, not JSON files | An unbounded run costs money and produces nothing |
| 015 | `scope.deny` **enforced** (promoted from 003 P1); HTML escaping verified at all four render sites | A parsed-but-unenforced security key reads as protection |
| 016 | `needs_response_by` written when an escalation or approval is created | One column. Without it the whole SLA ladder is unbuildable retroactively |
| 017 | `FakeModel` + `pmo_project` / `pmo_two_projects` / `fake_clock` fixtures | Every test written before these exist has to be rewritten |
| 018 | `upstream` remote added; `plugins/pmo/UPSTREAM.md` written **at fork time** | Reconstructing the divergence list three weeks later is guesswork |

Two of these change the §1 schedule rather than adding to it:

- **017's fixtures belong in block 1**, not squeezed in later. Building `FakeModel`
  before the first agent test costs 20 minutes and saves every subsequent test.
- **015's `scope.deny` moves into block 3** with the rest of `pmo_scope`. It is ten
  lines once you are already writing `assert_path`; it is an afternoon later.

Everything else on this list is one or two lines added to code you were writing anyway.

**If the day runs short, these are still not the thing to cut.** §3 has the cut list.
None of these are on it, because every one of them is a door that closes.
