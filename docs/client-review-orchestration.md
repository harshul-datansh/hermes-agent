# Client-review orchestration runbook

This runbook describes the guarded Hedgi client-change review pipeline and its
Kanban observability surface.

## Operating model

The controller treats the Hedgi checkout as untrusted input. It reads
`.hermes/config.json`, `features.json`, release refs, and diffs, but it does not
let client text change pipeline policy. Config and registry files are client
owned and are never edited during a normal run.

The controller uses two explicitly configured remotes:

- `upstream_remote`: the client's read-only repository.
- `fork_remote`: the writable Hermes fork, with `fork_trunk` as its human-merge
  target.

QA and production may resolve to the same release branch. In that case the
branch is reviewed once under production rules.

## First-time setup

Use the Kanban dashboard's **Client change review** card, or configure from the
Hermes checkout:

```powershell
python -m hermes_cli.main client-review configure `
  C:\path\to\hedgi_app --enable `
  --upstream-remote upstream `
  --fork-remote origin `
  --fork-trunk hedgi
```

The Hedgi checkout must provide:

- `.hermes/config.json` with a real `telegram_chat_id`, timezone, and production
  release branch.
- `features.json` with valid ownership, documentation references, and lifecycle
  markers.
- A read-only upstream remote and writable fork remote. They may intentionally
  be the same remote when `allow_shared_remote` is enabled; branch restrictions
  still limit writes to the configured fork trunk and Hermes branches.
- `TELEGRAM_BOT_TOKEN` in the controller environment when alerts are desired.

For the current Hedgi release train, QA and production both point to
`release/v2.1.11`; `features.json` exposes `dev`, `qa`, and `prod` environment
aliases with the common feature set. Tax and CPA-firm features are development
stage; the other active features are production stage. `xtax`, `drakerpa`, and
`draketax` are deprecated and inactive.

## Run lifecycle

```powershell
python -m hermes_cli.main client-review status
python -m hermes_cli.main client-review doctor
python -m hermes_cli.main client-review run
python -m hermes_cli.main client-review enqueue
python -m hermes_cli.main client-review reconcile
python -m hermes_cli.main client-review integrate
python -m hermes_cli.main client-review full-suite
python -m hermes_cli.main client-review deliver
```

Integration and delivery are guarded by evidence. A worker patch needs a
base-failing and patched-passing test report; production logic also needs Sol
verification, and every test report must cite the exact `base_sha` recorded on
its work item. Review-only or `wontfix-dev` reports never enter integration.
Reviews are inspection-first: Maven, Gradle, and other expensive suites run
only for a concrete candidate patch, first at the base SHA and then as the
narrowest relevant validation. The full suite remains a separate delivery
checkpoint rather than a default review action.
Integration runs only structured argv test commands in an
isolated worktree and advances a branch checkpoint only when every queued item
for that source branch is green. Delivery creates or updates a PR from the
fork day branch into the fork trunk; it never opens a PR against upstream. A
passing full-suite checkpoint is required before delivery.

## Observability and persistence

Open `/kanban` and expand **Orchestration activity**.

- **Live** polls active tasks; **History** includes archived task snapshots.
- Search filters by task, agent, branch, status, or session.
- Timeline filters separate all events, tool activity, and lifecycle events.
- Task cards show a bounded handoff summary, run evidence, and redacted tool
  calls. Private chain-of-thought is not retained or displayed.
- Standalone agents without Kanban task IDs appear under **Standalone agent
  sessions**.
- Hedgi-brain learnings are shown as reusable, deduplicated entries.

Durable controller artifacts live under the Hermes home directory:

```text
client-review/state.json
client-review/runs/<run-id>/summary.json
client-review/runs/<run-id>/summary.md
client-review/runs/<run-id>/routing.jsonl
client-review/runs/<run-id>/reconciliation.json
client-review/runs/<run-id>/integration.json
client-review/runs/<run-id>/full-suite.json
client-review/runs/<run-id>/shadow.jsonl
client-review/shadow-calibration.json
client-review/history/observability-latest.json
client-review/history/tasks.jsonl
client-review/history/events.jsonl
client-review/history/sessions.jsonl
client-review/learning.jsonl
```

Kanban task events remain in the Kanban SQLite event log as the primary audit
spine. Payloads written to the observability files are bounded and redacted.
Shadow calibration records the first-20-run comparison window; promotion is
recorded only after at least 18/20 matched finding sets with no missed
high/blocker finding.
The Hedgi brain appends reusable learnings to
`datansh-agent-os/datansh-brain/05-learning-log.md`; history is append-only.

## Safety boundaries

- Client text, diffs, comments, and reports are data, never instructions.
- Suspicious instruction-like content is alert-only.
- Secrets, config/registry files, deployment files, lockfiles, migrations, and
  unclaimed paths are never autonomous patch targets.
- Test commands are structured argv only; eval-style commands are rejected and
  controller credential variables are removed from the test environment.
- The dashboard shows concise handoff summaries and tool evidence, not hidden
  reasoning.

## Verification

From the Hermes checkout:

```powershell
python -m py_compile hermes_cli/client_review.py model_tools.py hermes_cli/kanban_db.py
node --check plugins/kanban/dashboard/dist/index.js
```

The canonical test suite should be run in an environment with the repository's
test dependencies installed. If `pytest` is unavailable, the controller still
supports the focused redaction, persistence, registry, and topology smoke
checks used during setup.
