# Handover Prompt — Datansh PM-OS

Paste everything below the line into a fresh session, with the working directory set to
`C:\Users\HarshulNamdev\Documents\datansh_multi_agents\datansh-pm-os`.

---

You are the **coordinator** for building Datansh PM-OS. You will do some work yourself and
delegate the rest to subagents, one plan each.

> **First, read `plans/026-hermes-compatibility-reset-PLAN.md`.** It is binding over the
> original plan set: retain Hermes' core agent capabilities and deliver PM-OS as a
> 5–10% additive extension. Copy Kanban once into `plugins/pmo/` and preserve the
> upstream path; do not replace Kanban with a parallel agent runtime,
> or disable skills, memory, delegation, messaging, or normal tools by default.

## What this is

The current acceptance authority is the Product-requirement audit in
`plans/IMPLEMENTATION-MATRIX.md`. It supersedes optimistic completion claims
about auth/RBAC and records the remaining Hedgi ERP integration boundary.

A project-management OS built on Hermes. Humans in a "founder's office" (CEO, CFO, CTO)
talk to a **per-project Project Manager agent**. The PM is the only orchestrator: it
decomposes work into Kanban tickets, worker agents pick them up, and all machine-to-machine
talk happens as `@mention` comments on tickets — Jira-style, no side channels. Blockers
escalate PM → founder's office. Sensitive decisions go through an approval flow with a rank
hierarchy, where a CFO can escalate to the CEO.

## Where you are

- Repo: `datansh-pm-os/`, a clone of `harshul-datansh/hermes-agent`
- Branch: `feat/pm-os-day1`, cut from `main` @ `222101071`
- Windows 11. PowerShell and Git Bash both available; each takes its own syntax.
- Plans 001-026 are implemented. The authoritative evidence is
  `plans/IMPLEMENTATION-MATRIX.md`; the copied PMO board and PMO platform are under
  `plugins/pmo/` and `plugins/platforms/pmo/`.

## Read this, in this order, before anything else

1. `plans/000-CONTEXT.md` — codebase survey and architecture decisions
2. `plans/VERIFIED.md` — **an assumption audit. 15 of 53 claims in these plans were wrong.**
   Read it before trusting any plan detail.
3. `plans/021-core-architecture-gaps-and-modifications-PLAN.md` — where the product exceeds
   what Hermes does. Amends plans 002, 004, 006, 009, 010.
4. `plans/DECISIONS.md` — 20 ADRs, three of which supersede earlier ones
5. `plans/SUBAGENT-EXECUTION.md` — file-ownership matrix, frozen interfaces, wave schedule,
   and the spawn brief you will use for every subagent
6. `plans/008-day1-execution-and-acceptance-PLAN.md` — schedule, cut lines, acceptance run

`../reverse-eng-docs/` is a prior 17-document reverse-engineering atlas of this same
checkout. **It is authoritative over the plans.** Where they disagree, it wins. Where two
of its own documents disagree, prefer the one with file:line citations.

## Non-negotiables

These are the things that will otherwise be re-derived wrong. Each cost was measured.

1. **Zero upstream modification.** `git diff --stat -- plugins/kanban` must stay empty, and
   no file without a `pmo` prefix gets edited. Everything registers through `PluginContext`
   — platform, auth provider, CLI subcommand. If you think you need to edit upstream, stop
   and ask.
2. **Grep `tests/` before trusting any claim about Hermes behaviour.** It is 2,455 files,
   the largest area in the repo. Regression names often answer the question outright.
3. **Use upstream's ticket statuses**: `triage · todo · scheduled · ready · running ·
   blocked · review · done · archived`. `list_tasks` raises `ValueError` on anything else.
   `triage` is our draft state; `create_task(triage=True)` already means "a triager must
   promote this."
4. **Keep the upstream dispatcher.** It claims only `ready`, so drafts are already invisible
   to it. Gate budget and concurrency at **promotion**, not claim.
5. **`tasks.assignee` is a profile name**, not an `@handle`. The dispatcher runs
   `hermes -p <assignee>`.
6. **`encoding="utf-8"` on every file operation.** `PLW1514` is the only lint rule this repo
   enables, added after three Windows regressions.
7. **Never `with connect() as conn:`** — it does not close. Use a `_transaction()`
   contextmanager with `try/finally: conn.close()`. A real FD-leak incident here.
8. **Never `logging.FileHandler`.** Use `hermes_logging` — stdlib rotation fails with
   `WinError 32` across processes on Windows.
9. **Exact-pin any new dependency** (`==X.Y.Z`), with a comment. Policy written in response
   to a live PyPI supply-chain worm.
10. **PM-OS toolsets are leaf sets.** No `includes`, and nothing includes them. Our PM is
    gateway-facing; one broad composite and it inherits `terminal`, which makes "agents
    communicate only via `@` on tickets" false.
11. **The platform adapter appends only.** Never edit, merge or strip an existing message —
    it invalidates extended-thinking signatures and returns HTTP 400, which will look like
    a provider outage.
12. **Do not cut plan 002 (auth/authz) for any reason.** A half-wired permission system
    looks like it works and does not. Cut features instead; the cut order is in 008 §3.

## Start here — wave 0, you do this yourself

1. **Verify U-2** (`VERIFIED.md`, "Still unverified"): hit `/api/plugins/kanban/board` with
   no cookie and confirm you get 401. If plugin routes are *not* behind the auth middleware,
   the guard model in plan 002 changes shape. One request, and it is a day-1 blocker.
2. **Start the grade-D experiments** (U-4, U-5): can a plugin platform be addressed with a
   synthetic `chat_id`/`thread_id`, and can `profile_routes` be written programmatically?
   Both gate the platform adapter in wave 3 and neither can be settled by reading source.
   If either fails, the fallback is 021 GAP 1 option 3 and wave 3 must be told before it
   starts.
3. **Execute plan 001** — fork `plugins/kanban` → `plugins/pmo`, write `hermes_cli/pmo_db.py`
   with the full schema, `hermes pmo init`, `hermes pmo doctor`.
4. **Write the frozen interface stubs** from `SUBAGENT-EXECUTION.md` §4 — real signatures,
   `raise NotImplementedError` bodies. Subagents import these and must not change them.
5. **Write the 017 fixtures**: `FakeModel`, `pmo_project`, `pmo_two_projects`, `fake_clock`.
   Every test written before these exist has to be rewritten.

Then brief wave 1 using the template in `SUBAGENT-EXECUTION.md` §6 — three subagents:
002 (auth), 003 (scope + config), 007 (stylesheet + shell).

## How to run subagents

Use the spawn brief in `SUBAGENT-EXECUTION.md` §6 **verbatim**. Do not paste the plan index
or this prompt into a subagent — a subagent that reads 26 plans has burned its context
before writing a line. The brief gives it four documents and its own plan.

Enforce the file-ownership matrix (§2). Two subagents editing one file is the failure that
costs a day. The two genuinely contended files — `plugin_api.py` and `dist/index.js` — have
a split-by-concern layout in §3; check the plugin manifest supports multiple entries before
committing to it, otherwise serialize.

Between waves, run the coordinator loop (§7): collect reports, fold wrong assertions into
`VERIFIED.md` as new `V-NN` entries, supersede any ADR that was overturned, re-freeze §4 if
a signature changed, then brief the next wave. **Do not start a wave with the previous
wave's reports unread** — a wrong assumption found in wave 2 and not propagated becomes
three subagents building on it in wave 3.

## What a subagent owes you

Its most valuable deliverable is **not** the code. It is: *"this assertion in my plan turned
out to be wrong, here is the file and line."* Fifteen were wrong before a line was written;
assume more are. Ask for it explicitly and read it first.

## Done, for day 1

The acceptance scenario is `008` §4 — sixteen steps, one narrative, from login through a
CFO escalating an approval to the CEO, through a worker hitting a blocker that reaches a
human and comes back. If it runs clean, the day succeeded.

Five tests must pass; each is the only proof of its requirement:

| Test | Proves |
|---|---|
| `test_every_pmo_route_is_guarded` | no unguarded route shipped |
| `test_cfo_can_escalate_to_ceo` | the founder's-office hierarchy works |
| `test_cross_project_handle_is_unknown` | project scope holds at the comms layer |
| `test_pm_toolset_excludes_messaging_tools` | one communication channel, structurally |
| `test_draft_tickets_are_not_dispatchable` | finalization means something |

Plus `008` §9: about 40 minutes of small write-side work that cannot be backfilled —
recording token cost per run, stamping `needs_response_by` on escalations, storing the
session id at spawn, setting `workspace_kind='worktree'`. Do not cut those; they are doors
that close.

## Working agreement

- Commit per block, prefixed with the plan number: `002: guard all pmo routes`.
- Do not merge to `main` until `008` §4 passes. Do not push without asking.
- If a plan is wrong, fix the plan as well as the code. The plans are the artifact that
  outlives the session.
- If you hit something genuinely ambiguous, say so and pick a default rather than stopping —
  but record it as an ADR so the choice is visible.

For a fresh session, begin with the completion ledger and run the regression/checker
commands above. Treat any new upstream or PMO change as a compatibility review: preserve
the copied-Kanban baseline, keep Hermes self-learning/memory/delegation/tools intact, and
add a focused parity test before changing behavior.
