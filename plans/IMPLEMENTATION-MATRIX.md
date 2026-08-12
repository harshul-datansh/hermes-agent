# PM-OS implementation matrix

This is the authoritative completion ledger for plans 001-026. “Complete” means the
requirement is implemented through a Hermes-compatible seam and has named acceptance
evidence. The PMO board is a one-time copy of `plugins/kanban`; Hermes core and the
upstream Kanban tree remain the compatibility baseline.

## Product-requirement audit (2026-08-05)

The original plan ledger was too optimistic about authentication and the ERP
workflow. This table is the stricter acceptance contract for the clean-slate
`datansh-pm-os` surface. A project-local policy is the source of truth; no
user-level PMO config or replacement Hermes runtime is allowed.

| Requirement | Status | Evidence / remaining boundary |
|---|---|---|
| 1. Authentication + role/access levels | Complete | *Authentication:* `plugins/dashboard_auth/datansh` — multi-user, scrypt hashes held per project, canonicalizing `name@example.com` → `human:name@example.com`, dummy-hash fallback on the unknown-user path. *Authorization:* deny-by-default project roles, org ranks, guards on all **71** routes (`tests/plugins/test_pmo_route_authz.py`), project-local audit. Verified live across **two** tenants — see the verification section below. **L-6 closed.** |
| 2. Founder’s Office always routes to a project PM | Complete | Project-bound Founder’s Office native thread and configured PM route; dashboard chat is project-selected. |
| 3. PM is never shared between projects | Complete | `ensure_unique_orchestrator_profile()` rejects duplicate PM profiles across active project configs. |
| 4. PM is the project orchestrator and discussion/decision hub | Complete | Normal Hermes gateway profile, memory, self-learning, delegation, tools, and skills remain intact; PMO adds scoped routing/context only. |
| 5. PM finalizes tickets; humans can be delegated | Complete | Native `triage` drafts, `/tasks/{id}/finalize`, and `/human-tasks` keep finalization in the PM workflow; human tasks cannot be dispatched as agent profiles. |
| 6. Blockers escalate PM -> human input | Complete | Native blocker comments, PM retry policy, approval gate, rank-aware decision, and resume path are covered by escalation/approval acceptance tests. |
| 7. Agent communication is @-comment based | Complete | Agent-authored PMO task comments now require a project-handle @mention, are written under the authenticated principal, and route through native Kanban comments/notifications. Founder chat additionally requires @pm for agent-authored posts; human Founder instructions may remain plain text and do not wake an agent. |
| 8. Founder’s Office group chat + CFO -> CEO approvals | Complete | Project folders support multiple conversations, the global Founder’s Office can address every authorized project PM, live thinking/tool/status events are rendered inline, and both decision and escalation controls are rank-aware. Multi-user Datansh authentication closes L-6; the CFO→CEO API/UI path and two-PM global broadcast are verified. |
| 9. Project/folder scope isolation | Complete | Explicit board/project binding, resolved path containment, deny globs, symlink/traversal rejection, and cross-project filtering. |
| 10. Project-specific config | Complete | Strict project-local `.datansh/project.yaml` plus `.datansh/access.yaml`; no user-level PMO config. |
| 11. No user-level config | Complete | No `/me/config` route or profile/user policy layer; policy and audit remain project-owned. |
| Role-gated Hermes administration workspace | Complete | **Administration** is intentionally separate from Founder’s Office: global CEO administration covers IAM/onboarding, while each authorized project exposes only its own agent creation, model routing, OpenAI Codex device sign-in, and bounded health. The chat returns explicit action cards/links, and the responsive UI keeps conversations primary. Covered by `tests/test_pmo_admin_workspace.py`, dashboard API tests, copied-Kanban divergence accounting, and live Browser scope/action walkthrough. |
| ERP dashboard human-task query/delegation | Complete | `hedgi_app` now embeds the protected, project-scoped PMO workspace at `/project-management` (with a full-dashboard fallback link), so managers can query the board and delegate human tasks from the ERP cockpit. PMO authentication and authorization remain authoritative; full cross-origin SSO/API proxying is still an optional deployment hardening layer. |
| Copied Kanban baseline | Complete | PMO dashboard/API is copied and extended independently; `check_pmo_kanban_sync.py` shows `plugins/kanban: clean`. |

### Status correction (2026-08-06)

The authentication and CEO-scope rows above predate the Datansh multi-user
provider and are superseded by the current implementation: Datansh accepts
multiple human identities, rank 100 is global CEO administration, and project
switchers are filtered from the same authorized portfolio. The historical L-6/L-8
notes below remain for traceability only.

| Plan | Implemented behavior and evidence | Status |
|---|---|---|
| 001 Foundation | Independent `plugins/pmo` manifest/API/client state, bundled profile discovery, `hermes pmo` CLI, bootstrap/service operations, provenance and sync checks. | Complete |
| 002 Auth/RBAC | Session/token identity, deny-by-default project capability policy, human rank and agent-role policy, guarded routes, project-local member/rank write API + UI, redacted append-only audit. Covered by `tests/test_pmo_access_policy.py` and dashboard API tests. | Complete |
| 003 Project scope/config | Native project/board/workspace binding, strict `.datansh/project.yaml`, resolved-path deny rules, symlink/traversal protection, project isolation tests. | Complete |
| 004 PM agent | Additive PM profile/SOUL/skill, project gateway route and real `@pm` wake, native draft/finalize workflow, decomposition convention, roster/evidence/approval/project gates, capability-parity coverage. | Complete |
| 005 Comms/escalation | Scoped mention parser with email/code exclusions, roster autocomplete API/UI, durable delivery/read receipts, bounded drain, blocker -> PM -> human gate -> worker resume; Founder composer requires a project @handle and visibly reports gateway errors. | Complete |
| 006 Founder's Office/approvals | Native linked approval gates, rank escalation, immutable human decision/audit, routed Founder and client conversations, approval inbox and inline decision form. Browser walkthrough verified approve flow. | Complete |
| 007 PM dashboard | Copied board at `/pmo` with independent selection, PM shell/nav, portfolio/project/approvals/Founder/access/human-task views, responsive layout, readable dark theme, focus/live-region/i18n behavior, and a Datansh Hermes wrapper that role-filters the inherited navigation. Browser walkthrough covers access editing, delegation, chat guard, approval, login protection, branding, and reduced navigation. | Complete |
| 008 Day-1 acceptance | Deterministic production-API story covers bootstrap, real `@pm`, decomposition, CFO -> CEO approval, finalization, worker completion and QA review. `tests/run_agent/test_pmo_live_acceptance.py` and scripted acceptance pass. | Complete |
| 009 Ticket lifecycle | Native `triage` draft/finalize gate, body/evidence/closed-label contract, approval validation, legal PM actions, idempotent create/finalize/reopen, cancel/archive/rework lineage. | Complete |
| 010 Client collaboration | Native client thread and restricted client profile, threat scanning/invisible stripping/escaping, project membership/RBAC, explicit human-approved send, dashboard conversation surface. | Complete |
| 011 Git concurrency | Project-bound worktrees/branches, linked review workers, advisory `touches` overlap warnings and dashboard indicator, human-owned main, opt-in integration branch, conflict blocking, dry-run evidence-preserving GC, review-worker acceptance. | Complete |
| 012 Cost/model routing | Decimal/string native usage attribution, PM-thread synthetic attribution, project/ticket/agent/cache projections, soft/hard/monthly gates, CFO escalation, role model overrides, Spend dashboard, MoA restriction only for unattended PM routes. | Complete |
| 013 Observability/audit | Bounded redacted timeline merging native task events, comments, runs and approvals, authorization audit/session links, HTTP timeline/activity views. | Complete |
| 014 Resilience/recovery | Native reclaim/heartbeat/retry retained; doctor projects bindings/profiles/threads/worktrees/claims/retries/budget/reconciliation/contracts; bounded restart drain; degraded-state and process-recovery dashboard states. | Complete |
| 015 Security/lifecycle | Hermes approval/path/render/attachment protections retained, PM project deny rules, XSS/redaction tests, retention/export lifecycle and guarded project endpoints. | Complete |
| 016 Notifications | Native-comment notification projection, unread/read/egress receipts, bounded inbox, quiet-hours/high-urgency egress-only policy, dashboard inbox and outbound-channel configuration. | Complete |
| 017 Testing strategy | Strict traced FakeModel, isolated one/two-project/fake-clock fixtures, path/mention properties, production-API L3 acceptance, opt-in metered L4 trend, pinned PMO Ruff/ty lanes, frontend runtime/security tests. | Complete |
| 018 Upstream/release | Recorded provenance and copied-board sync/capability checks, configured upstream remote, weekly review command, release/service operations, CI contract and live workflows. | Complete |
| 019 Context engineering | Stable gateway prefix plus bounded trigger/board/blocker/retry/roster/knowledge projection; client context excludes internals; prefix/bounds/redaction tests. | Complete |
| 020 Decisions/knowledge | Append-only versioned decisions/knowledge, immutable supersession, deduplicated usage events, bounded redacted context projection; Hermes memory/self-learning and skill evolution remain enabled. | Complete |
| 021 Architecture gaps | PMO registers through `ctx.register_platform`; native conversation records and Hermes `ProfileRoute` reuse the normal gateway turn lease/delivery path. | Complete |
| 022 Portfolio | Authorized-project rollup with rank-aware needs-you, progress/risk/spend/activity ordering, project overview, guarded HTTP endpoints, 30-second cache, first-run identity bootstrap. | Complete |
| 023 Onboarding/demo | Installable profile distribution, bootstrap/setup command and docs, deterministic isolated demo/reset, seeded credentials, responsive empty states and onboarding surfaces. | Complete |
| 024 Performance/scale | Copied Kanban WebSocket/polling behavior retained; PM pagination/backoff/load/performance tests and structural transaction/SQL guards are present. | Complete |
| 025 Accessibility/i18n | Copied Kanban SDK shims/controls retained; PM keyboard/focus/live-region/contrast/i18n verification, inline approval form and mobile layout fixes. | Complete |
| 026 Compatibility reset | Core/Kanban unchanged; executable 10% budget; PM profile retains normal tools, memory, self-learning, delegation, messaging/gateway and skill evolution; behavioral parity and native Kanban execution pass. Measured customization: 50/4,174 production files (1.20%). | Complete |

## Live-environment test findings (2026-08-05, running dashboard)

Found by testing the running app rather than the test suite. **247 PM-OS tests pass
and every row above was marked Complete, yet the demo shipped a board that could not
dispatch and a start command that could not start.** Green tests plus a green ledger
did not equal a working product; only driving the actual binary surfaced these.

| # | Finding | Severity | Status |
|---|---|---|---|
| L-1 | `hermes pmo demo` wrote a roster naming five profiles (`pm-acme`, `acme-dev-1/2`, `acme-qa`, `acme-ops`) and created none. The dispatcher spawns `hermes -p <assignee>`, so **no ticket on the demo board could ever start.** | High | **Fixed** — `_create_agent_profiles()`, plus a `DEMO_AGENTS` constant so config and profiles cannot drift again. The drift *was* the bug. |
| L-2 | A **fresh** demo failed its own health check: `[ERROR] worktrees: missing active worktrees`. Worktrees are materialized by `_dispatch_once_locked`, not `claim_task`; the demo claims directly, producing dispatcher-state without the dispatcher's side effect. | High | **Fixed** — `_materialize_worktree()` mirrors the dispatcher's `_resolve_worktree_workspace` → `set_workspace_path` → `set_branch_name` sequence. |
| L-3 | **The demo's own printed instructions could not work.** It printed credentials and `hermes dashboard --host 0.0.0.0`, but configured no auth provider, so that command exits with *"no auth providers are registered."* | High | **Fixed** — `_configure_dashboard_auth()` enables the additive Datansh provider (hashes only, never plaintext) in the isolated demo home. |
| L-4 | `hermes pmo demo --reset` failed with a bare `WinError 32` when the dashboard held the demo's `HERMES_HOME`. Unactionable. | Medium | **Fixed** — names the locked file and the remedy. |
| L-5 | Two upstream test files were edited (both legitimate Windows fixes; one is this repo's own `PLW1514` rule) but `UPSTREAM.md` recorded neither. | Medium | **Fixed** — full accounting added, including the private-API coupling L-2 introduces. |
| L-6 | **The dashboard can authenticate exactly one user.** | **High — fixed** | Datansh Project Accounts accepts multiple human identities; each project's `.datansh/credentials.yaml` stores only hashes, while `.datansh/access.yaml` keeps membership and roles project-local. |
| L-7 | Portfolio read **"0 Active projects"** directly above a table listing one live project. `metrics.active` counted `status == "Active"`, but `status` is a *health label* — an "At risk" project is still active. | Medium | **Fixed** — counts non-`Paused` projects. |
| L-8 | The `review` column rendered lowercase with no description, beside title-cased siblings — missing from `FALLBACK_COLUMN_LABEL`/`_HELP`, so it fell through to the raw status string. `scheduled` was missing too. | Medium | **Fixed** — both added; comment ties the map to `VALID_STATUSES`. |
| L-9 | **No Escalate control anywhere in the product.** `approval.escalate_approval()` was implemented and tested, `approval.escalate` was in `RANK_ACTIONS` — but there was **no HTTP route and no UI**. | **High** | **Fixed** — added `POST /approvals/{id}/escalation` + an Escalate ↑ button and form. |
| L-10 | Founder's Office rendered authors as raw principals (`human:ceo@datansh.local`, `pm-acme`) instead of identity chips with role badges (design.md §8, plan 006 §5). | Low — **fixed** | Visible authors now use readable handles with Person/Agent and project-role badges; storage principals remain available only as identity metadata/accessibility context. |
| L-11 | The original demo session appeared reachable only on `localhost`; switching the same host-scoped cookie to `127.0.0.1` silently returned to login. | Medium — **resolved** | Datansh login works directly on `127.0.0.1`; cookies correctly remain host-scoped, so changing hostname requires authenticating on that hostname instead of sharing a cookie across origins. |

### L-6 — authentication cannot distinguish the roles authorization enforces (resolved)

The original live finding is retained below as historical evidence. It was resolved
by the additive `datansh` provider and project-owned credential hashes; the old
single-user/basic-auth conclusions no longer describe the current implementation.

Current behavior: the active provider is `datansh`; it accepts multiple human
identities, and a human can be enrolled in multiple projects. The selected
project's `.datansh/access.yaml` is still the only source of role/capability
authorization, so authentication never becomes a cross-project grant.

Row 1 above reads *"Authentication + role/access levels — Complete."* The
**authorization** half is real: deny-by-default project roles, org ranks, route
guards, and a project-local audit, all covered by passing tests. The
**authentication** half is not.

Available providers are the four upstream ones: `basic` (a **single**
username/password), `drain` (token-only), `nous` (OAuth IDP), `self_hosted` (OIDC —
needs an external IDP). **Plan 002 §1's multi-user `plugins/dashboard_auth/datansh`
provider was never built**, and no `pmo` auth provider exists on disk.

So `.datansh/access.yaml` defines four principals at three ranks, PM-OS correctly
authorizes against them — and the dashboard can only ever prove you are one of them.

Consequences, stated plainly:

- **Requirement 8's centerpiece is not reachable through the UI.** You cannot sign in
  as the CFO, so you cannot perform the CFO→CEO escalation by hand. The mechanics are
  implemented and tested at the API level, and the demo *seeds* an already-escalated
  approval, but the human walkthrough that the requirement describes stops at "the CEO
  approves something the seed escalated."
- Row 1 should read **Partial**, not Complete. Rank-aware authorization: complete.
  Multi-user authentication: not started.
- Row 8's evidence line — *"Browser approval walkthrough"* — can only have exercised
  the approve step, not the escalate step, for this reason.

`demo.py` now prints this limitation at the end of every run rather than leaving it to
be discovered, and `_configure_dashboard_auth`'s docstring records it at the source.

### L-9 — the escalation control did not exist in the product

Row 8 reads *"Founder's Office group chat + CFO → CEO approvals — Complete… rank-aware
approval routing, inline decision UI, and Browser approval walkthrough."*

The **decision** UI was complete. The **escalation** UI did not exist, and neither did
its HTTP route. `plugins/pmo/approval.py::escalate_approval` was fully implemented and
covered by tests, and `access_policy.RANK_ACTIONS` already carried
`"approval.escalate": 50` — but nothing connected them to the dashboard. Requirement #8's
literal wording, *"cfo can ask for ceo approval here for tickets, if he's requiring it,"*
was reachable only from Python.

The demo masked it by **seeding** an already-escalated approval, so the walkthrough could
approve something that was never escalated by a human through the UI.

Now added: `POST /approvals/{approval_id}/escalation` mirroring the decision route's
error mapping, plus an **Escalate ↑** button with a rank / route-to / mandatory-reason
form. Verified live against the running dashboard — a down-escalation attempt returns
**409 `target rank must be above current required rank 100`**, so ADR-015's up-only
invariant holds through the new surface.

**This is the clearest example of why the ledger needed live testing.** Library code,
policy entry, and passing tests all existed; the product still could not do the thing.

**To close L-6,** build plan 002 §1's `datansh` provider: `supports_password = True`,
`complete_password_login` resolving `human:<email>` principals against the existing
project-local policy, argon2id or scrypt hashes, sha256-hashed session tokens. The
authorization layer it needs already exists and is tested — this is the missing
front door, not a missing building.

## Auth / authorization / tenancy verification (2026-08-05, live two-tenant)

Scoped review of authentication, authorization, and projects-as-tenant, against a
**two-project** environment. The demo shipped one project, so cross-tenant isolation
had never been exercised — you cannot prove isolation with a single tenant. A second
project (`beta`) was created for this.

### What holds

| Check | Result |
|---|---|
| Cross-tenant denial | `acme` members — including two `project_admin`s and the `pm` — are **denied every action** in `beta` |
| Cross-tenant denial on the wire | A live CEO session (rank 100, `project_admin` in `acme`) gets **403 `Project access denied`** on `beta` board / access / approvals / spend |
| Portfolio filtering | Server-side; the CEO's portfolio does not reveal `beta`'s existence |
| Per-project roles | `cfo@` is `contributor` in `acme` and `project_admin` in `beta` — one identity, independent authority |
| Role matrix | `project_admin` / `pm` / `contributor` / `viewer` match plan 002 §2b exactly |
| Rank gating | 100 / 80 / 70 / 0 / unranked all gate `approval.decide`, `approval.escalate`, `org.manage` correctly |
| Prefix confusion | `testAdmin` and `testAdmin2` are denied `member.write` and `org.manage`; real `admin@` is allowed. **Substring bug confirmed fixed.** |
| Unknown principal | Denied |
| Agent principal | `agent:acme/pm` may `task.write` but is **denied `approval.decide`** — ADR-005's structural guarantee holds |
| Route coverage | **All 71 PM-OS routes carry an authorization dependency; zero unguarded** |
| Credential handling | scrypt via `hash_password`; no plaintext at rest; dummy-hash fallback keeps the unknown-user path cost-comparable; atomic write with `fsync` + `os.replace`; symlink refused on read *and* write; strict pydantic with `extra="forbid"`; duplicate principals rejected |

### L-12 — a bootstrapped project could not be entered by any human

**Fixed.** `hermes pmo bootstrap` wrote an `access.yaml` naming only
`dashboard:local-operator` (the loopback operator identity). With real logins enabled
that project had **no human member**, and there was no way in:

- the Access screen requires `member.write` **on that project**, which no human had;
- no CLI granted membership;
- so the only route in was hand-editing YAML.

A project that exists and cannot be opened is worse than one that fails to create.

Added `grant_project_admin()` plus `--admin <email>` / `--admin-rank` on bootstrap
(idempotent — re-granting updates the role rather than duplicating the member), and
bootstrap now **prints a warning when no human member exists** and names the next
command when one is granted.

### L-13 — the route-coverage test did not exist

Plan 002 §2e calls it *"the load-bearing test of the whole authorization story."* It
was never written. Added `tests/plugins/test_pmo_route_authz.py`:

- every route carries an authorization dependency (parametrised, so a failure names
  the offending method and path);
- no state-changing route is guarded by a read-only action — catches the copy-paste
  bug where a `POST` inherits the `project.read` guard above it and a viewer can
  mutate the project;
- the live-updates WebSocket is guarded (plan 006 §4's most consequential trap: an
  unscoped subscribe leaks one project's spend and Founder's Office discussion to a
  member of another).

Currently 74 assertions, all passing.

### Still open

- **L-6 is now closed.** The `datansh` provider exists, is multi-user, and delegates
  identity to project-local credentials. Row 1 below is upgraded accordingly.
- **`sqlite3 3.45.3`** is vulnerable to the WAL-reset corruption bug, so Hermes falls
  back to `journal_mode=DELETE` and warns once per process. Correct behaviour, and it
  confirms VERIFIED **V-47**. Worth upgrading before this environment holds anything
  that matters.
- Org **rank below 100 is project-local** — a person can be rank 70 in one project and
  unranked in another. Rank 100 is the deliberate global CEO-administrator exception.
  This policy is now explicit in `docs/pmo/concepts.md` and `docs/pmo/tab-roles.md`.

## Final live acceptance refresh (2026-08-06)

The local dashboard and gateway were restarted from the current worktree and tested at
`http://127.0.0.1:8787/pmo` with a Datansh CEO session and two active projects.

- A message to `@pm` in **Global Founder's Office** produced independent replies from
  `pm-pmo-e2e` and `pm-pmo-product-lab` in the same conversation.
- Each project folder retained its own dedicated PM and multiple conversations; direct
  project-chat messages were answered only by that project's PM.
- Thinking, tool calls, completion status, human/agent badges, and project PM handles
  were visible in the Browser. The all-caps `DATANSH` / `HERMES` brand and responsive
  chat layout were visually checked.
- A live PM terminal run resolved to `/workspace`, read only the selected project's
  sentinel, enumerated no other project root, and left no sandbox container behind.
- The complete PM-OS plus copied-Kanban gate passed: **45 files, 365 tests, 0 failures**.
  The focused terminal/sandbox/session gate passed **101 tests**, and both compatibility
  scripts remain clean at **1.20%** customization with upstream Kanban untouched.

## Plan-conformance review round (2026-08-06, concurrent-development)

Review against the plan set while a second agent (Codex) was actively developing
git-OAuth onboarding, project-scoped model config, and per-project sandboxing.
Findings are split by ownership, because editing a file another agent holds is
the failure mode `SUBAGENT-EXECUTION.md` §2 exists to prevent.

### Fixed this round

| # | Finding | Status |
|---|---|---|
| L-14 | Four attachment tests in `test_pmo_security_hardening.py` failed because the fixture built a bare `HERMES_HOME` with no project, while `_pmo_scope` now requires a project-bound board (403). Proven pre-existing by reverting unrelated changes and re-running. | **Fixed** — fixture now builds a real project via `make_pmo_test_project`, so these tests exercise the production path instead of a shape that can no longer exist. |

### Verified against the plans

- **Non-negotiable #1 (`plugins/kanban` untouched)** — holds; `check_pmo_kanban_sync` reports `plugins/kanban: clean`.
- **All five must-pass acceptance tests from 008 §4 exist and pass**: `test_every_pmo_route_is_guarded`, `test_cfo_escalates_to_ceo_and_ceo_approval_unblocks_target`, `test_cross_project_handle_is_unknown`, `test_pm_toolset_excludes_messaging_tools`, `test_draft_tickets_are_not_dispatchable`.
- **ADR-010 (never read active state)** — enforced at the wire: `GET /projects/{ref}/conversations` without an explicit `board` returns 403 rather than falling back to the ambient current board.
- **L-7 and L-8 fixes confirmed live**: portfolio reports "3 Active projects" (was 0 above a populated table); the `Review` column renders capitalised with its description.
- **Three upstream core files are now modified** (`gateway/run.py`, `gateway/session.py`, `hermes_cli/kanban_db.py`). All three are additive, backward-compatible seams and **are recorded in `UPSTREAM.md` §35-41** — the accounting 018 requires is present. `kanban_db`'s change is a `candidate_filter_fn=None` dispatch hook with no schema change, so ADR-002's `_REBUILD_SPECS` hazard is not engaged.

### Open — owned by concurrent development, deliberately not touched

| # | Finding | Owner |
|---|---|---|
| L-15 | The delivery board lists conversation threads as `blocked` tickets. Hedgi App shows "Blocked: 4" where all four are chat threads (Founder's Office, Client Collaboration, …). Portfolio is **correct** here — it excludes them via `_is_conversation` / `_approval_rank` — so the two screens disagree. The fix is to reuse portfolio's predicate in the `/board` response, but threads-as-tasks is deliberate and Codex's conversation-folder feature depends on those rows, so this is a product-design call, not a defect to patch unilaterally. | `plugin_api.py` |
| L-16 | `doctor` reports `reconciliation: 3 unattributed run(s)` on `pmo-product-lab`. Investigated: runs 8–10 belong to one e2e harness ticket and carry no `usage` block because no model call was made. Technically accurate but not actionable — a permanent unactionable warning trains people to ignore `doctor`. Worth distinguishing "cost lost" from "no cost incurred". | `cost.py` / `doctor.py` |
| L-17 | Hash-only navigation (`location.hash = '#pmo/portfolio'`) does not re-render; a full load does. Deep links therefore survive reload (025 §3 satisfied for notification links) but in-app hash changes do not route. | frontend |

## Completion gates

1. Every row above is `Complete`.
2. `python scripts/check_pmo_kanban_sync.py` passes.
3. `python scripts/check_pmo_capability_contract.py` passes.
4. The complete PMO suite and copied-Kanban regression suite pass.
5. Hermes core and `plugins/kanban` remain unchanged; PM behavior lives in
   `plugins/pmo`, profile distributions, skills, tests, and supported plugin seams.

## Live functional test against CEO requirements (2026-08-06, 4 projects)

Driven through the running dashboard and CLI against `hedgi-app`, `pmo-e2e`,
`pmo-product-lab` and `bewizor`. Four defects found, all fixed and covered by
regression tests. Two of them broke requirements outright.

### L-18 — the CFO→CEO approval flow was destroyed by a background sweep (req #8)

**Severity: critical — requirement #8 is the CEO's "most important".**

Raised an approval at rank 70, escalated to 100, then tried to decide it:

```
pmo approval: task 't_df7e859e' is not a PM-OS approval
```

`gateway/kanban_watchers.py` runs an **auto-decompose sweep** (`kanban.auto_decompose`,
default **True**) over `list_triage_ids()` on *every* board. The decomposer calls
`specify_triage_task`, which sends the task to the auxiliary LLM and replaces its
title and body. That erased `[pmo:approval:v1]` **and** the stored required rank,
target task and raiser — all of which live in the body. The record became
permanently undecidable and was promoted into the dispatcher's claim set.

The collision is structural: PM-OS parks records in `triage` because the dispatcher
ignores that column; the specifier treats every `triage` task as a rough idea to
flesh out, its only skip condition being `status != "triage"`. Every PM-OS record
is therefore a guaranteed target. It only worked in the seeded demo because nothing
was sweeping.

**Fixed three ways:**
- `kanban.auto_decompose: false` in the PM-OS home. PM-OS *should* have this off —
  requirement #4 makes the PM agent the decomposer, not a background sweeper.
- `workflow.protected_record_kind()` and a guard on the PM-OS
  `/tasks/{id}/specify` endpoint, which now refuses records with a clear reason.
- `tests/plugins/test_pmo_record_protection.py`, including an ordering assertion —
  refusing *after* the LLM call would already have destroyed the record.

Verified end to end after the fix: raise@70 → escalate→100 → **CFO refused**
("approval requires rank 100; actor has 70") → CEO approves → approval `done`.

### L-19 — `@` mentions were never validated or delivered (req #7)

`@nonexistent` was **accepted**. Both the CLI and the API only ran `parse()`, which
proves an `@` is *present*, not that the handle *exists*; they then called
`kanban_db.add_comment` directly. `mentions.post_comment` — which validates and
enqueues the fan-out — was wired into `escalation.py` only.

So requirement #7's single communication channel accepted mentions to nobody and
**enqueued no notifications at all**, which is why every project's inbox read 0.

**Fixed:** the comment route now goes through `mentions.post_comment`. Unknown
handles return 422 with the valid list; valid ones return the comment id and the
notification ids.

### L-20 — every human's inbox resolved to `founders-office` (notifications)

`_notification_handle()` mapped **all** humans to `founders-office`, predating the
per-person handles now derived from `access.yaml`. A notification addressed to
`@ceo` was written but unreadable by the CEO. Fixed with `_notification_handles()`,
which merges the personal handle and the broadcast handle.

### L-21 — human tasks could be delegated but not queried (req #5)

Requirement #5 is "**query** on erp dashboard to delegate human tasks". `human-tasks`
was POST-only and the UI was a create form with no ledger — you delegated and got a
toast. Added `GET /human-tasks` plus a "Delegated human tasks" table.

Human-ness also lived only in a body marker the specifier strips, so a human task
silently became an agent task assigned to `human:ceo@…` — a profile no dispatcher
can spawn, retrying as nonspawnable forever. `workflow.is_human_task()` now reads
the `assignee` column, which no prose rewrite can touch
(`tests/plugins/test_pmo_human_task_durability.py`).

### Verified working

| Surface | Result |
|---|---|
| Auth / RBAC (#1) | CEO resolves rank 100, `project_admin`, 19 capabilities |
| Founder's Office (#2, #8) | 4 projects, conversation folders, `AGENT`/`PERSON` participant chips |
| Board | Renders all 9 upstream statuses; `Review` column labelled correctly |
| Ticket `@` comms (#7) | Handoff and blocker comments route; unknown handles rejected with the valid list |
| Blocker → PM escalation (#6) | `blocker` comment to `@pm` accepted and routed |
| Approvals + hierarchy (#8) | Full CFO→CEO flow; CFO cannot decide what it escalated |
| Human tasks (#5) | Create **and** query, per project |
| Timeline | 35 entries across 7 event kinds, merged |
| Notifications | Delivered to the personal handle |
| Decisions & context | 1 decision + 2 knowledge entries, bounded context projection |
| Project isolation (#9) | Rosters and data differ per project; no cross-project bleed |

---

## L-22 .. L-25 — findings from the agent-driven collaboration run

Everything above was verified by driving each step. These four were found by
seeding work designed around a genuine gap and letting the agents choose their
own moves (`scripts/pmo_collab_scenario.py`). None of them was reachable by
calling the handlers directly, which is why they survived earlier passes.

### L-22 (critical) — agents had the collaboration tools and no reason to use them

Every provisioned PM-OS profile, orchestrator included, carried the stock
Hermes SOUL: the same 513 bytes, no role, no handle, no mention of the board,
no rule for when to hand work over. Registering `pmo_ask` / `pmo_transfer` /
`pmo_escalate` and putting them in the profile's toolset made them reachable;
nothing made them *used*.

Fixed by `plugins/pmo/agent_soul.py`, which writes a role charter into each
profile at provision time (`hermes pmo agents charter --project <slug>`
backfills existing installs). The charter is written as decision rules with
triggers rather than a description of the role, and is built around one
invariant: **a ticket must never go quiet** — every turn ends finished, asked,
transferred or escalated.

Marker-guarded, so a re-provision rewrites a charter it wrote and never
overwrites an operator's hand-edited SOUL.

### L-23 (critical) — a dispatched worker could not resolve its own project

`projects.db` is per-profile by design. A dispatched worker runs with
`HERMES_HOME` pointed at its own profile, where no project has ever been
registered, so the project id carried on the board it was handed resolved to
nothing. Every collaboration tool failed with `unknown project: p_...`.

Three live agents hit this simultaneously, and each reported it honestly on
the board rather than pretending the handoff had happened. Fixed with a
read-only root fallback in `project_scope._get_project`, the same shape as the
auth store's global fallback. No cross-project reach is opened: callers derive
the project id from the board they were dispatched for, and `resolve` still
checks the board/project binding both ways.

### L-24 (critical) — `pmo_transfer` crashed the worker that called it

The transfer used `reassign_task(reclaim_first=True)`. That reads correctly
and fails in production: a handoff happens *while* the sending agent holds the
claim, so reclaiming pulls the claim out from under a live process. The
dispatcher then sees a running pid that no longer owns its task, records
`crashed`, respawns the **same** agent on the **same** ticket, and after three
rounds gives up — leaving the ticket blocked and still owned by the sender,
with a handoff comment on the board that makes it look as though the transfer
worked.

The contrast that exposed it: `request_info` never touches the claim and was
clean, `escalate_to_human` blocks first and was clean, only `transfer_task`
reclaimed. Fixed to block → reassign → unblock, so the claim is released the
way the dispatcher expects and the ticket only becomes claimable once
ownership has already moved.

Regression coverage needs a caveat. The end state is identical under both
implementations, so every assertion about rows passes on the broken code too;
the difference is visible only to the dispatcher, which watches a live
process. `tests/plugins/test_pmo_transfer_claim.py::TestClaimReleaseSequence`
therefore asserts the **call sequence**, and was confirmed to fail on the old
implementation and pass on the new. The rest of that file guards the end state
against future regressions but does not pin this bug.

### L-25 (cosmetic) — two warnings in the worker log, neither a defect

* `Warning: Unknown toolsets: pmo-collab` — `cli.py` validates toolset names
  before registry discovery has imported the tool modules. The tools resolve
  and execute correctly. The validation site is Hermes core, so this is left
  alone rather than patched.
* `read SKILL.md [File not found: ...]` — the skill loads correctly
  (`skill datansh-pm-os 1.3s`); the agent then additionally tried a relative
  `read SKILL.md` from its worktree and missed.

### Verified live, agent-driven

| Behaviour | Evidence |
|---|---|
| Refuses to guess an unknowable value | `dev` asked `@ops` for the staging window, quoting the ticket's own prohibition |
| Escalates a production credential | `ops` tagged `@founders-office` with three specific asks, and volunteered that no secret should reach the ticket |
| Does real work before handing over | `dev` changed 3 files, added a test file, ran `npm test` (3 passed) and `npm run typecheck` |
| Reports its own tooling failure honestly | `dev` recorded that ESLint crashed loading a rule, rather than claiming a clean lint |
| Refuses to self-certify | `dev`: "This moves money and must not be self-certified", then transferred to `@qa` |
| Plans a hop it cannot yet make | `dev` transferred to `@ops` *and* named `@qa` as the following step |
