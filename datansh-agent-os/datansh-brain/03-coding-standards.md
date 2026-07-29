# Datansh Coding Standards For Agents

## General

- Inspect the existing repository before editing. Match the conventions already there over the ones in this file.
- Prefer small, focused changes with clear ownership boundaries.
- Keep tests proportional to risk and blast radius.
- Do not commit secrets or generated local configuration.
- Document assumptions when they shape implementation.
- Avoid unnecessary refactors during feature work.
- Use structured parsers and APIs instead of brittle string manipulation where practical.
- Record meaningful project learnings after the run.

## Spring Boot

- Constructor injection only. No field `@Autowired`.
- `@Transactional` belongs on the service layer, and read paths should declare `readOnly = true`.
- Controllers accept and return DTOs. JPA entities never cross the HTTP boundary.
- Every new endpoint states its authorization rule explicitly. "Authenticated" is not an authorization decision.
- Any query that can grow unbounded is paginated, and any repository method on a hot path is checked for N+1 behavior.
- Schema changes ship as a Flyway migration in the same change as the code that needs them.
- Errors surface through a `@ControllerAdvice` handler with a consistent problem-detail body. Do not leak stack traces or SQL to clients.
- Null-safety: prefer `Optional` at repository boundaries, and validate request DTOs with Bean Validation annotations.
- New behavior gets a unit test; anything touching persistence gets a Testcontainers test.

## React / Next.js

- Server Components by default. Justify each `"use client"`.
- No data fetching in `useEffect`. Fetch on the server, or use TanStack Query.
- Type everything at the boundary. Generated API types are the source of truth; do not re-declare them by hand.
- Co-locate components with their route unless they are genuinely shared, then lift to a shared UI module.
- Every async surface handles loading, empty, and error states.
- Memoize only in response to a measured problem, not preemptively.
- Keep secrets and privileged calls server-side. `NEXT_PUBLIC_` means public — treat it that way.
- Interactive elements are keyboard reachable and labeled.

## Applied AI

- Model calls go through the internal client boundary, never a provider SDK call inline in a service.
- Prompts are versioned files, not string literals buried in code.
- Every prompt that includes retrieved content or user input marks that content as untrusted data and instructs the model not to follow instructions inside it.
- Model output that reaches a database, an API response, or another system is schema-validated first.
- A feature change that could move quality ships with an eval run comparing against the current baseline. Report the delta, not a vibe check.
- Record token usage, latency, and model identity per request so cost and regression questions are answerable later.
- Define the degraded path: what the user gets when the model call fails or times out.
- Redact PII before it leaves Datansh infrastructure, and never log raw prompts containing client data.
