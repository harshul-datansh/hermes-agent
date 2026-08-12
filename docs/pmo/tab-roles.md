# Datansh Hermes tab roles

Datansh Hermes is a wrapper around the upstream Hermes dashboard.  The wrapper
keeps the upstream routes and only controls which navigation links are shown
for the signed-in project actor.  The PMO API remains the authorization
boundary; hiding a tab is a usability reduction, not a replacement for the
server-side project capability checks.

| Role | Visible Hermes surfaces |
| --- | --- |
| `manager` | Chat, Sessions, Files, Logs, Skills, PMO |
| `backend-developer` | Chat, Sessions, Files, Models, Logs, Skills, MCP, PMO |
| `frontend-developer` | Chat, Sessions, Files, Models, Logs, Skills, PMO |
| `client` | Chat, Files, PMO |
| `ceo` | All existing Hermes dashboard tabs and all PM-OS projects |
| `cfo` | Chat, Sessions, Files, Analytics (when enabled), PMO |
| `qa` | Chat, Sessions, Files, Logs, Skills, PMO |
| `hr` | Chat, Sessions, Files, PMO |
| `data-analytics` | Chat, Sessions, Files, Analytics, Models, Logs, PMO |
| `maintainer` | Chat, Sessions, Files, Models, Logs, Cron, Skills, MCP, Channels, Webhooks, PMO |
| `admin` | All existing Hermes dashboard tabs |

Role resolution is project-aware and additive: explicit identity aliases such
as `human:cfo@…` or `agent:acme/backend-developer` are recognized first, then
the existing PMO project role and organization rank provide safe fallbacks.
There is no user-level PMO configuration.  To keep upstream merges small, the
policy is served by `/api/plugins/pmo/tab-policy` and applied from the PMO
plugin's `header-left` shell slot; Hermes `web/src/App.tsx` and the core page
implementations are not forked.
