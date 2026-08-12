# 017 — Testing Strategy

**Why this plan exists:** plans 001–016 each list unit tests, and every one of those
tests stops at the boundary where a model gets called. The interesting failures in this
system live on the other side of that boundary: the PM decomposing badly, a worker
declaring success on the wrong thing, two agents talking past each other. Nothing in the
plan set says how to test *that* without spending money and getting a different answer
each run.

**Day-1 touchpoint:** a `FakeModel` fixture and a `pmo_test_project()` factory. Every
test written after these exist gets them for free; every test written before has to be
rewritten.
**Full build:** 2 h 30 m.

---

## 1. The four layers

| Layer | What it covers | Model | Speed | Runs |
|---|---|---|---|---|
| **L1 Unit** | pure logic — authz matrix, transitions, mention parsing, path scoping | none | ms | every save |
| **L2 Integration** | store + API + guards, real SQLite, no agent | none | ~1 s | every commit |
| **L3 Scripted agent** | full loop with a **scripted** model | `FakeModel` | ~1 s | every commit |
| **L4 Live** | real models, real repo, the 008 acceptance run | real | minutes, costs money | before merge, nightly |

L3 is the layer that does not exist yet in the plans, and it is where most of this
system's behaviour lives. Almost everything currently described as "hard to test" is
straightforwardly testable at L3.

---

## 2. `FakeModel`

> **The reverse-engineering atlas independently specifies this same harness**
> (`15-architecture-audit-and-open-questions.md` §6) and its version is better than my
> first draft. Adopt theirs.
>
> **Fixture provider → AIAgent → event recorder**, with a temporary skill/memory store and
> a temporary session/kanban DB, asserting on order, policy and persistence.
>
> **Record this trace for every fixture** — it is the contract, and it is far more useful
> than asserting on a final state:
>
> ```
> provider request
>   → normalized assistant message
>   → policy decision
>   → execution lane          ← A inline / B registry / C bridge (V-33)
>   → tool result
>   → persisted history
>   → next provider request
>   → finalizer outcome
> ```
>
> `execution lane` is the field that makes V-33 testable: it is how you prove
> `delegate_task` is unreachable (ADR-018) rather than merely absent from a toolset.
>
> Their required fixture sequences: final text; one tool call then final text; parallel
> independent calls; malformed arguments; unknown tool; repeated delegation; incomplete
> reasoning; provider failure then failover; interruption during a tool call.

A test double that returns a scripted sequence of assistant turns — including tool
calls — and asserts on what it was asked.

```python
class FakeModel:
    """Deterministic model stand-in.

    Scripted turns are consumed in order. Each is either a text reply or a
    tool call. Running out of script is an error, not a silent stop — a test
    whose agent took an unexpected extra turn should fail loudly.
    """
    def __init__(self, script: list[Turn]): ...
    def calls(self) -> list[Request]: ...          # for asserting on context
    def assert_script_exhausted(self) -> None: ...
```

```python
def test_pm_decomposes_then_finalizes(pmo_project):
    model = FakeModel([
        Text("I'll break this into two tickets."),
        ToolCall("pmo_ticket_create", title="Add SSO redirect", assignee="dev-1", ...),
        ToolCall("pmo_ticket_create", title="Add SSO callback",  assignee="dev-1", ...),
        ToolCall("pmo_ticket_finalize", ids=["$1", "$2"]),
        Text("Two tickets are on the board."),
    ])
    run_pm_turn(pmo_project, model, wake=ThreadMessage("@pm add SSO"))

    model.assert_script_exhausted()
    assert board_statuses(pmo_project) == ["todo", "todo"]
```

`$1` / `$2` resolve to ids returned by earlier calls in the script — without that,
every multi-step script needs a callback and becomes unreadable.

What this buys you, all deterministically and for free:

- the PM's toolset excludes messaging tools (assert on the tool schema it was offered)
- a worker cannot call PM-only tools (script the call, assert the refusal)
- `finalize` refuses a ticket with no acceptance criteria
- blocked → `@pm` → escalate → human answer → unblock, end to end (005)
- the client-thread turn gets a restricted toolset (010)
- turn-limit and retry circuit breakers fire (014)
- context excludes internal threads in a client turn (010 §5) — assert on `model.calls()`

That last one is the pattern worth internalising: **assert on the context the model was
given**, not only on the actions it took. Most scope and leakage bugs are visible in the
prompt long before they show up in behaviour.

---

## 3. Fixtures

```python
@pytest.fixture
def pmo_project(tmp_path) -> ProjectScope:
    """A fully bootstrapped project: pmo.db + projects.db + kanban board +
    a git repo at primary_path + .datansh/project.yaml + 3 agent identities
    + a founders_office thread with a CEO, a CFO and the PM."""
```

Backed by a `tmp_path` `HERMES_HOME` so nothing touches the developer's real state — a
test suite that can corrupt `~/.hermes` will be run with hesitation, which means it will
be run less.

Companion fixtures:

- `pmo_two_projects` — for every isolation test (003, 005, 010). Cross-project tests are
  a large share of the suite; make the setup one line.
- `as_user(role)` / `as_agent(handle)` — context managers setting the principal.
- `fake_clock` — freeze and advance time, for SLA, timeouts, reclaim, quiet hours (016,
  014). Without a controllable clock those tests either sleep or are flaky; usually both.

---

## 4. Property-based tests

Three places where hand-written cases will miss the interesting inputs. `hypothesis` is
worth the dependency for these and nothing else:

| Target | Property |
|---|---|
| `path_allowed` (003) | for any generated path, allowed ⟹ the resolved path is under some scope folder. Generate `..`, symlinks, UNC, mixed separators, unicode |
| Mention parser (005) | for any text, every returned handle occurs in the text outside code fences, and no email local-part is ever returned |
| Transition table (009) | no sequence of legal transitions reaches a state with no outbound edges except the terminals |

`path_allowed` is the one that earns it. Windows path semantics have more edge cases
than anyone enumerates by hand, and this is a security control.

---

## 5. Frontend testing

The plugin is a plain IIFE with no build step (007 §1), so the usual component-test
tooling does not apply without inventing a build. Given a one-day budget, the honest
answer:

- **Do not** build a test harness for the plugin bundle on day 1. The cost exceeds the
  benefit at this size.
- **Do** put the pure logic — status→colour mapping, mention tokenisation for display,
  filter predicates, relative-time formatting — in small functions at the top of the
  bundle, and cover them from `web/`'s existing vitest setup by importing the file. The
  IIFE can expose them on `window.__PMO_TESTABLE__` under a flag.
- **Do** write the four XSS render assertions from 015 §7 as integration tests through
  the browser, since they are security controls rather than UI polish.
- When the plugin gets a real Vite build (007 P1), add component tests then.

---

## 6. The L4 live suite

The 008 acceptance scenario, automated, run nightly and before any merge to `main`.

- Real models, a scratch git repo, a fresh `HERMES_HOME`.
- Budget-capped (012) so a runaway loop in CI costs a bounded amount — set
  `monthly_cap` low enough that a bad night is annoying rather than expensive.
- **Assert on outcomes, not on wording.** "The PM created 2–5 tickets, each with
  acceptance criteria, each assigned to a roster handle" is a stable assertion. "The PM
  said 'I'll break this into two tickets'" is not, and will fail on the next model
  update for no useful reason.
- Record cost per run and trend it. A live suite that quietly triples in cost is a
  signal about the product, not just about CI.
- Quarantine, do not delete, a flaky L4 test — and treat repeated flakiness as a
  product finding. If the PM only decomposes correctly 70% of the time, that is
  information the roadmap needs, not a test to disable.

---

## 7. What to test first

Given the day-1 budget, this order:

1. `test_every_pmo_route_is_guarded` (002) — before porting routes, so it guides you.
2. `path_allowed` property tests (003) — before the scope enforcement, so the
   implementation is written against them.
3. `FakeModel` + `pmo_project` fixture — before the first agent test.
4. The five gate tests from 008 §5.
5. Everything else, as its plan lands.

Items 1 and 2 are test-first for a reason: both are security controls where the test is
easier to specify correctly than the implementation, and where writing the
implementation first tends to produce a test that agrees with the bug.

---

## 8. CI

```
lane 1  L1 + L2          every push       ~30 s
lane 2  L3 scripted      every push       ~90 s
lane 3  L4 live          nightly + pre-merge   minutes, metered
lane 4  lint + types     every push       ruff + `ty` on hermes_cli/pmo_*.py
```

**Type checker is `ty` (Astral's), not mypy** (V-45). **`hypothesis` (§4) is a dev-only
extra, exact-pinned** — ranged pins violate a policy written in response to a live PyPI
supply-chain worm (V-46).

**Reuse the repo's pytest markers** rather than inventing ours: `integration` (skipped by
default), `real_concurrent_gate`, `real_agent_prewarm`, `requires_wal`. The atlas notes
*"each marker name is itself a clue to a past bug class"* — `requires_wal` in particular
belongs on every `pmo_db` concurrency test, since WAL is assumed and silently absent on
network filesystems (V-32) and buggy below SQLite 3.53.4 (V-47).

The repo has CI config in `.github/` — extend the existing lanes rather than adding a
parallel workflow. Note the upstream `bb/ci-lockfile-python-lane` branch name in the
remote listing; there is an established Python lane to hook into.

Coverage: require it on `hermes_cli/pmo_*.py` and `tools/pmo_tools.py` only. A global
threshold on a fork of a large upstream repo produces a number that measures the fork
ratio rather than our diligence.

## P0 (day-1 touchpoint) / P1

**Day 1:** `FakeModel`, `pmo_project` / `pmo_two_projects` / `fake_clock` fixtures, and
the two test-first items (route coverage, `path_allowed`).

**P1:** property tests beyond `path_allowed`; the L4 automated acceptance suite; the
frontend logic extraction; CI lanes 3 and 4; cost trending on L4.

## Traps

- Testing agent behaviour only at L4. It is slow, costs money, and is non-deterministic
  — so it gets run rarely, and then it is not a test.
- Asserting on model wording. It will break on the next model release for no reason and
  train the team to ignore failures.
- A `FakeModel` that silently stops when the script runs out. An unexpected extra turn
  is exactly the bug you want surfaced.
- A test suite that writes to the real `~/.hermes`.
- Sleeping instead of using `fake_clock`. Every SLA test becomes slow and flaky.
