# Datansh PM-OS: getting started

Datansh PM-OS is an additive project-management layer for Hermes. Hermes still
owns agents, delegation, memory, self-learning, tools, sessions, approvals, and
Kanban execution. PM-OS adds project scope, a Founder's Office conversation,
structured tickets, and human decision routing around those capabilities.

## See the product without configuring a provider

```console
hermes pmo demo
```

The command creates a real, small Git repository and populated native Kanban
board below the dedicated `~/.hermes-demo` root. It makes no model calls and
does not read or write your normal Hermes home. The printed “Try” instruction
points to a CFO approval that has been escalated to the CEO.

Start the dashboard with the authenticated bind so the login screen protects
the Hermes shell, copied Kanban, PM-OS, and every other dashboard page:

```console
hermes dashboard --host 0.0.0.0 --port 8787 --no-open
```

The demo enables Datansh Project Accounts. The command prints a credential for
each seeded role (CEO, CFO, project manager, contributors, QA, client, and admin).
Passwords are stored only as scrypt hashes in the demo project's
`.datansh/credentials.yaml`; membership and roles remain in that project's
`.datansh/access.yaml`. Binding to `127.0.0.1` is intentionally trusted by Hermes
and therefore does not show the login screen; use the authenticated bind above for
local QA.

Remove only that verified demo state with:

```console
hermes pmo demo --reset
```

Pass `--root <path>` to both commands when you want a different isolated demo
location. Reset refuses any directory that does not contain its exact PM-OS
demo marker.

## Set up a real project

From an existing Git repository:

```console
hermes pmo setup --interactive
```

Every question has a displayed default and accepts a blank response. Setup
reuses existing Hermes provider routing and never asks you to re-enter a key.
It creates `.datansh/project.yaml` and `.datansh/access.yaml` without
overwriting existing files, then ends with project-specific doctor output.
Commit `.datansh/`: it is reviewable project configuration. The credentials file
contains hashes only, never plaintext passwords. A human email can be enrolled in
multiple projects; each project independently grants its membership, role, rank,
and folder scope, so signing in never grants access to an unrelated project.

The initial access policy is deny-by-default for humans: `viewer` can read and
comment, `contributor` can write/transition tasks, `pm` can orchestrate and
finalize, and `project_admin` can manage members. Organization ranks are kept
separate for approvals (for example CFO rank 70 and CEO rank 100). The Access
view edits only this project's `.datansh/access.yaml`; there is no user-level
PMO config.

PM conversations keep the normal Hermes file, patch, terminal, skill, memory,
and delegation tools. File and patch paths are resolved (including symlinks)
against the selected project's folder set. Terminal sessions start in that
project. By default `scope.terminal_isolation: docker` runs the normal Hermes
terminal in a distinct per-project container whose only project mounts are that
project's validated folders and copied Kanban board. Delegated agents share their
PM's project sandbox, never another project's. Build the pinned local image before
starting the gateway:

```powershell
.\scripts\build_pmo_project_sandbox.ps1
```

If Docker or a required mount is unavailable, terminal use fails closed instead of
falling back to the host. `scope.terminal_isolation: host_guard` is an explicit
local-development downgrade: its lexical checks are defense in depth, not a hard
filesystem jail. `scope.deny` continues to protect normal file/patch calls; keep
shell-inaccessible secrets outside a mounted project tree because bind mounts cannot
selectively hide matching files. Active projects must also use disjoint folder trees:
setup/resolution rejects equal, parent, or nested roots because a parent bind mount
would necessarily expose its nested project.

A rank-100 CEO is the global PMO administrator: every registered project is
visible in the portfolio and project switchers, and all Hermes tabs remain
available. Other users see only projects where their project-local membership or
rank grants access.

Use **Human tasks** in the PM-OS dashboard when a project manager needs work
from a person in the ERP team. It creates a native Kanban triage card assigned
to `human:<principal>`; the PM must finalize it, and Hermes never dispatches it
as an agent work item. The Hedgi ERP repository now exposes a protected
`/project-management` workspace in its existing shell, embedding the
project-scoped PMO dashboard so managers can query and delegate without leaving
the ERP. Set `VITE_PMO_DASHBOARD_URL` in the ERP build for a deployed PMO
origin; the PMO keeps its own sign-in so project and folder boundaries remain
authoritative. A full cross-origin SSO/API proxy remains optional deployment
hardening, not a reason to bypass PMO authorization.

For scripts and CI, provide the same defaults explicitly:

```console
hermes pmo setup --workspace . --slug acme --name "Acme Portal"
```

To onboard directly from an existing checkout while recording its Git remote:

```console
hermes pmo repo onboard --slug hedgi-app --name "Hedgi App" \
  --path ../hedgi_app --url https://github.com/hedgiai/hedgi_app.git \
  --branch main --admin ceo@datansh.local --admin-rank 100
```

Use this one-shot command for local checkouts and public remotes. Private GitHub
repositories use **Project onboarding** in the dashboard: create the project draft,
approve the Datansh GitHub App installation, select one primary repository and any
number of secondary repositories, then complete the draft. A single project may
select repositories from multiple GitHub organizations or App installations.

Configure public App metadata in the server's `config.yaml` and keep the private key
only in the named server environment variable:

```yaml
pmo:
  github_app:
    app_slug: datansh-pm-os
    app_id: 123456
    client_id: Iv1.example
    callback_url: http://127.0.0.1:8787/api/plugins/pmo/github-app/callback
    private_key_env: DATANSH_GITHUB_APP_PRIVATE_KEY
```

The App must have **Contents** permission for every selected repository; write access
is required for the primary. Project config stores only installation account/ID and
repository name/ID. Installation access tokens are minted for one Git operation,
restricted to that repository and requested read/write permission, and are never
returned to the browser, written to disk, embedded in URLs, or placed in command
arguments. PM-OS does not use a person's Git Credential Manager, SSH identity, or
personal access token for private project repositories. HTTPS URLs containing
userinfo or passwords are rejected.

One project can attach additional sibling repositories without making them visible
to any other project's agents:

```console
hermes pmo repo connect --project hedgi-app --board hedgi-app \
  --name shared-api --path ../shared-api \
  --url https://github.com/example/shared-api.git --branch main --access read
hermes pmo repo list --project hedgi-app --board hedgi-app --json
hermes pmo repo validate --project hedgi-app --board hedgi-app
```

Use `repo clone` instead of `repo connect` to create the checkout. Each entry is
declared under `repositories:` in the project's versioned config with its path,
sanitized URL, default branch, read/write access, and primary flag. Exactly one
repository is primary. Active projects cannot attach equal, parent, or nested paths.
Disconnecting a repository removes only its PM-OS metadata and folder binding—it
never deletes checkout files. A repository marked `read` is mounted read-only in
the default Docker project sandbox.

Your first instruction belongs in **Founder's Office**. Tell `@pm` the outcome
you need; the PM turns that instruction into scoped, dependency-aware tickets.
Do not bypass that route by treating an agent card as an instruction channel.
Agent-authored task comments must mention a project handle (for example `@pm`
or `@qa`); the API records the authenticated agent principal rather than a
caller-supplied author name.

Read [the PM-OS mental model](concepts.md) next. Terms used by the plans and
implementation are defined in the [reference glossary](../../plans/REFERENCE-glossary.md).
