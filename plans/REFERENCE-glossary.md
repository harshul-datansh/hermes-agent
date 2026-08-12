# Reference — Glossary & Naming

Four different id systems overlap in this codebase (Hermes profiles, project slugs,
agent handles, user ids) and they are easy to confuse. This page is the disambiguation.

---

## Identity

| Term | Format | Lives in | Example | Notes |
|---|---|---|---|---|
| **User** | `u_<hex8>` | `pmo.db users` | `u_3f21ab90` | A human. Has an email. |
| **Org role** | key | `pmo.db org_roles` | `cfo` | Carries a **rank**. Founder's-office hierarchy. |
| **Rank** | int | `org_roles.rank` | `70` | Approval authority. CEO 100 > CFO/CTO 70 > founder_office 50 > staff 10. |
| **Project role** | key | `project_members.role_key` | `contributor` | Read/write scope *inside one project*. Unrelated to rank. |
| **Client contact** | `c_<hex8>` | `pmo.db client_contacts` | `c_88a1` | External. Never a `user`. Never holds a rank. |
| **Agent identity** | `(project_id, handle)` | `pmo.db agent_identities` | `(p_9f2a, "dev-1")` | The `@`-namespace. Unique per project only. |
| **Handle** | `^[a-z0-9][a-z0-9._-]{1,31}$` | ditto | `dev-1`, `pm`, `qa` | What you type after `@`. **Not globally unique.** |
| **Principal** | string | runtime | `u_3f21ab90` or `agent:p_9f2a/dev-1` | What `pmo_authz.can()` takes. |

### The two-axis rule

> **Org rank** answers *"is this person senior enough to approve?"*
> **Project role** answers *"can this person write in this project?"*

A CEO with no membership in project `acme` cannot edit its tickets but can approve an
approval raised there. That separation is intentional (002 §2a) and it is what lets the
founder's office govern without holding blanket write access everywhere.

---

## Project & work

| Term | Format | Lives in | Example | Notes |
|---|---|---|---|---|
| **Project** | `p_<hex8>` | `projects.db projects` | `p_9f2a1c04` | The scope primitive. |
| **Project slug** | `^[a-z0-9][a-z0-9\-_]{0,63}$` | `projects.slug` | `acme` | Safe as a path component — upstream validates it. |
| **Board slug** | same grammar | `projects.board_slug` | `acme` | The kanban board. One per project. |
| **Primary path** | absolute path | `projects.primary_path` | `C:\work\acme` | The repo root. |
| **Project folders** | absolute paths | `project_folders` | | The filesystem scope (#9). |
| **Ticket / task** | `t<hex>` | `kanban.db tasks` | `t8a3f1` | "Ticket" in the UI, `task` in the schema. Same thing. |
| **Run** | int | `kanban.db task_runs` | `4471` | One attempt at a ticket. Many runs per ticket. |
| **Thread** | `th_<hex>` | `pmo.db threads` | `th_9f2` | A conversation. `founders_office` / `client` / `team`. |
| **Approval** | `ap_<hex>` | `pmo.db approvals` | `ap_3f21` | Carries a `required_rank`. |
| **Escalation** | `es_<hex>` | `pmo.db escalations` | `es_77b` | A blocker handed to humans. |

### Ticket vs task

The UI says **ticket**; the schema says `task` because that is upstream's name and we do
not rename upstream columns. Both refer to a row in `kanban.db tasks`. Use "ticket" in
user-facing strings and `task` in code.

---

## Agents

| Term | Meaning |
|---|---|
| **Profile** | A Hermes profile (`hermes_cli/profiles.py`). The runtime configuration — model, toolset, soul. This is what actually executes. |
| **Agent identity** | The PM-OS row that gives a profile a `@handle` inside one project. |
| **Soul** | The markdown instruction file for an agent (`.datansh/agents/*.md`). |
| **Toolset** | The tool allowlist. **The enforcement mechanism** for #2 and #7 — not the soul. |
| **PM** | The orchestrator. Handle `pm`, `kind='pm'`, exactly one per project. |
| **Worker** | Any `kind='worker'` agent. Picks up `todo` tickets assigned to its handle. |
| **Reviewer** | A worker with a read-only-source toolset that owns `review → done`. |

Profile ↔ handle is the mapping people get wrong. A **profile** is global to the Hermes
install (`acme-dev-1`); a **handle** is local to a project (`dev-1`). One profile could
in principle back handles in two projects — don't do it, but the schema permits it, so
never assume `handle == profile`.

---

## Statuses

| Status | Dispatchable | Set by | Plan |
|---|---|---|---|
| `draft` | ❌ | PM | 009 §1 |
| `todo` | ✅ | PM via `finalize` | |
| `in_progress` | — | dispatcher | |
| `blocked` | ❌ | worker via `pmo_block` | 005 |
| `review` | ❌ | worker on completion | 009 |
| `done` | — | reviewer | |
| `cancelled` | ❌ | PM / project_admin | 009 |

**Run outcomes** are a different vocabulary, owned by upstream `task_runs.outcome`:
`completed | blocked | crashed | timed_out | spawn_failed | gave_up | reclaimed`.
A ticket has a *status*; a run has an *outcome*. Do not mix them.

---

## Files & databases

| Path | Owner | Contents |
|---|---|---|
| `~/.hermes/kanban.db` | upstream | tickets, comments, events, runs, attachments |
| `~/.hermes/projects.db` | upstream | projects, folders |
| `~/.hermes/pmo.db` | **us** | identities, roles, threads, messages, approvals, escalations, mentions, notifications, audit |
| `~/.hermes/config.yaml` | upstream | global config (layer 2 of 3) |
| `<project>/.datansh/project.yaml` | **us** | per-project config, layer 3 of 3 (#10) |
| `<project>/.datansh/agents/*.md` | **us** | souls |
| `<project>/../.pmo-worktrees/` | **us** | agent worktrees — **outside** the repo (011 §6) |
| `plugins/pmo/` | **us** | the forked dashboard plugin |
| `plugins/kanban/` | upstream | **never modify** (018 §2) |

---

## Naming conventions

| Thing | Convention | Example |
|---|---|---|
| Python module | `hermes_cli/pmo_<area>.py` | `pmo_authz.py` |
| Agent tool | `pmo_<verb>` / `pmo_<noun>_<verb>` | `pmo_ticket_finalize` |
| Capability | `<noun>.<verb>` | `task.transition` |
| Rank action | `<noun>.<verb>` in `RANK_ACTIONS` | `approval.decide` |
| HTTP route | `/api/plugins/pmo/<resource>` | `/projects/{pid}/approvals` |
| Git branch (agent) | `pmo/<slug>/<ticket>-<title>` | `pmo/acme/t8a3f1-add-sso` |
| Git branch (human) | `feat/<plan-number>-<topic>` | `feat/006-approvals` |
| Test file | `tests/**/test_pmo_<area>.py` | `test_pmo_approvals.py` |
| CSS class | `.pmo-*` under `.pmo-root` | `.pmo-col-head` |
| Plan file | `NNN-<kebab-name>-PLAN.md` | `009-ticket-lifecycle-and-workflow-PLAN.md` |

---

## Terms that mean something specific here

| Term | In this project it means |
|---|---|
| **Finalize** | The PM moving a ticket `draft → todo`. The only way work becomes claimable (#5). |
| **Escalate** (ticket) | A blocker going from agent → PM → human (#6). |
| **Escalate** (approval) | Raising an approval's `required_rank`, e.g. CFO → CEO (#8). Up-only. |
| **Founder's office** | The trusted, instructing channel. The *only* inbound path to the PM (#2). |
| **Scope** | A `ProjectScope` — project + board + folders + config, resolved together (003). |
| **Handle namespace** | Per-project. Cross-project mention is *unnameable*, not merely blocked. |
| **Untrusted envelope** | The wrapper around client content before it reaches an agent (010 §3). |
| **Day-1 touchpoint** | The small write-side thing a hardening plan needs today so the rest is cheap later. |
| **Reject-with-valid-list** | The error convention: name what's wrong *and* the legal set. |
