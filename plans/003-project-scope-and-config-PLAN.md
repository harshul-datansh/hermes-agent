# 003 — Project Scope & Per-Project Config

**Covers:** #3 (PM is project-based, never shared), #9 (folder/project scopes — no
looking into other projects), #10 (project-specific `config.yaml`), #11 (no user-level
config).
**Depends on:** 001. Runs in parallel with 002.
**Time budget:** 1 h 30 m.

> **Compatibility reset (binding):** Do not add `pmo_scope`, custom filesystem tool
> wrappers, `pmo_config`, a parallel handle namespace, or `pmo project bootstrap` in
> this phase. Hermes Kanban boards, profile isolation, project/worktree support, and
> existing approval controls are the active boundaries. A PM-OS profile may document
> and select those existing features; any new metadata must be proven necessary and
> remain a narrow plugin-owned addition.
**Definition of done:** an agent running on project A cannot read a file under project
B's folders, cannot see B's tickets, and cannot resolve `@handle` into B — and there is
a test for each of the three.

---

## 1. The scope primitive

`000-CONTEXT.md` D3: **project == board == folder set**. All three already exist
upstream, they just are not enforced together.

```
projects.id ──┬── projects.board_slug  ────► kanban.db board (tickets)
              ├── project_folders[]    ────► filesystem scope
              ├── agent_identities[]   ────► @handle namespace  (pmo.db)
              └── threads[]            ────► founder's office   (pmo.db)
```

### `hermes_cli/pmo_scope.py`

```python
@dataclass(frozen=True)
class ProjectScope:
    project_id: str
    slug: str
    name: str
    board_slug: str
    primary_path: Path
    folders: tuple[Path, ...]     # all project_folders, normalized absolute
    config: dict                  # merged, see §3

def resolve(project_ref: str) -> ProjectScope:
    """Resolve a slug or id to a scope. Raises UnknownProject."""

def path_allowed(scope: ProjectScope, path: str | Path) -> bool:
    """True iff ``path`` resolves inside one of ``scope.folders``."""

def assert_path(scope: ProjectScope, path) -> Path:
    """Return the resolved path or raise ScopeViolation."""
```

`path_allowed` must:

1. `Path(path).expanduser().resolve()` — **resolve symlinks**. A symlink out of the
   project directory is the obvious bypass and `os.path.abspath` will not catch it.
2. Compare with `Path.is_relative_to()`, not string `startswith`. `/srv/acme-secrets`
   starts with `/srv/acme` and `startswith` would wave it through.
3. Reject any path that does not exist *and* whose nearest existing parent is outside
   scope (so writes to new files are checked too).

```python
def path_allowed(scope, path):
    p = Path(path).expanduser()
    probe = p
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent          # nearest existing ancestor
    try:
        real = probe.resolve(strict=True)
    except OSError:
        return False
    return any(real == f or real.is_relative_to(f) for f in scope.folders)
```

Test it against: `../`, an absolute path to a sibling project, a symlink pointing out,
a path whose *parent* is in scope but which resolves out, and a UNC path
(`\\?\C:\...`) — this runs on Windows.

---

## 2. Enforcing scope in three layers

Requirement #9 has three attack surfaces. Cover all three; any one alone is theatre.

> ### ⚠ Much of this is already enforced — VERIFIED V-22
>
> The reverse-engineering atlas (`13` §1, §3) documents worker isolation that is stronger
> than this plan assumed and already built:
>
> - **Board is a hard boundary.** Workers spawn with `HERMES_KANBAN_BOARD` pinned and
>   *"cannot see other boards at all."*
> - **Profile-scoped `HERMES_HOME`** — separate config, credentials, memory, sessions,
>   skills. `_apply_profile_override()` sets it *before any other module is imported*, so
>   every `get_hermes_home()` resolves inside the profile.
> - **Pinned `TERMINAL_CWD`** and `HERMES_KANBAN_WORKSPACE`.
> - *"The env var is the entire binding between an OS process and a specific claimed
>   card."*
> - **Tenant** is a soft namespace within a board (workspace-path + memory-key isolation)
>   — a second axis we get for free.
>
> So §2a below is **defence in depth**, not the only line: a pinned CWD is a default, not
> a jail, and an agent can still pass an absolute path. Keep `path_allowed`, but check
> **`tools/path_security.py`** first (`04` §5 lists it) — it may already do the work.
> Also note `tools/approval.py` force-asks on `.env`, `id_rsa`, `.git/`, `.ssh/`
> regardless of policy — but only on surfaces where its ContextVar is set, which excludes
> CLI and gateway (`07` §5). Do not rely on it.

### 2a. Filesystem — the worker's cwd and tool guard

Hermes already isolates worktrees: see `tests/hermes_cli/test_kanban_worktree_isolation.py`
and `tasks.workspace_path` / `branch_name`. Build on it, do not reinvent.

- When the dispatcher claims a PM-OS task, set `workspace_path` under
  `scope.primary_path` (upstream already does this when `tasks.project_id` is set —
  verify, then rely on it).
- Add a **toolset wrapper** that calls `assert_path(scope, ...)` on the path argument
  of every filesystem tool (`read_file`, `write_file`, `list_dir`, `glob`, `grep`,
  shell `cwd`). Register it in the PM-OS profile's toolset so it is not bypassable by
  prompt.
- Shell tool: pin `cwd` to `scope.primary_path` and reject a command containing an
  absolute path outside scope. This is best-effort — a determined shell command can
  always escape. Note this honestly in the docs; the real boundary for untrusted work
  is a container, and that is P1.

### 2b. Board — ticket isolation

Every PM-OS query addresses a board by **explicit slug** derived from the project.
Never call the upstream "active board" helper; the active board is process-global
state and it is exactly how a cross-project read happens.

```python
# right
with kanban_db.connect_closing(board=scope.board_slug) as conn: ...

# wrong — inherits whatever board was last switched to
with kanban_db.connect_closing() as conn: ...
```

Grep for `connect_closing()` and `connect()` with no `board=` in `plugins/pmo/` after
porting; there should be zero.

Belt and braces: after loading a task, assert `task["project_id"] == scope.project_id`
before returning it. A board slug collision or a manual DB edit should fail loudly.

### 2c. Comms — handle namespace

`agent_identities` is keyed `(project_id, handle)`. Mention resolution (plan 005)
takes a `project_id` and only ever queries within it. An `@handle` that exists in
another project resolves to *nothing*, and the comment is rejected with the list of
valid handles for the current project.

This is the neatest part of the design: because there is no global handle namespace,
cross-project messaging is not "blocked", it is **unnameable**.

---

## 3. Per-project config (#10)

### File location

```
<project.primary_path>/.datansh/project.yaml
```

Inside the project's own repo, so it is version-controlled with the work and reviewable
in a PR. `.datansh/` also holds the agent souls (plan 004).

### Schema

```yaml
version: 1

project:
  name: "Acme Portal"
  slug: "acme"
  client: "Acme Corp"

orchestrator:
  profile: "pm-acme"          # the PM agent for THIS project (#3)
  model: "anthropic/claude-opus-4.6"
  auto_decompose: true
  auto_promote_children: true

agents:                        # the @handle roster (#7)
  - handle: "dev-1"
    profile: "acme-dev-1"
    role: "Backend engineer"
    toolset: "coding"
  - handle: "qa"
    profile: "acme-qa"
    role: "QA reviewer"
    toolset: "review"

board:
  columns: ["todo", "in_progress", "blocked", "review", "done"]
  wip_limits: { in_progress: 3 }
  default_assignee: "dev-1"

approvals:
  # A ticket matching any rule needs sign-off before it can leave `todo`.
  rules:
    - when: { label: "spend" }        , required_rank: 70    # CFO
    - when: { label: "scope-change" } , required_rank: 70
    - when: { label: "release" }      , required_rank: 100   # CEO

escalation:
  pm_retry_limit: 2            # PM attempts before escalating to humans (#6)
  blocked_timeout_minutes: 30

scope:
  folders:                     # additive to project_folders in projects.db
    - "."
  deny:
    - ".env"
    - "**/secrets/**"
```

### `hermes_cli/pmo_config.py`

```python
def load_project_config(scope_or_project_id) -> dict:
    """Merge repo defaults ← ~/.hermes/config.yaml ← <project>/.datansh/project.yaml."""
```

Rules:

- **Exactly three layers.** Requirement #11 means there is no user layer, ever.
  Add an assertion in this function's test.
- Deep-merge dicts; **replace** lists (a project overriding `agents:` means "this
  roster", not "append to the global one").
- Validate with a pydantic model. An invalid `project.yaml` is a hard error at project
  load — not a silent fallback to defaults. This is the opposite of what
  `hermes_cli/config.py` does for the global file (it warns and falls back), and the
  difference is deliberate: a global config falling back degrades one user's session,
  a project config falling back could point an agent at the wrong board.
- Cache per `(path, mtime_ns, size)`, mirroring `hermes_cli/config.py`'s pattern.
- **Never** read `project.yaml` from a path that fails `path_allowed`. A project
  cannot bootstrap another project's config.

### Reconciling with the global kanban settings

The upstream kanban plugin reads `config.kanban.orchestrator_profile` globally, and
`PUT /orchestration` writes it globally. PM-OS must not: requirement #3 says the PM is
never shared.

So in `plugins/pmo/`, `GET/PUT /orchestration` becomes
`GET/PUT /projects/{project_id}/orchestration` and reads/writes
`.datansh/project.yaml`, not `~/.hermes/config.yaml`. Keep the response shape
(`orchestrator_profile`, `resolved_orchestrator_profile`, `active_profile`, …) so the
forked UI code needs minimal change — that shape is already good.

---

## 4. Project bootstrap

`hermes pmo project bootstrap --slug acme --path C:\work\acme --name "Acme Portal"`

Steps, all idempotent:

1. `projects_db.create(slug, name, primary_path)` → `project_id`.
2. `project_folders` row for `primary_path`, `is_primary=1`.
3. Create Kanban board `acme` (`POST /boards` equivalent), set `projects.board_slug`.
4. Write `.datansh/project.yaml` from a template if absent.
5. Create the PM profile `pm-<slug>` from `.datansh/agents/project-manager.md` (plan 004).
6. Insert `agent_identities` rows for `pm` + each entry under `agents:`.
7. Create the founder's-office thread for the project (plan 006).
8. Add the bootstrapping user as `project_admin` in `project_members`.

Run `hermes pmo doctor` after — it should report zero orphans.

---

## 5. Tests

```
tests/hermes_cli/test_pmo_scope.py
  test_path_allowed_rejects_parent_traversal
  test_path_allowed_rejects_symlink_escape
  test_path_allowed_rejects_sibling_prefix          # /srv/acme vs /srv/acme-secrets
  test_path_allowed_accepts_new_file_in_scope
  test_path_allowed_windows_unc

tests/hermes_cli/test_pmo_config.py
  test_merge_order_is_three_layers
  test_no_user_config_layer                          # asserts #11
  test_list_values_replace_not_append
  test_invalid_yaml_raises_not_falls_back

tests/plugins/test_pmo_project_isolation.py
  test_board_query_requires_explicit_slug
  test_task_from_other_project_is_rejected
  test_mention_of_other_project_handle_is_unresolvable
```

The last three are the real proof of requirement #9. Write them first; they will fail
for the right reasons and then pass for the right reasons.

---

## P0 / P1

**P0:** `pmo_scope.resolve/path_allowed/assert_path`; explicit-board-slug discipline
across the forked routes; `.datansh/project.yaml` with load + validate + 3-layer merge;
per-project `/orchestration`; `pmo project bootstrap`; the isolation tests.

**P1:** container-level isolation for the shell tool; `scope.deny` glob enforcement
(day 1 can ship the field parsed but unenforced — **say so in the docs, do not imply
it works**); a UI editor for `project.yaml`; project archive/restore.

## Traps

- `str.startswith` for path containment. Use `Path.is_relative_to` on resolved paths.
- Forgetting `board=` on one `connect_closing()`. That single call site is a
  cross-project read. Grep for it as a build step.
- Windows path comparison is case-insensitive and has two separator characters.
  Normalize through `Path` and compare `Path` objects, never strings.
- Do not silently fall back when `project.yaml` is malformed. Loud failure here is a
  feature.
