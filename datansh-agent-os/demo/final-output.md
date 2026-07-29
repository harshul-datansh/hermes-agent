# Master Orchestrator Output

## Route Plan
This task spans all three Datansh competencies, so every specialist runs. PM first, to pin down what "good enough to bill for" means before anyone designs. Researcher next, because the plan depends on pgvector behavior and on model pricing and limits that must be checked live rather than recalled. Applied AI Engineer before Developer, deliberately — retrieval and evaluation design constrains the schema and the API contract, so letting the Developer improvise it would mean reworking the migration. Developer turns that into Spring Boot and Next.js specifics. Reviewer/QA gates it. Memory Curator records what should outlive this run.

## Quality Checks
- PM must give acceptance criteria a third party could verify, including a quality threshold rather than "answers are good".
- Researcher must mark model pricing and context windows as requiring live confirmation instead of asserting them.
- Applied AI Engineer must state retrieval design, eval metrics with a gate, and latency and cost budgets as numbers.
- Developer must name real files, real commands, the transaction boundary, and the tenant authorization rule.
- Reviewer must produce failure scenarios, not preferences, and must treat tenant isolation and prompt injection as first-class.
- Memory Curator must append durable, non-secret learnings.

## Final Synthesized Plan
Build a vertical slice of grounded document search inside the existing portal service. Retrieval goes in pgvector on the portal's own PostgreSQL, with organization scope enforced in the query itself and covered by an isolation test written before the endpoint exists. Ingestion extracts page-level text, chunks on structural boundaries, and upserts by content hash. Retrieval is hybrid — vector plus full-text — because exact party and contract names are where embeddings underperform. Generation is a thin cited-summary layer over the top five reranked passages, behind a single swappable model boundary, with a fallback ladder ending in extractive results rather than an error. Answers are schema-validated, citations verified against the passages actually supplied, and unsupported questions refused rather than guessed. The whole surface ships behind a feature flag with a per-organization query cap.

Day one delivers the migration, the AI client boundary with its fallback, the tenant-scoped retrieval query, and the isolation test. The rest of the first week delivers ingestion at corpus scale, the Next.js streaming surface against generated OpenAPI types, the labeled eval set with a recorded baseline, and the CI gate that fails on retrieval regression.

## Confidence And Risk Summary
Confidence is high on the architecture and on the vertical slice landing in a day. It is medium on the schedule, because pgvector availability on the managed database and the presence of scanned PDFs are both unconfirmed and either can move the estimate. It is low on production answer quality until an eval baseline exists against a production-grade model — the free-tier configuration used here proves the pipeline works, not that the answers are good enough to put in front of a paying client.

The two risks that deserve executive attention are cross-tenant leakage, which is severe but fully mitigable by design and test, and sending client document text to a third-party provider, which is a contractual question engineering cannot resolve on its own.

## Recommended Next Actions
1. Confirm pgvector is available on the portal's managed PostgreSQL instance — this blocks step one.
2. Get legal confirmation that pilot clients' contracts permit sending document content to the chosen model provider.
3. Sample the real corpus for scanned versus text PDFs before committing to the estimate.
4. Have someone who knows the corpus label the first eval questions; this is the long-lead item and it is not an engineering task.
5. Record the eval baseline against a production-grade model before scheduling the client pilot.
