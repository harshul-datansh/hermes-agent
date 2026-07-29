from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path

from datansh_common import (
    ROOT,
    RUNS_DIR,
    append_event,
    append_text,
    brain_context,
    call_openrouter,
    get_env,
    is_free_model,
    read_text,
    role_prompt,
    run_id,
    write_json,
    write_text,
)

ROLE_SEQUENCE = [
    ("Master Orchestrator", "master-orchestrator.md", "01-master-route.md", "Create the routing plan and quality bar."),
    ("Product/Project Manager", "project-manager.md", "02-project-manager.md", "Convert the task into milestones and acceptance criteria."),
    ("Researcher", "researcher.md", "03-researcher.md", "Identify current facts, unknowns, assumptions, and research risks."),
    ("Applied AI Engineer", "ai-engineer.md", "04-ai-engineer.md", "Design retrieval, model routing, evaluation, budgets, and guardrails."),
    ("Developer", "developer.md", "05-developer.md", "Create the Spring Boot and Next.js implementation plan and verification steps."),
    ("Reviewer/QA", "reviewer-qa.md", "06-reviewer-qa.md", "Review the plan for gaps, tests, security, and delivery readiness."),
    ("Memory Curator", "memory-curator.md", "07-memory-curator.md", "Identify durable memory updates and redactions."),
    ("Master Orchestrator", "master-orchestrator.md", "08-final-master-report.md", "Synthesize the final Datansh execution plan."),
]

FINAL_REPORT_FILE = "08-final-master-report.md"

# Guardrails against weak free models. Two failure modes were observed on live runs:
# a role degenerating into a repetition loop (400KB of one repeated sentence), and the
# resulting oversized output blowing the context window of every downstream role until
# the final report came back as a 19-byte fragment. Roles are chained, so one bad
# output silently poisons the rest of the run unless it is caught here.
MAX_OUTPUT_CHARS = 40_000
MIN_OUTPUT_CHARS = 400
MAX_CONTEXT_CHARS_PER_ROLE = 6_000
MAX_REPEAT_RATIO = 0.3


def reject_reason(content: str) -> str | None:
    """Return why this output is unusable, or None when it looks sane.

    Rejected output is replaced by the deterministic offline text so the run still
    produces a coherent artifact instead of propagating garbage downstream.
    """
    stripped = content.strip()
    if len(stripped) < MIN_OUTPUT_CHARS:
        return f"too_short({len(stripped)}chars)"
    if len(stripped) > MAX_OUTPUT_CHARS:
        return f"too_long({len(stripped)}chars)"

    lines = [line.strip() for line in stripped.splitlines() if line.strip()]
    if len(lines) >= 20:
        most_common = max(set(lines), key=lines.count)
        if lines.count(most_common) / len(lines) > MAX_REPEAT_RATIO:
            return f"repetition_loop({lines.count(most_common)}x)"

    # Structure check, deliberately lenient. Roles are asked for numbered sections, and
    # models satisfy that with "## Findings", "1. Findings", or a table just as validly.
    # An earlier stricter version rejected a perfectly good Memory Curator output purely
    # because it used numbered headers and tables instead of hash headings.
    has_heading = "#" in stripped
    has_numbered_section = re.search(r"^\s*\d+[.)]\s+\S", stripped, re.MULTILINE) is not None
    has_table = "|" in stripped and "---" in stripped
    if not (has_heading or has_numbered_section or has_table):
        return "no_structure"
    return None


def clip_for_context(content: str) -> str:
    """Bound how much of a previous role's output is replayed into later prompts."""
    if len(content) <= MAX_CONTEXT_CHARS_PER_ROLE:
        return content
    return content[:MAX_CONTEXT_CHARS_PER_ROLE] + "\n\n[truncated for context budget]"


def offline_output(agent: str, task: str, previous: dict[str, str]) -> str:
    if agent == "Product/Project Manager":
        return """# Product/Project Manager Output

## Problem Framing
Portal clients can only browse uploaded contracts, SOWs, and reports by filename. Past a few hundred documents that stops being navigation and starts being archaeology. We want a plain-language question to return a short, cited answer drawn only from documents that client's organization is entitled to see. The hard part is not the answer — it is tenant isolation, citation accuracy, and knowing when the answer is good enough to bill for.

## Milestones
1. Ingestion: extract text from uploaded PDFs/DOCX, chunk, embed, and index with the owning organization ID on every row.
2. Retrieval API contract: agree the OpenAPI shape for query, filters, answer, and citations before the frontend starts.
3. Search endpoint in the Spring Boot portal service, tenant-scoped at the query level rather than filtered after the fact.
4. Next.js search surface with streamed answer, citation chips linking to the source document and page.
5. Evaluation harness: a labeled question set per document type, with a retrieval and answer-quality threshold wired into CI.
6. Pilot with two friendly client accounts behind a feature flag before general availability.

## Acceptance Criteria
- A client can ask a question and receive an answer with at least one citation resolving to a document their organization owns.
- A cross-tenant query returns zero results from other organizations, proven by an automated test, not by inspection.
- Retrieval recall@5 is at or above the agreed threshold on the labeled eval set, and the number is reported in CI.
- Answers with no supporting document say so explicitly instead of guessing.
- p95 end-to-end latency and cost per query are measured and within the agreed budget.
- The feature is behind a flag and can be disabled without a redeploy.

## Dependencies
pgvector enabled on the portal PostgreSQL instance, a document text-extraction path for the existing upload pipeline, an embedding and generation model budget approved by finance, and labeled evaluation questions from someone who knows the document corpus.

## Open Questions
- Which client accounts are in the pilot, and do their contracts permit sending document text to a third-party model?
- Do we re-index historical uploads, or only documents from go-live forward?
- What is the retention policy for query logs that may contain client-confidential phrasing?
"""
    if agent == "Researcher":
        return """# Researcher Output

## Verified Facts
- pgvector is a PostgreSQL extension providing vector column types and similarity search, so retrieval can live in the database the portal already runs rather than a separate service.
- Spring Boot 3 on Java 21 is the Datansh backend baseline, and Spring Security supports method-level authorization suitable for enforcing tenant scope.
- The Next.js App Router supports streaming responses, so a generated answer can render progressively instead of blocking on the full completion.
- OpenRouter exposes a public `/api/v1/models` endpoint with pricing metadata, which is how this POC keeps model configuration free-only.

## Assumptions
- The portal's existing upload pipeline stores the original file but not extracted text; an extraction step will need to be added. This must be confirmed against the repository.
- Documents are predominantly English and text-based rather than scanned images. If scanned PDFs are common, OCR becomes a prerequisite and changes the estimate materially.
- Client contracts permit sending document content to a third-party model provider. This is a legal question, not an engineering one.

## Useful Docs Or APIs
- pgvector indexing documentation for HNSW versus IVFFlat tradeoffs at the corpus size we expect.
- Spring Security method-security reference for enforcing organization scope.
- OpenRouter models API for current pricing and context windows.

## Risks From Uncertainty
Embedding and generation model pricing, context windows, and availability change frequently and must be checked live rather than recalled — every cost estimate in this plan is provisional until confirmed against the provider. Free-tier models used for the POC may be rate-limited or withdrawn, which would prove orchestration without proving production answer quality.

## Recommended Follow-Up Research
Measure retrieval quality on a real sample of the client corpus before committing to a chunking strategy, and price the same eval run against both a free model and a production-grade model so the quality-versus-cost tradeoff is a number rather than an opinion.
"""
    if agent == "Applied AI Engineer":
        return """# Applied AI Engineer Output

## Problem Framing And Whether An LLM Is Warranted
Two capabilities are being conflated: finding the right documents, and phrasing an answer. Retrieval is the part that carries the value and the risk. A generation model is warranted only for the final summarization step, over passages we already retrieved and are willing to cite. If retrieval is weak, adding a stronger generation model makes the failure more fluent, not less wrong. Build and measure retrieval first; the answer layer is thin on top of it.

## Data, Retrieval, And Context Design
- Extract text per page so a citation can resolve to a document and page rather than a whole file.
- Chunk at roughly 800 tokens with about 15 percent overlap, splitting on structural boundaries first, since contracts and SOWs are clause-structured and naive fixed-width splitting severs clauses from their headings.
- Store chunks in pgvector on the existing portal database. Every row carries `organization_id`, `document_id`, `page`, and a content hash for idempotent re-ingestion.
- Tenant scope is a WHERE clause on the vector query, not a post-filter on results. Filtering after retrieval means the wrong tenant's data was already read, and it silently degrades recall.
- Hybrid retrieval: vector similarity combined with PostgreSQL full-text search, since exact contract and party names are precisely where pure embedding search underperforms.
- Retrieve 20 candidates, rerank to the top 5 passages that go into the prompt.
- Re-embed on document replacement, and treat the content hash as the idempotency key so a redelivered upload does not duplicate rows.

## Model Choice, Routing, And Fallback
- Two model roles: an embedding model for indexing and query encoding, and a generation model for the cited answer. They version independently.
- The embedding model is a commitment, not a setting. Changing it invalidates the entire index and requires a full re-embed, so pin the version and record it on every row.
- Generation goes through one internal client boundary so the provider stays swappable. No provider SDK calls in portal business logic.
- Fallback ladder: primary generation model, then a secondary model, then extractive mode — return the ranked passages with citations and no generated prose. Degraded search still beats an error page.
- For this POC the configured model is free-tier via OpenRouter, which is adequate to prove the pipeline and inadequate to judge production answer quality. Do not conflate the two.

## Prompt And Output Contract
- The prompt instructs the model to answer only from the supplied passages, to cite the passage IDs it used, and to say it does not know when the passages do not support an answer.
- Retrieved passages are wrapped in explicit untrusted-content delimiters, with an instruction that text inside them is data and never an instruction. Client-uploaded documents are an injection vector by definition.
- Output is JSON with `answer`, `citations` as passage IDs, and `confidence`. It is schema-validated before it reaches the API response.
- Every citation is verified to resolve to a passage actually supplied in that request. A citation the model invented is dropped, and an answer left with zero valid citations is downgraded to the no-answer response.
- Prompts are versioned files pinned per release, not string literals.

## Evaluation Plan, Metrics, And Release Gate
- Build a labeled set of roughly 150 questions across contracts, SOWs, and reports, each with known correct source documents. Include adversarial cases: questions with no answer in the corpus, questions answerable only by combining two documents, and a document containing embedded instructions attempting injection.
- Retrieval metrics: recall@5 and MRR. Answer metrics: citation validity rate, groundedness, and refusal correctness on the unanswerable subset.
- Retrieval quality is the gate. Ship only when recall@5 clears the threshold agreed with the PM on the labeled set.
- CI runs the eval suite on any change to chunking, the embedding model, the prompt, or the generation model, and fails on a regression beyond the agreed tolerance. A model version change is a code change and goes through the same gate.

## Latency, Cost, And Token Budgets
- Budget p50 under 1.5 seconds and p95 under 4 seconds end to end, with the answer streamed so perceived latency tracks time-to-first-token rather than total completion.
- Retrieval should stay in tens of milliseconds; the generation call dominates. Cache embeddings for repeated queries.
- Cap prompt size at 5 passages so cost per query is bounded regardless of document length. Long documents must not translate into unbounded spend.
- Set a per-organization daily query cap to bound both cost and abuse, and record tokens, latency, and model identity per request.
- Every cost figure here is provisional until priced against current provider rates — see the Researcher output.

## Guardrails, Safety, And Failure Modes
- Prompt injection via uploaded documents is the primary threat. A client can upload a file containing instructions and, without delimiting and a system-prompt rule, influence answers. Mitigate with untrusted-content framing, output schema validation, and never letting model output trigger an action.
- Cross-tenant leakage is the highest-severity failure. Enforce at the query level and cover with an automated test that seeds two organizations and asserts zero cross-reads.
- Redact PII before content leaves Datansh infrastructure where the contract requires it, and never log raw prompts containing client text.
- Expected failure modes: hallucinated citations, caught by citation verification; retrieval misses on exact names, mitigated by hybrid search; silent quality drift after a provider-side model update, caught by the CI eval gate; cost blowup on long documents, bounded by the passage cap.

## Observability And Production Quality Monitoring
- Trace each query as retrieval, rerank, and generation spans with token and latency attributes.
- Log query metadata, retrieved passage IDs, model identity, and prompt version — never raw client document text.
- Track refusal rate, zero-result rate, and citation validity as production health signals. A rising refusal rate usually means retrieval broke, not that the model got cautious.
- Add a thumbs-down control in the UI and route flagged queries into the eval set, so production disagreement becomes future test coverage.
"""
    if agent == "Developer":
        return """# Developer Output

## Technical Approach
Add the capability to the existing portal service rather than standing up a new one; the tenant model, auth, and document tables already live there. Ingestion runs as an asynchronous job off the existing upload path so a slow embed never blocks an upload. The search endpoint is read-only and streams. Model access goes behind one internal client interface per the AI Engineer's boundary, so the portal never imports a provider SDK directly.

## File/Module Plan (Backend)
- `db/migration/V<n>__document_search.sql`: enable pgvector, create `document_chunk` with `organization_id`, `document_id`, `page`, `content`, `content_hash`, `embedding`, plus a vector index and a composite index leading on `organization_id`.
- `document/search/DocumentSearchController.java`: `POST /api/v1/documents/search`, streaming response, DTOs only.
- `document/search/DocumentSearchService.java`: orchestrates retrieve, rerank, generate; `@Transactional(readOnly = true)`.
- `document/search/DocumentChunkRepository.java`: hybrid vector plus full-text query with `organization_id` bound in the WHERE clause.
- `document/search/dto/`: `SearchRequest`, `SearchResponse`, `Citation` — records with Bean Validation.
- `document/ingest/DocumentIngestJob.java`: extract, chunk, embed, upsert by content hash.
- `ai/AiClient.java` and `ai/OpenRouterAiClient.java`: the single model boundary, with the fallback ladder.
- `ai/prompts/document-search.v1.md`: versioned prompt artifact.

## File/Module Plan (Frontend)
- `app/(portal)/documents/search/page.tsx`: Server Component, renders the shell and any initial query from search params.
- `app/(portal)/documents/search/_components/SearchInput.tsx`: Client Component, the only interactive piece.
- `app/(portal)/documents/search/_components/AnswerStream.tsx`: Client Component consuming the streamed response.
- `app/(portal)/documents/search/_components/CitationChip.tsx`: Server Component, links to document and page.
- `app/(portal)/documents/search/loading.tsx` and `error.tsx`: required loading and error states; the empty and no-answer states render inside the answer component.
- Types are generated from the service OpenAPI schema. Do not hand-write the response type.

## Implementation Steps
1. Write the Flyway migration and confirm pgvector is available on the target database before anything else — this is the step most likely to be blocked by infrastructure.
2. Implement `AiClient` with embedding and generation methods plus the fallback ladder, and unit test the fallback with a stubbed failure.
3. Build the ingest job and backfill a small sample corpus in a scratch environment.
4. Implement the repository query with tenant scope in the WHERE clause, and write the cross-tenant isolation test before the endpoint exists.
5. Implement the service and controller; publish the OpenAPI change and regenerate the frontend types.
6. Build the Next.js surface against the generated types.
7. Wire the eval harness and record the first baseline.
8. Put the whole surface behind a feature flag, default off.

## Test Plan
- `@DataJpaTest` with Testcontainers running a pgvector-enabled PostgreSQL image, seeding two organizations and asserting a query from one returns zero rows belonging to the other.
- Unit tests for chunking boundaries and content-hash idempotency: ingesting the same document twice must not duplicate rows.
- `@WebMvcTest` for the endpoint covering authorization, validation failures, and the no-answer response shape.
- `AiClient` fallback test: primary fails, secondary answers; both fail, extractive mode returns passages.
- Vitest and React Testing Library for the answer component across streaming, empty, no-answer, and error states.
- Playwright journey: ask a question, see a streamed answer, click a citation, land on the right document page.

## Verification Commands
```bash
./gradlew test
./gradlew :portal:test --tests '*DocumentSearch*'
npm run test
npx playwright test document-search
python datansh-agent-os/scripts/run_demo.py
```

## Rollback Plan
The feature flag is the rollback. Turning it off removes the surface without a redeploy. The migration is additive — a new table and indexes, no changes to existing columns — so it can be left in place on rollback; drop the table only after the feature is abandoned rather than paused.
"""
    if agent == "Reviewer/QA":
        return """# Reviewer/QA Output

## Findings
- Tenant isolation is the highest-severity risk in this plan and it depends on one WHERE clause. It needs a test that would fail if someone later refactors the repository query, not a code comment. The plan does this correctly; make sure the test lands before the endpoint, not after.
- Prompt injection via uploaded documents is correctly identified, but the mitigation is incomplete: delimiters and schema validation reduce influence over the answer, they do not eliminate it. Since answers are client-facing, a poisoned document could produce a misleading but well-cited answer. Recommend adding an ingestion-time scan flagging documents containing instruction-like text for review.
- The embedding model is an index-wide commitment. If it is not pinned and recorded per row from day one, the first upgrade becomes an outage-length re-embed with no way to run old and new side by side. Recommend storing the model identity per row so a migration can be incremental.
- Citation verification drops invented passage IDs, which is right, but the plan does not say what happens when the model cites a real passage that does not actually support the claim. Valid-but-unsupported citation is the failure clients will notice, and no listed metric catches it. Groundedness scoring on the eval set needs to cover it explicitly.
- Free-tier models prove the pipeline, not the product. The go/no-go for the client pilot must rest on eval numbers from a production-grade model.
- Cost is bounded per query by the passage cap but unbounded in aggregate until the per-organization daily cap exists. Ship the cap with the feature, not after.

## Missing Tests
- Cross-tenant isolation test seeding two organizations, asserting zero cross-reads — the single most important test here.
- An injection test: ingest a document containing embedded instructions, assert the answer is unaffected and the attempt is flagged.
- Idempotency test: ingest the same document twice, assert no duplicate chunks.
- A no-answer test: ask something the corpus cannot support, assert refusal rather than a fluent guess.
- Fallback-ladder test covering primary failure, secondary failure, and extractive mode.
- Frontend states: streaming, empty, no-answer, error, and long-answer truncation.
- A test asserting raw document text never appears in application logs.

## Delivery And Demo Risks
- pgvector availability on the managed PostgreSQL instance is an infrastructure dependency outside the team's control and blocks step one. Confirm before committing to the timeline.
- Scanned image PDFs would require OCR and materially change the estimate. This is flagged as an assumption in research and is not yet confirmed.
- The one-day plan is realistic only for a working vertical slice on a sample corpus. Production readiness for paying clients is the next-week roadmap, and the two should not be presented as the same thing.
- Streaming through the Spring Boot layer to a Next.js client has more edge cases than it appears — proxy buffering and client disconnect handling both need explicit attention.

## Security/Privacy Checks
- Tenant scope enforced at query level, verified by test.
- Client document text sent to a third-party model is a contractual question that must be answered before the pilot, not before general availability.
- No raw prompts or document content in logs; query metadata only.
- Secrets in environment or secret manager, never in prompts, config, or the event log.
- Per-organization rate limiting present at launch to bound both cost and abuse.

## Go/No-Go Recommendation
Go for the internal vertical slice and the POC demo. No-go for the client pilot until three conditions hold: the cross-tenant isolation test passes in CI, the eval baseline is recorded against a production-grade model rather than a free tier, and legal has confirmed that sending client document text to the chosen provider is permitted under those clients' contracts.
"""
    if agent == "Memory Curator":
        return """# Memory Curator Output

## Decisions To Append
- Retrieval lives in pgvector on the existing portal PostgreSQL instance rather than a separate vector database, to avoid adding an operational dependency for a first production AI feature.
- Tenant scope is enforced inside the retrieval query rather than by filtering results afterward.
- Model access goes through a single internal client boundary so the provider stays swappable.
- Retrieval quality, not answer fluency, is the release gate.

## Learnings To Append
- Adding a stronger generation model on top of weak retrieval produces failures that are more fluent rather than less wrong. Measure retrieval first.
- Client-uploaded documents are an untrusted input channel and a prompt-injection vector by construction, so ingestion is a security boundary and not just a data pipeline.
- Changing an embedding model invalidates an entire index, so the model identity belongs on every row from the first migration rather than being added when the first upgrade is needed.
- Citation verification catches invented sources but not valid-yet-unsupported ones; groundedness has to be measured separately.

## Risks To Append
- Cross-tenant leakage in document retrieval, mitigated by query-level scoping plus an automated isolation test.
- Prompt injection through uploaded client documents, partially mitigated and needing an ingestion-time scan.
- Answer quality is unproven until an eval baseline exists against a production-grade model rather than a free tier.
- pgvector availability on the managed database instance is an unconfirmed infrastructure dependency that blocks the first implementation step.

## Project-Index Updates
- Datansh Agent OS now routes tasks across seven roles including a dedicated Applied AI Engineer, and the reference demo exercises the Spring Boot, Next.js, and applied AI stack in a single run.

## Redactions Or Skipped Memory
Client names, document contents, contract terms, and provider API keys are excluded. Only the engineering decisions and their reasoning are recorded.
"""
    return f"""# {agent} Output

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
"""


def build_prompt(agent: str, role_file: str, task: str, previous: dict[str, str]) -> str:
    previous_text = "\n\n".join(f"## {name}\n{clip_for_context(content)}" for name, content in previous.items())
    return f"""{role_prompt(role_file)}

# Datansh Brain Context
{brain_context()}

# User Task
{task}

# Previous Agent Outputs
{previous_text or "None yet."}

# Current Assignment
Act as {agent}. Produce the required sections in markdown. Keep the output practical and Datansh-specific.
"""


def append_once(path: Path, line: str) -> None:
    """Append a row only when an identical one is not already recorded.

    Risk-register entries describe a standing condition rather than a single run,
    so repeating them on every demo run just buries the real content.
    """
    if path.exists() and line.strip() and line.strip() in path.read_text(encoding="utf-8"):
        return
    append_text(path, line)


def update_memory(run: str, curator_output: str) -> None:
    date = datetime.now().strftime("%Y-%m-%d")
    append_text(
        ROOT / "datansh-brain" / "04-decision-log.md",
        f"| {date} | {run} | Enforce tenant scope inside the retrieval query, not as a post-filter | Filtering after retrieval means the other tenant's rows were already read, and it silently degrades recall | Datansh Agent OS | Cover with an automated cross-tenant isolation test |\n",
    )
    append_text(
        ROOT / "datansh-brain" / "05-learning-log.md",
        f"| {date} | {run} | A stronger generation model on weak retrieval produces failures that are more fluent, not less wrong | Any RAG feature | demo run |\n",
    )
    append_once(
        ROOT / "datansh-brain" / "06-risk-register.md",
        "| Client-uploaded documents are a prompt-injection vector into client-facing answers | Medium | High | Untrusted-content delimiters, output schema validation, ingestion-time scan for instruction-like text | Open |\n",
    )
    append_once(
        ROOT / "datansh-brain" / "06-risk-register.md",
        "| Answer quality unproven until an eval baseline is recorded against a production-grade model | High | Medium | Run the labeled eval set on a paid model before scheduling any client pilot | Open |\n",
    )
    append_text(
        ROOT / "datansh-brain" / "01-project-index.md",
        f"\n- {date} `{run}`: Demo workflow generated role outputs, final report, structured events, and memory updates.\n",
    )


def main() -> int:
    model = get_env("DATANSH_DEFAULT_MODEL", "openrouter/free")
    if not is_free_model(model):
        raise SystemExit(f"Refusing non-free model for POC: {model}")

    run = run_id()
    run_dir = RUNS_DIR / run
    run_dir.mkdir(parents=True, exist_ok=True)
    task = read_text(ROOT / "demo" / "input.md")
    write_text(run_dir / "00-input.md", task)

    append_event(run, "System", "task_received", "running", "Run Datansh demo task", "Demo task received", str(run_dir / "00-input.md"), model)

    previous: dict[str, str] = {}
    outputs: list[str] = []
    started_at = datetime.now().isoformat(timespec="seconds")
    live_modes: list[str] = []
    degraded: list[str] = []

    for agent, role_file, filename, assignment in ROLE_SEQUENCE:
        output_path = run_dir / filename
        append_event(run, agent, "agent_started", "running", assignment, f"{agent} started", str(output_path), model)
        prompt = build_prompt(agent, role_file, task, previous)
        append_event(run, agent, "model_call_started", "running", assignment, "Model call started", str(output_path), model)
        content, metadata = call_openrouter(prompt, model)
        live_modes.append(metadata.get("mode", "unknown"))
        status = "success"
        if not content:
            content = offline_output(agent, task, previous)
            metadata["fallback"] = "offline_poc_output"
        else:
            reason = reject_reason(content)
            if reason:
                metadata["rejected_live_output"] = reason
                metadata["fallback"] = "offline_poc_output"
                metadata["rejected_output_path"] = str(output_path.with_suffix(".rejected.md"))
                write_text(output_path.with_suffix(".rejected.md"), content)
                content = offline_output(agent, task, previous)
                status = "degraded"
                degraded.append(f"{agent}: {reason}")
                print(f"  ! {agent} live output rejected ({reason}); used offline fallback")
        write_text(output_path, content)
        previous[agent if filename != FINAL_REPORT_FILE else "Final Master Report"] = content
        outputs.append(str(output_path.relative_to(ROOT)))
        append_event(run, agent, "agent_completed", status, assignment, f"{agent} completed", str(output_path), model, metadata)
        time.sleep(0.2)

    final = previous["Final Master Report"]
    write_text(ROOT / "demo" / "final-output.md", final)
    update_memory(run, previous.get("Memory Curator", ""))
    append_event(run, "Memory Curator", "memory_updated", "success", "Append durable brain entries", "Brain memory files updated", str(ROOT / "datansh-brain"), model)

    metadata = {
        "run_id": run,
        "started_at": started_at,
        "ended_at": datetime.now().isoformat(timespec="seconds"),
        "provider": get_env("DATANSH_LLM_PROVIDER", "openrouter"),
        "models": [model],
        "agents": [item[0] for item in ROLE_SEQUENCE],
        "status": "degraded" if degraded else "success",
        "modes": live_modes,
        "degraded_roles": degraded,
        "outputs": outputs,
    }
    write_json(run_dir / "run-metadata.json", metadata)
    run_status = "degraded" if degraded else "success"
    append_event(run, "System", "run_completed", run_status, "Complete Datansh Agent OS demo", "Run completed", str(ROOT / "demo" / "final-output.md"), model, metadata)
    print(f"Demo run completed: {run} ({run_status})")
    if degraded:
        print(f"Roles that fell back to offline output: {len(degraded)}")
        for item in degraded:
            print(f"  - {item}")
        print("Rejected live output is kept alongside each role as *.rejected.md for inspection.")
    print(f"Final output: {ROOT / 'demo' / 'final-output.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
