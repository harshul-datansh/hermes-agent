---
name: datansh-pm-os
description: Use when planning, assigning, tracking, reviewing, or closing project work on a Datansh PM-OS board.
version: 0.1.0
author: Datansh
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [project-management, kanban, delegation, delivery, datansh]
---

# Datansh PM-OS

Run delivery through the PM-OS board while retaining the normal Hermes agent
lifecycle and capabilities. The board is the durable project record; it
complements sessions, memory, skills, background review, delegation, configured
messaging, terminal/file work, browser work, plugins, MCP, and approvals.

## Operating loop

1. If the project has not been initialized, run the repository bootstrap utility:
   `python plugins/pmo/bootstrap.py --slug <slug> --name <name> --workspace <path>`.
   It binds the existing Hermes project and same-named Kanban board without creating
   a separate PM-OS store. Inspect the relevant project, current board state, open blockers, repository
   context, and useful memory before changing work.
   For a read-only delivery snapshot, run `python plugins/pmo/report.py --board <slug>`
   (or add `--json` for automation). It always requires the board explicitly and
   does not modify the board database.
2. Express the requested outcome as small tickets with scope, acceptance
   criteria, dependencies, an owner, a review owner, and evidence required for
   closure. Use `python plugins/pmo/ticket.py --board <slug> --title <title>
   --outcome <outcome> --assignee <profile> --acceptance <criterion> --evidence
   <evidence>` to write the standard body and native assignee. Repeat the structured
   flags as needed and use `--parent <task-id>` for native dependencies. Use
   `--triage` only for work whose specification is incomplete; otherwise Kanban
   derives `ready` or `todo` from its existing dependency rules.
3. Delegate independently verifiable work when parallel execution is useful.
   Give child agents the ticket context and let them use the standard Hermes
   lifecycle, tools, memory, and skills.
4. Keep status, decisions, handoffs, and blockers in ticket comments. When
   messaging or another Hermes channel is operationally useful, use it and
   record the durable outcome back on the board. Use `python plugins/pmo/comment.py
   --board <slug> --task <task-id> --type <handoff|blocker|review> --author
   <profile> --summary <update>` for the standard durable shape. Handoffs and
   blockers require `--owner`; reviews require `--verdict`. The command uses the
   existing Kanban comment and event behavior, not a separate communication path.
5. Review evidence against the acceptance criteria before closing. Preserve
   reusable lessons through Hermes memory and self-learning/skill workflows.

## Guardrails

- Do not create a parallel agent loop, message store, memory system, approval
  system, or project database.
- Do not remove tools or disable skills, learning, delegation, messaging,
  terminal access, browser access, gateway surfaces, MCP, or plugins merely to
  enforce a preferred workflow.
- Apply existing targeted approval, workspace, credential, and profile
  boundaries when a particular action needs protection.
- Treat task status as workflow state, not as proof of correctness.
- For a blocker, record the concrete condition, owner, and next decision.

## Ticket shape

```text
Outcome:
Scope and constraints:
Acceptance criteria:
Dependencies and risks:
Assignee and review owner:
Evidence required to close:
```
