# PM-OS implementation changelog

## 2026-08-05 - acceptance and UX hardening

- Final focused evidence is green: 196 PMO tests across the auth, scope, workflow,
  dashboard, approval, escalation, and platform suites; Node syntax checks pass.
- Browser verification fixed first-run portfolio identity bootstrap, sticky native
  conversation blocking, Founder-thread ordering, dark-theme contrast, rank-aware
  approval routing, and approval confirmation UX. Decisions now use an accessible inline
  form with visible error and live-status feedback.
- Portfolio delivery rollups exclude native Founder/client conversation records while
  keeping those threads visible; real blocked work remains counted.
- The local demo fixture remains intentionally available at `.pmo-local-demo/` so the
  authenticated dashboard can be restarted and handed off for local QA. Credentials
  are local-only test values (`ceo@datansh.local` / `demo-password`).

## 2026-08-05 - auth and human delegation follow-up

- Added project-local member/rank management with atomic `access.yaml` writes,
  effective actor/capability output, and role-aware Access UI.
- Added PM-only native human-task drafts, a PM finalize endpoint, and a Human tasks
  dashboard form. Human assignees stay in triage until PM finalization and are not
  valid Hermes worker profiles.
- Enforced project-handle mentions in the Founder composer and added visible gateway
  error feedback instead of silently leaving a failed message in the input.
- Added duplicate project-manager profile detection across active project configs.
- Agent-authored task comments now require a project @mention and persist the
  authenticated principal; added the protected Hedgi ERP `/project-management`
  handoff with configurable `VITE_PMO_DASHBOARD_URL`.

## 2026-08-05 — additive foundation

- Fork baseline: `plugins/kanban` at `22210107177b63428ef2a8a3a58d2f0eb6566ed6`,
  copied to `plugins/pmo`; the copy itself is not counted as PM-OS customization.
- Customized production files: `plugins/pmo/dashboard/manifest.json`,
  `plugins/pmo/dashboard/plugin_api.py`, `plugins/pmo/dashboard/dist/index.js`, and
  the additive PM edge utilities
  `plugins/pmo/{bootstrap,project_scope,ticket,comment,report,workflow}.py`.
- Plan 003 scope/config behavior: bootstrap writes a validated project-local
  `.datansh/project.yaml` idempotently; project consumers must select a board
  explicitly and validate its Hermes project binding; plugin-owned path checks use
  resolved `Path` containment so traversal, sibling prefixes, and symlink escapes are
  rejected; project `scope.deny` globs are also enforced on resolved PMO-owned paths.
  This is not a replacement filesystem tool or shell sandbox.
- New additive profile payload: `distributions/datansh-pm-os/**`; new reusable skill:
  `skills/productivity/datansh-pm-os/**`.
- Existing Hermes core and upstream `plugins/kanban/**` changed: **0 files**.
- Guard: `python scripts/check_pmo_kanban_sync.py` must report a clean upstream Kanban
  tree and only the documented PM-OS divergences.
- Capability guard: `python scripts/check_pmo_capability_contract.py` must report that
  the PM profile remains a SOUL-and-skill payload and that PMO additions introduce no
  replacement runtime, database, model tool, raw SQL, or core/upstream Kanban change.
- Configured `upstream` as `https://github.com/NousResearch/hermes-agent.git` so sync
  reviews can compare the copied PMO plugin against the real Hermes source.
- Plan 009 and the finalization edge of plan 004 use native Kanban end to end:
  `triage` is the PM draft gate, `specify_triage_task` is finalization, approval cards
  are linked parents, and cancel/archive/reopen retain predecessor cards and history.
  The legal-action contract refuses unsupported transitions without direct status SQL.
- Plan 012 uses the existing `worker_session_id` completion stamp and Hermes
  `SessionDB` accounting for project attribution. PMO-owned completions serialize
  cost as a decimal string in native run metadata, hard caps route through native
  blockers/CFO approvals, monthly caps gate finalization, and project role models are
  ordinary per-task overrides. No billing database, model runtime, or dispatcher was
  added, and MoA is excluded only from unattended PM project routes.
- Plan 011 keeps Hermes' native project-bound worktree path and deterministic branch
  convention, adds linked native review workers, makes `main` human-owned by default,
  permits automatic integration only to a separate configured branch, and blocks
  production conflicts with an `@pm` comment instead of resolving them. Declared
  `touches` overlaps are advisory. Cleanup is dry-run/evidence-preserving by default.
- Plan 014 adds a read-only doctor projection and an explicitly applied bounded restart
  drain over native reclaim and mention-delivery APIs. It reports contract, binding,
  profile, thread, worktree, retry, budget, and reconciliation health without adding a
  parallel recovery runtime.
- Plan 017 now has deterministic production-API acceptance for PM decomposition and
  native worker/reviewer execution, an opt-in one-turn metered L4 scenario with cost
  trending, and a pinned PMO static lane. Ruff checks the full PMO-owned Python surface;
  blocking `ty` covers the additive gateway, cost, doctor, git-integration, CLI, and
  compatibility-checker boundary while the existing repository lint lane continues to
  publish advisory full-tree type debt.
- Plan 026 behavioral parity now proves that the PM platform inherits Hermes gateway
  turns and the normal toolset, delegation still spawns a normal child agent (with only
  the external model mocked), human tool approvals still approve/deny through the core
  gate, memory and skill create/edit remain available, and Kanban workers heartbeat,
  comment, and complete through the normal registry-dispatched agent tools.
- The executable customization budget is **42 / 4,174 unique production files
  (1.01%)**, measured against the recorded Hermes baseline. The numerator excludes the
  byte-identical Kanban copy plus docs/tests/CI and includes only changed/added PMO
  plugin files and shipped platform/profile/skill/operator-script surfaces. Under 5%
  is accepted as minimal; the checker fails only above 10% (core/Kanban changes remain
  independently forbidden).

The customization count is deliberately tracked by owned production files, not copied
baseline lines. Any future core/upstream-Kanban change or change that removes a Hermes
capability requires an ADR and parity coverage before implementation.
