# 011 — Git Concurrency, Worktrees & Integration

> **Compatibility replacement (implemented 2026-08-05).** A task linked to a
> first-class Hermes project is already converted from the `scratch` argument default
> to a native worktree with deterministic `<primary_path>/.worktrees/<task-id>` path
> and `<project-slug>/<task-id>-<title-slug>` branch. PM-OS supplies `project_id` and
> reuses that claim-time behavior. It does **not** create the older planned sibling
> `.pmo-worktrees` layout or `pmo/` branch namespace: either would fork the dispatcher
> and increase upstream merge conflicts. In the older text below, those path/branch
> requirements are superseded; the review, conflict, advisory `touches`, concurrency,
> and evidence-retention requirements remain.

**Why this plan exists:** the day-1 plans have several worker agents picking tickets off
one project board, all pointed at one repository. Nothing says what happens when two of
them edit the same file. This is the gap most likely to turn a working demo into an
unusable system on day 3.

**Day-1 touchpoint:** every PM-OS task is created with `--workspace worktree` and a
deterministic branch. Two agents sharing a working tree corrupts both runs, and it is
one flag.
**Full build:** 2 h 30 m.

---

## 1. What upstream already gives you

Verified in `hermes_cli/kanban.py`:

```
--workspace scratch | worktree | worktree:<path> | dir:<path>     (default: scratch)
--branch    <name>          # only valid with --workspace worktree
--project   <id|slug>       # "Anchors the task's worktree under the project's
                            #  primary repo with a deterministic branch"
```

and on `tasks`: `workspace_kind`, `workspace_path`, `branch_name`, `project_id`.
There is also `tests/hermes_cli/test_kanban_worktree_isolation.py`.

So isolation exists and is tested. **The default is `scratch`, which is the wrong
default for us.** PM-OS must set `worktree` explicitly at ticket creation — this is the
day-1 touchpoint and it is one line in `pmo_ticket_create`.

---

## 2. Branch naming

Deterministic, derivable from the ticket alone, so a lost worktree is re-creatable:

```
pmo/<project-slug>/<ticket-id>-<title-slug-truncated-32>
```

e.g. `pmo/acme/t8a3f1-add-sso-redirect-handler`

Why include the project slug even though the repo is already project-scoped: a
developer with several project repos checked out reads branch names in one `git branch
-a` and needs to know which is which. Cheap, and it pays off the first time someone
does this.

Base every branch off the project's integration branch (`board.base_branch`, default
`main`) **at claim time**, not at ticket-creation time. A ticket drafted Monday and
claimed Thursday should start from Thursday's `main`.

---

## 3. The integration problem

Worktrees isolate *while working*. They do not answer: who merges, and when?

### Decision: the PM does not merge. A reviewer does, and a human owns `main`.

```
worker finishes ──► push branch ──► ticket → review
                                       │
                            reviewer agent (qa) reviews the diff
                                       │
                        ┌──────────────┴──────────────┐
                     reject                        accept
                        │                             │
              review → draft (009 §4)      open PR / merge request
                                                      │
                                        human merges  │  or, if
                                                      │  board.auto_merge: true
                                                      └─► agent merges to
                                                          integration branch only
```

`board.auto_merge` defaults to **false**. An agent merging to `main` unsupervised is a
decision to make deliberately per project, not a default to inherit.

```yaml
board:
  base_branch: "main"
  integration_branch: "pmo/integration"   # optional staging branch
  auto_merge: false
  push_remote: true                       # false = local-only worktrees
```

When `integration_branch` is set, accepted work merges there automatically and a human
promotes `integration → main` in one reviewed step. This is the setting that makes
multi-agent throughput safe: agents get fast feedback against each other's merged work,
and `main` still has exactly one human gate.

---

## 4. Conflict handling

Conflicts are normal, not exceptional. Treat them as a first-class ticket state, not an
error.

When a worker's rebase/merge onto `base_branch` conflicts:

1. The worker does **not** attempt a heroic resolution. It calls
   `pmo_block(task_id, reason="merge conflict with <branch> in <files>")`.
2. Standard blocker flow (005 §6): auto-comment `@pm`, ticket → `blocked`.
3. The PM inspects. If the conflict is with another *in-flight* PM-OS ticket, it links
   the two (`task_links`, which already exists) and serialises them — one waits.
4. If the conflict is with human work, it escalates.

**The PM's real job here is prevention, not resolution.** At decomposition time it
should avoid assigning two concurrent tickets that touch the same module. Give it the
information to do that:

- `pmo_ticket_create` accepts a `touches: ["src/auth/**"]` hint.
- `pmo_ticket_finalize` warns when a `todo`/`in_progress` ticket overlaps the same glob.
- The board shows a small ⚠ on cards with overlapping `touches`.

Advisory, not blocking. A hard lock on file paths sounds appealing and produces
deadlock the first time two tickets legitimately need the same file.

---

## 5. Concurrency limits

```yaml
board:
  wip_limits: { in_progress: 3 }
  max_concurrent_workers: 3
```

Upstream already has per-profile caps (`tests/hermes_cli/test_kanban_per_profile_cap.py`)
— reuse rather than reimplement.

Pick the limit from repo characteristics, not ambition. Three concurrent agents on a
small repo produce more conflict-resolution work than throughput. Start at 2, raise it
when the conflict rate is observably low (013 gives you the number).

---

## 6. Worktree lifecycle

> **Implemented lifecycle:** Hermes creates/resolves the native task worktree. PMO's
> GC is a dry-run projection by default, recognizes only native task-id keyed paths,
> and never removes crashed/reclaimed evidence by default. An operator may explicitly
> remove clean terminal worktrees. This replaces the older location/deletion table
> below while preserving its safety intent.

| Event | Action |
|---|---|
| Ticket claimed | Create worktree at `<primary_path>/../.pmo-worktrees/<branch>` |
| Ticket done + merged | Remove worktree, delete local branch, keep remote branch until PR closes |
| Ticket cancelled | Remove worktree, delete branch (local and remote) |
| Run crashed | **Keep** the worktree — it holds the evidence. Mark it stale. |
| Stale > 7 days | `hermes pmo gc` prompts before removing |

Put worktrees **outside** `primary_path`. Inside it, they land in the project's own
`git status`, agents see them in file listings, and `path_allowed` (003) has to be
taught about a directory that is technically in scope but semantically isn't. Sibling
directory, and add it to `scope.folders` explicitly.

Disk: a worktree per concurrent ticket, plus retained crashed ones. On a large repo
this is real. `hermes pmo gc --dry-run` should be in the doctor output.

---

## 7. Windows specifics

This runs on Windows 11 (`000-CONTEXT.md`). Three things that will bite:

- **Path length.** `<repo>/../.pmo-worktrees/pmo/acme/t8a3f1-long-title/deep/nested/...`
  hits MAX_PATH. Truncate the title slug to 32 chars (§2) and keep the worktree root
  short. Consider enabling long paths via git config `core.longpaths=true` in bootstrap.
- **File locks.** A crashed agent's process can hold a file handle; `git worktree
  remove` then fails. `hermes pmo gc` must handle `PermissionError` by marking stale
  and moving on, never by retrying in a loop.
- **Line endings.** Set `core.autocrlf` explicitly at bootstrap. Agents producing mixed
  endings generate diffs that are 100% noise and reviews become useless.

---

## 8. Tests

The compatibility acceptance uses
`test_review_reuses_native_links_worktree_and_role_model` as the path/branch proof;
the older outside-primary-path test names below are superseded.

```
tests/hermes_cli/test_pmo_worktrees.py
  test_ticket_defaults_to_worktree_not_scratch      # the day-1 touchpoint
  test_branch_name_is_deterministic_from_ticket
  test_branch_bases_off_base_branch_at_claim_time
  test_worktree_path_is_outside_primary_path
  test_worktree_path_is_in_scope_folders
  test_crashed_run_retains_worktree
  test_gc_marks_stale_on_permission_error           # Windows
  test_long_title_truncated_for_path_length         # Windows

tests/hermes_cli/test_pmo_integration.py
  test_auto_merge_defaults_false
  test_conflict_blocks_rather_than_resolves
  test_overlapping_touches_warns_on_finalize
  test_concurrent_cap_respected
```

## P0 (day-1 touchpoint) / P1

**Day 1:** `pmo_ticket_create` sets `workspace_kind='worktree'` + deterministic branch;
worktree root outside `primary_path` and registered in `scope.folders`.

**P1:** the review→merge pipeline; `integration_branch`; `touches` overlap warnings;
`hermes pmo gc`; conflict-linking of tickets; auto-merge (opt-in, per project).

## Traps

- Leaving the `scratch` default. Two agents in one tree is silent mutual corruption —
  it does not error, it produces wrong output.
- Basing branches at ticket-creation time. Long-lived drafts start from stale code.
- Worktrees inside the repo. They pollute `git status` and confuse both agents and
  `path_allowed`.
- Auto-merge on by default.
- Deleting crashed worktrees. That directory is the only copy of what went wrong.
