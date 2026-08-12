---
name: datansh-pm-os
description: Use when running a project through the Datansh PM-OS Kanban workflow.
version: 0.1.0
author: Datansh
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [project-management, kanban, delegation, delivery, datansh]
    related_skills: [hermes-agent-skill-authoring, plan, requesting-code-review]
---

# Datansh PM-OS

## Overview

Run project delivery through the PM-OS board. The project PM may inspect the
repository for knowledge, but repository and board mounts are read-only from its
terminal; implementation belongs to roster workers through planned tickets.

The board is the durable project record. It complements a conversation; it does not
replace Hermes sessions, delegation, background review, or the normal tool loop.

## When to Use

Use this skill when a request involves planning, assigning, tracking, reviewing, or
closing work on a Datansh PM-OS project. Do not use it to turn ordinary one-off
questions into tickets without a clear project need.

## Operating Loop

1. Establish the project and board before creating work. Initialize or reuse them
   with `hermes pmo bootstrap --slug <slug> --name <name> --workspace <path>`.
   The utility binds existing Hermes project and Kanban records; it does not create
   a PM-OS runtime. Inspect the existing board with `hermes pmo report --board
   <slug>` (or add `--json` for automation),
   project conventions, open blockers, and relevant memory. Completion: the work has
   a known project, owner, and current board state.
2. In Founder\'s Office, inspect the repository read-only and call `pmo_plan`.
   Record the requested outcome, repository findings, alternatives, sequencing,
   trade-offs, and open questions. A ticket cannot be created without this visible
   discussion and its returned plan id.
3. Ask project specialists before asking a human. Call `pmo_consult` with the plan
   id and a roster handle. This opens a visible Founder\'s Office sub-chat, routes
   that agent there for analysis, and links its reply to the plan. If the specialty
   is absent, call `pmo_create_agent` rather than implementing the missing role.
4. Turn the agreed plan into small, independently verifiable tickets with
   `pmo_create_ticket`. Include acceptance criteria, dependencies, relevant paths,
   and closure evidence. Assign every implementation ticket to a worker; the PM
   profile is never a legal assignee.
5. Ask a human only for a decision or fact the project agents cannot resolve. Use
   `pmo_ask_human` with the plan id after a specialist reply exists. Do not use
   `clarify` to bypass the recorded consultation.
6. Keep progress and decisions in the ticket comments. Use configured messaging or
   other Hermes communication when it is the right operational channel, then record
   the durable project outcome back on the board. For a consistent durable record,
   run `hermes pmo comment --board <slug> --task <task-id> --type
   <handoff|blocker|review> --author <profile> --summary <update>`; handoffs and
   blockers require `--owner`, while reviews require `--verdict`. Repeat `--evidence`
   and `--next-action` as useful. This writes through the normal Kanban comment/event
   API and does not replace live Hermes messaging. Completion: the next operator can
   reconstruct why the ticket changed state.
7. Review against acceptance criteria before closing. Preserve useful lessons through
   Hermes memory and skill/background-review workflows; do not suppress learning to
   make a project workflow simpler. Completion: evidence, unresolved risks, and any
   follow-up work are recorded.

## Guardrails

- The PM plans, consults, provisions, assigns, monitors, and escalates. It never
  writes repository files, runs implementation through `terminal`/`execute_code`,
  uses `delegate_task` for off-board implementation, or self-assigns work.
- Do not create a parallel agent runtime, message store, or memory system for PM-OS.
  Prefer Kanban comments/events, profile routing, and existing session state.
- Do not treat a task status as proof that work is correct. Require test output,
  review evidence, or an explicit human decision appropriate to the ticket.
- When a task is blocked, record the concrete blocker, owner, and next decision rather
  than silently retrying or dropping it.

## Ticket Template

Use this shape when the existing board fields are insufficiently explicit:

```text
Outcome:
Scope and constraints:
Acceptance criteria:
Dependencies / risks:
Assignee and review owner:
Evidence required to close:
```

## Verification Checklist

- [ ] Board state and ticket comments reflect the current project decision.
- [ ] Delegated work has a named owner and acceptance criteria.
- [ ] Required approvals used Hermes' existing approval path.
- [ ] Review evidence is attached or linked before closure.
- [ ] Reusable learning remains available to Hermes memory/skills workflows.
