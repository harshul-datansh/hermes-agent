# Reference — Data Model

Every table PM-OS reads or writes, in one place. Schema fragments live in the plans that
introduce them (001, 010, 016, 020); this is the consolidated map.

**Three databases** (ADR-002). Cross-DB references are by id, resolved in the service
layer — never `JOIN`ed.

---

## 1. Ownership

```
~/.hermes/kanban.db      upstream  ── read-write via kanban_db, schema NEVER modified
~/.hermes/projects.db    upstream  ── read-write via projects_db, schema NEVER modified
~/.hermes/pmo.db         ours      ── everything PM-OS-specific
```

Why the schema rule: `kanban_db._REBUILD_SPECS` **drops** a table whose columns have
drifted from `SCHEMA_SQL`. Anything per-ticket that we need goes into `task_events`
payloads or a `pmo.db` table keyed by `task_id` (009 §3).

---

## 2. Entity map

```
                    ┌──────────────┐
                    │   projects   │ projects.db
                    │  id, slug,   │
                    │  board_slug, │
                    │ primary_path │
                    └──┬────┬───┬──┘
         ┌─────────────┘    │   └──────────────┐
         │                  │                  │
┌────────▼────────┐  ┌──────▼───────┐  ┌───────▼────────┐
│ project_folders │  │    tasks     │  │ project_members│  pmo.db
│  (filesystem    │  │  kanban.db   │  │  role_key      │
│   scope, #9)    │  │ project_id ──┘  │  user_id ──────┼──┐
└─────────────────┘  └──┬───┬───┬───┬──┘  └─────────────┘  │
                        │   │   │   │                      │
        ┌───────────────┘   │   │   └──────────┐           │
        │                   │   │              │           │
┌───────▼──────┐ ┌──────────▼┐ ┌▼───────────┐ ┌▼────────┐  │
│task_comments │ │task_events│ │ task_runs  │ │task_links│ │
│  kanban.db   │ │ kanban.db │ │ kanban.db  │ │kanban.db │ │
└───────┬──────┘ └───────────┘ └─────┬──────┘ └──────────┘ │
        │                            │                     │
        │ comment_id           metadata: usage, cost,      │
        │                      session_id (012, 013)       │
┌───────▼──────┐                                    ┌──────▼──────┐
│   mentions   │ pmo.db  ── the @ routing (#7)      │    users    │ pmo.db
└──────────────┘                                    │  email,     │
                                                    │ password_h  │
┌──────────────┐   ┌──────────────┐                 └──┬───────┬──┘
│   threads    │──►│   messages   │  pmo.db            │       │
│ kind: founders│   │ kind: text | │           ┌───────▼──┐ ┌──▼────────┐
│  office|client│   │ system |     │           │user_org_ │ │ sessions  │
│  |team        │   │ approval_ref │           │  roles   │ │token_hash │
└───┬──────┬────┘   └──────┬───────┘           └────┬─────┘ └───────────┘
    │      │               │ ref_id                 │
    │  ┌───▼──────────┐    │                   ┌────▼─────┐
    │  │thread_members│    │                   │org_roles │  rank: 100/70/50/10
    │  └──────────────┘    │                   └──────────┘
    │                      │
┌───▼──────────┐   ┌───────▼──────┐   ┌──────────────────────┐
│ escalations  │   │  approvals   │──►│ approval_escalations │  pmo.db
│  (#6)        │   │ required_rank│   │  from_rank→to_rank   │  the #8 trail
└──────────────┘   └──────────────┘   └──────────────────────┘

┌──────────────────┐ ┌───────────┐ ┌───────────┐ ┌──────────────┐ ┌────────┐
│ agent_identities │ │ decisions │ │ knowledge │ │notifications │ │ audit  │  pmo.db
│ (project,handle) │ │ append-   │ │ ≤280 char │ │ egress source│ │ every  │
│ →profile, kind   │ │  only     │ │  facts    │ │  of truth    │ │ authz  │
└──────────────────┘ └───────────┘ └───────────┘ └──────────────┘ └────────┘

┌─────────────────┐   client-only, single project, never a `user`  (010)
│ client_contacts │
└─────────────────┘
```

---

## 3. Table index

### Upstream — read/write, schema frozen

| Table | DB | We use it for | Plan |
|---|---|---|---|
| `projects` | projects.db | scope root; `board_slug`, `primary_path` | 003 |
| `project_folders` | projects.db | filesystem scope (#9) | 003 |
| `tasks` | kanban.db | tickets; `project_id`, `status`, `assignee`, `workspace_*` | 009 |
| `task_comments` | kanban.db | **the only agent channel** (#7) | 005 |
| `task_events` | kanban.db | timeline, watchers, estimates/dates as payloads | 009, 013 |
| `task_runs` | kanban.db | runs; `metadata` carries usage/cost/session_id | 012, 013 |
| `task_links` | kanban.db | epic→child; conflict serialisation | 004, 011 |
| `task_attachments` | kanban.db | files | 015 |

### Ours — `pmo.db`

| Table | Purpose | Plan |
|---|---|---|
| `users` | human identity, argon2id hash | 002 |
| `org_roles` | ceo 100 / cfo 70 / cto 70 / founder_office 50 / staff 10 | 002 |
| `user_org_roles` | rank assignment | 002 |
| `project_members` | project role: viewer / contributor / pm / project_admin | 002 |
| `sessions` | sha256 token hashes only | 002 |
| `agent_identities` | `(project_id, handle)` → profile, kind | 004 |
| `mentions` | `@` fan-out; `UNIQUE(comment_id, mentionee)` | 005 |
| `escalations` | blocker → human (#6) | 005 |
| `threads` | `founders_office` / `client` / `team` | 006, 010 |
| `thread_members` | users + `agent:<project>/<handle>` | 006 |
| `messages` | the conversation record; `kind`, `ref_id` | 006 |
| `approvals` | `required_rank`; immutable once decided | 006 |
| `approval_escalations` | CFO→CEO trail (#8) | 006 |
| `client_contacts` | external, single-project, no rank | 010 |
| `decisions` | append-only decision log | 020 |
| `knowledge` | ≤280-char project facts, deduped | 020 |
| `notifications` | egress source of truth | 016 |
| `availability` | away windows + delegation | 016 |
| `audit` | every authz decision + role/config/approval changes | 002, 013 |
| `idem_keys` | idempotent create mapping | 009 §5 |
| `pmo_cursors` | watcher cursors — **in the DB, never a JSON file** | 014 §4 |

---

## 4. Invariants

Worth asserting in `hermes pmo doctor` (013 §6):

| # | Invariant | Violation means |
|---|---|---|
| 1 | Every `tasks.project_id` resolves to a live `projects.id` | orphan tickets |
| 2 | Every `projects.board_slug` names an existing board | unreachable board |
| 3 | Every `agent_identities.profile` exists as a Hermes profile | unroutable handle |
| 4 | Every project has exactly one `kind='pm'` identity with `handle='pm'` | #3 broken |
| 5 | Every project has exactly one `founders_office` thread | #2 has no channel |
| 6 | Every `mentions.mentionee` resolves within `mentions.project_id` | cross-project leak |
| 7 | No `approvals` row transitions out of a terminal status | ADR-015 broken |
| 8 | Every `approval_escalations.to_rank > from_rank` | de-escalation happened |
| 9 | No `agent:*` principal appears in `user_org_roles` | an agent can approve |
| 10 | Every `client_contacts` row has exactly one `project_id` | client sees two projects |
| 11 | `sessions.token_hash` is never a raw token (length + charset) | tokens stored in clear |
| 12 | Recorded `cost_usd` sum within 5% of `hermes insights` | attribution leaking |

Invariants 6, 9 and 10 are the security-relevant ones. 7 and 8 protect the audit trail
that requirement #8 exists to produce.

---

## 5. Conventions

| Rule | Why |
|---|---|
| Id prefixes: `u_` `p_` `th_` `ap_` `es_` `d_` `c_`, tickets `t<hex>` | An id in a log line identifies its own type |
| Timestamps: unix seconds, `INTEGER`, UTC | Matches upstream; no timezone ambiguity at rest |
| Money: `Decimal` in Python, `TEXT` in SQLite | Floats drift from the invoice (012 §2) |
| Booleans: `INTEGER` 0/1 | SQLite has no bool |
| Soft delete: `archived` / `retired_at` / `status` | Hard delete takes the audit trail with it |
| No `UPDATE` on decided rows | ADR-015 |
| All SQL in `pmo_db.py` / store modules only | Keeps the Postgres port bounded (024 §4) |
| Every table with a `project_id` is queried with it in the `WHERE` | #9, always |

---

## 6. Migrations

`pmo_db.SCHEMA_SQL` is idempotent (`CREATE TABLE IF NOT EXISTS`) plus an additive
`_migrate_add_optional_columns()` pass — same shape as `kanban_db` and `projects_db`, so
there is one pattern in the codebase, not two.

Rules:

- **Additive only.** New tables, new nullable columns. Never change a column's type — that
  is the drift `kanban_db._REBUILD_SPECS` exists to repair, and we do not want our own.
- `schema_version` in a `pmo_meta` table, checked by `doctor`.
- A destructive change needs an explicit migration script, a backup first (014 §6), and an
  ADR.
