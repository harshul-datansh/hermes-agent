# Fork provenance

Forked byte-for-byte from `plugins/kanban/` at repository commit
`22210107177b63428ef2a8a3a58d2f0eb6566ed6` on 2026-08-05.

Last reviewed against the recorded source: 2026-08-05.

## Sync contract

- `plugins/kanban/` remains owned by upstream and is never edited for PM-OS work.
- PM-OS changes live under `plugins/pmo/`.
- Review a new upstream Kanban revision before porting it into this fork.
- Update the source commit and review date here whenever the fork is rebased onto a
  newer Kanban baseline.
- Run `python scripts/check_pmo_kanban_sync.py` before and after each sync review.
  The check rejects changes outside the recorded intentional-divergence set.
- Run `python scripts/check_pmo_capability_contract.py` before delivery. It verifies
  that the PM distribution owns only its additive role payload, PMO additions do not
  introduce replacement tools or runtimes, and Hermes core changes remain limited to
  the explicit, customization-budgeted allowlist.

## Initial baseline

The initial copy contained the five source files below byte-for-byte. Identity changes
were then applied only to the three files listed under intentional divergences.

- `dashboard/dist/index.js`
- `dashboard/dist/style.css`
- `dashboard/manifest.json`
- `dashboard/plugin_api.py`
- `systemd/hermes-kanban-dispatcher.service`

## Intentional divergences

- `gateway/session.py` adds one local-only, wire-invisible runtime cwd field for an
  adapter-validated project turn. `gateway/run.py` bridges it into Hermes' existing
  ContextVar and per-session terminal cwd record. `tools/terminal_tool.py` adds a
  generic allowlisted per-task backend/config seam, stable sandbox key, session-id
  fallback, and host-to-container workdir mapping. `tools/file_tools.py` applies that
  same task-local backend/config seam so direct file operations cannot escape the
  project's container. `tools/environments/docker.py` adds strict mounts that disable
  implicit credentials, skills, caches, global volumes, and extra Docker arguments.
  `hermes_cli/kanban_db.py` invokes one lazy,
  additive PMO candidate policy before claim; it permits ordinary Hermes boards
  unchanged and rejects a foreign project profile before a PMO task reaches
  `running` or spawns. These six approved core
  divergences preserve
  the normal agent loop, tools, skills, learning, memory, and delegation while making
  each PM project's terminal an isolated Docker environment. The two terminal seams
  are intentionally generic and suitable for upstreaming.

- `UPSTREAM.md` exists only in the PMO fork to track provenance and sync reviews.
- `OPERATIONS.md` documents the executable weekly sync, release, and inherited
  Hermes service-deployment procedure; it contains no runtime implementation.
- `dashboard/manifest.json` gives the copy its `pmo` plugin name, `/pmo` tab, PM-OS
  label, and independent version.
- `dashboard/plugin_api.py` documents the PMO API mount, attaches the single
  introspectable PMO access dependency, retains every copied Kanban endpoint,
  and adds one bounded `PMO-DASHBOARD-EXTENSION` block. The block exposes
  portfolio/project overview, native conversation threads, approvals, timeline,
  notification, access, decision/context, and spend projections through existing
  PMO edge modules and native Hermes records. It creates no table, auth runtime,
  dispatcher, tool, or message store.
- `dashboard/dist/index.js` sends the copied UI to `/api/plugins/pmo`, stores
  its browser-selected board under `hermes.pmo.selectedBoard`, independent of the
  Kanban dashboard's `hermes.kanban.selectedBoard`, and registers the independent
  `pmo` page. One bounded PM shell wraps (rather than replaces) `KanbanPage`; all
  inherited board, drag/drop and move controls, runs, attachments, diagnostics,
  orchestration, and model controls remain present. The shell externalizes PM
  strings with English fallback and adds keyboard/focus/live-region behavior.
- `dashboard/dist/style.css` is the recorded Kanban stylesheet plus one bounded
  PM shell block with contrast-safe tokens, visible focus, responsive layouts,
  minimum control sizes, logical properties, and reduced-motion handling.
- `tool_scope.py` is a plugin hook that binds normal path-bearing Hermes tools to the
  adapter-validated project folders and registers the normal terminal's project
  sandbox override. It registers no new tools and does not restrict skills, memory,
  self-learning, configuration, or delegation.
- `sandbox.py` provisions a per-project Hermes home and filtered native project DB,
  then binds only that project's validated folders and copied Kanban board into the
  existing Docker terminal backend. It never falls back to the host shell, shares a
  sandbox across delegated agents only within one project, and does not fork the
  terminal schema or execution runtime. Its narrowly allowlisted SQL rewrites only
  the generated sandbox copy of Hermes' existing project store so the container sees
  one fixed project id and Linux mount paths; it defines no schema or product state.
- `bootstrap.py` is a thin CLI edge utility that binds existing Hermes project and
  Kanban records and writes `.datansh/project.yaml` and `.datansh/access.yaml` once
  from validated templates. It creates no PM-OS database, dispatcher, message
  runtime, or tool.
- `access_policy.py` projects deny-by-default PM capabilities from the project's
  version-controlled `.datansh/access.yaml`, using only Hermes-authenticated
  `Session`/`TokenPrincipal` identities. Human project membership and org rank are
  separate axes; PM/worker agent roles are structural and agents can never acquire
  org rank. Every decision appends a redacted JSONL audit event under `.datansh`.
  It contains no credentials, sessions, user config, auth provider, or database;
  project-owned password enrollment is isolated in the additive `credentials.py`
  module below.
- `admin_workspace.py` stores a bounded, project-scoped administration transcript
  and projects explicit, role-gated operational actions for agents, model routing,
  provider sign-in, health, onboarding, and IAM. It is separate from Founder’s
  Office: it does not instruct project managers, own a gateway runtime, or replace
  Hermes chat, memory, skills, self-learning, delegation, or project-local policy.
- `credentials.py` is a small project-local enrollment edge. It stores only scrypt
  hashes in `.datansh/credentials.yaml`, scans active project records for the
  Datansh identity provider, and never owns sessions or authorization decisions.
- `project_scope.py` resolves an explicit Hermes project, board, and workspace
  binding; validates plugin-owned `.datansh/project.yaml`; and provides a
  symlink-safe containment check for PMO-owned file operations. It never selects the
  process-global active board or wrap/replace Hermes tools. Docker isolation is the
  default terminal boundary; `host_guard` is an explicit local-development downgrade.
  `scope.deny` is enforced for calls through these PMO path helpers but is not a
  content-filtered shell mount boundary. Active project roots must be disjoint;
  equal, parent, and nested roots are rejected before a PM turn can start.
- `comment.py` formats handoff, blocker, and review bodies, then writes them through
  `hermes_cli.kanban_db.add_comment`. It requires an explicit existing board and task,
  preserves Kanban's normal comment event, and creates no message router or wake loop.
- `cost.py` joins Kanban run metadata to Hermes' existing session usage, normalizes
  PMO-owned completion metadata with Decimal/string money, and projects month,
  ticket, agent, cache, and reconciliation totals without a billing store. Optional
  caps stop work through native blockers/approvals and refuse PM draft promotion;
  configured role models remain ordinary Kanban task overrides, and only unattended
  PM-OS routing rejects MoA. Hermes' model runtime and normal MoA capability remain
  unchanged.
- `git_flow.py` adds review cards, a human-owned-main policy, read-only merge-tree
  conflict checks, and opt-in atomic updates to a separate integration branch. It
  reuses native project task worktrees, deterministic branches, links, reviewers,
  comments, blockers, and completion APIs. Its garbage collector is a dry-run by
  default and preserves crash/reclaim evidence unless an operator explicitly removes
  clean terminal worktrees. It does not resolve conflicts or alter main.
- `doctor.py` projects binding, roster, conversation, native diagnostics, claims,
  retries, worktrees, budget attribution, reconciliation, and contract-check health.
  Its optional bounded restart drain delegates stale-claim recovery and mention
  delivery to existing Kanban/PMO APIs; it creates no monitor, scheduler, or state.
- `project_context.py` keeps immutable decisions and bounded shared project knowledge
  in append-only JSONL under an explicit workspace's version-controlled `.datansh/`
  directory. Supersession and dedupe usage append new events rather than mutating old
  records. Its bounded `context.md` projection complements Hermes memory, skills, and
  self-learning; it does not replace them or create a database/provider runtime.
- `report.py` is a read-only CLI edge utility. It requires an explicit board slug,
  resolves the existing Hermes Kanban database, and reports status counts, blocked
  and ready work, and assignee load in human-readable or JSON form. It opens SQLite
  in read-only mode and creates no reporting database or stored state.
- `ticket.py` is a thin structured-creation CLI edge. It requires an explicit
  existing board, repeated acceptance criteria and closure evidence, and a native
  Kanban assignee. It delegates status derivation and parent dependency links to
  `hermes_cli.kanban_db`; it adds no status, database, dispatcher, or model tool.
- `workflow.py` gives PM-OS a PM-owned lifecycle entirely through native Kanban
  operations. Draft/finalize map to `triage` and atomic `specify_triage_task`;
  cancel/archive retain cards; reopen creates one idempotent triage successor linked
  to its archived predecessor. Project roster, body/evidence, label-rule, and linked
  approval checks run before promotion. It adds no status, database, dispatcher,
  tool registry, or raw SQL.
- `timeline.py` builds one bounded chronological view from native task events,
  comments, runs, and linked approval tasks. It redacts durable content through
  Hermes's redactor and creates no audit store or observability runtime.
- `portfolio.py` derives cached project/portfolio rollups from explicit authorized
  project ids and native projects, boards, tasks, approvals, events, and runs. It
  has no all-project default, stored aggregate, or SQL of its own.
- `context_builder.py` separates a stable project/config prefix from bounded
  trigger, board, blocker, retry-checkpoint, and project-knowledge projections.
  Client contexts exclude internal projections and use the restricted Hermes
  client profile; normal memory and self-learning remain complementary inputs.
- `approval.py` models a project approval as a native Kanban parent task linked to
  the work it gates. Pending, approved, rejected, and withdrawn outcomes use the
  existing `triage`, `done`, `blocked`, and `archived` states; escalation routes the
  same task to a higher-rank approver, and native comments/events retain the audit
  trail. The edge accepts an authenticated caller identity/rank from its invoking
  surface and adds no auth service, database, chat runtime, dispatcher, or model tool.
- `approval_sla.py` performs a bounded, idempotent pass over pending native
  approvals. Reminder, overdue, deadline, and CEO-stop evidence are comments;
  automatic rank changes reuse `approval.escalate_approval` with the narrow
  `system` principal, while decisions remain human-only.
- `availability.py` records temporary availability, delegation, revocation, and
  acting-as decision audit in append-only project context. Delegated decisions
  still use the native approval edge; it adds no authority database and forbids
  subdelegation or granting authority above the delegator's rank.
- `mentions.py` parses project-local handles outside email and code, validates them
  only against `.datansh/project.yaml`, and records source comments, fan-out,
  delivery, read, and outbound acknowledgements as native Kanban comments/events.
  Restart delivery is oldest-first and bounded, and the actual gateway wake remains
  an injected callback. It creates no message table, watcher database, or transport.
- `escalation.py` composes Hermes' typed blocker transitions with project-local
  `@pm` routing and the Kanban-native approval gate. Human answers are copied back
  to the ticket before native unblocking resumes the worker; retry counts, deadline,
  and resolution evidence remain in task comments/events.
- `notifications.py` is a structurally egress-only quiet-hours policy over durable
  inbox projections. It cannot write messages/comments or wake agents. High urgency
  bypasses quiet hours; configured Hermes outbound adapters and signed HMAC webhook
  bodies project alerts without inbound handles. Successful outbound IDs are
  acknowledged separately through the PMO coordination edge so failures are retryable.
- `performance.py` and `scripts/pmo_loadgen.py` exercise representative project,
  board, ticket, comment, run, timeline, portfolio, and dashboard-read workloads
  through public APIs. They are bounded diagnostic/load-generation utilities, not
  a second scheduler, dispatcher, metrics store, or production runtime.
- `attachment_security.py` adds PMO-copy-only byte sniffing, an explicit content
  allowlist, generated storage names, and traversal-resistant display filenames.
  The copied route keeps native attachment records and the streaming size cap,
  stores outside the web bundle, and forces attachment plus `nosniff` downloads.
- `lifecycle.py` exports native project, board, task, comment, event, run,
  attachment, and project-context records before deletion. Exact-slug deletion
  removes only the bound native board and project record, preserves the repository
  and `.datansh`, and appends a profile-scoped tombstone. Retention removes only old
  archived native cards in bounded batches through public Kanban APIs.
- `repositories.py` projects one primary and zero or more connected Git
  repositories into the existing Hermes project-folder store. Public/local remotes
  retain normal Git behavior; private GitHub operations can use an ephemeral,
  repository-scoped GitHub App environment, persist no secrets, reject cross-project
  path overlap, and never delete a checkout when metadata is disconnected.
- `github_app.py` owns App-JWT verification, single-use principal/project-bound
  consent state, installation repository discovery, and short-lived installation
  token minting. Tokens and App secrets are process-local and never enter project
  config, API responses, Git URLs, arguments, logs, or temporary files.
- `project_onboarding.py` stages one new project, exactly one primary repository,
  and optional secondary repositories across multiple App installations before it
  invokes the existing additive project bootstrap. Each completed project keeps its
  dedicated `pm-<slug>` orchestrator profile; a PM profile is never shared.
- `agent_soul.py` writes a bounded role charter only into PM-OS-created agent
  profiles. It describes ticket-comment collaboration and preserves Hermes'
  normal memory, skills, tools, learning, and delegation runtime.
- `collaboration.py` expresses ask, transfer, and human escalation solely as
  native Kanban comments plus native task-assignee changes. It adds no message
  store, dispatcher, or side channel.
- `planning.py` records Founder’s Office planning and specialist consultations
  as marked native Kanban comments. It creates no separate planning database.

`systemd/hermes-kanban-dispatcher.service` remains byte-identical to the recorded
source. PM-OS intentionally reuses the existing Kanban dispatcher and database; it
must not start a competing PMO dispatcher.

Record future PM-OS-only changes in this section before changing the copied files.

### Private upstream APIs we call

| Symbol | Caller | Why |
|---|---|---|
| `kanban_db._resolve_worktree_workspace` | `plugins/pmo/demo.py::_materialize_worktree` | The demo claims tasks directly, but worktrees are materialized one layer up in `_dispatch_once_locked`. Re-implementing resolution would be a second, drifting copy (ADR-012). Re-check on every merge: if the signature moves, the demo produces boards that fail `hermes pmo doctor`. |

## Files edited outside `plugins/pmo/` — the whole accounting

The standing rule is zero upstream modification. These are the exceptions, recorded
so the CI boundary check has an explicit allowlist rather than a surprise, and so a
merge conflict in any of them is expected rather than alarming.

| File | Change | Why it is not a PM-OS concern |
|---|---|---|
| `tests/plugins/test_kanban_attachments.py` | `run_slash(f"attach {task_id} {src.as_posix()}")` | Windows portability. `run_slash` uses POSIX tokenization, so a native Windows path breaks an inherited test. Fixes upstream on Windows; changes no product behaviour. |
| `tests/plugins/test_kanban_dashboard_plugin.py` | `bundle.read_text(encoding="utf-8")` | Windows portability, and literally this repo's own lint rule — `PLW1514` is the single rule `ruff` enables, added after three locale-encoding regressions. |
| `.github/workflows/ci.yml` | added PM-OS lanes | Additive; no existing lane changed. |
| `README.md` | PM-OS section | Additive. |
| `package-lock.json` | npm workspace resolution | Incidental to `npm install`; review before every merge. |

**The two test edits are upstream bug fixes, not PM-OS adaptations, and both are
good candidates to contribute back** (018 §5). Neither weakens an assertion: the
attachment test still asserts `"Attached" in out`, and the sanitizer test still
asserts on the same allowlist symbols. Verify that remains true on every merge —
an upstream test edited by a downstream fork is exactly how a regression gets
masked, so the bar for touching one is that it must fix the test's *environment*,
never its *expectations*.

## Upstream fixes reviewed and skipped

| Upstream commit | Summary | Why skipped |
|---|---|---|
