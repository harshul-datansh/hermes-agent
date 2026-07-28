# Datansh Agent OS Fork Notes

This checkout is the Datansh customization branch of `nousresearch/hermes-agent`.

Datansh-specific POC files live under:

```text
datansh-agent-os/
```

Use that folder for Datansh brain files, role prompts, wrapper scripts, Mission Control, OpenRouter free-model guardrails, demo artifacts, and local setup docs. This keeps upstream Hermes internals easy to merge while still letting Datansh evolve its own agent layer inside the fork.

## Remote Fork Setup

This local machine does not have GitHub CLI installed, so the remote fork could not be created automatically. Create a GitHub fork of:

```text
https://github.com/nousresearch/hermes-agent
```

Then configure the fork URL in:

```text
datansh-agent-os/.env
```

Set:

```text
DATANSH_HERMES_FORK_URL=https://github.com/<datansh-org-or-user>/hermes-agent.git
```

Then run:

```powershell
powershell -ExecutionPolicy Bypass -File datansh-agent-os/scripts/attach_fork_remote.ps1
```

After that, the standard remote layout should be:

```text
origin   Datansh fork
upstream NousResearch/hermes-agent
```

