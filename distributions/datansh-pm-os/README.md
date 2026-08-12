# Datansh PM-OS profile

This is a local Hermes profile distribution for running project work through
the Datansh PM-OS board. It adds a PM identity and operating skill; it does not
replace or restrict Hermes tools, memory, skills, self-learning, delegation,
gateway messaging, terminal access, or browser access.

Install it from the repository root:

```bash
hermes profile install ./distributions/datansh-pm-os
```

The installed profile is named `datansh-pm-os`. Pass `--name <name>` when a
separate instance is useful. The distribution owns only its `SOUL.md` and
`skills/datansh-pm-os/` payload, so normal profile state remains user-owned and
future distribution updates stay narrow.

Create or reuse a project and its isolated Kanban board from the repository root
(the board defaults to the project slug):

```bash
python plugins/pmo/bootstrap.py --slug acme --name "Acme" --workspace C:\work\acme
```

The bootstrap utility uses Hermes' existing project and Kanban stores. It refuses
to silently retarget an existing project or board to a different workspace. It also
creates `.datansh/project.yaml` once and preserves that file on later runs. Use
`--board delivery` when the explicit board slug differs from the project slug.

Plugin code can resolve the binding with
`plugins/pmo/project_scope.py:resolve(project_ref, board_slug=...)`. The board is
required and is checked against both the Hermes project row and board metadata; the
process-global active board is never consulted. Its `path_allowed`/`assert_path`
helpers resolve symlinks and protect PMO-owned config/file operations across all
registered Hermes project folders.

This is containment for plugin-owned paths, not a shell sandbox. `scope.deny` globs
are enforced by those helpers after symlink resolution, but they do not restrict
normal Hermes tools or arbitrary shell commands. Use Hermes approvals and a container
or remote terminal backend when commands themselves are untrusted.

For a read-only project snapshot, run:

```bash
python plugins/pmo/report.py --board acme
```

Use `--json` when the report feeds an automation. The utility opens the existing
board database read-only and never infers whichever board is currently selected.

Create an assigned, structured ticket on that board with:

```bash
python plugins/pmo/ticket.py --board acme --title "Ship release" --outcome "Users can install the release" --assignee release-manager --acceptance "The package installs cleanly" --evidence "Installation transcript"
```

Repeat `--constraint`, `--acceptance`, `--dependency`, `--parent`, and `--evidence`
as needed. Parent task IDs use Kanban's existing dependency graph, so Kanban itself
derives `todo` versus `ready`; `--triage` uses the existing triage workflow. The
utility creates no PM-OS status, database, dispatcher, or model tool.

Record a structured handoff, blocker, or review on an existing ticket with:

```bash
python plugins/pmo/comment.py --board acme --task t_123 --type handoff --author builder --summary "Ready for review" --owner reviewer --evidence "Focused tests pass"
```

Handoffs and blockers require `--owner`; reviews require `--verdict` (`approved`,
`changes-requested`, or `observations`). Repeat `--evidence` and `--next-action` as
needed. The utility writes through Hermes' existing Kanban comment API, including its
normal audit event; it does not create a message store, router, or wake loop.

Projects may optionally add `models` and `budget` blocks to
`.datansh/project.yaml`. Model values are passed to Hermes through the native
per-task override; PM-OS does not run a separate model router. Budget money is
parsed as decimal values and cost is joined from Hermes session accounting through
the `worker_session_id` already stored on Kanban completions. `plugins/pmo/cost.py`
provides project/ticket/agent projections, cap enforcement, PM-thread attribution,
and a reconciliation check. Unattended PM project routes reject `moa`, while normal
Hermes MoA use and every other Hermes capability remain available.
