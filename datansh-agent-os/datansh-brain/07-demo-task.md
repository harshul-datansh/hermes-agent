# Demo Task

Add grounded document search to the Datansh client portal.

Clients upload contracts, statements of work, and reports. Browsing by filename does not scale past a few hundred documents. A client should be able to ask a question in plain language and receive a short answer with citations to the exact source documents, restricted to their own organization's documents.

The portal is a Next.js App Router frontend over a Spring Boot service backed by PostgreSQL. This is framed as a real production feature, not a prototype, so the plan has to address quality measurement, cost, and abuse — not just the happy path.

This task is deliberately cross-cutting: it exercises the Spring Boot backend, the React/Next frontend, and applied AI in one run, so every Datansh specialist role has real work to do.

## Acceptance Criteria

- A Master Orchestrator routes the task to specialist roles and justifies the route.
- PM, Researcher, Applied AI Engineer, Developer, Reviewer/QA, and Memory Curator outputs are saved separately.
- A final master report combines the outputs into a practical one-day plan and next-week roadmap.
- The backend plan names controllers, services, DTOs, and Flyway migrations, and states the tenant authorization rule for the search endpoint.
- The frontend plan names route segments, marks Server versus Client Components, and covers loading, empty, and error states.
- The AI plan names the retrieval design, model choice with a fallback, an eval set with a threshold, and explicit latency and cost budgets.
- Reviewer/QA raises tenant isolation and prompt-injection-via-uploaded-document as first-class risks.
- Structured events are written for visual monitoring.
- Brain memory files receive dated updates.
- No secrets are written to tracked files or event logs.
