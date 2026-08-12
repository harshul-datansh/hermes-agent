# Subagent Execution Model

How to run these plans with several agents working in parallel, one plan each.

> **Binding scope gate:** Before accepting a plan assignment, read
> [026-hermes-compatibility-reset-PLAN.md](026-hermes-compatibility-reset-PLAN.md). Its
> 5–10% additive-change budget and Hermes capability-preservation rules override the
> file-ownership and wave instructions below.

The binding constraint is **not** plan length — it is **file ownership** and **interface
agreement**. Two subagents editing `hermes_cli/pmo_authz.py` at the same time is the
failure mode that costs a day; a subagent reading a long plan is fine.

---

## 1. The three rules

1. **One file has one owner per wave.** If two plans need the same file, they are in
   different waves, or one of them owns it and the other requests a change through the
   coordinator.
2. **Interfaces are frozen before parallel work starts.** Every cross-plan function
   signature in §4 is written as a stub — with the real signature and a
   `raise NotImplementedError` — by the coordinator, in wave 0, before any subagent
   starts. Subagents import the stub; they never invent a signature.
3. **A subagent may not edit an upstream file.** Zero upstream modifications
   (VERIFIED V-01). If a subagent believes it needs to, it stops and reports — that is a
   decision for the coordinator and an ADR, not a subagent call.

---

## 2. File ownership matrix

| Path | Owner plan | Wave | Notes |
|---|---|---|---|
| `hermes_cli/pmo_db.py` | **001** | 0 | Schema for *all* plans. Written once, up front. `_transaction()` per V-24 |
| `hermes_cli/pmo_authz.py` | **002** | 1 | Nobody else writes this file |
| `hermes_cli/pmo_scope.py` | **003** | 1 | |
| `hermes_cli/pmo_config.py` | **003** | 1 | |
| `plugins/dashboard_auth/datansh/` | **002** | 1 | |
| `hermes_cli/pmo_workflow.py` | **009** | 2 | Sole writer of `tasks.status` |
| `hermes_cli/pmo_orchestrator.py` | **004** | 2 | |
| `hermes_cli/pmo_context.py` | **019** | 2 | |
| `tools/pmo_tools.py` | **004** | 2 | 005 adds comment tools **in wave 3** |
| `hermes_cli/pmo_mentions.py` | **005** | 3 | |
| `hermes_cli/pmo_chat.py` | **006** | 3 | |
| `plugins/platforms/pmo/` | **021** | 3 | The gateway adapter (ADR-006) |
| `hermes_cli/pmo_watchers.py` | **005** | 3 | 004 specifies it; 005 writes it |
| `plugins/pmo/dashboard/plugin_api.py` | **001** → **002** → feature plans | all | **Contended — see §3** |
| `plugins/pmo/dashboard/dist/index.js` | **007** | all | **Contended — see §3** |
| `plugins/pmo/dashboard/dist/style.css` | **007** | 1 | Written first, then stable |
| `hermes_cli/subcommands/pmo.py` | **001** | 0 | Others add subcommands via a registry dict |
| `tests/**/test_pmo_<area>.py` | the plan that owns `<area>` | — | One test file per owned module |
| `plans/DECISIONS.md` | **coordinator only** | — | Subagents propose; the coordinator writes |
| `plans/VERIFIED.md` | **coordinator only** | — | Same |
| **anything not prefixed `pmo`** | **nobody** | — | Upstream. Report, do not edit |

---

## 3. The two contended files

`plugin_api.py` and `dist/index.js` are touched by nearly every feature plan. Two
strategies, pick one and hold to it:

**Preferred — split by concern.** Both files support splitting:

```
plugins/pmo/dashboard/
├── plugin_api.py          # router assembly + guards only; owner 001/002
├── api_board.py           # owner 009
├── api_chat.py            # owner 006
├── api_projects.py        # owner 003
└── api_portfolio.py       # owner 022
```

```
plugins/pmo/dashboard/dist/
├── index.js               # bootstrap + router; owner 007
├── screen_board.js        # owner 007 → 009
├── screen_chat.js         # owner 006
└── strings.js             # owner 025, written in wave 1, then append-only
```

The IIFE bundle has no build step (007 §1), so multiple files means multiple `<script>`
entries in the manifest — check the manifest supports a list before committing to this. If
it does not, fall back to:

**Fallback — serialize.** Only one subagent holds these files at a time. Others write
their handlers in their own module and hand the coordinator a 5-line registration diff to
apply. Slower, and it never conflicts.

---

## 4. Frozen interfaces (wave 0)

The coordinator writes these stubs **before** any subagent starts. They are the contract.

```python
# hermes_cli/pmo_scope.py                                          [003]
@dataclass(frozen=True)
class ProjectScope:
    project_id: str; slug: str; name: str; board_slug: str
    primary_path: Path; folders: tuple[Path, ...]; config: dict

def resolve(project_ref: str) -> ProjectScope: ...
def path_allowed(scope: ProjectScope, path: str | Path) -> bool: ...
def assert_path(scope: ProjectScope, path) -> Path: ...

# hermes_cli/pmo_authz.py                                          [002]
Principal = str          # "u_<hex>" | "agent:<project_id>/<handle>"

def can(user_id: Principal, action: str, project_id: str | None = None) -> bool: ...
def require(action: str, *, project: str | None = None): ...    # FastAPI dependency
def rank_of(user_id: str) -> int: ...

# hermes_cli/pmo_workflow.py                                       [009]
def transition(task_id: str, to: str, principal: Principal, *, scope) -> dict: ...
def validate_body(body: str) -> list[str]: ...      # [] == valid

# hermes_cli/pmo_context.py                                        [019]
def build_pm_wake_context(scope, trigger) -> "TurnNotes": ...
def build_worker_task_context(scope, task, run) -> "TurnNotes": ...

# hermes_cli/pmo_mentions.py                                       [005]
def parse(body: str) -> list[str]: ...
def resolve(scope, handles) -> tuple[list["Recipient"], list[str]]: ...
def post_comment(scope, task_id, author, body) -> int: ...

# hermes_cli/pmo_chat.py                                           [006]
def post(scope, thread_id, author, body, kind="text", ref_id=None) -> int: ...
def raise_approval(scope, **kw) -> str: ...
def decide_approval(approval_id, decision, by_user, note) -> None: ...
def escalate_approval(approval_id, to_rank, reason, by_user) -> None: ...
```

### Conventions every subagent follows

- **`encoding="utf-8"` on every file operation.** `open()`, `read_text()`, `write_text()`,
  `json.dump` to a file, `subprocess` text pipes. `PLW1514` is the *only* lint rule this
  repo enables, added after *"three separate Windows sandbox regressions from
  locale-default text encoding"* (V-45). We are on Windows 11.
- **Never `logging.FileHandler`.** Use `hermes_logging`. Stdlib rotation fails with
  `WinError 32` when several Hermes processes hold one log file — Windows-only, will not
  reproduce in CI (V-48).
- **Exact-pin any new dependency** (`==X.Y.Z`, never ranged), with a comment. Policy
  tightened after a live supply-chain worm on PyPI (V-46). Dev-only deps go in an extra.
- **Errors are structured**, using the reject-with-valid-list shape from
  `REFERENCE-api-and-tools.md` §7. Never a bare string.
- **All SQL lives in `pmo_db.py` / store modules.** No SQL in a route handler or a tool
  (024 §4 — it is what keeps the eventual Postgres port bounded).
- **Never `with connect() as conn:`** — it does not close. Use `pmo_db._transaction()`
  (V-24, a real FD-leak incident in this repo).
- **Type-check with `ty`, not mypy.** That is what this repo uses (V-45).

---

## 5. Wave schedule

```
WAVE 0  — coordinator, alone, ~1 h
  001 scaffold · pmo_db.py with full schema · stubs from §4 · 017 fixtures
  Verify U-2 (grade A) and start the grade-D experiments (U-4, U-5)
        │
        ▼
WAVE 1  — 3 subagents in parallel
  A: 002 auth + authz          B: 003 scope + config       C: 007 stylesheet + shell
     owns pmo_authz.py,           owns pmo_scope.py,          owns style.css,
     dashboard_auth/datansh/      pmo_config.py               index.js shell
        │                            │                            │
        └────────────┬───────────────┴────────────────────────────┘
                     ▼
WAVE 2  — 3 subagents; all depend on scope + authz
  D: 004 PM orchestrator       E: 009 workflow            F: 019 context
     owns pmo_orchestrator.py,    owns pmo_workflow.py       owns pmo_context.py
     tools/pmo_tools.py
                     │
                     ▼
WAVE 3  — 3 subagents
  G: 005 mentions + escalation H: 006 chat + approvals    I: 021 platform adapter
     owns pmo_mentions.py,        owns pmo_chat.py           owns plugins/platforms/pmo/
     pmo_watchers.py
                     │
                     ▼
WAVE 4  — coordinator
  Integration · 008 §4 acceptance · the five gate tests
```

Waves 1–3 are each ~1.5–2 h wall-clock with three agents. The critical path is
0 → 1B → 2D → 3H.

**Wave 3's I (the platform adapter) is the highest-risk item** — it depends on grade-D
experiments U-4 and U-5. Start those in wave 0. If either fails, fall back to 021 GAP 1
option 3 (own the storage, invoke through the gateway's public entry point) and tell agent
H before it starts, because it changes `pmo_chat.post()`.

---

## 6. Subagent brief template

Every spawn gets exactly this. Nothing else — a subagent that reads all 26 plans has
burned its context before writing a line.

```markdown
## Your task
Implement plan <NNN>. You own it end to end, including its tests.

## Read, in this order — and nothing else
1. plans/<NNN>-<name>-PLAN.md          ← your plan
2. plans/VERIFIED.md                    ← 8 of our assertions were wrong; yours may be
3. plans/SUBAGENT-EXECUTION.md §4       ← frozen interfaces you must not change
4. plans/REFERENCE-glossary.md          ← profile ≠ handle ≠ slug ≠ user id

If your plan cites a section of another plan, read that section only.

## Before you trust any claim your plan makes about Hermes: grep the tests

`tests/` is **2,455 files — the largest area in the repository**, larger than `agent/`,
`tools/`, `gateway/` and `hermes_cli/` combined. The reverse-engineering atlas
(`16` §5) is explicit: *"search tests before implementation when a behavior is unclear.
Issue-number comments and regression names often explain why a seemingly unusual branch
exists."*

Twelve assertions in these plans were wrong. Every one would have been caught faster this
way — `test_kanban_block_kinds.py` answers V-04 by its **filename**.

```bash
ls tests/hermes_cli/ | grep -i <thing>
grep -rn "<constant_or_function>" tests/ | head -20
```

Do this for every behaviour your plan asserts, before you write code against it.
Report what you find — that is deliverable #2 below.

## Files you own (create/edit freely)
<from §2>

## Files you must NOT touch
- Anything without a `pmo` prefix. That is upstream; zero modifications (V-01).
- Any file owned by another plan in §2.
- plans/DECISIONS.md and plans/VERIFIED.md — propose, don't write.

## Interfaces
The stubs in SUBAGENT-EXECUTION.md §4 are frozen. Implement the ones you own;
import the others. If a signature is wrong, STOP and report — do not change it.

## Done means
- <the plan's "Definition of done" line>
- Your tests pass: `python -m pytest tests/**/test_pmo_<area>.py -q`
- `hermes pmo doctor` is no worse than when you started
- `git diff --stat -- plugins/kanban` is empty
- Every P0 item in your plan's "P0 / P1" section is complete

## Report back
1. What you built, and anything you cut
2. **Any assertion in your plan that turned out wrong** — the single most valuable
   thing you can return. Cite the source file and line.
3. Interfaces you needed that §4 does not have
4. Anything you noticed that belongs in another plan
```

---

## 7. Coordinator loop

Between waves:

1. **Collect the wrong-assertion reports.** Add them to `VERIFIED.md` as `V-NN`. This is
   the highest-value output of a wave — pass 2 of that file overturned two ADRs.
2. **Update `DECISIONS.md`** if anything was overturned. Supersede, never edit (ADR-015).
3. **Run the boundary checks:**
   ```bash
   git diff --stat -- plugins/kanban          # must be empty
   python -m pytest tests/ -k pmo -q
   hermes pmo doctor
   ```
4. **Re-freeze §4** if a signature changed, and tell every subagent in the next wave.
5. **Brief the next wave** with the template, amended by what wave N learned.

Do not let a wave start while the previous wave's reports are unread. A wrong assertion
found in wave 2 and not propagated becomes three subagents building on it in wave 3.

---

## 8. Conflict resolution

| Situation | Rule |
|---|---|
| Two subagents need the same file | Different waves, or one owns it and the other sends a diff to the coordinator |
| A subagent wants a new interface | It reports; the coordinator adds it to §4 and rebroadcasts. **Never** invent a signature |
| A subagent believes it must edit an upstream file | Stop. Coordinator decision + an ADR. Zero is the budget |
| A subagent's plan contradicts `VERIFIED.md` | `VERIFIED.md` wins. It is grounded in source; the plans are not |
| A plan contradicts `../reverse-eng-docs/` | The atlas wins, for the same reason |
| Two plans disagree | Higher plan number wins if it explicitly amends the lower (021 amends 002/004/006/009/010); otherwise coordinator decides and records an ADR |

---

## 9. Adapting the schedule

Not every plan is wave-shaped. Rough guide:

| Plan | Parallelism | Note |
|---|---|---|
| 001, 008 | coordinator only | Scaffold and integration |
| 002, 003, 004, 005, 006, 009, 019, 021 | one subagent each | Clean file ownership |
| 007, 025 | one subagent, spans waves | Owns the frontend throughout |
| 010, 011, 012, 013, 014, 015, 016, 020, 022, 023, 024 | one each, any order after their deps | Hardening track |
| 017, 018 | **coordinator, first** | They change how everything else is built and merged |

Post-day-1 order is in `README.md`. `017` and `018` before more features — testing and
merge discipline are what make parallel subagent work safe rather than merely fast.

---

## 10. What to do when a subagent finishes early

Not "pick up the next plan." Give it one of these, in order:

1. **Verify an open item from `VERIFIED.md`.** The grade-D ones need a running system, and
   a subagent that just built the thing is well placed to run the experiment.
2. **Write the black-box fixture for its own surface** (017 §2, `15` §6 trace format).
   The atlas's completion criterion (`15` §7) asks every surface for an entry point, a
   call graph, a tool inventory, a state map, an authorization path, a failure path, a
   concurrency model, and one executable fixture. PM-OS is a new surface and should meet
   the same bar.
3. **Re-read its plan's Traps section against what it actually built.** Traps were written
   before the code existed; half of them will now be checkable.
