# 002 — Authentication & Authorization

**Covers:** requirement #1 (initial auth + authz, access + role levels, role-specific
read/write), #11 (no user-level config).
**Depends on:** 001.
**Time budget:** 2 h.
**Definition of done:** an unauthenticated request to any `/api/plugins/pmo/*` route
gets 401; a `viewer` gets 403 on every write; a `contributor` can move a ticket but
cannot change project config; a test proves no route escaped the guard.

> **Compatibility implementation (binding):** PM-OS reuses Hermes dashboard
> `Session` and `TokenPrincipal` authentication unchanged. Project authorization is
> an additive PMO-owned policy in `plugins/pmo/access_policy.py`, projected from the
> version-controlled `.datansh/access.yaml`; decisions are appended, redacted, to
> `.datansh/access-audit.jsonl`. The additive Datansh password provider
> (`plugins/dashboard_auth/datansh`) recognizes a human identity from project-owned
> scrypt hashes in `.datansh/credentials.yaml`; it does not add a user table,
> replace Hermes sessions, or create global PMO permissions. A human may be a member
> of several projects: each project's membership, role, rank, config, and credential
> hash remain independent, and a valid identity never becomes an all-project grant.
> The inherited PMO router has one introspectable deny-by-default dependency, while
> upstream Kanban behavior and core authentication remain intact. Human org rank is
> separate from project membership; agents receive structural PM/worker project
> roles and cannot hold org rank.

> This is the highest-risk plan in the build. `000-CONTEXT.md` §3c documents the gap:
> Hermes has pluggable **authentication** and effectively zero **authorization**. If
> you run out of day, cut features — never cut this. A permission system that is 80%
> wired reads as working and silently isn't.

---

## 1. Authentication — reuse, don't rebuild

`hermes_cli/dashboard_auth/` already gives you the whole login lifecycle, cookies,
CSRF/PKCE state, WS tickets, and a `Session` on `request.state.session`. You are
writing **one provider**, not an auth system.

### `plugins/dashboard_auth/datansh/`

The implemented provider lives in `plugins/dashboard_auth/datansh/` and subclasses
Hermes' bundled password provider so the host owns cookies, sessions, and token
validation. Set:

```python
class DatanshProvider(BasicAuthProvider):
    name = "datansh"
    display_name = "Datansh Project Accounts"
    supports_password = True     # renders the credential form on the login page
    supports_session  = True
    # Token auth is intentionally not part of the Datansh password provider;
    # workers use in-process agent principals.
    supports_token    = True     # for agent workers, see §4
```

The sketch above is retained for protocol context; the shipped class intentionally
inherits Hermes' password/session behavior and does not expose a PMO token or
agent-login surface. Do not implement the historical `pmo.db`, Argon2, or custom
session-table bullets below.

Implementation status (2026-08-06): the original database/session design below is
superseded. Keep the plan's compatibility and authorization invariants, but use the
already-shipped additive provider: project `.datansh/credentials.yaml` stores only
scrypt hashes; `PUT /access/password` enrolls a member; Hermes' base provider mints
the session; and `verify_project_password()` scans active projects so one human can
belong to multiple projects. Do not introduce a user-level PMO credential table or
replace the Hermes session lifecycle.

Implement (remaining hardening only):

- `complete_password_login(username, password)` — look up `users` by email in
  `pmo.db`, verify with **argon2id** (`argon2-cffi`). On unknown user, still run a
  dummy verify against a fixed hash so the endpoint is not a timing oracle — the base
  class docstring explicitly asks for this. Raise `InvalidCredentialsError` with no
  distinction between "unknown user" and "wrong password".
- `verify_session(access_token)` — `sha256(token)` → `sessions.token_hash`, check
  `expires_at > now` and `revoked_at IS NULL`, return a `Session`.
- `refresh_session(refresh_token)` — rotate: insert a new row, revoke the old.
- `revoke_session(refresh_token)` — set `revoked_at`. Must not raise.
- `start_login` / `complete_login` — `raise NotImplementedError`. The base class
  explicitly permits this for a pure-password provider.

Store **only** `sha256` of session tokens. Mint tokens with `secrets.token_urlsafe(32)`.

Register it in `plugin.yaml` alongside the other providers, and add it to the
dashboard's provider list.

Run `assert_protocol_compliance(DatanshProvider)` in the unit test — the base module
provides that helper for exactly this.

### ⚠ Registration failure is silent — assert it at startup (VERIFIED V-27)

Two fail-open behaviours compound here:

- `ctx.register_dashboard_auth_provider` — *"Misbehaving providers (wrong type, duplicate
  name) are logged at WARNING and silently ignored — never raised — so a broken plugin
  cannot crash the host."*
- `PluginManager.discover_and_load()` *"fails open on partial-load exceptions."*

Both are correct for the host and a hazard for us: **if our provider fails to register,
the dashboard serves with our authorization absent and nothing says so.** The
reverse-engineering atlas names this class directly (Theme 6): *"several of the codebase's
own safety mechanisms could silently stop working and nothing would surface that to a user
by default."*

So add a PM-OS startup self-check that **refuses to serve** unless:

1. the `datansh` auth provider is present in the provider registry;
2. the `pmo` platform is present in `platform_registry` (ADR-006);
3. the route-coverage check (§2e) passes at runtime, not only in CI.

Surface all three in `hermes pmo doctor`. **The host fails open on our behalf; we fail
closed.**

### What `Session` gives you

`Session(user_id, email, display_name, org_id, provider, expires_at, ...)`.
For Datansh accounts, `user_id` is the canonical `human:<email>` identity. That is
the only field the authz layer needs; membership is resolved from the selected
project's `.datansh/access.yaml`.

---

## 2. Authorization — `hermes_cli/pmo_authz.py`

### 2a. Two role axes

Requirement #8 needs a **hierarchy** (CFO escalating to CEO). Requirement #1 needs
**read/write scoping**. These are different questions and one flat role list cannot
answer both. So:

| Axis | Table | Answers |
|---|---|---|
| **Org rank** | `user_org_roles` → `org_roles.rank` | "Is this person senior enough to approve this?" |
| **Project role** | `project_members.role_key` | "Can this person write in this project?" |

A CEO with no membership in project `acme` cannot write tickets there — but can
approve an approval raised there, because approvals are an org-rank question. That
separation is deliberate and it is what makes the founder's-office hierarchy work
without giving executives blanket write access to every board.

### 2b. Project roles

| Role | Read | Comment | Move/edit tickets | Create tickets | Members | Project config |
|---|---|---|---|---|---|---|
| `viewer` | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ |
| `contributor` | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ |
| `pm` | ✅ | ✅ | ✅ | ✅ | ❌ | ✅ |
| `project_admin` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |

Rank 100 is the CEO/global-admin tier. It is still derived from project-owned
rank policy, but a verified rank-100 identity may administer every registered
project and retain every Hermes dashboard tab. Lower ranks remain project-local.

### 2c. Capability matrix

Actions are dotted strings. Deny by default: an action not in the map is denied.

```python
CAPABILITIES: dict[str, frozenset[str]] = {
    # action                    roles that hold it
    "project.read":        frozenset({"viewer", "contributor", "pm", "project_admin"}),
    "task.read":           frozenset({"viewer", "contributor", "pm", "project_admin"}),
    "comment.write":       frozenset({"viewer", "contributor", "pm", "project_admin"}),
    "task.write":          frozenset({"contributor", "pm", "project_admin"}),
    "task.transition":     frozenset({"contributor", "pm", "project_admin"}),
    "task.delete":         frozenset({"pm", "project_admin"}),
    "board.dispatch":      frozenset({"pm", "project_admin"}),
    "agent.manage":        frozenset({"pm", "project_admin"}),
    "config.read":         frozenset({"viewer", "contributor", "pm", "project_admin"}),
    "config.write":        frozenset({"pm", "project_admin"}),
    "member.read":         frozenset({"pm", "project_admin"}),
    "member.write":        frozenset({"project_admin"}),
    "thread.read":         frozenset({"viewer", "contributor", "pm", "project_admin"}),
    "thread.post":         frozenset({"contributor", "pm", "project_admin"}),
    "approval.request":    frozenset({"contributor", "pm", "project_admin"}),
}

# Org-rank-gated actions. Value is the minimum rank.
RANK_ACTIONS: dict[str, int] = {
    "approval.decide":   50,   # founder_office and up; per-approval rank checked separately
    "approval.escalate": 50,
    "org.manage":       100,   # CEO only: assign org roles
}
```

### 2d. The one choke point

```python
def can(user_id: str, action: str, project_id: str | None = None) -> bool: ...

def require(action: str, *, project: str | None = None):
    """FastAPI dependency factory. Returns the Principal or raises 401/403."""
```

Behaviour:

1. No `request.state.session` and no `request.state.token_principal` → **401**.
2. Action in `RANK_ACTIONS` → compare the user's highest org rank. Insufficient → **403**.
3. Action in `CAPABILITIES` → resolve `project_id` (path param, query, or body),
   look up `project_members.role_key`, check membership. Not a member → **403**
   (*not* 404 — this is an internal tool, leaking project existence to an
   authenticated employee is not a threat worth an inconsistent API).
4. Unknown action → **403** and a loud `log.error`. Fail closed.
5. Every decision writes an `audit` row with `allowed` 0/1.

Usage:

```python
@router.patch("/tasks/{task_id}")
def patch_task(task_id: str,
               principal = Depends(require("task.write", project="path:project_id"))):
    ...
```

### 2e. Route coverage test — the thing that makes this real

```python
# tests/plugins/test_pmo_route_authz.py
def test_every_pmo_route_is_guarded():
    """Fails if any route on the PM-OS router lacks a require(...) dependency.

    This is the load-bearing test of the whole authorization story. Without it,
    a route added at 4pm on a busy day is a silent hole.
    """
    from plugins.pmo.dashboard.plugin_api import router
    unguarded = [
        r.path for r in router.routes
        if r.path not in PUBLIC_PATHS
        and not any(getattr(d.call, "_pmo_guard", False) for d in r.dependant.dependencies)
    ]
    assert unguarded == [], f"unguarded PM-OS routes: {unguarded}"
```

Mark the dependency: `require(...)` sets `_pmo_guard = True` on the returned callable.
`PUBLIC_PATHS` is a short explicit allowlist (`/health` and nothing else on day 1).

Write this test **before** you port the 44 routes. It will tell you when you are done.

### 2f. The three execution lanes (VERIFIED V-33)

Route guards cover HTTP. **Agent tool calls do not go through routes**, and the atlas is
explicit that *"the registry dispatches all tools" is false*:

| Lane | Path | Gated by |
|---|---|---|
| A — inline runtime | `memory`, `todo`, `skill_manage`, `session_search`, `clarify`, `delegate_task`, context-engine ops — recognised **before** the registry bridge | profile toolset, **verify per-lane** |
| B — registry | `ToolRegistry.dispatch()` | profile toolset + `check_fn` |
| C — bridge | Tool Search, MCP, plugin middleware; scope gates run **before** hooks/guardrails | scope gate |

Two consequences:

1. **ADR-005's turn-start assertion must enumerate all three lanes.** Checking only the
   resolved registry toolset would pass while `delegate_task` (lane A) stays reachable —
   silently breaking ADR-018.
2. **The `audit` table must record the attempt, not only the dispatch.** From `14` §5:
   *"an out-of-scope call can be rejected before a normal dispatch hook runs, so 'no
   dispatch log' does not necessarily mean the model never emitted the call."* The atlas
   lists this as an open experimental question (`15` §5 #3). Hook at the executor, not at
   `registry.dispatch`.

The supported veto seam for our own policy is the **plugin `pre_tool_call` hook**
(`resolve_pre_tool_block`, invoked from `model_tools.py`) — use it for 003's path scoping
rather than wrapping tools. But note Theme 1: there are already three unaware-of-each-other
veto layers. **Add one owner for PM-OS, not a fourth ad-hoc gate**, and route every PM-OS
policy decision through `pmo_authz` so "why was this blocked" has a single answer.

---

## 3. Porting the 44 forked routes

`plugins/pmo/dashboard/plugin_api.py` inherits every route from the kanban plugin. Go
through them once and attach a guard. Suggested mapping:

| Route pattern | Action |
|---|---|
| `GET /board`, `/tasks/{id}`, `/stats`, `/assignees`, `/boards` | `task.read` |
| `POST /tasks`, `PATCH /tasks/{id}`, `POST /tasks/bulk`, `/links` | `task.write` |
| `DELETE /tasks/{id}`, `DELETE /links` | `task.delete` |
| `POST /tasks/{id}/comments` | `comment.write` |
| `POST /dispatch`, `/tasks/{id}/reclaim`, `/runs/{id}/terminate` | `board.dispatch` |
| `POST /tasks/{id}/decompose`, `/specify`, `/reassign` | `board.dispatch` |
| `GET /config`, `GET /orchestration` | `config.read` |
| `PUT /orchestration`, `PATCH /profiles/{name}` | `config.write` |
| `GET /diagnostics`, `/workers/active`, `/runs/*` | `task.read` |
| attachments up/down/delete | `task.write` / `task.read` / `task.delete` |

Anything you cannot classify in ten seconds: guard it with `task.write` and open a
TODO. Over-restricting is recoverable; under-restricting is not.

---

## 4. Agent (non-human) authentication

Worker agents call the API in-process, not over HTTP — they go through
`tools/pmo_tools.py` → `hermes_cli/pmo_*` directly. So they need an authorization
principal but not a login.

Model an agent principal as `agent:<project_id>/<handle>`, resolved from
`agent_identities`. `can()` accepts it and maps:

- `kind='pm'` → project role `pm`
- `kind='worker'` → project role `contributor`

An agent **never** holds an org rank, so `approval.decide` is structurally impossible
for an agent. That is not a policy setting; there is no row that could grant it. This
is the guarantee behind requirement #6's "escalates to human input".

For the HTTP path (the dispatcher, the gateway watcher), use the existing
`supports_token` seam: `verify_token()` returns a
`TokenPrincipal(principal="agent:acme/dev-1", provider="datansh", scopes=(...))`.
Use `hmac.compare_digest` for the secret comparison — the base class asks for it.

---

## 5. Requirement #11 — no user-level config

Explicit non-goal, and it needs to be enforced rather than merely intended:

- No `user_settings` / `user_prefs` table. Not in the schema in `001`; do not add one.
- No `GET/PUT /api/plugins/pmo/me/config` route.
- Config resolution order is exactly: **repo defaults → `~/.hermes/config.yaml` →
  project `.datansh/project.yaml`.** No fourth layer. See `003`.

Assertion test:

```python
def test_no_user_level_config():
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert not any("user" in t and "config" in t or t.endswith("_prefs")
                   for t in tables)
    assert not any("/me/config" in r.path for r in router.routes)
```

Theme choice is UI state in `localStorage`, not config. That is fine and is not a
violation.

---

## 6. UI

Follow the ERP's own pattern — `design/design.md` §6c documents it precisely, because
`erp.datansh.com/project/settings` solves this exact screen:

- A `.widget` "Members" with a `+ Add member` secondary button in the head.
- Table `MEMBER | EMAIL | PROJECT ROLE | ORG ROLE | STATUS | ⋯`.
- **Role is an inline `<select>` per row**, not a modal. Change is immediate, with an
  optimistic update and a toast on failure.
- Status is an Active/Suspended pill.
- Only `project_admin` sees the page; only `project_admin` gets non-disabled selects.
- The **org role** column is editable only by a CEO (`org.manage`, rank 100).

Login page: the existing `hermes_cli/dashboard_auth/login_page.py` renders the
credential form automatically for a `supports_password` provider. Restyle it with the
tokens in `design/design.md` §2b — centred card, `--sh-lg`, brand wordmark on top.

---

## 7. Deployment boundary — core pages stay admin-only

> Added by [021](021-core-architecture-gaps-and-modifications-PLAN.md) GAP 4 (ADR-008).

Once this plan lands, a CFO has a dashboard login. The **core** Hermes pages — Env,
Files, Config, MCP, Skills, Sessions, Plugins, Logs — expose the whole machine and have
no authorization layer of their own (that is the gap `000-CONTEXT.md` §3c documents, and
we are deliberately not fixing it upstream).

> **Core Hermes dashboard pages are single-tenant admin surfaces. PM-OS is the
> multi-user surface. Do not mix them.**

If the dashboard is reachable by more than one person, gate it at deployment: a reverse
proxy that exposes only `/pmo` and `/api/plugins/pmo/*` to non-admin sessions, or tab
gating that hides core pages for users without an admin flag. Prefer the proxy — it fails
closed.

Guarding our 44 routes while `/api/env` stays open to any authenticated session is not a
partial win. It is the same hole with more steps.

**Ship this on day 1 if more than one person can reach the port.**

---

## 8. P0 / P1

**P0:** `DatanshProvider` password login; `sessions` with hashed tokens; `pmo_authz.can/require`;
the capability matrix; guards on all forked routes; the route-coverage test; agent
principals; the `#11` assertion test.

**P1:** SSO/OAuth provider; password reset flow; MFA; session listing + remote revoke;
rate limiting on `/auth/password-login`; the audit-log viewer UI (the `audit` table is
written from day 1 — only the viewer is deferred).

## Traps

- Do not gate on `request.state.session` inside route bodies. It works, and it means
  the coverage test in §2e cannot see the guard, which means the next route silently
  ships unguarded.
- Argon2 parameters: use the library defaults. Do not hand-tune; a wrong `time_cost`
  is either a DoS or a weak hash.
- The WebSocket path (`/events`) has its own auth gate
  (`hermes_cli.web_server._ws_auth_ok`). The kanban plugin delegates to it — keep that
  delegation and add a project-scope check on top, or a viewer on project A will
  receive project B's live events. This is the easiest scope leak to ship by accident.
- `403` for non-members, consistently. Mixing 403 and 404 across routes turns into an
  existence oracle and, worse, into flaky tests.
