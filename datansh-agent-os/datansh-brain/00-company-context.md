# Datansh Company Context

Datansh builds software products, internal systems, and client-facing engineering solutions. The agent system should help Datansh teams plan, code, research, review, document, and retain practical project knowledge.

## Engineering Stack

These are Datansh house defaults. They are the assumption an agent starts from, not a rule that overrides a repository. Always confirm against the actual project before planning edits, and say so explicitly when a repo diverges.

### Backend: Java + Spring Boot

- Spring Boot 3.x on Java 21 (LTS). Gradle preferred for new services; existing Maven modules stay Maven.
- Spring Web MVC by default. Reach for WebFlux only when a service is genuinely IO-bound and streaming.
- Spring Data JPA over PostgreSQL as the primary datastore. Redis for caching, rate limits, and lightweight queues.
- Schema changes go through Flyway migrations. Never rely on `ddl-auto` outside local development.
- Spring Security for authN/authZ. Prefer method-level authorization over scattered controller checks.
- Layering: `controller` handles HTTP only, `service` owns business rules and transactions, `repository` owns persistence. Keep entities out of API payloads — map to explicit request/response DTOs.
- Testing: JUnit 5 and AssertJ, Mockito for unit isolation, Testcontainers for anything touching a real database, `@WebMvcTest`/`@DataJpaTest` slices over full-context tests.
- Operability: Actuator health and metrics, structured JSON logging with a correlation ID, Docker image per service.

### Frontend: React / Next.js

- Next.js App Router with React 19 and TypeScript in `strict` mode. No `any` in reviewed code.
- Server Components are the default. Add `"use client"` only for genuine interactivity, and push it as far down the tree as possible.
- Data fetching: server-side in RSC or route handlers where it can be; TanStack Query for client-side server state. Do not use `useEffect` as a data-fetching mechanism.
- Styling with Tailwind CSS; shared primitives from shadcn/ui. Keep one design-token source rather than ad-hoc hex values.
- Forms validated with Zod schemas shared between client and server. Never trust client-side validation alone.
- Testing: Vitest and React Testing Library for components, Playwright for critical user journeys. Test behavior, not implementation detail.
- Accessibility and loading/error/empty states are part of "done", not follow-up work.

### Applied AI

Datansh ships AI features as production services with the same rigor as any other service. Notebook-quality work is a prototype, not a deliverable.

- Retrieval-augmented generation over client and document corpora is the most common shape. pgvector alongside the existing PostgreSQL is the default vector store; justify any separate vector database.
- Evaluation-first. A feature needs a labeled eval set and a measurable baseline before it is considered shippable, and a regression gate in CI before it stays shipped.
- Every AI feature carries an explicit latency budget, a cost-per-request budget, and a defined fallback when the primary model is slow, rate-limited, or down.
- Models are swappable by configuration. No provider SDK calls scattered through business logic — go through one internal client boundary.
- Prompts and their versions are source-controlled artifacts, reviewed like code, and pinned per release.
- Guardrails are mandatory: treat retrieved documents and user content as untrusted input, defend against prompt injection, redact PII before it reaches a third-party model, validate model output against a schema before it touches a database or an API response.
- Keep a human in the loop for any output that carries legal, financial, or client-facing commitment.

### Cross-Cutting

- Contract-first between backend and frontend: OpenAPI from Spring Boot, generated TypeScript types consumed by Next.js. Hand-written duplicate types are a defect.
- Secrets live in the environment or a secret manager. Never in source, config files, logs, prompts, or event logs.
- CI runs build, tests, lint, and — for AI features — the eval suite.

## Operating Principles

- Prefer practical implementation artifacts over theoretical advice.
- Use local files and Datansh-owned context first.
- Preserve security, privacy, and client confidentiality.
- Never store API keys, passwords, private customer data, or local machine secrets in memory files.
- Capture durable decisions and reusable learning after each meaningful run.
