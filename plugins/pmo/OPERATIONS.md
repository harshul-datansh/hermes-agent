# PM-OS operations

PM-OS stays mergeable by keeping Hermes core and `plugins/kanban` upstream-owned.
The product is the copied `plugins/pmo` plugin plus additive profile and skill
payloads; it does not replace Hermes agents, memory, self-learning, delegation,
gateway, MCP, approvals, or the normal tool registry.

## Weekly upstream review

Run on a fixed weekly cadence:

```powershell
python scripts/pmo_upstream_review.py --fetch
python scripts/check_pmo_kanban_sync.py
python scripts/check_pmo_capability_contract.py
```

Review every changed path printed by the first command. Port relevant Kanban fixes
deliberately into `plugins/pmo`, record the divergence in `UPSTREAM.md`, and update
the recorded source only after focused tests pass. Never edit `plugins/kanban` to
resolve a PM-OS problem.

## Release gate

Before tagging `pmo-vMAJOR.MINOR.PATCH`, run the two contract checks, all focused
`test_pmo_*` tests, and the scripted/live acceptance lanes. Record the PM-OS version
and upstream base in `plans/CHANGELOG.md`. The copied manifest is the product version.

## Health, restart recovery, and worktree cleanup

Run the read-only projection first:

```powershell
hermes pmo doctor --project <slug> --board <board> --json
hermes pmo git --project <slug> --board <board> gc
```

`doctor` reports project bindings, profiles, conversation records, native Kanban
diagnostics, expired claims, retry streaks, worktrees, pending mention deliveries,
cost attribution, reconciliation, and compatibility contracts. `git gc` is a PMO
subcommand here, not Git's object database command; it is a dry-run unless `--apply`
is supplied. Crashed/reclaimed worktrees are preserved as evidence by default.

After a process restart, an operator may apply a bounded native reclaim pass:

```powershell
hermes pmo doctor --project <slug> --board <board> --drain --limit 25
```

Review approval never updates `main`. With `board.auto_merge: false` (the default),
approval records that a human merge is required. Automatic integration requires a
different `board.integration_branch`; production conflicts block the review and
comment `@pm` without attempting resolution. PM-OS keeps Hermes' native
`<repo>/.worktrees/<task-id>` layout and deterministic branch convention because
replacing them with the older planned sibling `.pmo-worktrees` layout would fork core
dispatch behavior and increase upstream merge risk.

## Service deployment

Use Hermes's existing dashboard service/container packaging and its existing Kanban
dispatcher. PM-OS must not start a second agent loop, dispatcher, or gateway. Keep
`HERMES_HOME` on a local persistent volume and back it up. A shared deployment also
requires TLS, the host authentication surface, and the project-scope/security gates.

Build the additive project sandbox image on every host that executes PM turns:

```powershell
.\scripts\build_pmo_project_sandbox.ps1
```

The default project config selects `scope.terminal_isolation: docker` and
`scope.terminal_image: datansh-pm-os:sandbox`. The gateway fails terminal calls
closed when that image, Docker daemon, or any required project mount is unavailable.
Use `host_guard` only for trusted local development; it is not a security boundary.

## Repository connections

Repository connections are versioned in the primary checkout's
`.datansh/project.yaml`; Hermes `projects.db` remains the folder/session projection.
Inspect both through one validated edge:

```powershell
hermes pmo repo list --project <slug> --board <board> --json
hermes pmo repo validate --project <slug> --board <board>
```

Connect existing public/local checkouts with `repo connect`, or use `repo clone`.
Private GitHub repositories must go through the dashboard's staged **Project
onboarding** GitHub App consent flow. Configure `pmo.github_app.app_slug`, `app_id`,
`client_id`, and `callback_url` in server `config.yaml`; configure
`private_key_env` there, but put the PEM value only in that server environment
variable. Never put a token or private key in a project file or remote URL.

Each selected repository is re-verified against `/installation/repositories` and
stores only App installation/repository identifiers. Git receives a short-lived,
single-repository installation token through a process-local credential helper. The
token is not persisted, logged, returned to the browser, or placed in argv. Contents
write permission is mandatory for a primary or write-enabled repository. One
project may use multiple installations; the consent grant remains bound to the
authenticated global administrator and that project or onboarding draft. In-memory
drafts/grants intentionally expire and must be consented again after a server restart.

Repository paths must be disjoint from every other active project and from other
repositories in the same project. `remove` is intentionally non-destructive and
leaves files on disk. `access: read` is enforced by a read-only Docker bind mount;
do not use the development-only `host_guard` mode when that boundary matters.
