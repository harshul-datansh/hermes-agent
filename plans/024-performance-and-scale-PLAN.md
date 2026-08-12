# 024 — Performance & Scale

**Why this plan exists:** the plans never state how big this is meant to get, and the
inherited implementation has a specific, measurable scaling characteristic that will bite
at a predictable size. The forked kanban plugin's live-updates WebSocket polls SQLite:

```python
_EVENT_POLL_SECONDS = 0.3
```

**Every open connection runs a SQLite query 3.3 times a second.** For one developer with
one board that is the "fraction of a percent of CPU" the comment claims. For twelve people
across five projects with the dashboard open all day, it is 40 queries/second of pure
polling before anyone does anything.

**Day-1 touchpoint:** none. But know the number, because 006's fallback plan is "ship
3-second polling" and that decision now has a defensible basis.
**Full build:** 2 h.

---

## 1. Target scale

State it, so "fast enough" has a meaning:

| Dimension | Day 1 | 6 months | Where it breaks |
|---|---|---|---|
| Projects | 1–2 | 20 | profile count (§4) |
| Concurrent human users | 3 | 25 | WS polling (§2) |
| Agents per project | 3–4 | 6 | git conflicts (011 §5) |
| Concurrent agent runs | 2 | 12 | host CPU/RAM, provider rate limits |
| Tickets per board | 30 | 2,000 | board query + render (§3) |
| Comments per ticket | 10 | 200 | context assembly (019 §6) |
| Messages per thread | 50 | 5,000 | thread load (§3) |

Anything beyond the 6-month column is a different product and needs Postgres, not tuning.
Say that now rather than discovering it as a surprise.

---

## 2. The polling problem

**Cost today:** `open_connections × 3.3 × (board query + events query)` per second.

Three fixes, in order of value:

### 2a. One connection per user, not per tab or per project

Multiplex every subscription over a single WebSocket per browser session (006 §4 already
says multiplex; this is the performance reason). A user with the board and the chat open
in two tabs should still be one connection — reconnect the second tab onto a shared
worker, or accept two and cap it.

### 2b. Poll the event cursor only

The current loop reads events, and clients separately re-fetch the board. Make the poll a
single cheap query:

```sql
SELECT MAX(id) FROM task_events WHERE task_id IN (SELECT id FROM tasks WHERE project_id = ?)
```

and push only when the cursor moves. An idle board then costs one indexed max-scan per
tick and sends nothing. Most ticks on most days are idle.

### 2c. Back off when idle

```python
IDLE_BACKOFF = [0.3, 0.3, 0.5, 1.0, 2.0, 3.0]   # reset to 0.3 on any event
```

An active board stays at 300 ms; a board nobody has touched in five minutes polls every
3 s. This alone removes roughly 90% of the query load in real use, and it is about fifteen
lines.

Do **not** replace the poll with SQLite triggers or a notify mechanism. SQLite has no
`LISTEN/NOTIFY`, the workarounds are fragile across processes, and the upstream comment is
right that polling "has no shared state to synchronize across workers."

---

## 3. Query shapes

| Query | Risk | Fix |
|---|---|---|
| Board load | full scan at 2,000 tickets | paginate per column (50 + "load more"); `idx_tasks_status` exists upstream |
| Portfolio rollup (022) | aggregates × N projects | 30 s in-process cache; single query with `GROUP BY project_id` |
| Timeline (013 §2) | 4-way merge per ticket | already one endpoint; add `LIMIT` + cursor |
| Thread load (006) | 5,000 messages | `after=<id>&limit=100`, newest-first, scroll back |
| Comment thread | 200 comments | 30 newest + "show older" (matches 019's context cap) |
| Mention backlog drain (014 §4) | unbounded on restart | bound the pass |

Add these indexes to `pmo.db` (001 has most; these are the ones scale needs):

```sql
CREATE INDEX IF NOT EXISTS idx_messages_thread_id   ON messages(thread_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_approvals_rank       ON approvals(status, required_rank);
CREATE INDEX IF NOT EXISTS idx_notif_user_unread    ON notifications(user_id, read_at, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_decisions_active     ON decisions(project_id, status, created_at DESC);
```

---

## 4. SQLite as the ceiling

Three databases, WAL mode, a dispatcher writing, watchers polling, and N dashboard
readers. WAL handles concurrent readers with one writer well — that is the right shape
here, and `kanban_db` already has busy-retry (`test_kanban_write_txn_busy_retry.py`).

Where it stops being right:

- **Multiple writer processes.** The dispatcher, the gateway and the dashboard all write.
  Keep transactions short and never hold one across a model call. A write transaction open
  across an LLM round trip will lock the board for thirty seconds and present as "the UI
  froze."
- **Network filesystems.** `projects_db` already falls back from WAL to DELETE on network
  FS. Do not put `HERMES_HOME` on a network share; it converts a fast local DB into a slow
  unreliable one.
- **Postgres migration point:** more than ~25 concurrent users, or more than one host.
  The store modules (`pmo_db.py`) are the only place with SQL, so the port is bounded —
  keep it that way by never writing SQL in route handlers.

### Profile sprawl

021 GAP 5 maps project → profile-set: roughly 3–5 profiles per project. Twenty projects is
~80 profiles. Check what `hermes profile list` and the dashboard's profile switcher do at
that count before project ten, not after project twenty.

---

## 5. Agent-side cost

Wall-clock, not CPU — and the numbers agents care about:

| Lever | Effect | Plan |
|---|---|---|
| Prompt caching | the largest single lever; needs a stable prefix | 019 §2 |
| Board summarised, not dumped | linear→constant context growth | 019 §5 |
| `max_concurrent_workers` | contention and conflict rate | 011 §5 |
| Model routing per role | Sonnet workers vs Opus PM | 012 §4 |
| Checkpoint summaries | retries read 200 words, not a transcript | 019 §7 |

The first two dominate. A PM whose prefix is stable and whose board is summarised costs
roughly an order of magnitude less than one where neither is true, and the difference is
invisible until you look at 012's cache-hit tile.

---

## 6. Frontend

The plugin bundle is a plain IIFE (007 §1) — no virtualization library available without
a build.

- **Paginate rather than virtualize.** 50 cards per column with "load more" is simpler
  than a virtual list written by hand and performs adequately.
- Memoize card renders on `(id, status, updated_at)`.
- Debounce the filter input at 150 ms.
- The chat renders newest-100 and scrolls back. Never render 5,000 messages.
- Watch drag-and-drop: the fork uses HTML5 DnD plus a pointer fallback. Re-rendering the
  whole board on every drag event is the standard way this gets slow — the fork already
  handles it, so **do not restructure the DnD path while restyling** (007 traps).

---

## 7. Measuring

Add to `hermes pmo doctor`:

```
Perf   board query p50 12ms / p95 41ms   (2,140 tickets)
       thread load p50 8ms
       WS connections 6, polls/sec 4.2 (idle-backed-off)
       SQLite busy retries (24h) 3
       largest board 2,140 tickets · largest thread 5,102 messages
```

`busy retries` is the leading indicator. It rises long before anything feels slow, and it
is the signal that a transaction is being held too long somewhere.

Add a load test (`scripts/pmo_loadgen.py`) that seeds 2,000 tickets and 5,000 messages and
runs the three hot endpoints. Run it before every release. Cheap, and it turns "is this
fast enough" from an argument into a number.

---

## 8. Tests

```
tests/plugins/test_pmo_perf.py
  test_board_query_paginates
  test_event_poll_is_cursor_only
  test_idle_backoff_resets_on_event
  test_portfolio_rollup_is_single_query
  test_thread_load_respects_limit
  test_no_write_txn_spans_a_model_call     # source-level guard
  test_no_raw_sql_outside_store_modules    # keeps the Postgres port bounded
```

The last two are structural greps in CI. Both protect properties that are cheap to keep
and expensive to restore.

## P0 / P1

**P0:** nothing on day 1 beyond knowing the numbers. If the WebSocket ships, add the idle
backoff — fifteen lines, largest single win.

**P1:** cursor-only polling; per-column pagination; the indexes; the doctor perf block;
the load generator; the two structural tests.

## Traps

- Leaving `_EVENT_POLL_SECONDS = 0.3` with no backoff as user count grows. It is fine
  today and quietly not fine at twelve people.
- A write transaction open across a model call. Presents as a frozen UI, diagnosed as a
  frontend bug.
- `HERMES_HOME` on a network share.
- Hand-rolling virtualization in a no-build bundle. Paginate.
- Restructuring the DnD path while restyling.
- Raw SQL in route handlers. It is what makes the eventual Postgres port unbounded.
