# Demo Script

The reference task is a real Datansh-shaped feature: grounded document search in the client portal, spanning a Spring Boot service, a Next.js surface, and a retrieval/model layer. It is chosen so every specialist role has genuine work and the output is recognizable to any Datansh engineer.

1. Open Datansh Mission Control at `http://127.0.0.1:8501`.
2. Start Hermes dashboard at `http://127.0.0.1:9119` after the Hermes CLI is installed/on PATH.
3. Show `demo/input.md` so the audience knows what was asked before anything runs.
4. Run `powershell -ExecutionPolicy Bypass -File scripts/run_demo.ps1`.
5. Watch the role rows move through queued, running, and success — seven roles including the Applied AI Engineer.
6. Open `demo/runs/<latest>/04-ai-engineer.md`. This is the differentiator: retrieval design, an eval gate, and latency and cost budgets as numbers rather than adjectives.
7. Open `05-developer.md` and point out that it names real Spring Boot classes, a Flyway migration, and which Next.js components are Server versus Client.
8. Open `06-reviewer-qa.md` and show that it independently caught tenant isolation and prompt injection, and that it returned a conditional no-go rather than rubber-stamping.
9. Open `demo/final-output.md` for the synthesized one-day plan and next-week roadmap.
10. Show the memory updates in `datansh-brain/04-decision-log.md`, `05-learning-log.md`, and `06-risk-register.md`.
11. Run `powershell -ExecutionPolicy Bypass -File scripts/audit_free_model_config.ps1` to show free-only model guardrails.

Executive summary:

```text
I have set up the first Datansh Agent OS POC locally, using a Hermes Agent fork as the runtime base.

It is configured for how Datansh actually builds:
- Spring Boot on the backend, React/Next on the frontend, applied AI as a first-class discipline
- seven specialist agents, including a dedicated Applied AI Engineer that owns retrieval,
  evaluation, cost budgets, and guardrails rather than leaving them to a generalist
- a Datansh-owned brain holding our stack defaults and coding standards, so changing the
  house standard is one file edit rather than rewriting every prompt
- a local visual monitor showing each agent's status and outputs
- OpenRouter free-model configuration for cheap initial validation

The demo runs one realistic feature request end to end and produces a plan an engineer could
act on: named classes and migrations, an evaluation gate before shipping, and a reviewer that
raised tenant isolation and prompt injection on its own and withheld a go for the client pilot.

Two honest caveats. The free models prove the pipeline, not production answer quality. And this
is an orchestration and review layer, not autonomous engineering — a senior engineer still owns
the outcome.

The next step is to benchmark reliability: free OpenRouter models against stronger API models,
then decide whether we keep extending Hermes or build a thinner Datansh-owned orchestration layer.
```
