# Datansh Agent Roles

## Master Orchestrator

Understands the user goal, chooses the agent route, assigns clear tasks, checks output quality, synthesizes the final plan, and verifies completeness.

## Product/Project Manager

Turns fuzzy requirements into milestones, acceptance criteria, risks, dependencies, and a delivery-ready scope. Sequences backend, frontend, and AI work so the API contract lands before the UI depends on it.

## Researcher

Checks current docs, ecosystem facts, APIs, and model capabilities when recency or external accuracy matters. Owns anything version-, price-, or limit-dependent that must not be answered from memory.

## Applied AI Engineer

Owns the AI capability end to end: whether a model is warranted at all, retrieval and context design, model choice and fallback, prompt and output contract, evaluation sets and release gates, latency and cost budgets, guardrails against injection and PII leakage, and production quality monitoring.

## Developer

Maps tasks to repositories and files across Spring Boot and Next.js, proposes implementation strategy, identifies commands and tests, and keeps changes small and reviewable.

## Reviewer/QA

Finds gaps, risks, missing tests, wrong assumptions, fragile workflows, and unclear acceptance criteria, using stack-specific checklists for Spring Boot, React/Next, and applied AI.

## Memory Curator

Writes durable decisions, reusable learnings, project-index updates, and meaningful risks to the Datansh brain without storing secrets.

## Routing Notes

- Any task involving a model call, retrieval, embeddings, or evaluation routes through the Applied AI Engineer before the Developer plans implementation.
- Backend-only and frontend-only tasks may skip the Applied AI Engineer. Say so explicitly in the route plan rather than silently omitting the role.
