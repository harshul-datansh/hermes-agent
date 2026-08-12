# 026 — Hermes Compatibility Reset

**Status:** binding amendment to every earlier PM-OS plan.  
**Decision:** Datansh PM-OS is a clean-slate *product configuration and additive extension* of the newer Hermes fork. It is not a replacement agent runtime, an alternative orchestration system, or a reduced-capability worker fleet.

## 1. Why the plan set is reset

The reverse-engineering atlas establishes that Hermes already provides the durable
agent lifecycle: turn construction and repair, tool dispatch and approvals, context
compression, pluggable memory, skills and self-improvement, profiles, subagent
delegation, Kanban workers, gateway delivery, MCP, and plugin loading. The earlier
plans frequently treat these as missing and propose parallel equivalents (`pmo.db`, a
new dispatcher, a new chat runtime, new authorization and routing layers, copied
Kanban UI, and broad worker tool removals). That would make PM-OS a large fork and
silently remove strengths of the agent it is supposed to specialize.

The atlas wins over the earlier plans wherever they conflict. In particular, see
`../../reverse-eng-docs/01-architecture-overview.md`, `02-multi-agent-orchestration.md`,
`03-skills-system.md`, `04-tools-toolsets-registry.md`,
`06-memory-context-state.md`, `07-gateway-surfaces-mcp-plugins.md`, and
`14-source-evidence-runtime-contract.md`.

## 2. Non-negotiable compatibility contract

1. **Preserve Hermes agent behavior by default.** PM, worker, and reviewer profiles
   retain the ordinary Hermes core loop, prompt-cache rules, tool protocol repair,
   approvals, context compression, memory, skills, background review, delegation,
   terminal/browser/file capabilities, gateway delivery, and MCP/plugin resolution.
2. **Add before overriding.** New PM behavior must first be expressed as a project
   profile, project config, skill, Kanban convention, service-gated tool, plugin, or
   MCP server—following Hermes' footprint ladder. A core edit needs a written
   compatibility case and an upstream contribution attempt.
3. **No feature subtraction as policy.** Do not use a blanket toolset allowlist,
   disabled skill lifecycle, disabled delegation, replacement dispatcher, or parallel
   memory/context system merely to make agents easier to control. Narrow a capability
   only for a demonstrated project-data boundary or safety requirement, and preserve a
   documented, user-approved route to the original capability.
4. **Fork Kanban at the plugin boundary, then preserve its behavior.** Start with a
   tracked copy at `plugins/pmo/`, leaving `plugins/kanban/` byte-identical to upstream.
   Make PM-OS changes only in the copy so upstream Kanban can be reviewed and synced with
   low merge-conflict risk. Reuse the copied Kanban's dispatcher, routes, data model, and
   UI behavior; do not replace them with a second agent or conversation runtime.
5. **Keep prompts and schemas stable inside a conversation.** Project-specific setup is
   selected when the session/profile starts. Do not rebuild prompts or swap toolsets
   mid-turn except through Hermes' existing cache-aware mechanisms.

## 3. Change budget and measurement

Target **5–10% customized code** relative to the current Hermes fork, measured by unique
non-documentation production files changed (not raw line count). The initial byte-identical
`plugins/kanban → plugins/pmo` copy is a tracked fork baseline, not PM-OS customization;
measure the PM-OS delta from that recorded baseline. The initial budget is:

| Area | Budget | Default implementation |
|---|---:|---|
| Existing Hermes core (`agent/`, `run_agent.py`, `toolsets.py`, `model_tools.py`, `gateway/`, `hermes_state.py`) | 0 files | Supported extension points only |
| Upstream `plugins/kanban/**` | 0 files | Copy once into `plugins/pmo/**`; keep upstream path untouched |
| Forked `plugins/pmo/**`, new skills/config/tests/docs | up to 10% of production files | Additive PM behavior on the copied Kanban plugin |
| Bug fixes needed to preserve Hermes behavior | exception | Minimal upstreamable fix with regression test |

Before each implementation wave, record `git diff --numstat <base>...HEAD` and the
unique production-file ratio in `plans/CHANGELOG.md`. Crossing 10%, touching an existing
core/Kanban file, or removing a user-visible Hermes capability requires an ADR that states
the measured impact, rejected extension seams, migration/rollback, and parity tests.

## 4. Replacement scope for plans 001–025

Earlier documents remain useful as product requirements and risk inventories, but their
implementation prescriptions are superseded as follows:

| Earlier direction | Replacement |
|---|---|
| Copy `plugins/kanban` into `plugins/pmo`; maintain a separate dashboard/API | **Keep this.** Copy once, track provenance, and make all PM-OS UI/API changes in `plugins/pmo` while preserving the copied Kanban behavior |
| New `pmo.db`, chat tables, parallel message runtime, and PM wake machinery | Use Hermes sessions, gateway adapter/plugin hooks, Kanban comments/events, and profile routing; add only a narrow project metadata store if existing state cannot represent a fact |
| New dispatcher and PM-only `draft → todo` workflow | Use the existing Kanban dispatcher/statuses and its orchestration controls; add conventions, skills, or plugin hooks before altering claim behavior |
| PM/worker toolset allowlists that remove messaging, skills, memory, delegation, or normal tools | Start from the normal Hermes profile bundle; add PM skills and project-scoped guardrails. Restrict only the specifically risky action at its existing approval/scope boundary |
| Disable skill-writing/background review | Preserve it, with provenance, review, and existing write-approval controls. Tune per project only after measured unwanted writes, never as a blanket default |
| New auth/RBAC and a multi-user dashboard as Day 1 | Keep PM-OS single-operator/profile-scoped in the clean slate. Defer multi-user product auth until it is independently justified and can be built as an additive plugin feature |
| New project-wide memory/context engine | Use profile isolation, `MemoryProvider`, context files, skill loading, and compression already in Hermes |

## 5. Minimal delivery sequence

1. Copy `plugins/kanban` to `plugins/pmo` byte-for-byte, record the source revision in
   `plugins/pmo/UPSTREAM.md`, and make every PM-OS dashboard/API change in that copy.
2. Establish a PM profile template plus a PM operating skill that teaches the existing
   Kanban workflow, project conventions, escalation format, and acceptance criteria.
3. Add project bootstrap/config as a CLI command or plugin command that creates profiles,
   initializes/reuses a board, installs the skill, and records only essential metadata.
4. Add narrow read-only dashboard enhancements or reporting only if the existing Kanban
   UI cannot express the user journey.
5. Add safety and project-boundary checks at existing approvals, workspace, and credential
   controls. Do not replace general Hermes functionality.
6. Validate representative PM, worker, reviewer, delegation, memory/skill, gateway, and
   Kanban flows against an unmodified Hermes profile to prove capability parity.

## 6. Definition of done

**Acceptance implemented 2026-08-05:** the executable capability checker reports the
unique production-file numerator, recorded-baseline denominator, ratio, and 10% ceiling.
The current measured result is `42 / 4,174 (1.01%)`. Production-API parity tests cover
the inherited PM gateway turn, equal normal toolset, delegation, human tool approval,
memory, skill create/edit, PM decomposition, and registry-dispatched Kanban execution.

PM-OS is done for this phase when it adds a usable project-management workflow while a
PM-OS profile can still perform the same core Hermes workflows as a normal profile,
subject only to explicit, tested project-safety restrictions. The diff remains within
the 5–10% budget; upstream Kanban and core files are untouched; the tracked `plugins/pmo`
copy stays synchronizable; and parity tests cover skills, memory, delegation, tool
approval, gateway turns, and Kanban execution.

## Traps

- Treating an agent capability as dangerous because it is powerful, then deleting it
  instead of using Hermes' existing approval, scope, or profile controls.
- Editing upstream `plugins/kanban` directly. Keep the fork copy isolated and review
  upstream changes into it deliberately.
- Reimplementing a lifecycle that Hermes already owns; this loses retries, transcript
  repair, compression locks, prompt caching, and production incident hardening.
- Counting only lines added to a new plugin while ignoring copied or replaced subsystem
  behavior. The budget is about divergence and ownership, not cosmetic diff size.
- Letting a project-specific requirement redefine Hermes for every user. Keep PM-OS
  optional, additive, and removable without changing normal profiles.
