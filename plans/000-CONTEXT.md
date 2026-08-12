# 000 — Shared Context

Every plan in this directory assumes what is written here. Read it once, then read the
numbered plans.

> **Implementation amendment:** [026](026-hermes-compatibility-reset-PLAN.md) supersedes
> any direction here or in the numbered plans that replaces a Hermes subsystem, copies
> Kanban, broadly removes Hermes agent capabilities, or exceeds the 5–10% additive-change
> target. Preserve and extend this newer Hermes fork.

---

## 1. Workspace

| | |
|---|---|
| Path | `C:\Users\HarshulNamdev\Documents\datansh_multi_agents\datansh-pm-os` |
| Origin | `https://github.com/harshul-datansh/hermes-agent.git` |
| Base | `main` @ `222101071` ("fmt(js): `npm run fix` on merge (#79155)") |
| Working branch | `feat/pm-os-day1` |
| Sibling checkout | `../hermes-agent` on `codex/datansh-agent-os` — **prior art, do not copy from it.** This is a fresh start by request. |

The `datansh-agent-os/` directory that exists in the sibling checkout is *not* on
`main` and is not part of this build.

---

## 2. What we are building

A project-management OS on top of Hermes. Humans in the "founder's office" (CEO, CFO,
CTO) talk to a **per-project Project Manager agent**. The PM is the only orchestrator:
it decomposes work into Kanban tickets, worker agents pick tickets up, and all
machine-to-machine talk happens as `@mention` comments on tickets — Jira-style, no
side channels. Blockers escalate PM → founder's office. Sensitive decisions go through
an approval flow with a rank hierarchy (CFO can escalate to CEO).

---

## 3. Codebase survey — what already exists

This was verified against `main` in this checkout. It is the single most important
input to the plans: **most of the substrate is already there.**

### 3a. Kanban (reuse wholesale)

| Thing | Where |
|---|---|
| SQLite board store, ~9k lines | `hermes_cli/kanban_db.py` → `~/.hermes/kanban.db` |
| CLI surface | `hermes_cli/kanban.py`, `kanban_decompose.py`, `kanban_specify.py`, `kanban_swarm.py`, `kanban_diagnostics.py` |
| Agent tools | `tools/kanban_tools.py` — `kanban_show/list/create/complete/block/unblock/comment/attach/attach_url/attachments/heartbeat/link` |
| Gateway watchers | `gateway/kanban_watchers.py` (tails `task_events`, pushes to chat platforms) |
| HTTP API (~44 routes) | `plugins/kanban/dashboard/plugin_api.py` |
| Dashboard UI | `plugins/kanban/dashboard/dist/index.js` |
| Docs | `website/docs/user-guide/features/kanban*.md`, `docs/kanban/multi-gateway.md` |

**Existing schema** (`kanban_db.SCHEMA_SQL`):

```
tasks(id, title, body, assignee, status, priority, created_by, created_at,
      started_at, completed_at, workspace_kind, workspace_path, branch_name,
      project_id, claim_lock, ...)
task_links(parent_id, child_id)
task_comments(id, task_id, author, body, created_at)
task_events(id, task_id, run_id, kind, payload, created_at)
task_runs(id, task_id, profile, step_key, status, claim_lock, claim_expires,
          worker_pid, max_runtime_seconds, last_heartbeat_at, started_at,
          ended_at, outcome, summary, metadata, error)
task_attachments(id, task_id, filename, stored_path, content_type, size, uploaded_by, created_at)
kanban_notify_subs(task_id, platform, chat_id, chat_type, thread_id, user_id, ...)
```

Load-bearing facts:
- `tasks.project_id` **already exists** and anchors a task's worktree under the
  project's primary repo.
- `task_comments` **already exists** — requirement #7 (`@` comms) is a parser + a
  router on top of it, not a new table.
- `task_runs.outcome` already includes `blocked` and `gave_up` — requirement #6's
  state machine has hooks.
- Boards are multi-tenant already: `GET/POST/PATCH/DELETE /boards`, `POST
  /boards/{slug}/switch`.

### 3b. Projects (reuse)

`hermes_cli/projects_db.py` → `~/.hermes/projects.db`:

```
projects(id, slug, name, description, icon, color, board_slug, primary_path, created_at, archived)
project_folders(project_id, path, label, is_primary, added_at)
project_meta(key, value)
discovered_repos(root, label, last_seen)
```

`projects.board_slug` already links a project to a Kanban board. `project_folders`
already models the folder scope requirement #9 needs. Slugs are validated against
`^[a-z0-9][a-z0-9\-_]{0,63}$` — no traversal, safe as a path component.

### 3c. Auth (extend)

`hermes_cli/dashboard_auth/` is a pluggable provider system.

- `base.py` defines `DashboardAuthProvider` with `start_login` / `complete_login` /
  `verify_session` / `refresh_session` / `revoke_session`, plus opt-in
  `supports_password` (`complete_password_login`) and `supports_token`
  (`verify_token` → `TokenPrincipal(principal, provider, scopes)`).
- `middleware.py` attaches a verified `Session` to **`request.state.session`**.
- `Session(user_id, email, display_name, org_id, provider, expires_at, access_token, refresh_token)`.
- Bundled providers: `plugins/dashboard_auth/{basic,self_hosted,nous,drain}`.

**Critical gap.** `plugins/kanban/dashboard/plugin_api.py` says so itself, verbatim:

> *"The auth still isn't multi-user — anyone who can read the printed URL+token gets
> full dashboard access."*

So: **authentication exists and is pluggable. Authorization does not exist at all.**
There are no roles, no per-project membership, no per-route capability checks.
Requirement #1 is genuinely net-new work and it is the riskiest item in the build.

### 3d. Frontend (fork)

- `web/` — Vite + React 19 + TS. Pages in `web/src/pages/`, plugin host in
  `web/src/plugins/` (`registry.ts`, `PluginPage.tsx`, `usePlugins.ts`, `slots.ts`).
- Plugins are discovered from `plugins/*/dashboard/manifest.json`:
  ```json
  { "name":"kanban", "label":"Kanban", "icon":"Package", "version":"1.0.0",
    "tab": {"path":"/kanban","position":"after:skills"},
    "entry":"dist/index.js", "css":"dist/style.css", "api":"plugin_api.py" }
  ```
- **`plugins/kanban/dashboard/dist/index.js` is not a build artifact.** Its own header
  reads *"Plain IIFE, no build step. Uses `window.__HERMES_PLUGIN_SDK__` for React +
  shadcn primitives."* 4,280 lines of readable, hand-written source. There is no
  TypeScript source for it anywhere in the repo.

  This is the single best piece of news in the survey: **requirement #12 ("copy paste
  the frontend service where kanban lives") is a directory copy with zero build
  tooling.** Edit-and-refresh, no bundler, no `npm run build`.

### 3e. Config

`hermes_cli/config.py` → `$HERMES_HOME/config.yaml`, profile-aware, with
`load_config()` / `save_config()`. The kanban plugin already reads
`config.kanban.{orchestrator_profile, default_assignee, auto_decompose,
auto_promote_children}` and writes it back via `PUT /api/plugins/kanban/orchestration`.

There is **no project-scoped config today** — config is global per profile.
Requirement #10 is net-new.

### 3f. Agents

An "agent" is a Hermes **profile** (`hermes_cli/profiles.py`, `web/src/pages/ProfilesPage.tsx`).
`tools/kanban_tools.py` already has `_require_orchestrator_tool(...)` and
`_reject_delegated_child_mutation(...)` guards — an orchestrator-vs-worker distinction
exists in the tool layer and requirement #4 builds directly on it.

---

## 4. Architecture decisions

> **These align with Hermes's own stated design principles** (`01-architecture-overview.md`
> §6), which is worth knowing before you read them as clever workarounds:
>
> 1. *"Per-conversation prompt caching is sacred — never mutate past context
>    mid-conversation"* → ADR-014, plan 019
> 2. *"Core is the narrow waist — new capability arrives as CLI commands, skills,
>    service-gated tools, plugins, MCP servers"* → ADR-006, ADR-007, and the zero-upstream-
>    modification result (V-01)
> 3. *"Smallest footprint governs wiring — expansive at edges, conservative at the waist"*
>
> PM-OS is entirely edge capability. That is not a way *around* the repo; it is what the
> repo is designed for.

### D1 — Fork the plugin, don't modify it

Create `plugins/pmo/` as a *new bundled plugin* by copying `plugins/kanban/`.
Leave `plugins/kanban/` byte-identical to upstream.

*Why:* we want to keep pulling `upstream/main` (NousResearch) forever. Every line we
change inside `plugins/kanban/` becomes a merge conflict. A sibling plugin has none.
It also means we can run both boards side by side while migrating.

Names: plugin `pmo`, label `Datansh PM-OS`, tab `/pmo`, API mounted at
`/api/plugins/pmo/`.

### D2 — Two databases

| DB | Owner | Contents |
|---|---|---|
| `~/.hermes/kanban.db` | upstream | tasks, comments, events, runs, attachments |
| `~/.hermes/projects.db` | upstream | projects, project_folders |
| `~/.hermes/pmo.db` | **us** | identities, roles, memberships, threads, messages, approvals, escalations, mentions, audit |

*Why:* upstream owns `kanban_db.SCHEMA_SQL` and rebuilds tables that drift
(`_REBUILD_SPECS` will `DROP TABLE` on drift). Adding our columns there is a data-loss
footgun on the next upstream merge. A separate file has zero collision surface.

Cross-DB references are by id, resolved in the service layer. SQLite `ATTACH` is
available if a join is genuinely needed; prefer two queries.

### D3 — Project == board == folder scope

One project maps to exactly one Kanban board (`projects.board_slug`) and one set of
`project_folders`. That single mapping satisfies #3 (PM is per-project), #5 (tickets
live on the project board) and #9 (folder scope) with no new primitive.

### D4 — Deny by default, one choke point

A single FastAPI dependency `require(action, project_id)` guards every PM-OS route.
No route may read `request.state.session` directly. A route with no guard fails a test
(see `002`, "route coverage test").

### D5 — All agent comms are task comments

> **Superseded by plan 026:** Task comments are the preferred auditable workflow, but
> PM-OS profiles retain Hermes messaging, skills, memory, delegation, and normal tools.
> Use scoped safety controls for the particular risky action; do not apply a blanket
> toolset allowlist.

The following historical proposal is rejected: the PM-OS agent profiles get a **toolset allowlist** that excludes every messaging
tool. The only way an agent can emit words to another agent is `pmo_comment`. This is
enforced in config, not by prompt instruction — a prompt is a request, an allowlist is
a guarantee.

---

## 5. Requirement → plan map

| # | Requirement | Day 1 | Hardening |
|---|---|---|---|
| 1 | Auth + authz, role-scoped read/write | 002 | 013, 015 |
| 2 | Project-level view; instructions reach PM only from founder's office | 004, 006 | 010, 016 |
| 3 | PM is per-project, never shared | 003, 004 | — |
| 4 | PM is orchestrator + discussant (founder's office, **clients**, team) | 004 | **010** |
| 5 | PM finalizes tickets on the Kanban board; agents pick them up | 004 | 009, 011 |
| 6 | Blocker → PM → escalate to human | 005 | 014, 016 |
| 7 | Agents communicate only via `@` on task comments | 005 | 016 |
| 8 | Dashboard + group chat, founder's office hierarchy, approvals | 006 ⭐ | 012, 016 |
| 9 | Folder/project scopes; no cross-project reads | 003 | 011, 015 |
| 10 | Project-specific `config.yaml` | 003 | `REFERENCE-project-yaml.md` |
| 11 | No user-level config | 003 (explicit non-goal + assertion test) | 016 §4 (one explained exception) |
| 12 | Copy the kanban frontend service, build on top | 001, 007 | 018 |
| 13 | `design.md` from `erp.datansh.com` | ✅ done → `../design/design.md` | — |

Requirement #4 is the one only partly served on day 1: the PM discusses with the
founder's office and the team, but the **client** channel is plan 010. Do not describe
client support as shipped until it is.

---

## 6. Scope honesty — read this before promising a day

You asked for a one-day build. Here is the straight assessment, stated once so the
rest of the plans can get on with it.

**Achievable in one focused day by one capable agent:** a working vertical slice —
password login with roles, per-project boards with enforced scope, a PM agent that
takes an instruction from a founder's-office thread and writes tickets, workers that
pick them up, `@mention` comment routing, blocker escalation, an approval card with
CFO→CEO escalation, and a restyled dashboard that looks like the ERP.

**Not achievable in one day, and the plans say so at each point:** production-grade
SSO, a hardened permission matrix with a full audit trail, real-time multi-user chat
presence, a polished pixel-perfect port of all 4,280 lines of the kanban UI, and
migration tooling.

Every plan below is split into **P0 (ship today)** and **P1 (next)**. `008` holds the
cut lines. If you are behind schedule, cut from P1 first and never cut from `002`
(auth) — a half-built permission system is worse than none, because it looks like it
works.

### Two tracks

Plans **001–008** are the day-1 build. Plans **009–025** are the architecture and
hardening track: the things a working system needs that a working demo does not — a
ticket state machine, git concurrency, cost control, observability, recovery, security,
notifications, context engineering, project memory, the portfolio view, onboarding,
scale, and merge discipline against a fast-moving upstream.

**One of them is day-1 reading.** [`021-core-architecture-gaps…`](021-core-architecture-gaps-and-modifications-PLAN.md)
enumerates the places where the product exceeds what Hermes does — the founder's-office
group chat has no equivalent in the repo — and decides each one. It **amends 002, 004,
006, 009 and 010**, so building those without it means building the superseded design.
Its headline result: everything requirement #8 needs is reachable through supported
extension points, and total upstream modification is **one line**.

Track B is explicitly **not** day-1 work. But each of those plans names a **day-1
touchpoint**: one small write-side thing that must happen today because it cannot be
backfilled. Recording token cost per run, stamping `needs_response_by` on an escalation,
storing the session id at spawn — each is one or two lines, and each is a door that
closes. They are consolidated in `008` §9 and total about 40 minutes.
