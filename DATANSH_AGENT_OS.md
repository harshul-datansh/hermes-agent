# Datansh Agent OS Fork Notes

This checkout is the Datansh customization branch of `nousresearch/hermes-agent`.

Datansh-specific POC files live under:

```text
datansh-agent-os/
```

Use that folder for Datansh brain files, role prompts, wrapper scripts, Mission Control, OpenRouter free-model guardrails, demo artifacts, and local setup docs. This keeps upstream Hermes internals easy to merge while still letting Datansh evolve its own agent layer inside the fork.

Nothing in `datansh-agent-os/` modifies Hermes core, so pulling upstream should stay a routine merge.

## Org Configuration

The agent layer is configured for the Datansh stack: Java 21 with Spring Boot 3 on the backend, React and Next.js App Router with TypeScript on the frontend, and applied AI shipped as production services.

The stack knowledge is deliberately centralized in two files rather than duplicated across prompts:

```text
datansh-agent-os/datansh-brain/00-company-context.md    house defaults per stack layer
datansh-agent-os/datansh-brain/03-coding-standards.md   per-stack rules agents apply and reviewers check
```

Both are injected into every agent prompt as brain context, so changing a house standard is a one-file edit. Agents treat them as starting assumptions and defer to whatever a real repository actually does.

Seven roles run in sequence, defined in `datansh-agent-os/datansh-agents/` and sequenced by `ROLE_SEQUENCE` in `datansh-agent-os/scripts/run_demo.py`. The Applied AI Engineer is a first-class role and runs before the Developer, because retrieval design, evaluation strategy, and cost budgets constrain the schema and the API contract.

## Remote Layout

The fork is attached:

```text
origin    https://github.com/harshul-datansh/hermes-agent.git
upstream  https://github.com/NousResearch/hermes-agent.git
```

Datansh work lives on the `codex/datansh-agent-os` branch. To reattach or repoint the remote on another machine, set `DATANSH_HERMES_FORK_URL` in `datansh-agent-os/.env` and run:

```powershell
powershell -ExecutionPolicy Bypass -File datansh-agent-os/scripts/attach_fork_remote.ps1
```

