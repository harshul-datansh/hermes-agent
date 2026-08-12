# Verification Log — assumptions checked against the repo

The plans assert a lot about Hermes. Each assertion below was checked against this
checkout (`main` @ `222101071`). **Four were wrong**, two of them in ways that would have
blocked an implementing agent within the first hour.

Re-run this pass after every upstream merge (018 §3).

Status: ✅ confirmed · ❌ **wrong, plan corrected** · ⚠️ partly right

---

## V-01 ❌ "One line of core modification" → **zero**

**Asserted:** 018 §2 and 021 §2 budgeted three upstream touchpoints, spending one on
registering the auth provider.

**Found:** `hermes_cli/plugins.py` `PluginContext` exposes every registration we need:

```python
ctx.register_platform(name, label, adapter_factory, check_fn,
                      validate_config=None, required_env=None, install_hint="")   # :950
ctx.register_dashboard_auth_provider(provider)                                     # :694
ctx.register_cli_command(name, help, setup_fn, handler_fn=None)  # → `hermes pmo`  # :523
ctx.register_command(name, handler, ...)                          # slash commands  # :548
```

`register_dashboard_auth_provider`'s docstring even specifies the failure mode we want:
*"Misbehaving providers (wrong type, duplicate name) are logged at WARNING and silently
ignored — never raised — so a broken plugin cannot crash the host."*

**Corrected in:** 021 §2, 018 §2.
**Consequence:** `plugins/kanban/` *and every other upstream file* stays untouched. The
merge strategy (018) is stronger than planned — weekly merges should be conflict-free by
construction, not merely cheap.

---

## V-02 ❌ "`status` is free text; invent `draft`/`in_progress`/`cancelled`"

**Asserted:** 009 §1 — *"`kanban_db` stores `status` as free text, so this needs no
migration."*

**Found:** `hermes_cli/kanban_db.py:102`

```python
VALID_STATUSES = {"triage", "todo", "scheduled", "ready",
                  "running", "blocked", "review", "done", "archived"}
```

and `list_tasks` raises `ValueError(f"status must be one of {sorted(VALID_STATUSES)}")`.
**The invented statuses would have thrown on the first board query.**

Worse — the gate 009 exists to build is already there. `create_task(..., triage=True)`:

> *"Status is `ready` when there are no parents (or all parents already `done`), otherwise
> `todo`. If `triage=True`, status is forced to `triage` regardless of parents — a
> specifier/triager is expected to promote the task to `todo` once the spec is fleshed
> out."*

That is requirement #5's finalize gate, upstream, with the same intent.

**Corrected in:** 009 §1 (rewritten), `REFERENCE-glossary.md`, `REFERENCE-data-model.md`.
**Consequence:** 009 drops from 3 h to 2 h. `review` already exists. UI labels map to
upstream storage (009 §1).

---

## V-03 ❌ "Build an `idem_keys` table"

**Asserted:** 009 §5 and `REFERENCE-data-model.md`.

**Found:** `kanban_db.create_task` takes **`idempotency_key`** directly.

**Corrected in:** 009 §5. Strike `idem_keys` from the data model.

---

## V-04 ❌ "Blockers are untyped; build a retry ladder and a loop breaker"

**Asserted:** 005 §6, 014 §3.

**Found:** `kanban_db.py:125`

```python
VALID_BLOCK_KINDS = {"dependency", "needs_input", "capability", "transient"}
```

with the exact semantics we needed already documented: *"`needs_input` and `capability`
are 'truly blocked': they go to `blocked` for a human."* Plus `BLOCK_RECURRENCE_LIMIT`, an
unblock-loop breaker that routes a repeatedly-re-blocked task to `triage`, whose comment
explains why the counter resets only on completion — *"exactly the amnesia that let the
loop run unbounded."*

`tasks` also already carries `consecutive_failures`, `max_retries`, `block_recurrence`
and `current_run_id`.

**Corrected in:** 005 §6, 014 §1.
**Consequence:** `pmo_block` must pass a `block_kind`, and `needs_input`/`capability`
escalate immediately rather than burning a retry budget first.

---

## V-05 ✅ Kanban plugin UI is hand-written, not a build artifact

`plugins/kanban/dashboard/dist/index.js`, 4,280 lines: *"Plain IIFE, no build step. Uses
`window.__HERMES_PLUGIN_SDK__` for React + shadcn primitives."* No TypeScript source
anywhere in the repo. → 007 §1 stands.

## V-06 ✅ Gateway platform plugins need no core changes

`gateway/platforms/ADDING_A_PLATFORM.md`: *"This requires **zero changes to core Hermes
code**."* Lists inherited behaviour: adapter creation, config parsing, user authorization,
cron delivery, send_message routing, system prompt hints, status display, gateway setup.
→ ADR-006 stands.

## V-07 ✅ Turn serialisation exists

`gateway/turn_lease.py` serialises `[load history → run → flush]` per resolved session id.
Its docstring documents bug #64934. → 004 §3 amended to use it.

## V-08 ✅ Event-driven wake exists

`gateway/wake.py` — synthetic `MessageEvent(internal=True)` for push-capable adapters;
names "plugin platforms" as push-capable. Set `supports_async_delivery = True`
(`base.py:2690`) to stay off the API-server self-POST path. → 004, 005 stand.

## V-09 ✅ Thread-granular profile routing exists

`gateway/profile_routing.py` — `platform + chat_id + thread_id` at specificity 14.
→ ADR-006, ADR-007 stand.

## V-10 ✅ Auth gives `Session`, not roles

`dashboard_auth/base.py` — `Session(user_id, email, display_name, org_id, provider, …)` on
`request.state.session`. `supports_password` / `supports_token` / `supports_session`
capability flags. No role concept anywhere. → 002 stands; authorization is genuinely
net-new.

## V-11 ✅ Projects already carry board and folders

`projects_db.py` — `projects(slug, board_slug, primary_path, …)`, `project_folders(path,
is_primary)`, slug regex `^[a-z0-9][a-z0-9\-_]{0,63}$`. → ADR-003 stands.

## V-12 ✅ Worktree isolation exists, default is wrong for us

`kanban.py` — `--workspace scratch|worktree|worktree:<path>|dir:<path>` (**default
`scratch`**), `--branch`, `--project` *"anchors the task's worktree under the project's
primary repo with a deterministic branch."* → 011's day-1 touchpoint confirmed necessary.

## V-13 ✅ Memory is per-profile, frozen-snapshot

`tools/memory_tool.py` — MEMORY.md + USER.md injected at session start; mid-session writes
hit disk but not the prompt, *"this preserves the prefix cache for the entire session."*
Keyed on `get_hermes_home()` → per profile. → 019 §3, 020 §1 stand.

## V-14 ✅ Context assembly seams exist

`agent/prompt_builder.py` (identity, platform hints, skills index, context files;
`_find_hermes_md`; `_get_context_file_max_chars(context_length)`; truncation warnings) and
`agent/turn_context.py` (`build_turn_context`, compression preflight, idle compaction,
`consume_gateway_turn_context_notes`). → 019 stands.

## V-15 ✅ The 0.3 s poll is real

`plugins/kanban/dashboard/plugin_api.py:2508` — `_EVENT_POLL_SECONDS = 0.3`, awaited per
connection. → 024 §2 stands.

## V-16 ✅ Threat scanning exists

`tools/threat_patterns.py` — `scan_for_threats(content, scope)` with scopes
`all|context|strict`, invisible/bidi unicode denylist, `MAX_SCAN_CHARS = 65_536` against
backtracking DoS. → 010 §3 stands.

## V-17 ⚠️ Cost tooling exists; the join does not

`hermes_cli/model_cost_guard.py` (pricing, `expensive_model_warning`) and `hermes insights`
(*"token usage, costs, tool patterns"*). **But** nothing joins run → ticket → project →
budget. → 012's day-1 touchpoint confirmed necessary.

## V-18 ⚠️ 20 locales in the host; our plugin ships none

`web/src/i18n/` has 20 locale files; the kanban bundle carries a `useI18n` shim.
→ ADR-016 (English-only, strings centralised) is a recorded regression, not an oversight.

---

---

# Pass 2 — checked against `reverse-eng-docs/`

The workspace already contained a reverse-engineering atlas at
`../reverse-eng-docs/` (18 documents + annotated code walkthroughs). Reading it
resolved four of the six open questions below and **overturned two decisions**.

Its own scope note applies: it describes this checkout, not every upstream release.
Re-read after rebases.

---

## V-19 ❌ ADR-009 "run our own dispatcher" → **keep upstream's**

**Asserted:** 021 GAP 6 / ADR-009 — patching the core dispatcher is expensive, so PM-OS
should run its own claiming `status='todo'`.

**Found** (`13-agent-orchestration-deep-dive.md` §1): the upstream dispatcher already does
exactly what we need, and does it well.

```sql
UPDATE tasks SET status='running', claim_lock=?, claim_expires=?
WHERE id=? AND status='ready' AND claim_lock IS NULL
```

- **It only claims `ready`.** `triage` and `todo` are already invisible to it — V-02
  again. Our finalize gate needs no dispatcher change at all.
- Success is `cur.rowcount == 1`; a lost race returns "nothing claimed" — no exception,
  no retry storm.
- Lock is `host:pid`, 15-minute TTL. Stale reclaim is deliberately conservative: it will
  not steal from a live same-host PID unless the heartbeat is stale by **an hour** — a
  guard added for incident #23025 (premature reclaim during a slow, tool-free LLM turn).
- `kanban.failure_limit` (default 2) auto-blocks after consecutive failures — 014's retry
  budget, already built.

The atlas explicitly calls this *"a real, working example of the lease/CAS pattern done
well… degrades safely by construction"* and contrasts it with the fail-open patterns
elsewhere in the codebase.

**Corrected:** ADR-009 superseded by **ADR-017**. Keep the upstream dispatcher.
Budget gating and concurrency caps move from *claim* time to **promotion** time
(`todo → ready`), which the PM already controls. That is a better place for them anyway:
refusing to promote is free, whereas refusing to claim wastes a dispatcher tick.

**Consequence:** 012 §3's "monthly cap stops claiming" becomes "stops promoting". 011 §5's
`max_concurrent_workers` is enforced by counting `running` before promoting. ADR-012's
"build no parallel path" now applies to the dispatcher too — which is where it should
have applied from the start.

---

## V-20 ❌ ADR-005 "the toolset allowlist is a closed set" → **not closed by default**

**Asserted:** ADR-005 — every "the agent must not X" is X being absent from the toolset,
and that is a guarantee.

**Found** (`04-tools-toolsets-registry.md` §2): `toolsets.get_toolset(name,
include_registry=True)` **unions the static list with `registry.get_tool_names_for_toolset(name)`
at call time** — *"this is how plugins/MCP servers add tools into an existing named
toolset without editing `toolsets.py`."*

So a named toolset is open by construction. Any installed plugin or configured MCP server
can inject a tool into a toolset our profile uses. The allowlist is a guarantee only if we
close it ourselves.

**Corrected:** ADR-005 amended. Three requirements, all cheap:

1. PM-OS profiles use **dedicated toolset names** (`pmo-orchestrator`, `pmo-worker`,
   `pmo-reviewer`, `pmo-client`) that nothing else registers into.
2. **Assert the resolved tool list at turn start**, not only in tests. If the resolved set
   differs from the expected set, refuse the turn and alert. This is the runtime version
   of the test 004 §8 already specifies.
3. Follow the `kanban_tools` precedent: that toolset has *"zero schema footprint for any
   session that isn't inside a dispatcher-spawned kanban task — it's only appended when
   `HERMES_KANBAN_TASK` is present."* Gate `pmo_tools` on an equivalent env marker.

**Consequence:** the enforcement story survives, but "absent from the profile" becomes
"absent from the *resolved* set, checked at turn start."

---

## V-21 ❌ `tasks.assignee` is a **profile name**, not a free-form handle

**Found** (`13` §1, §3): *"A kanban task's `assignee` field is a profile name; the
dispatcher spawns `hermes -p <assignee>`."*

Plans 004/005 treat `assignee` as our `@handle`. Those are different namespaces.

**Corrected:** `agent_identities` already maps `(project_id, handle) → profile`.
`pmo_assign(task, handle)` must resolve the handle and write the **profile name** into
`tasks.assignee`. The `@handle` is a project-local display and mention alias only. Add to
`REFERENCE-glossary.md` — this is exactly the profile-vs-handle confusion that page exists
to prevent, and my own plans fell into it.

---

## V-22 ⚠️ Requirement #9 is already substantially enforced

**Found** (`13` §1, §3): worker isolation is stronger than planned and already built.

- **Board is a hard boundary.** Workers are spawned with `HERMES_KANBAN_BOARD` pinned in
  the environment and *"cannot see other boards at all."*
- **Tenant** is a soft namespace within a board (workspace-path + memory-key isolation).
- The worker gets a **profile-scoped `HERMES_HOME`** — separate config, credentials,
  memory, sessions, skills — plus a pinned `TERMINAL_CWD` and `HERMES_KANBAN_WORKSPACE`.
- *"The env var is the entire binding between an OS process and a specific claimed card."*

**Consequence:** 003 §2b's "always pass an explicit board slug" is still right for *our
API layer*, but the worker process itself is already pinned. `path_allowed` (003 §2a)
remains necessary — the pinned CWD is a default, not a jail — but it is defence in depth,
not the only line. Also check `tools/path_security.py` before writing it (`04` §5 lists
it); it may already do the work.

---

## V-23 ⚠️ Cost data may already exist — check before writing

**Found** (`06-memory-context-state.md` §1): `hermes_state.py` has a
**`session_model_usage`** table — *"per-model billing rollup"* — and `sessions` carries
~45 columns including cost-accounting fields.

**Consequence:** 012's day-1 touchpoint may be a **join, not a write**. Store the session
id in `task_runs.metadata` at spawn (013 already requires this) and read
`session_model_usage`. Verify the join covers cached-input tokens before falling back to
writing our own usage block. Either way the session id must be captured at spawn — that
part is unchanged and still unrecoverable if missed.

---

## V-24 ❌ `with connect() as conn:` leaks file descriptors

**Found** (`06` §2): a real production incident
(`relatorio-issue-69678-sqlite-fd-leaks.md`). `sqlite3`'s context manager manages the
*transaction*, never `conn.close()`. In long-lived processes this leaked FDs (main DB +
WAL `-wal`/`-shm` sidecars) until `RLIMIT_NOFILE`, at which point **unrelated** operations
began failing with `[Errno 24] Too many open files`.

The postmortem **explicitly declined** to build a shared connection-lifecycle helper, so
each module reimplements `_transaction()` with `try/finally: conn.close()`. It names
SessionDB, kanban and the API server as siblings with residual instances.

**Corrected:** `hermes_cli/pmo_db.py` must ship an explicit `_transaction()` contextmanager
with `try/finally: conn.close()` from its first commit. Add to 001 §3. This is a known,
documented, recurring bug class and we would have walked straight into it — `projects_db.py`
is the module 001 says to copy from.

---

## V-25 ✅ "Prompt caching is sacred" is a stated architecture principle

**Found** (`06` §4, citing `01-architecture-overview.md`): compression is *"the one
documented exception."* `protect_first_n=3` / `protect_last_n` preserve the system prompt
and first N messages verbatim as the cached-prefix guard, and
`conversation_compression.py` **reuses the cached system-prompt string byte-for-byte**
when reloaded memory blocks are unchanged.

→ ADR-014 and 019 §2–3 stand, and are aligned with an explicit house principle rather
than merely a good idea.

---

## V-26 ✅ Memory is a plugin ABC — a better home for project knowledge

**Found** (`06` §3): `MemoryProvider` is a genuine plugin ABC (`initialize`, `prefetch`,
`sync_turn`, `on_pre_compress`, `get_tool_schemas`, `on_delegation`, `backup_paths`) with
8 backends under `plugins/memory/`. **`MemoryManager.add_provider()` appends to a list —
multiple providers can be active at once.**

**Consequence for 020:** keep the `decisions` / `knowledge` tables as the source of truth
(they need SQL, dedup, curation and a UI), but expose them to agents through a thin
**PM-OS MemoryProvider plugin** rather than hand-rolled context injection. Injection,
prefetch and compression interaction then come from the host. Revise 020 §4 accordingly.

---

## V-27 ⚠️ Registration failures are silent — assert at startup

**Found:** two fail-open behaviours that compound.

- `register_dashboard_auth_provider`: *"Misbehaving providers (wrong type, duplicate name)
  are logged at WARNING and silently ignored — never raised — so a broken plugin cannot
  crash the host."*
- `PluginManager.discover_and_load()` *"fails open on partial-load exceptions"* (`07` §7).

I quoted the first approvingly in V-01. It is the right call for the host and a **hazard
for us**: if our auth provider fails to register, the dashboard comes up with our
authorization absent and nothing says so.

`11-architectural-problems.md` Theme 6 names this class directly — *"several of the
codebase's own safety mechanisms could silently stop working and nothing would surface
that to a user by default."*

**Corrected:** add to 002 — a startup self-check that asserts (a) the `datansh` auth
provider is registered, (b) the `pmo` platform is registered, (c) the PM-OS routes carry
guards, and **refuses to serve** otherwise. Surface it in `hermes pmo doctor`. Fail
closed; the host fails open on our behalf.

---

## V-28 ⚠️ Turn lease fails open after 30 minutes

**Found** (`07` §3): `SessionTurnLeaseRegistry` uses generation-scoped ownership tokens
and *"fails **open** on timeout (30 minutes) — degrading to unserialized rather than
deadlocking."*

**Consequence:** 004's single-flight claim must be stated accurately — serialisation is
guaranteed for turns under 30 minutes, which every PM turn should be, but it is not
absolute. Combine with `resilience.max_runtime_seconds` well under 30 min (014) so the
lease never times out in practice.

---

## V-29 ✅ Precedent for cost-scoping a headless surface

**Found** (`13` §2): cron's toolset resolution *"explicitly excludes `moa`,
`homeassistant`, and `rl` toolsets by default… a direct guard against surprise cost,
referencing a previously-real incident (a $4.63 unattended cron run)"* — plus a 3-minute
hard interrupt ceiling and a cross-process tick lock.

→ Adopt for PM-OS worker profiles: exclude `moa` explicitly. MoA also has a known
non-cancellable-advisor billing leak (`02` §2). Add to 012 §4.

---

## V-30 ⚠️ Guardrail hard-stop is **off** by default

**Found** (`04` §4): `ToolCallGuardrailController` has `hard_stop_enabled=False` — it
*warns only* unless opted in. Caps like `max_subagents=50` / `max_web_searches=50` exist
but do not stop anything by default. Separately (`02` §4) there is *"no hard ceiling on
fan-out"* — only a log warning above 10 concurrent children.

**Consequence:** 014's circuit breakers are not redundant after all. Enable
`hard_stop_enabled` for PM-OS profiles and set the caps deliberately.

---

## V-31 ⚠️ Delegated children skip context files and memory

**Found** (`02` §1): `_build_child_agent` sets `skip_context_files=True, skip_memory=True`
and gives a fresh scoped prompt capped at 32,000 chars. Toolsets are *"inherited/
intersected, never expanded."*

**Consequence:** PM-OS workers must be **dispatcher-spawned, not `delegate_task`-spawned**
— otherwise they lose `.datansh/context.md` and project memory (019 §4, 020). This also
explains `_reject_delegated_child_mutation` in `tools/kanban_tools.py`. Record as
**ADR-018** so nobody "optimises" the PM into delegating directly.

---

## V-32 ⚠️ WAL failure previously disabled kanban *silently*

**Found** (`06` §5): on NFS/SMB/FUSE-mounted `HERMES_HOME`, `PRAGMA journal_mode=WAL`
raises `SQLITE_PROTOCOL`; unhandled, this **silently disabled** `/resume`, `/title`,
`/history`, session search **and kanban**, because callers swallowed `SessionDB()` init
failure into `None`. Fixed with a DELETE-mode fallback and a one-time warning.
`preflight_db_writability()` exists for the read-only-file variant.

→ 024 §4's "don't put `HERMES_HOME` on a network share" now has a citation. Add both
checks to `hermes pmo doctor`.

---

## V-33 ❌ "The registry dispatches all tools" — it does not

**Found** (`15-architecture-audit-and-open-questions.md` §3, `14` §5): there are **three
execution lanes**, and the registry is only one.

| Lane | What | Notes |
|---|---|---|
| **A — inline runtime tools** | `memory`, `todo`, `skill_manage`, `session_search`, `clarify`, `delegate_task`, context-engine ops | Recognised by `agent/tool_executor.py` *before* the registry bridge. Control-plane operations that mutate agent state, alter the next prompt, or launch children |
| **B — registry dispatch** | `ToolRegistry.dispatch()` | The lane ADR-005 assumed was the only one |
| **C — bridge / middleware** | Tool Search, MCP, plugin middleware | Scope gates apply *before* hooks/guardrails/dispatch |

The atlas states it flatly: *"Tool inventory, instrumentation, and authorization must
cover both branches."*

**Consequence — two corrections:**

1. **ADR-005's runtime assertion must enumerate all three lanes**, not just the resolved
   registry toolset. Checking only lane B would pass while `delegate_task` (lane A) is
   still reachable — which would silently break ADR-018.
2. **013's audit will have blind spots.** From `14` §5: *"an out-of-scope call can be
   rejected before a normal dispatch hook runs, so 'no dispatch log' does not necessarily
   mean the model never emitted the call."* The atlas lists this as an unresolved
   experimental question (§5 #3: *"Which tool calls are counted in usage telemetry when
   they are rejected before registry dispatch?"*). Our audit must record the **attempt**,
   not only the dispatch.

---

## V-34 ⚠️ Background review writes skills unattended — disable it for PM-OS

**Found** (`14` §9.1, `11` Theme 2): around turn completion, a forked `AIAgent` runs with
the parent's conversation snapshot, memory/skills toolsets only, persistence disabled and
dangerous-command auto-denial — and *"its prompt explicitly asks it to update skills and
memory."* The system prompt itself *"tells the agent to create or patch skills after
complex work"* (`14` §8).

There is **no default human-in-the-loop gate** (Theme 2). The curator's own prompt names
*"hundreds of narrow skills"* as an expected failure mode of the first unattended loop.

**Consequence:** for an unattended delivery fleet this is skill sprawl plus unbudgeted
token spend, per worker profile, forever. Profile isolation means it cannot leak *across*
projects (separate `HERMES_HOME` → separate skills dir), so this is a cost and quality
issue rather than a security one.

**Decision:** disable background review and curator writes on PM-OS **worker** profiles.
Consider leaving them on for the **PM** profile only, where durable procedural knowledge
about the project is genuinely wanted — and route it through 020's `decisions` /
`knowledge` tables, which are reviewable, instead of into opaque skill files. Add to
012 §4 and 015.

Also note (`14` §3): the skill-learn nudge counter increments **per tool-loop iteration,
not per user message** — one instruction that runs ten iterations can trigger it.

---

## V-35 ⚠️ Souls are executable policy with no versioning

**Found** (`15` §4 P4): *"Skill lifecycle rules, Kanban protocol, self-learning policy, and
tool-use guidance are encoded in large prompt strings. Prompt text is therefore executable
policy, but it has no schema/versioning or static compatibility check."*

Our PM and worker souls (004 §6–7) are exactly this — the four hats, the intake rule, the
finalize contract and the escalation policy are all prompt text that behaves like code.

**Consequence:** version the souls. `.datansh/agents/*.md` gets a frontmatter
`contract_version`, and `pmo doctor` warns when a soul's version predates the tool surface
it references. Cheap, and it is the difference between "the PM stopped escalating" being
diagnosable or not. Add to 004 §6.

---

## V-36 ✅ `.datansh/context.md` is threat-scanned for free

**Found** (`14` §8, and `prompt_builder._scan_context_content`): *"Context files are
threat-scanned before injection."*

**Consequence:** 010's scanning obligation is narrower than planned — context files are
covered by the host. **Thread and client messages are not**, because they arrive through
the turn rather than the prompt builder. Keep `scan_for_threats` on the message path; drop
it from the context-file path.

---

## V-37 ⚠️ Our toolsets must not be swept into a broad composite

**Found** (`17-tool-catalog-and-capability-resolution.md` §8, a security checklist that
reads as if written for us):

> *"Verify the toolset is not accidentally included in broad composites such as
> CLI/full/gateway."*
> *"Verify gateway-facing tools do not inherit local-terminal capability through an overly
> broad composite."*

**This is pointed directly at ADR-006.** Our PM runs as a *gateway* platform. Hermes has
20+ `hermes-*` per-surface composites (`hermes-gateway`, `hermes-cli`, …), and
`toolsets.py` resolves `includes` **recursively**. If `pmo-orchestrator` ends up inside
`hermes-gateway`, or if the PM profile enables a broad composite for convenience, the PM
silently gains `terminal` — and requirement #7's "no other way to communicate" quietly
becomes false, because a shell is a communication channel.

**Corrected — add to 004 and 002:**

- PM-OS toolsets are **leaf sets**: no `includes`, and nothing includes them.
- A test asserts `pmo-*` appears in no other toolset's transitive closure.
- A test asserts the PM profile's resolved set contains **no** terminal, browser, or
  messaging tool — by name, not by toolset.

Run the other six items of `17` §8 over `pmo_tools` as a checklist before shipping.

---

## V-38 ⚠️ Capability resolution has seven gates, not one

**Found** (`17` §4): a model-visible tool passes name resolution → toolset inclusion →
`check_fn` availability → provider schema exposure → executor scope → action
authorization → handler execution.

> *"This explains why the same tool can appear in a schema but still be refused at
> execution time: schema exposure and action authorization are separate decisions."*

**Consequence for ADR-005's turn-start assertion:** assert on **what the model was told**
(the provider schema, gate 4) *and* on what will actually run. Asserting only one leaves a
gap in either direction — a tool present in the schema but refused wastes turns and
confuses the agent; a tool absent from the schema but reachable by another lane is the
security case.

`17` §3 adds a third vector beyond V-20's registry union: the resolver *"explicitly
supports registry-only toolsets and **aliases**."* Our leaf-set test must be alias-proof.

**One thing in our favour** (`17` §5): the registry *"tracks plugin override policy and
caller ownership… extension code is not automatically allowed to replace or remove
arbitrary built-ins."* So another plugin cannot hijack `pmo_*` tools.

---

## V-39 ✅ Adopt the atlas's instrumentation fields wholesale

**Found** (`17` §7): a per-tool-call record far better than 013's:

```
tool_name · toolset
origin            = model | harness | plugin | child | background_review | curator
execution_lane    = inline | registry | tool_search | mcp | delegated
availability      = available | unavailable | unknown
scope_decision    = allowed | denied
approval_decision = auto | user | denied | not_applicable
started_at / completed_at
result_class      = success | tool_error | policy_error | timeout | cancelled
side_effect_class = none | filesystem | process | network | memory | skill | orchestration
```

with the rationale: *"Without these fields, a dashboard that says 'tool call failed' cannot
distinguish an invalid model name from a missing prerequisite, a child-scope refusal, a
dangerous-command denial, or a handler exception."*

`origin` and `execution_lane` are the two 013 lacked, and they are exactly the two that
make V-33's blind spot visible. **Adopted in 013 §4.**

---

## V-40 ⚠️ Injecting messages can invalidate a thinking signature → HTTP 400

**Found** (`05-providers-model-layer.md` §4): `agent/prompt_caching.py` applies up to four
`cache_control` breakpoints, and `strip_anthropic_cache_control()` exists to **re-apply**
them after a mid-turn failover (#72626) — *"caching state is treated as fragile and
re-derivable, not a stable invariant."*

More sharply: `anthropic_adapter.py` has a tracked failure mode where *"message
editing/stripping/merging invalidates an extended-thinking signature and causes an
outright HTTP 400,"* guarded by `_thinking_signature_invalidated`.

**Consequence for ADR-006:** our platform adapter injects synthetic `MessageEvent`s into a
live session. Anything that edits or merges existing messages risks this. Rules for the
adapter:

- **Append only.** Never edit, merge or strip a message already in the transcript.
- Let the gateway own history shape; we supply an event, not a transcript mutation.
- Add the extended-thinking + injected-wake case to the 017 fixture list, since it is a
  400 rather than a degraded response.

This also reinforces `14` §6: transcript shape is a reliability boundary, and the
`user;user` alternation wedge that motivated `turn_lease.py` came from exactly this class
of problem.

---

## V-41 ⚠️ Map 014's failure matrix onto `FailoverReason`

**Found** (`05` §5): `agent/error_classifier.py` already unifies vendor error shapes into
one `FailoverReason` enum — `auth`/`auth_permanent`, `billing`, `rate_limit` vs
`upstream_rate_limit` (user key vs aggregator, *different recovery paths*),
`overloaded`/`server_error`, `timeout`, `context_overflow`/`payload_too_large`,
`content_policy_blocked`, `thinking_signature`, and more. Each `ClassifiedError` carries
`retryable`, `should_compress`, `should_rotate_credential`, `should_fallback`.

014 row 6 says "provider error → fallback chain, then `blocked`" — too coarse. It should
consume the classification: `context_overflow` → compress and retry; `rate_limit` → back
off; `upstream_rate_limit` → rotate credential; `content_policy_blocked` → **do not
retry**, block the ticket for a human.

**Caveat:** Bedrock has its **own** parallel classifier and does not feed this taxonomy.
If a project routes to Bedrock, the hints are absent. Note it in 012 §4.

---

## V-42 ⚠️ Credential distribution across a profile fleet is unplanned

**Found** (`05` §3, `13` §3): credentials live in the profile's own `HERMES_HOME/.env`,
and profiles are *fully separate filesystem namespaces*. `CredentialPool` supports
multiple credentials per provider with priority and rotation, sourced from
`~/.hermes/.env` (preferred over `os.environ`) with 1Password `op://` reference
resolution.

Our design is 3–5 profiles per project (021 GAP 5). Twenty projects is ~80 profiles —
**and nothing in 003 or 023 says how a credential reaches 80 `.env` files**, or what
happens when they all rotate the same underlying key. `mark_exhausted_and_rotate()`
already marks *sibling entries sharing the same runtime token* exhausted, which helps, but
80 profiles hammering one exhausted key is precisely the shape of incident #70401
(infinite 401-retry starving the event loop, fixed with a heuristic streak counter — *"not
a structural guarantee"*).

**Corrected — add to 023 bootstrap and 024 scale:**

- `pmo project bootstrap` writes credentials by **`op://` reference**, not literal keys, so
  there is one secret and N references.
- Or a shared read-only `.env` symlinked into each profile — verify Hermes follows it.
- `pmo doctor` reports profile count and flags shared-credential fan-out above ~20.

---

## V-43 ✅ Our architecture matches the house principles

**Found** (`01-architecture-overview.md` §6), Hermes's own stated design principles:

1. *"Per-conversation prompt caching is sacred — never mutate past context mid-conversation"*
2. *"Core is the narrow waist — new capability arrives as CLI commands, skills,
   service-gated tools, plugins, MCP servers"*
3. *"Smallest footprint governs wiring — expansive at edges, conservative at the waist"*

Principle 2 is ADR-006, ADR-007 and V-01 restated by the people who built it: PM-OS is
entirely edge capability, zero waist modification. Principle 1 is ADR-014. Worth citing in
`000-CONTEXT.md` — it is the difference between "we found a clever way around the repo"
and "we did what the repo is designed for."

---

## V-44 ⚠️ Read the tests before trusting a plan

**Found** (`16-repository-topology-and-entrypoints.md` §5): `tests/` is **2,455 files —
the largest area in the repository**, larger than `agent/`, `tools/`, `gateway/` and
`hermes_cli/` combined.

> *"For future reverse-engineering passes, search tests before implementation when a
> behavior is unclear. In this repository, issue-number comments and regression names
> often explain why a seemingly unusual branch exists."*

Every wrong assertion in this file would have been caught faster this way. `VALID_STATUSES`
(V-02) is exercised by `tests/hermes_cli/test_kanban_boards.py`; the worktree default
(V-12) by `test_kanban_worktree_isolation.py`; the block taxonomy (V-04) by
`test_kanban_block_kinds.py` — a file whose *name alone* answers the question.

**Added to `SUBAGENT-EXECUTION.md` §6** as a standing instruction: when a plan asserts
something about Hermes behaviour, grep `tests/` for it **before** writing code against it.

---

## V-45 ❌ Windows encoding — the one lint rule the repo enables

**Found** (`10-tech-stack.md` §1): `ruff` has **almost everything disabled**. The single
enabled rule is `PLW1514` (unspecified-encoding), and the reason is recorded in
`pyproject.toml:352-358` — *"reaction to **three separate Windows sandbox regressions**
from locale-default text encoding."*

We are on Windows 11. Nothing in these plans says to pass `encoding=` explicitly.

**Corrected — a standing rule for every subagent:** every `open()`, `Path.read_text()`,
`Path.write_text()`, `json.dump` to a file, and `subprocess` text pipe passes
`encoding="utf-8"` explicitly. It is the one thing this repo's lint config cares about,
which means it is the one thing that has repeatedly broken. Added to
`SUBAGENT-EXECUTION.md` §4 conventions.

**Also from §1:** type checking is **`ty`** (Astral's), not mypy; Python is capped
`<3.14` (load-bearing — `pydantic-core` has no cp314 wheel). 017 §8's CI lane said
"ruff, mypy" — wrong on both counts. Corrected there.

---

## V-46 ❌ Any dependency we add must be exact-pinned

**Found** (`10` §5): *"Every direct dependency is exact-pinned (`==X.Y.Z`), never
ranged,"* a policy **tightened on 2026-05-12 in direct response to the "Mini Shai-Hulud"
worm** that compromised `mistralai==2.4.6` on PyPI. Had it been ranged, every install in
the hours before quarantine would have pulled the malicious release. CVEs are tracked
inline as pin comments.

Heavy/optional backends are deliberately **excluded from `[all]`** and lazy-installed via
`tools/lazy_deps.py`, so one quarantined upstream release cannot break every fresh
install.

**Consequence:** 002 adds `argon2-cffi`; 017 proposes `hypothesis`. Both must be exact
pins with a comment, and `hypothesis` belongs in a dev/optional extra, not the main
dependency set. Do not add a ranged pin to this repo — it violates a policy written in
response to a live supply-chain attack.

---

## V-47 ⚠️ SQLite WAL correctness depends on the *build*, not just the config

**Found** (`10` §4): the Docker image **compiles SQLite 3.53.4 from source** rather than
using Debian 13's packaged 3.46.1, because that version has *"the upstream WAL-reset
corruption bug"* (issue #70480). Custom compile flags also raise `MAX_VARIABLE_NUMBER` to
250,000, implying very large `IN (...)` batches somewhere.

Combined with V-32 (WAL silently disabled on network filesystems, which silently disabled
kanban) this means `pmo.db`'s durability has two environmental preconditions that nothing
in our plans checks.

**Corrected — add to `hermes pmo doctor` and 018 §7:**

- report `sqlite3.sqlite_version` and warn below 3.53.4;
- report the journal mode actually in effect, and warn loudly if it fell back to DELETE;
- refuse to run production with `HERMES_HOME` on a network mount.

---

## V-48 ⚠️ Windows multi-process logging needs the shared handler

**Found** (`10` §7): `hermes_logging.py` routes Windows log rotation through
`concurrent-log-handler` (cross-process lock via `portalocker`/`pywin32`) *specifically
because* stdlib `RotatingFileHandler` fails with `WinError 32` when several Hermes
processes hold one log file.

PM-OS adds processes: the dashboard, the watchers, and one per claimed ticket.
013 §7 already says "use `hermes_logging`, do not configure a second logging stack" —
now with the reason. **A subagent that reaches for `logging.FileHandler` will produce a
Windows-only failure that does not reproduce in CI.**

---

## V-49 ✅ Skills load as a *user turn*, not a prompt mutation — copy the pattern

**Found** (`03-skills-system.md` §2): a skill's full body is injected via
`_build_skill_message()` as **a user-turn message, not a system-prompt mutation** —
*"this is what keeps it compatible with the 'prompt caching is sacred' rule: loading a
skill mid-conversation never invalidates the cached prefix."*

This is exactly 019 §2's layer split, already solved upstream, and it independently
validates using `consume_gateway_turn_context_notes` for per-turn context (mention,
blocker, thread message) rather than rebuilding the system prompt. **Cite the precedent
in 019 §3** — it turns "my design principle" into "the mechanism this repo already uses."

Also worth knowing: skills are **prompt-level guidance, not callable functions**;
`skill_manage` is the only real tool in that subsystem.

---

## V-50 ⚠️ The skill-write loop is configurable — name the knobs

**Found** (`03` §4, sharpening V-34): after `agent._iters_since_skill >=
_skill_nudge_interval` (**default 10**, `agent/agent_init.py:1707-1710`, checked in
`turn_finalizer.py:634-639`) the agent forks a daemon-thread `AIAgent` restricted to
memory/skill tools, which calls `skill_manage(create|patch)`. The write-approval gate is
`tools/write_approval.py:75-86`, `cfg_get(..., default=False)` — **off**.

Provenance is the sharp edge (`03` §7 #3): whether a skill is exempt from curator sweeps
*"hinges on a thread-local flag (`is_background_review()`), not an explicit parameter…
an easy invariant to accidentally break, with a silent consequence."* And rollback is
whole-tree (`03` §7 #4) — recovering from one bad edit discards every other change since
the snapshot.

**Corrected — 004 now names the specific knobs** rather than saying "disable background
review": raise `skill_nudge_interval` or disable the fork on worker profiles, and enable
`write_approval` wherever it stays on.

**And a genuine opportunity we had not considered.** Skills have `version` frontmatter,
enforced size limits, and a fixed section order (`## When to Use` → `## Prerequisites` →
`## How to Run` → `## Quick Reference` → `## Procedure` → `## Pitfalls` →
`## Verification`). That is a *versioned prompt contract* — precisely the gap V-35 flags
in our souls. Moving the stable parts of the PM soul (the ticket body contract, the
escalation policy) into a pinned PM-OS **skill**, loaded on demand, gets versioning,
size limits and cache-safe loading for free, and shrinks the soul to identity plus
project specifics. Recorded as an option in 004 §6; not day-1 work.

Their prose convention is worth adopting regardless: *tools named in a skill must be real
Hermes tool names in backticks* — `` `search_files` `` not `grep`, `` `patch` `` not
`sed`.

> **⚠ Weakened by V-51.** Skill invocation is **harness-side slash-command interception**,
> and a dispatcher-spawned worker is launched as `hermes -p <profile> chat -q "work kanban
> task <id>"` — no slash, no interception. A PM-OS agent would have to load a skill via a
> `skill_view` **tool call**, which is a different mechanism with different cache
> properties. The option is still worth considering; it is not the free win the paragraph
> above implies.

---

## V-51 ⚠️ The atlas contradicts itself on skill visibility — doc 12 wins

**Found:** two documents disagree, and it matters for 019.

- `03-skills-system.md` §2: *"Only the (≤60-char) description enters the system prompt as
  a lightweight menu."*
- `12-tool-skill-workflow-self-learning.md` §7: *"The system prompt does **not** enumerate
  installed skills' names or descriptions anywhere in the base path."*

Doc 12 is the authority: it is the file:line runtime trace, it opens by declaring itself a
*"headline correction to the architecture-level docs,"* and it cites
`system_prompt.py:232-233` directly. Doc 03 is the architecture-level summary it corrects.

**Two things fall out, both useful:**

1. **`SKILLS_GUIDANCE` is appended only if `"skill_manage" in agent.valid_tool_names`**
   (`prompt_builder.py:189-202`). So **removing `skill_manage` from PM-OS toolsets removes
   the "save skills after complex tasks" instruction from the prompt entirely.** That is a
   cleaner knob than the ones V-50 names — it removes the *instruction*, not just the
   *capability*. Add to 004.
2. **Skill invocation is harness-side slash interception, before the LLM is called at
   all.** Our workers are spawned `chat -q "work kanban task <id>"` — no slash path. So
   V-50's "move the soul into a skill" would need a `skill_view` tool call instead.
   Weakened, not dead.

**Method note:** the atlas is authoritative over our plans, but it is not internally
uniform. Where two of its documents disagree, prefer the one with file:line citations —
and 00's own scope note says to re-run the source pass after rebases.

---

## V-52 ⚠️ Iteration budgets — real numbers

**Found** (`12` Phase 2): `IterationBudget` (`agent/iteration_budget.py:17-59`) is a
thread-safe counter, **default cap 500 for a parent agent, 50 per delegated subagent**.
On exhaustion `_turn_exit_reason = "budget_exhausted"` — unless `_budget_grace_call` is
set, which grants *exactly one* extra forced call so the model can produce a final answer
instead of dying mid-tool-call.

012 proposes `per_run_max_turns: 40`. That is well inside the 500 default, so it is our
cap doing the work, not the platform's — which is what we want, but state it: **the
platform will not save you.** Also surface `budget_exhausted` distinctly from a crash in
`task_runs.outcome`; they mean different things and only one is a bug.

The `_budget_grace_call` behaviour is worth mirroring in our own cap: cutting a worker off
mid-tool-call loses the summary that a retry needs (019 §7).

---

## V-53 ⚠️ Background review is a daemon thread that races the response

**Found** (`12` Part A): `spawn_background_review()` is a daemon thread with **no
`.join()`** — *"runs concurrently with/after delivery, NOT strictly gated behind it
despite the code comment."* `15` §3 makes the same correction: *"'Background review happens
after the response' is an unsafe simplification."*

**Consequence for 014:** add a row to the failure matrix. A background-review fork can be
writing memory/skills while the next turn is already running, and `15` §5 #1–2 list "can
two review forks mutate the same skill concurrently, and which write wins?" as *unresolved*.
If the loop stays enabled anywhere in PM-OS, treat concurrent review writes as possible and
do not build anything that assumes turn N's review completed before turn N+1 starts.

---

## Adopt the atlas's evidence grading

`15` §1 grades findings A–D. Worth applying to our own open items, because it says
plainly which ones static reading can settle:

| Grade | Meaning |
|---|---|
| A | Directly visible in source and called by the runtime |
| B | Visible, but activated by configuration or a surface-specific path |
| C | Strong inference from prompts/comments/data shape |
| D | **Requires a runtime experiment** — static reading will not settle it |

U-2 is grade A (one request). U-4, U-5, U-7, U-8 are grade **D** — they need the thing
running. Budget for that in block 1 rather than assuming a source read will answer them.

---

## Resolved from the open list

| Was | Now |
|---|---|
| U-1 bundled plugin discovery | ✅ `07` §7 — four sources in override order: bundled `plugins/<name>/`, user, project (env-gated), pip entry-point |
| U-3 bundled platform plugins | ✅ `07` §1 — `plugins/platforms/{irc,teams,google_chat,line}/` are the reference implementations |
| U-6 dispatcher reuse | ✅ Moot — V-19, we keep upstream's |
| U-5 `profile_routes` written programmatically | ⚠️ Still open |

## Still unverified — check these in the first 30 minutes

| # | Assumption | Where it matters | How to check |
|---|---|---|---|
| U-2 | Plugin HTTP routes actually pass through the dashboard auth middleware | 002 — the guard model | Hit `/api/plugins/kanban/board` with no cookie; expect 401 |
| U-4 | A plugin platform can be addressed with a synthetic `chat_id`/`thread_id` that never came from a real network event | ADR-006 | Trace `wake.py` → `adapter.handle_message` |
| U-5 | `profile_routes` can be written programmatically at bootstrap, not only hand-edited in `config.yaml` | ADR-006, ADR-007 | `gateway/profile_routing.py` config loader |
| U-7 | `session_model_usage` covers cached-input tokens | V-23, 012 | `hermes_state.py` schema |
| U-8 | A `MemoryProvider` plugin can be scoped per profile without a global registration | V-26, 020 | `agent/memory_manager.py`, `plugins/memory/mem0/` |

**U-2 is the one day-1 blocker.** One command, and if false it changes the guard model in
002.

U-4 and U-5 gate the platform-adapter work (ADR-006). If either is false, the fallback is
option 3 from 021 GAP 1: own the storage and UI, invoke the agent through the gateway's
public entry point rather than as a registered platform. The fallback is recorded here so
nobody has to rediscover it under time pressure.

---

## How to re-run this pass

1. `git fetch upstream && git diff upstream/main --stat -- hermes_cli/kanban_db.py gateway/ tools/` — read anything that moved.
2. Re-check the four ❌ rows above; they are the ones that were wrong once.
3. Re-read `../reverse-eng-docs/00-reading-index.md` §"Important scope note" — the atlas
   describes *this checkout*, and its own advice is to re-run the source-evidence pass
   after rebases, provider changes, or tool/skill migrations.
4. Update `Last reviewed` in `plugins/pmo/UPSTREAM.md` (018 §4).

**Score: 15 assertions wrong out of 53 checked.** Roughly half of those would have cost an
implementing agent between an hour and a day; two (V-02 invented statuses, V-19 a
redundant dispatcher) would have produced code that failed on first run or duplicated a
subsystem. That ratio is the argument for doing this pass before writing code, not after.

**Coverage:** all 17 `reverse-eng-docs/*.md` read. Not yet read: `code/C01–C05`
(annotated core-object, turn-loop, tool-registry and provider walkthroughs, ~117 KB) and
`presentation.md`. The annotated code docs are the natural next pass for whoever
implements 019 (turn loop) or 021 (adapter) — C02 (turn loop, 37 KB) and C03 (tool
registry and dispatch, 39 KB) bear directly on both.

**Two method notes for the next pass:**

- **The atlas is authoritative over our plans, but not internally uniform.** V-51 found two
  of its documents disagreeing. Prefer the one with file:line citations.
- **Grep `tests/` first** (V-44). At 2,455 files it is the largest area in the repo, and
  regression names often answer the question outright.
