# Project decisions and shared knowledge

`project_context.py` adds plan 020's project-wide memory tier without replacing
Hermes profile memory, skills, background review, or curator behavior.

All commands require an explicit existing workspace. State is stored inside that
workspace so the project can review and version it:

```text
<workspace>/.datansh/
  decisions.jsonl  # immutable decision records; append-only
  knowledge.jsonl  # facts and dedupe-use events; append-only
  context.md       # bounded derived projection for PM/worker intake
```

The utility rejects a symlinked `.datansh` directory or state file. It never searches
for a workspace implicitly and never reads another project's state.

## Record and supersede decisions

```bash
python plugins/pmo/project_context.py --workspace /path/to/project decide \
  --title "Use PostgreSQL" \
  --context "The service needs transactional writes" \
  --decision "Use managed PostgreSQL for the primary store" \
  --rationale "It satisfies consistency and operational requirements" \
  --alternatives "SQLite remains suitable for local-only deployments" \
  --decided-by @pm --task-id task-123 --thread-id thread-456
```

To change the choice, append a replacement. The original line is never edited;
active/superseded status is derived from the link:

```bash
python plugins/pmo/project_context.py --workspace /path/to/project supersede d_abc123 \
  --title "Use distributed PostgreSQL" \
  --context "Regional availability is now required" \
  --decision "Adopt the managed multi-region offering" \
  --rationale "It meets the new recovery objective" \
  --decided-by @pm
```

List commands enforce a hard upper bound of 100 entries:

```bash
python plugins/pmo/project_context.py --workspace /path/to/project decisions \
  --active-only --limit 5
```

## Add shared knowledge

Knowledge is one fact of at most 280 characters. It is capped at 200 unique facts per
project. Exact and near-duplicate writes append a usage event for the existing fact.

```bash
python plugins/pmo/project_context.py --workspace /path/to/project learn \
  --kind env \
  --body "The test suite requires DATABASE_URL" \
  --created-by @worker --source task-123
```

Kinds are `convention`, `gotcha`, `contact`, `env`, and `risk`. Confidence is
`observed` by default and may be explicitly recorded as `confirmed`.

## Skill context

Every successful write refreshes `.datansh/context.md`. PM and worker skills can read
that stable path at the start of project work. The default projection contains at most
the five newest active decisions and twenty knowledge facts, ordered by derived usage,
and is capped at 8,000 characters. It is derived state and may be regenerated:

```bash
python plugins/pmo/project_context.py --workspace /path/to/project context
```

The logs are the source of truth. Commit them with the project when the decisions and
knowledge should be shared through version control. Do not hand-edit `context.md`.
