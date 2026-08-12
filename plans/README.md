# Datansh PM-OS — Plan Index

Workspace: `datansh-pm-os/` (second clone of `harshul-datansh/hermes-agent`)
Branch: `feat/pm-os-day1`, cut from `main` @ `222101071`.

> **Compatibility reset (binding):** Read
> [`026-hermes-compatibility-reset-PLAN.md`](026-hermes-compatibility-reset-PLAN.md)
> before executing any earlier plan. PM-OS must preserve Hermes capabilities and remain
> a 5–10% additive change; plans 001–025 are requirement/risk references rather than
> authorization to copy or replace Hermes subsystems.

> **Handing this to an AI to build?** Paste [`HANDOVER.md`](HANDOVER.md) into a fresh
> session. It is a self-contained coordinator prompt: reading order, the twelve
> non-negotiables, wave 0, and what "done" means.

> **Running this with parallel subagents?** Start at
> [`SUBAGENT-EXECUTION.md`](SUBAGENT-EXECUTION.md). It has the file-ownership matrix, the
> frozen interface stubs, the wave schedule, and the spawn brief — **do not paste this
> README into a subagent.** A subagent that reads 26 plans has burned its context before
> writing a line; the brief in §6 is the four documents it actually needs.

**Read in this order before writing any code:**

0. [`026-hermes-compatibility-reset-PLAN.md`](026-hermes-compatibility-reset-PLAN.md) — binding scope, preservation, and change budget

1. [`000-CONTEXT.md`](000-CONTEXT.md) — codebase survey, architecture decisions, scope honesty
2. [`VERIFIED.md`](VERIFIED.md) ⚠️ — **assumption audit. 8 of 32 assertions in these plans were wrong**; six would have cost between an hour and a day. Read before trusting any plan detail
3. [`021-core-architecture-gaps…`](021-core-architecture-gaps-and-modifications-PLAN.md) ⭐ — where the product exceeds what Hermes does. Amends 002, 004, 006, 009, 010
4. [`DECISIONS.md`](DECISIONS.md) — 18 ADRs, two of them superseding earlier ones
5. [`008-day1-execution…`](008-day1-execution-and-acceptance-PLAN.md) — schedule, cut lines, acceptance scenario

> **`../reverse-eng-docs/` is prior work and it is authoritative over these plans.**
> An 18-document reverse-engineering atlas of this same checkout, with annotated code
> walkthroughs. Pass 2 of `VERIFIED.md` reconciles the plans against it. Where they
> disagree, the atlas is grounded in source and these plans are not.

---

## Track A — Day 1 (001–008)

The vertical slice.

| # | Plan | Covers |
|---|---|---|
| 001 | [Foundation & scaffold](001-foundation-scaffold-PLAN.md) | #12 — fork the plugin, `pmo.db` schema |
| 002 | [Auth & RBAC](002-auth-and-rbac-PLAN.md) | #1, #11 — riskiest plan; never cut |
| 003 | [Project scope & config](003-project-scope-and-config-PLAN.md) | #3, #9, #10, #11 |
| 004 | [Project manager / orchestrator](004-project-manager-orchestrator-PLAN.md) | #2, #4, #5 |
| 005 | [Agent comms & escalation](005-agent-comms-and-escalation-PLAN.md) | #6, #7 |
| 006 | [Founder's office: chat, hierarchy, approvals](006-founders-office-chat-approvals-PLAN.md) | **#8 ⭐** |
| 007 | [Frontend dashboard](007-frontend-pmo-dashboard-PLAN.md) | #12, #13 |
| 008 | [Day-1 execution & acceptance](008-day1-execution-and-acceptance-PLAN.md) | schedule, cut lines, §9 touchpoints |

## Track B — Architecture & hardening (009–025)

Not day-1 work, with two exceptions marked below. **Each names a "day-1 touchpoint":**
the small write-side thing that must happen today because it cannot be backfilled. Those
are consolidated in [008 §9](008-day1-execution-and-acceptance-PLAN.md) — ~40 minutes
total.

| # | Plan | Fills |
|---|---|---|
| **021** | [**Core gaps & Hermes modifications**](021-core-architecture-gaps-and-modifications-PLAN.md) ⭐ | **Read on day 1.** Group chat, per-turn toolsets, dispatch gating, the admin/multi-user boundary |
| 009 | [Ticket lifecycle & workflow](009-ticket-lifecycle-and-workflow-PLAN.md) | No state machine, no ticket contract, no rework loop |
| 010 | [Client collaboration & untrusted input](010-client-collaboration-and-untrusted-input-PLAN.md) | #4's "end clients" half; the injection boundary |
| 011 | [Git concurrency & worktrees](011-git-concurrency-and-worktrees-PLAN.md) | Several agents, one repo |
| 012 | [Cost, budgets & model routing](012-cost-budgets-and-model-routing-PLAN.md) | The CFO's actual job |
| 013 | [Observability & audit](013-observability-and-audit-PLAN.md) | Plenty written, nothing readable |
| 014 | [Resilience & recovery](014-resilience-and-recovery-PLAN.md) | Only the happy path was planned |
| 015 | [Security & data lifecycle](015-security-hardening-and-data-lifecycle-PLAN.md) | Secrets, XSS, uploads, deletion |
| 016 | [Notifications & availability](016-notifications-and-human-availability-PLAN.md) | #6 and #8 end at a human who isn't looking |
| 017 | [Testing strategy](017-testing-strategy-PLAN.md) | Every test stopped at the model boundary |
| 018 | [Upstream merge & release](018-upstream-merge-and-release-PLAN.md) | A decision, not a procedure |
| 019 | [Context engineering](019-context-engineering-PLAN.md) | **Nothing said what goes in the context window** |
| 020 | [Project memory & knowledge](020-project-memory-and-knowledge-PLAN.md) | The PM re-derives everything every wake |
| 022 | [Project-level view & portfolio](022-project-portfolio-view-PLAN.md) | #2's first clause, never built |
| 023 | [Onboarding, seed & demo](023-onboarding-seed-and-demo-PLAN.md) | Clone → working system was undocumented |
| 024 | [Performance & scale](024-performance-and-scale-PLAN.md) | The inherited 0.3 s poll, and no stated target |
| 025 | [Accessibility, i18n & UX bar](025-accessibility-i18n-and-ux-quality-PLAN.md) | Two silent regressions against the host |

## References

| Doc | What it is |
|---|---|
| [SUBAGENT-EXECUTION.md](SUBAGENT-EXECUTION.md) | 🔧 **How to run these plans with parallel subagents.** File-ownership matrix, frozen interfaces, wave schedule, the spawn brief template. Read this before spawning anything |
| [DECISIONS.md](DECISIONS.md) | **Append-only ADR log.** 18 entries, two superseding. Never edit a decided entry — supersede it |
| [REFERENCE-data-model.md](REFERENCE-data-model.md) | Every table, the entity map, 12 invariants, migration rules |
| [REFERENCE-project-yaml.md](REFERENCE-project-yaml.md) | Complete `.datansh/project.yaml` schema + validation |
| [REFERENCE-api-and-tools.md](REFERENCE-api-and-tools.md) | Every route, agent tool, CLI command, error shape |
| [REFERENCE-user-journeys.md](REFERENCE-user-journeys.md) | What a day looks like per role. Surfaces missing UI |
| [REFERENCE-glossary.md](REFERENCE-glossary.md) | Four overlapping id systems, disambiguated |
| [../design/design.md](../design/design.md) | Design system, extracted live from `erp.datansh.com` |

---

## The five decisions that shape everything

From [DECISIONS.md](DECISIONS.md) — if you read nothing else:

| ADR | Decision |
|---|---|
| **006** | PM-OS threads are a **Hermes gateway platform plugin**, not a bespoke conversation runtime. Enabled by requirement #7 — because agents talk only on tickets, a thread never needs more than one agent |
| **005** | Every "the agent must not X" is **X absent from the resolved toolset**, checked at turn start. *Amended:* a named toolset is not closed — plugins and MCP servers union into it at call time |
| **017** | **Keep the upstream dispatcher**; gate at **promotion**, not claim. Supersedes ADR-009 — the core dispatcher already claims only `ready`, so `triage` was never visible to it |
| **004** | **Two authorization axes.** Org rank answers "senior enough to approve?"; project role answers "can write here?". CFO and CTO share rank 70 so neither can approve the other |
| **018** | Workers are **dispatcher-spawned, never delegated** — `delegate_task` children run with `skip_context_files=True, skip_memory=True` |

**Total upstream code we modify: zero.** All three registrations we need — platform, auth
provider, CLI subcommand — go through `PluginContext` (V-01).

---

## Requirement → plan map

| # | Requirement | Day 1 | Track B |
|---|---|---|---|
| 1 | Auth + authz, role-scoped read/write | 002 | 013, 015, **021** |
| 2 | **Project-level view**; instructions reach PM only from founder's office | 004, 006 | **022**, 010, 016 |
| 3 | PM is per-project, never shared | 003, 004 | 021 |
| 4 | PM orchestrates; discusses with founder's office, **clients**, team | 004 | **010** |
| 5 | PM finalizes tickets; agents pick them up | 004 | 009, 011, 021 |
| 6 | Blocker → PM → escalate to human | 005 | 014, 016 |
| 7 | Agents communicate only via `@` on task comments | 005 | 016, 021 |
| 8 | Dashboard + group chat, hierarchy, approvals ⭐ | 006 | **021**, 012, 016 |
| 9 | Folder/project scopes | 003 | 011, 015, 020 |
| 10 | Project-specific config | 003 | REFERENCE-project-yaml |
| 11 | No user-level config | 003 | 016 §4 (one explained exception) |
| 12 | Copy the kanban frontend service | 001, 007 | 018 |
| 13 | `design.md` from `erp.datansh.com` | ✅ done | 025 |

Two requirements are only partly served on day 1, and the plans say so rather than
implying otherwise:

- **#2** — the PM and the chat ship; the *project-level view* is 022.
- **#4** — founder's office and team ship; the *client* channel is 010.

---

## Execution order

**Day 1:** `001`+`017` fixtures → `002` ∥ `003`+`015` deny rules → `004` → `005` ∥ `006`
→ `007` weaves through → `008` §4 acceptance. Read `021` first; it changes how you build
`004`, `006` and `010`.

**Day 2 onward:** `017` and `018` in full (they make everything after them safe and cheap)
→ `022` (fast, high-visibility, and it proves the day-1 writes are correct) → `019`
→ `009` → `011` → `014` → `013` → `020` → `012` → `016` → `023` → `024` → `025` → `010`.

The reasoning: testing and merge discipline first; then the view that validates the data;
then context engineering, because agent quality gates everything downstream; then
workflow and git concurrency before increasing throughput; the client channel last,
because it is the largest new trust surface and belongs on a system that is already
observable and recoverable.
