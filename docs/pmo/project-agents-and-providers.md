# Project-specific agents, models, and provider accounts

One Hermes install, several client projects, each with its own agents, its own
model routing, and — where you want it — its own OpenAI-Codex account.

Nothing here is a second agent runtime. A project's agents are ordinary Hermes
**profiles**, which the existing Kanban dispatcher already spawns with
`hermes -p <assignee>`. A profile is a complete filesystem namespace (config,
credentials, memory, sessions, skills), and that is what makes the other two
features possible at all.

---

## 1. Agents are named `<role>-<slug>`

```bash
hermes pmo agents provision --project hedgi-app --roles dev,qa,ops
```

| handle | profile | what it is |
|---|---|---|
| `@pm` | `pm-hedgi-app` | orchestrator (owned by `bootstrap`) |
| `@dev` | `dev-hedgi-app` | engineer |
| `@qa` | `qa-hedgi-app` | reviewer |
| `@ops` | `ops-hedgi-app` | operations |

**Profiles carry the project name; handles do not.** Profiles share one global
namespace, so `dev-hedgi-app` and `dev-acme` must differ. Handles are already
scoped to a project, and they are typed on every comment, so they stay short.

Several agents of one role get numbered handles:

```bash
hermes pmo agents provision --project hedgi-app --roles dev --count dev=3
#  @dev-1 -> dev-hedgi-app-1 ...
```

Known roles: `pm`, `dev`, `qa`, `ops`, `research`, `design`, `data`.

Provisioning is **idempotent** — an existing profile is reused and a roster
entry with the same handle is replaced, not duplicated. Bootstrap can do it in
one step:

```bash
hermes pmo bootstrap --slug hedgi-app --name "Hedgi App" \
  --workspace ~/code/hedgi_app --admin you@example.com --agents dev,qa
```

Inspect and prune:

```bash
hermes pmo agents list   --project hedgi-app
hermes pmo agents remove --project hedgi-app --handle dev-3
```

`remove` drops the roster entry and **keeps the profile on disk** — it holds
session history, memory and credentials. Deleting it is a separate, explicit
act (`hermes profile delete`).

---

## 2. Model routing: global, per role, per agent

Three layers, most specific wins:

```yaml
# <project>/.datansh/project.yaml
models:                       # per role, project-wide
  pm: anthropic/claude-opus-4.6
  worker: anthropic/claude-sonnet-4.6
  reviewer: anthropic/claude-opus-4.6
  escalation_override: anthropic/claude-opus-4.6

agents:
  - handle: dev-1
    profile: dev-hedgi-app-1
    role: Engineer
    model: openai-codex-hedgi   # per agent — beats models.worker
  - handle: dev-2
    profile: dev-hedgi-app-2
    role: Engineer              # no pin: follows models.worker
```

Resolution order in `cost.model_route_for_assignee`:

1. `models.escalation_override` — **only** once a ticket has re-blocked twice.
   This is a safety valve and deliberately beats a per-agent pin.
2. `agents[].model` — the specific agent.
3. `models.reviewer` / `models.worker` — the role default.
4. Otherwise the profile's own configuration.

Set a pin at provision time:

```bash
hermes pmo agents provision --project hedgi-app --roles dev,qa \
  --model dev=openai-codex-hedgi \
  --model qa=anthropic/claude-opus-4.6
```

A project route may never select the virtual `moa` provider — an unattended
fleet must not fan out to advisor models implicitly, and MoA's in-flight
advisor calls are not cancellable on interrupt. Hermes' MoA tool is untouched
for interactive use.

---

## 3. A provider account per project

```bash
hermes pmo auth status --project hedgi-app
hermes pmo auth login  --project hedgi-app --provider openai-codex
```

Hermes writes `auth.json` to `get_hermes_home()`, and a profile *is* a
HERMES_HOME. Running the normal login with HERMES_HOME pointed at
`pm-hedgi-app` puts those credentials in that project's profile and nowhere
else:

```
.../profiles/pm-hedgi-app/auth.json     <- project's own account
.../profiles/pm-acme/auth.json          <- a different account
.../auth.json                           <- global, read-only fallback
```

Reads fall through to the global store, so **a project without its own
credentials keeps working on the shared account**. That is the useful part:
a project opts *in* to a dedicated account.

`auth status` reads the profile's `auth.json` directly rather than through the
usual accessor, precisely so it can distinguish "has its own account" from "is
borrowing the global one".

Give every agent in a project its own login:

```bash
hermes pmo auth login --project hedgi-app --provider openai-codex --all-agents
```

### On ports

Assigning a localhost port per project is **not needed** for Codex. Its login
is a *device-code* flow — you open a URL and type a code — so there is no
callback listener to allocate. Port allocation only applies to redirect-based
PKCE flows (Spotify is the only one in this tree).

Isolation comes from the profile's HERMES_HOME instead, which is the stronger
property: it separates the **stored credential**, not just the moment of
login. Two projects can hold two different Codex accounts indefinitely, and
neither can read the other's token.

---

## How it fits together

```
project.yaml (version-controlled, per project)
  ├─ orchestrator.profile ──► pm-hedgi-app ──► profiles/pm-hedgi-app/
  ├─ agents[].profile ──────► dev-hedgi-app-1 ─► profiles/dev-hedgi-app-1/
  │    └─ .model ───────────► openai-codex-hedgi        │
  └─ models.{pm,worker,…}                               ├─ auth.json   (own account)
                                                        ├─ memory/     (own memory)
tasks.assignee = profile name                           └─ config.yaml (own settings)
  └─ dispatcher runs `hermes -p dev-hedgi-app-1`
```

Everything above is per project and version-controlled with the project.
Nothing is stored per user, and nothing is global except the fallback account.
