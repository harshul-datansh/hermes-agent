# PM-OS configuration scopes

PM-OS preserves Hermes defaults and adds two explicit configuration scopes.

- Global defaults live in `pmo-admin-workspace/configuration.yaml`. They provide model routes and default skills, plugins, and channels for every project.
- A project’s `.datansh/project.yaml` supplies project-only additions. The effective configuration is a stable, de-duplicated merge: global values first, then project values.

Project agents receive the effective configuration in their bounded project context. They cannot read another project’s configuration or the global machine identity registry.

## Global configuration agents

A human global administrator may allowlist an existing Hermes machine principal using the exact format `agent:global/<handle>`. A listed agent may update global model and runtime defaults, but it does not receive access to any project and cannot alter the allowlist. Only a human global administrator can add or remove global configuration agents.
