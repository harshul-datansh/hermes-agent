# 001 — Foundation & Scaffold

**Covers:** requirement #12 (copy the frontend service where kanban lives).
**Depends on:** nothing. This is the first thing you do.
**Time budget:** 45 min.
**Definition of done:** `hermes dashboard` starts, a **Datansh PM-OS** tab appears in
the sidebar, it renders a board, and `plugins/kanban/` is still byte-identical to
upstream.

> **Compatibility reset (binding):** Sections 3–5 below describe a superseded
> parallel PM-OS runtime (`pmo.db`, auth/RBAC, chat, and PM-only tools). Do not
> implement them. The foundation is limited to the copied `plugins/pmo` identity,
> its provenance/sync guard, and additive PM profile/skill material. Use Hermes
> boards, sessions, approvals, memory, skills, delegation, and gateway lifecycle
> directly. See `026-hermes-compatibility-reset-PLAN.md` for the active delivery
> sequence.

---

## 1. Fork the plugin

```bash
cp -r plugins/kanban plugins/pmo
```

Then rewrite the three files that carry the name.

### `plugins/pmo/dashboard/manifest.json`

```json
{
  "name": "pmo",
  "label": "Datansh PM-OS",
  "description": "Project-manager-orchestrated delivery board — founder's office chat, approvals, and agent tickets",
  "icon": "LayoutDashboard",
  "version": "0.1.0",
  "tab": { "path": "/pmo", "position": "before:kanban" },
  "entry": "dist/index.js",
  "css": "dist/style.css",
  "api": "plugin_api.py"
}
```

### `plugins/pmo/dashboard/plugin_api.py`

Sed the mount path in the docstring and any hardcoded `"/api/plugins/kanban"`. The
router itself is path-relative, so the mount point comes from the manifest `name` —
verify by hitting `/api/plugins/pmo/board` once the server is up.

### `plugins/pmo/dashboard/dist/index.js`

One constant near the top controls every fetch. Find the base-URL expression and set:

```js
const API = "/api/plugins/pmo";
```

Also rename the systemd unit copy: `plugins/pmo/systemd/datansh-pmo-dispatcher.service`.

**Guard:** after this step, `git status` must show *only* additions under
`plugins/pmo/`. If `plugins/kanban/` shows as modified, revert it.

```bash
git diff --stat -- plugins/kanban   # must print nothing
```

---

## 2. Separate the board data

The forked plugin currently reads the same `~/.hermes/kanban.db` and the same active
board as upstream. That is fine and *desirable* — we reuse `kanban_db` wholesale (see
`000-CONTEXT.md` D2). Do **not** fork `hermes_cli/kanban_db.py`.

What you do fork is the *board selection*: PM-OS always addresses a board by explicit
slug derived from the project, never by "the active board". See `003`.

---

## 3. Create the PM-OS store

New file `hermes_cli/pmo_db.py`, modelled on `hermes_cli/projects_db.py` (it is the
smaller, cleaner of the two references at 655 lines — read it before writing this).

Copy from it: `connect()` with WAL + DELETE fallback, `_INITIALIZED_PATHS` caching,
idempotent `SCHEMA_SQL`, `_normalize_path`, the slug regex.

> ## ⚠ Do not write `with connect() as conn:` — VERIFIED V-24
>
> `sqlite3`'s context manager manages the **transaction**, never `conn.close()`. In a
> long-lived gateway process this leaks the main DB fd plus WAL `-wal`/`-shm` sidecars
> until `RLIMIT_NOFILE`, at which point **unrelated** operations start failing with
> `[Errno 24] Too many open files`. That is a real production incident in this repo —
> `relatorio-issue-69678-sqlite-fd-leaks.md`, 21 call sites across three modules.
>
> The postmortem **declined** to build a shared helper, so every module reimplements it.
> Ship ours in the first commit:
>
> ```python
> @contextlib.contextmanager
> def _transaction(db_path=None):
>     conn = connect(db_path)
>     try:
>         with conn:          # commit / rollback
>             yield conn
>     finally:
>         conn.close()        # the part sqlite3 does NOT do
> ```
>
> Every `pmo_db` function goes through it. `hermes pmo doctor` reports open-fd count.
> Also add `preflight_db_writability()`-style checks — a read-only `state.db` from a
> `sudo` run surfaces as an opaque `OperationalError` deep in schema init (V-32).

```python
def pmo_db_path() -> Path:
    """Per-profile PM-OS DB (``$HERMES_HOME/pmo.db``)."""
    return get_hermes_home() / "pmo.db"
```

Schema — the full set, created in one shot so later plans have no migration step:

```sql
-- ── identity & authorization (plan 002) ────────────────────────────────
CREATE TABLE IF NOT EXISTS users (
    id            TEXT PRIMARY KEY,
    email         TEXT NOT NULL UNIQUE COLLATE NOCASE,
    display_name  TEXT NOT NULL,
    password_hash TEXT,               -- argon2id; NULL for SSO-only users
    status        TEXT NOT NULL DEFAULT 'active',   -- active | suspended
    created_at    INTEGER NOT NULL
);

-- Org rank: the founder's-office hierarchy. Higher rank can approve
-- anything a lower rank can. See plan 006.
CREATE TABLE IF NOT EXISTS org_roles (
    key   TEXT PRIMARY KEY,     -- ceo | cfo | cto | founder_office | staff
    label TEXT NOT NULL,
    rank  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS user_org_roles (
    user_id  TEXT NOT NULL,
    role_key TEXT NOT NULL,
    PRIMARY KEY (user_id, role_key)
);

-- Project capability role: read/write scope inside one project.
CREATE TABLE IF NOT EXISTS project_members (
    project_id TEXT NOT NULL,
    user_id    TEXT NOT NULL,
    role_key   TEXT NOT NULL,   -- project_admin | pm | contributor | viewer
    added_at   INTEGER NOT NULL,
    added_by   TEXT,
    PRIMARY KEY (project_id, user_id)
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,   -- sha256 of the opaque token; never store raw
    user_id    TEXT NOT NULL,
    issued_at  INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    revoked_at INTEGER
);

-- ── agent identities (plan 004/005) ────────────────────────────────────
-- The @handle namespace. Handles are unique *within a project*, so
-- @dev-1 on project A and @dev-1 on project B are different agents and
-- neither can mention the other.
CREATE TABLE IF NOT EXISTS agent_identities (
    project_id  TEXT NOT NULL,
    handle      TEXT NOT NULL,      -- 'pm', 'dev-1', 'qa', ...
    profile     TEXT NOT NULL,      -- hermes profile name that runs it
    kind        TEXT NOT NULL,      -- pm | worker
    role_label  TEXT,               -- 'Backend engineer'
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  INTEGER NOT NULL,
    PRIMARY KEY (project_id, handle)
);

-- ── comms (plan 005) ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS mentions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  TEXT NOT NULL,
    task_id     TEXT NOT NULL,
    comment_id  INTEGER NOT NULL,   -- kanban.db task_comments.id
    mentioner   TEXT NOT NULL,      -- handle or user id
    mentionee   TEXT NOT NULL,
    created_at  INTEGER NOT NULL,
    delivered_at INTEGER,
    UNIQUE (comment_id, mentionee)
);

CREATE TABLE IF NOT EXISTS escalations (
    id          TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL,
    task_id     TEXT,
    raised_by   TEXT NOT NULL,
    level       TEXT NOT NULL,      -- pm | founders_office
    reason      TEXT NOT NULL,
    status      TEXT NOT NULL,      -- open | answered | closed
    thread_id   TEXT,
    created_at  INTEGER NOT NULL,
    resolved_at INTEGER,
    resolution  TEXT
);

-- ── founder's office chat + approvals (plan 006) ───────────────────────
CREATE TABLE IF NOT EXISTS threads (
    id         TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    kind       TEXT NOT NULL,      -- founders_office | client | team
    title      TEXT NOT NULL,
    created_by TEXT,
    created_at INTEGER NOT NULL,
    archived   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS thread_members (
    thread_id TEXT NOT NULL,
    principal TEXT NOT NULL,       -- user id, or 'agent:<project>/<handle>'
    added_at  INTEGER NOT NULL,
    PRIMARY KEY (thread_id, principal)
);

CREATE TABLE IF NOT EXISTS messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id  TEXT NOT NULL,
    author     TEXT NOT NULL,
    body       TEXT NOT NULL,
    kind       TEXT NOT NULL DEFAULT 'text',  -- text | system | approval_ref
    ref_id     TEXT,                          -- approval id / task id when kind != text
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS approvals (
    id            TEXT PRIMARY KEY,
    project_id    TEXT NOT NULL,
    thread_id     TEXT,
    task_id       TEXT,
    kind          TEXT NOT NULL,   -- ticket_gate | spend | scope_change | release
    title         TEXT NOT NULL,
    detail        TEXT,
    requested_by  TEXT NOT NULL,
    required_rank INTEGER NOT NULL,
    status        TEXT NOT NULL,   -- pending | approved | rejected | withdrawn
    decided_by    TEXT,
    decided_at    INTEGER,
    decision_note TEXT,
    created_at    INTEGER NOT NULL
);

-- Every escalation of an approval's required rank, so the CFO→CEO bump is
-- auditable rather than a silent UPDATE.
CREATE TABLE IF NOT EXISTS approval_escalations (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    approval_id  TEXT NOT NULL,
    from_rank    INTEGER NOT NULL,
    to_rank      INTEGER NOT NULL,
    by_user      TEXT NOT NULL,
    reason       TEXT,
    created_at   INTEGER NOT NULL
);

-- ── audit (plan 002) ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS audit (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    actor      TEXT NOT NULL,
    action     TEXT NOT NULL,
    subject    TEXT,
    project_id TEXT,
    allowed    INTEGER NOT NULL,
    detail     TEXT,
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_members_user     ON project_members(user_id);
CREATE INDEX IF NOT EXISTS idx_mentions_pending ON mentions(mentionee, delivered_at);
CREATE INDEX IF NOT EXISTS idx_messages_thread  ON messages(thread_id, created_at);
CREATE INDEX IF NOT EXISTS idx_approvals_status ON approvals(project_id, status);
CREATE INDEX IF NOT EXISTS idx_audit_actor      ON audit(actor, created_at);
```

Seed `org_roles` on first init:

| key | label | rank |
|---|---|---|
| `ceo` | CEO | 100 |
| `cfo` | CFO | 70 |
| `cto` | CTO | 70 |
| `founder_office` | Founder's Office | 50 |
| `staff` | Staff | 10 |

CFO and CTO share rank 70 deliberately: peers, neither can approve the other's
escalation, both must go up to CEO. That is what makes "CFO can ask for CEO approval"
mean something.

---

## 4. Bootstrap CLI

New `hermes_cli/subcommands/pmo.py`, registered the way `subcommands/dashboard.py` is.

```
hermes pmo init                              # create pmo.db, seed org_roles
hermes pmo user add --email X --name Y --role ceo
hermes pmo user passwd --email X
hermes pmo project bootstrap --slug acme     # project + board + PM profile + thread
hermes pmo doctor                            # schema, orphan refs, unroutable handles
```

`hermes pmo init` must be idempotent. `hermes pmo doctor` is your five-second sanity
check all day — write it early, it pays for itself.

---

## 5. Wire the plugin API to the new store

At the top of `plugins/pmo/dashboard/plugin_api.py`:

```python
from hermes_cli import kanban_db          # unchanged upstream store
from hermes_cli import projects_db        # unchanged upstream store
from hermes_cli import pmo_db             # ours
from hermes_cli.pmo_authz import require  # plan 002
```

Do not add business logic to `plugin_api.py`. It stays the thin wrapper layer it
already documents itself as. Put logic in `hermes_cli/pmo_*.py` modules so it is
importable by the CLI, the agent tools, and the gateway watcher without going through
HTTP.

Module layout to create now (empty stubs are fine):

```
hermes_cli/pmo_db.py         # store + schema
hermes_cli/pmo_authz.py      # roles, capability matrix, require()  [002]
hermes_cli/pmo_scope.py      # project → board/folders resolution   [003]
hermes_cli/pmo_config.py     # project.yaml loading + merge         [003]
hermes_cli/pmo_orchestrator.py  # PM lifecycle, ticket finalization [004]
hermes_cli/pmo_mentions.py   # @ parse, resolve, route              [005]
hermes_cli/pmo_chat.py       # threads, messages, approvals         [006]
tools/pmo_tools.py           # agent-facing tool defs               [004/005]
```

---

## 6. Verify

```bash
python -m pytest tests/hermes_cli/test_pmo_db.py -q
hermes pmo init && hermes pmo doctor
hermes dashboard
```

Then in the browser: the sidebar shows **Datansh PM-OS** above **Kanban**, `/pmo`
renders, and the network tab shows calls to `/api/plugins/pmo/*` and none to
`/api/plugins/kanban/*`.

---

## P0 / P1

**P0:** plugin fork renders; `pmo_db.py` with the full schema; `hermes pmo init`;
`hermes pmo doctor`; stub modules created.

**P1:** `hermes pmo user`/`project` subcommands beyond what 002/003 need; systemd unit;
uninstall path.

## Traps

- **Do not** let `plugins/pmo/dashboard/dist/index.js` keep pointing at
  `/api/plugins/kanban`. You will spend twenty minutes debugging a board that "works"
  but is writing to the wrong plugin's routes.
- **Do not** add columns to `kanban_db.SCHEMA_SQL`. `_REBUILD_SPECS` in that file will
  `DROP TABLE` a drifted table on next init. Your data would be gone and the failure
  would look like a UI bug.
- The plugin bundle is a plain IIFE served as-is. There is no HMR — hard-refresh
  (`Ctrl+Shift+R`) after every edit or you will debug a cached bundle.
