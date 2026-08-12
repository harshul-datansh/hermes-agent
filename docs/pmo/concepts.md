# Datansh PM-OS concepts

PM-OS has one deliberate flow:

```text
Founder's Office instruction
          |
          v
   PM clarifies and decomposes
          |
          v
 native Kanban tickets + dependencies
          |
          v
 Hermes agents work, delegate, learn, and comment
          |
          v
 review / approval / escalation gates
          |
          v
 PM finalizes the project outcome
```

## Founder's Office is the instruction boundary

Founders and project leaders give trusted project instructions in Founder's
Office. `@pm` wakes the project PM. The client collaboration route is separate
and untrusted: client text can inform work, but it cannot silently become an
authoritative founder instruction.

An empty Founder's Office should teach the next step: “Start by telling `@pm`
what you need.”

## The PM shapes work; Kanban executes it

The PM creates draft tickets, adds outcomes, constraints, acceptance criteria,
evidence, assignees, and native dependency links, then finalizes them. Hermes'
existing Kanban store and dispatcher remain authoritative. Project tickets use
the normal deterministic worktree and branch behavior.

An empty board should teach: “No tickets yet. Ask the PM in Founder's Office —
that's the only way work starts.”

## Agents retain Hermes capabilities

Workers and reviewers are normal Hermes profiles. They retain memory,
self-learning, skills, tools, delegation, agent-to-agent messaging, gateway
access, and MCP integrations. PM-OS adds scoped project context and lifecycle
comments; it does not replace or subtract those capabilities.

## `@` routes attention; it does not create a second runtime

Mentions on tickets route a bounded notification through the existing gateway
and native comments. A true blocker is escalated after the configured retry
policy. Dependency waits remain native Kanban dependencies and wake
automatically when their parents finish.

## Approvals are linked native tasks

An approval is a non-dispatched native Kanban parent linked to the work it
gates. Human rank determines who may decide it. Escalation only raises the
required rank; it never grants an agent human authority. Decisions and their
reasons remain in the native comment/event history.

## Access and rank are project-owned

Project membership, role, and organization rank are stored in that project's
`.datansh/access.yaml`. A person may therefore be a project administrator in one
project and a contributor or non-member in another. CEO rank `100` is the one
intentional portfolio-wide exception: enrolling a human at rank `100` makes that
identity the global PM-OS administrator, with every project and every existing Hermes
dashboard tab. CFO and lower ranks remain project-scoped and can grant access only on
projects where their effective policy permits `member.write` / `org.manage`.

## Decisions and knowledge complement self-learning

Project decisions and shared facts are append-only, version-controlled files
under `.datansh/`. Their bounded context projection is additional input for
agents. Hermes profile memory and self-learning continue unchanged.

See the [getting-started path](getting-started.md) and the
[reference glossary](../../plans/REFERENCE-glossary.md).
