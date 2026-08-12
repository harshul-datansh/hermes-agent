# 020 — Project Memory & Knowledge

**Why this plan exists:** the PM wakes, decides something, and the turn ends. Next wake,
it has no idea it made that decision. Over a week it re-derives the same architecture
choice four times, gives contradictory answers to the founder's office, and pays full
context cost each time. Nothing in plans 001–019 accumulates knowledge across turns.

**Day-1 touchpoint:** a `decisions` table and a `pmo_decide()` tool. The PM recording
what it decided, once, at the moment it decides. Everything else here is derived from
that record.
**Full build:** 2 h 30 m.

---

## 1. What Hermes already gives you

| Module | What it does |
|---|---|
| `tools/memory_tool.py` (56 KB) | Two file-backed stores — `MEMORY.md` (agent's own notes) and `USER.md` (what it knows about the user). `add` / `replace` / `remove` by short unique substring. Character-limited, `§`-delimited entries |
| `agent/memory_manager.py`, `memory_provider.py` | Injection into the system prompt |
| `hermes_cli/memory_setup.py`, `memory_oauth.py` | Provider setup |
| `agent/curator.py` + `hermes_cli/curator.py` | Curation runs, pause/resume, pinning |

**The gap:** memory is **per profile** (`get_hermes_home()`). Our PM is a per-project
profile, so PM memory is already project-scoped — that works out. But every worker has
its own profile and therefore its own private memory. There is **no project-level shared
knowledge** that all agents on a project see. That is the hole.

### A clarification worth writing down

`USER.md` is *agent memory about a person*, not *user configuration*. Requirement #11
prohibits user-level **config** — a settings layer that changes system behaviour. It does
not prohibit an agent remembering that the CFO prefers decisions stated before rationale.
Note this in the code, or someone will "enforce #11" by deleting the file.

---

## 2. Three tiers

| Tier | Scope | Store | Written by | Read by |
|---|---|---|---|---|
| **Agent memory** | one profile | `MEMORY.md` (upstream) | that agent | that agent |
| **Project knowledge** | one project | `.datansh/context.md` + `knowledge` table | PM, curated | **every agent on the project** |
| **Decision log** | one project | `decisions` table | PM via `pmo_decide` | every agent + humans |

Tier 1 is upstream's; leave it alone. Tiers 2 and 3 are what this plan builds.

---

## 3. The decision log — the day-1 touchpoint

```sql
CREATE TABLE IF NOT EXISTS decisions (
    id          TEXT PRIMARY KEY,           -- d_<hex6>
    project_id  TEXT NOT NULL,
    title       TEXT NOT NULL,              -- "Use Auth0 rather than self-hosted Keycloak"
    context     TEXT NOT NULL,              -- what forced a choice
    decision    TEXT NOT NULL,              -- what was chosen
    rationale   TEXT,                       -- why
    alternatives TEXT,                      -- what was rejected, and why
    decided_by  TEXT NOT NULL,              -- agent handle or user id
    thread_id   TEXT,                       -- where it was discussed
    task_id     TEXT,                       -- what it was for
    approval_id TEXT,                       -- if it needed sign-off (006)
    status      TEXT NOT NULL DEFAULT 'active',   -- active | superseded | reversed
    superseded_by TEXT,
    created_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_decisions_project ON decisions(project_id, status, created_at);
```

```python
pmo_decide(title, context, decision, rationale, alternatives=None,
           task_id=None, thread_id=None) -> decision_id
```

**Never update a decision.** Reversing one means inserting a new row and setting
`status='superseded'` + `superseded_by` on the old. The history of what was believed and
when is the point — it is what lets someone answer "why is it built this way" in
February.

The PM's soul (004 §6) gets one line: *"When you make a choice the team will have to live
with, call `pmo_decide` before you announce it."*

The last five active decisions go into every PM wake context (019 §5) and into every
worker's task context. That single injection is what stops the contradiction problem.

Surfaced in the UI as a **Decisions** entry in the section nav — a `.tbl` of title,
who, when, status, with a click-through to the thread where it was discussed. Humans
will read this more than they read the board.

---

## 4. Project knowledge

> **Expose it through a `MemoryProvider` plugin, don't hand-roll injection (V-26).**
> `MemoryProvider` is a genuine plugin ABC — `initialize`, `prefetch`, `sync_turn`,
> `on_pre_compress`, `get_tool_schemas`, `on_delegation`, `backup_paths` — with eight
> backends under `plugins/memory/`, and **`MemoryManager.add_provider()` allows several
> active at once.**
>
> So: keep the tables below as the source of truth (they need SQL, dedup, curation and a
> UI that a memory backend cannot give us), and ship a thin `plugins/memory/pmo/` provider
> that reads them. Injection, prefetch and — importantly — correct interaction with
> compression then come from the host instead of from us.
>
> `on_pre_compress` is the hook that matters: it is where project knowledge gets a chance
> to survive a compression pass that would otherwise drop it.

Facts an agent learns that the *whole project* should know: "the test suite needs
`DATABASE_URL` set", "the payments module has no tests, treat changes as high risk",
"the client always reviews on Thursdays".

```sql
CREATE TABLE IF NOT EXISTS knowledge (
    id          TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL,
    kind        TEXT NOT NULL,      -- convention | gotcha | contact | env | risk
    body        TEXT NOT NULL,      -- one fact, <=280 chars
    source      TEXT,               -- task_id / thread_id / handle
    confidence  TEXT NOT NULL DEFAULT 'observed',   -- observed | confirmed
    uses        INTEGER NOT NULL DEFAULT 0,
    last_used_at INTEGER,
    created_by  TEXT NOT NULL,
    created_at  INTEGER NOT NULL,
    retired_at  INTEGER
);
```

```python
pmo_learn(kind, body, source=None) -> knowledge_id     # any agent
```

Rules that keep this from becoming a landfill:

- **One fact per row, ≤280 chars.** Same discipline as `memory_tool`'s bounded entries.
  A 2,000-word "knowledge" row is a document, and it will not be read.
- **`confidence='observed'` by default.** Promoted to `confirmed` only by the PM or a
  human. Injected knowledge is labelled with its confidence so an agent weighs a guess
  differently from a fact.
- **Track `uses`.** A fact never used in 60 days is a candidate for retirement.
- **Cap the injection at 20 facts**, ordered by `uses` desc. Beyond that, an agent
  skims.
- **Dedupe on write.** Near-duplicate detection (normalised body hash + a similarity
  check) before insert; on a hit, increment `uses` instead of inserting.

The dedupe rule matters more than it sounds. Without it, five agents each independently
"learn" that the test command is `npm test` and the knowledge base is 80% noise inside
a month.

---

## 5. Curation

Upstream has `agent/curator.py` with pause/resume and pinning. Add a PM-OS curation pass
on the PM's scheduled tick:

1. Retire `knowledge` unused for `knowledge.retire_after_days` (default 60).
2. Merge near-duplicates that slipped past the write-time dedupe.
3. Promote facts used more than 10 times to `confirmed`.
4. Flag `decisions` whose subject matter has changed for the PM to review.
5. Refresh the **Current focus** section of `.datansh/context.md` from the board.

Curation is a *proposal* step, not an autonomous edit, for decisions — the PM proposes
and a human confirms in the founder's-office thread. For knowledge, autonomous is fine;
the blast radius is a stale hint.

---

## 6. What NOT to build

Named explicitly, because each is tempting and each is a week that buys little here:

- **A vector store / RAG over the repo.** Agents have `grep` and `read_file` and the repo
  is right there. Embedding search adds infrastructure, staleness, and a new failure
  mode to solve a problem the filesystem already solves at this scale.
- **Cross-project knowledge sharing.** It directly contradicts requirement #9. If a
  convention should apply everywhere, it goes in the repo defaults layer (003 §3), not
  in a shared knowledge pool.
- **Automatic summarisation of every thread into knowledge.** Volume without signal. Let
  agents call `pmo_learn` deliberately.
- **A second memory file format.** `memory_tool`'s `§`-delimited store works; use the
  tables above for what is genuinely project-shared and leave per-agent memory alone.

---

## 7. Tests

```
tests/hermes_cli/test_pmo_decisions.py
  test_decision_never_updated_in_place
  test_supersede_links_old_to_new
  test_recent_decisions_injected_into_pm_context
  test_recent_decisions_injected_into_worker_context
  test_decision_links_to_thread_and_approval

tests/hermes_cli/test_pmo_knowledge.py
  test_body_length_capped
  test_duplicate_write_increments_uses
  test_near_duplicate_deduped
  test_injection_capped_and_ordered_by_uses
  test_confidence_labelled_in_context
  test_curation_retires_unused
  test_knowledge_never_crosses_projects        # requirement #9
```

`test_knowledge_never_crosses_projects` belongs with the other isolation tests in 003 —
a shared knowledge pool is the most plausible accidental route to a cross-project leak,
because it feels helpful.

## P0 (day-1 touchpoint) / P1

**Day 1:** `decisions` table + `pmo_decide` tool + the last-5 injection into PM and
worker context. One table, one tool, one context block.

**P1:** `knowledge` + `pmo_learn` + dedupe; the curation pass; the Decisions UI;
`.datansh/context.md` refresh; confidence promotion.

## Traps

- Updating a decision in place. The history is the product.
- Unbounded knowledge injection. Twenty facts is a briefing; two hundred is wallpaper.
- No write-time dedupe. Five agents learn the same thing five times.
- Building RAG. The repo is on disk and the agent has `grep`.
- "Enforcing #11" by deleting `USER.md`. It is agent memory, not configuration.
