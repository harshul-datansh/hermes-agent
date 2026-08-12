# Reference — `.datansh/project.yaml`

The complete per-project config schema (#10), assembled from every plan. Lives in the
project's own repo at `<primary_path>/.datansh/project.yaml`, version-controlled with
the work.

**Merge order — exactly three layers, no more (#11):**

```
repo defaults  ←  ~/.hermes/config.yaml  ←  <project>/.datansh/project.yaml
```

Dicts deep-merge. **Lists replace.** Invalid YAML is a hard error, not a fallback
(003 §3).

---

## Full schema

```yaml
version: 1

# ── identity ──────────────────────────────────────────────────── 003
project:
  name: "Acme Portal"
  slug: "acme"                     # must match projects.slug
  client: "Acme Corp"              # display only
  description: "Customer-facing portal rebuild"

# ── the PM agent, per project ─────────────────────────────────── 004
orchestrator:
  profile: "pm-acme"               # hermes profile; one per project, never shared
  soul: ".datansh/agents/project-manager.md"
  auto_decompose: true
  auto_promote_children: true
  max_children: 12                 # cap on kanban_decompose output   004 traps

# ── the @handle roster ────────────────────────────────────────── 005, 009
agents:
  - handle: "dev-1"
    profile: "acme-dev-1"
    role: "Backend engineer"
    soul: ".datansh/agents/dev-1.md"
    toolset: "coding"
  - handle: "qa"
    profile: "acme-qa"
    role: "QA reviewer"
    soul: ".datansh/agents/qa.md"
    toolset: "review"              # read + comment; no source write   009 §6
  - handle: "research"
    profile: "acme-research"
    role: "Researcher"
    toolset: "read-web"            # no repo write at all

# ── board behaviour ───────────────────────────────────────────── 009, 011
board:
  columns: ["draft", "todo", "in_progress", "blocked", "review", "done"]
  labels:  ["spend", "scope-change", "release", "chore", "docs", "bug", "security"]
  default_assignee: "dev-1"
  wip_limits: { in_progress: 3 }
  max_concurrent_workers: 2        # start low; raise when conflict rate is low  011 §5

  review:
    default: true
    reviewer: "qa"
    skip_labels: ["chore", "docs"]

  # git integration                                                   011 §3
  base_branch: "main"
  integration_branch: "pmo/integration"   # optional staging branch; null to disable
  auto_merge: false                       # NEVER default true
  push_remote: true
  worktree_stale_days: 7                  # doctor/GC projection threshold
  restart_drain_limit: 25                 # bounded native reclaim/delivery pass

# ── approvals + hierarchy ─────────────────────────────────────── 006
approvals:
  rules:
    - when: { label: "spend" }        , required_rank: 70    # CFO
    - when: { label: "scope-change" } , required_rank: 70
    - when: { label: "release" }      , required_rank: 100   # CEO
    - when: { estimate_hours_gt: 40 } , required_rank: 70

# ── escalation + SLA ──────────────────────────────────────────── 005, 016
escalation:
  pm_retry_limit: 2
  blocked_timeout_minutes: 30
  respond_within_minutes: 120
  reminder_after_minutes: 60
  escalate_rank_after_minutes: 180

# ── scope + secrets ───────────────────────────────────────────── 003, 015
scope:
  folders:
    - "."
    # Hermes owns <primary_path>/.worktrees/<task-id>; do not add a PMO root.
  deny:                            # ENFORCED from day 1                       015 §1
    - ".env"
    - ".env.*"
    - "**/secrets/**"
    - "**/*.pem"
    - "**/*.key"
    - "**/id_rsa*"
    - "**/.aws/**"
    - "**/.ssh/**"
    - "**/.hermes/**"

# ── models + cost ─────────────────────────────────────────────── 012
models:
  pm:         "anthropic/claude-opus-4.6"
  worker:     "anthropic/claude-sonnet-4.6"
  reviewer:   "anthropic/claude-opus-4.6"
  summariser: "anthropic/claude-haiku-4-5-20251001"
  escalation_override: "anthropic/claude-opus-4.6"

budget:
  currency: "USD"
  monthly_cap: 500.00
  per_ticket_soft_cap: 5.00
  per_ticket_hard_cap: 15.00
  per_run_max_turns: 40
  on_soft_cap: "warn"              # warn | block
  on_hard_cap: "escalate"          # block | escalate
  alert_at_percent: [50, 80, 95]

# ── resilience ────────────────────────────────────────────────── 014
resilience:
  pm_wake_rate_limit: 12           # per project per hour
  worker_retry_limit: 2            # per TICKET, not per agent
  consecutive_failure_limit: 3     # per handle, then quarantine
  max_runtime_seconds: 1800

# ── notifications ─────────────────────────────────────────────── 016
notifications:
  channels:
    in_app:  { enabled: true }
    email:   { enabled: true,  min_urgency: "normal" }
    webhook: { enabled: false, url: "", secret_env: "PMO_WEBHOOK_SECRET" }
  quiet_hours: { start: "20:00", end: "08:00", timezone: "Asia/Kolkata" }
  digest: { enabled: true, at: "09:00" }

# ── observability + retention ─────────────────────────────────── 013, 015
observability:
  transcript_retention_days: 30

retention:
  audit_days: 400
  task_events_days: 180
  client_messages_days: 730

# ── attachments ───────────────────────────────────────────────── 015 §3
attachments:
  max_mb: 25
  allow_types: ["image/png", "image/jpeg", "application/pdf", "text/plain",
                "text/markdown", "application/zip"]

# ── client channel ────────────────────────────────────────────── 010
client:
  enabled: false                   # P1
  thread_title: "Acme Corp"
  require_human_send: true         # NEVER default false
  scan_scope: "strict"             # threat_patterns.scan_for_threats scope
```

---

## Validation rules

Enforced by the pydantic model in `hermes_cli/pmo_config.py`:

| Rule | Reason |
|---|---|
| `project.slug` matches `projects.slug` | Prevents a config file being copied between projects |
| `orchestrator.profile` exists as a Hermes profile | Fails loudly at load, not at first wake |
| Every `agents[].handle` matches `^[a-z0-9][a-z0-9._-]{1,31}$` | Same grammar as the mention regex (005 §3) |
| `handle` values unique within the file | Duplicate handles make mention routing ambiguous |
| `handle` never `pm` in `agents[]` | `pm` is reserved for the orchestrator |
| `board.columns` contains `draft` and `todo` | The finalize gate depends on both (009 §1) |
| `board.default_assignee` is in the roster | Otherwise every unassigned ticket fails finalize |
| `approvals.rules[].required_rank` exists in `org_roles` | A rule nobody can satisfy blocks the board forever |
| `board.review.reviewer` is in the roster | |
| `budget.per_ticket_hard_cap >= per_ticket_soft_cap` | |
| `scope.folders` all exist and are readable | Caught by `hermes pmo doctor` |
| `scope.deny` is non-empty when `client.enabled` | A client-facing project with no secrets denylist is a mistake |

Any failure is a hard error naming the field and the rule. Do not fall back to defaults
(003 §3 — a global config falling back degrades a session; a project config falling back
can point an agent at the wrong board).

---

## Minimal viable file

Everything above has a default. This is enough to bootstrap:

```yaml
version: 1
project:
  name: "Acme Portal"
  slug: "acme"
orchestrator:
  profile: "pm-acme"
agents:
  - { handle: "dev-1", profile: "acme-dev-1", role: "Engineer", toolset: "coding" }
board:
  default_assignee: "dev-1"
```
