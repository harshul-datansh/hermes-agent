# 023 — Onboarding, Seed Data & Demo

**Why this plan exists:** plan 008's acceptance scenario begins with six CLI commands and
assumes a project repo already exists at `C:\work\acme`. Nothing describes how a person
who has never seen this goes from a clone to a working system — and nothing produces the
realistic data needed to demo it or to see whether the portfolio screen (022) actually
works.

An empty board is not a demo, and a system whose first-run experience is six undocumented
commands does not get adopted.

**Day-1 touchpoint:** none, but `hermes pmo demo` will save you an hour *on* day 1 by
giving the acceptance run a repeatable starting state. Build it during block 3 if
bootstrap is going smoothly.
**Full build:** 1 h 30 m.

---

## 1. Three entry paths

| Path | Who | Command |
|---|---|---|
| **Demo** | evaluating, or testing a change | `hermes pmo demo` |
| **Guided** | first real project | `hermes pmo setup` |
| **Scripted** | CI, repeat installs | `hermes pmo init` + `project bootstrap` |

The scripted path exists already (001, 003). This plan adds the other two.

---

## 2. `hermes pmo demo`

One command, under 60 seconds, fully self-contained, **no model calls**.

Creates:

- A throwaway `HERMES_HOME` under `~/.hermes-demo` — never touches real state.
- Four users: `ceo@`, `cfo@`, `cto@`, `lead@`, password `demo`, with ranks.
- A small real git repo at `<demo>/acme-portal` (a few files, real commits, a test
  script that passes) — synthetic enough to be safe, real enough that a worktree works.
- Project `acme` with `.datansh/project.yaml`, four agent identities, a founder's-office
  thread.
- **A board mid-flight**, which is the part that matters:

| Status | Count | Why |
|---|---|---|
| `draft` | 3 | one is an epic with children — shows the finalize gate |
| `todo` | 4 | one awaiting approval — shows the gate blocking |
| `in_progress` | 2 | one with a live-looking heartbeat |
| `blocked` | 1 | with a real `@pm` comment thread and an open escalation |
| `review` | 1 | with a rework in its history |
| `done` | 11 | spread over 14 days so the flow chart has a shape |
| `cancelled` | 1 | so the status is visible somewhere |

- A founder's-office thread with ~12 messages including **a pending approval escalated
  from CFO to CEO** — requirement #8's centrepiece, visible on first load.
- `task_runs` rows with plausible usage and cost (012) spread over the period, so the
  Spend screen and the portfolio's cost-per-ticket are populated.
- Three `decisions` and eight `knowledge` rows (020).

Then prints:

```
Demo ready.   http://127.0.0.1:8787/pmo
  ceo@datansh.local / demo     (rank 100 — can approve everything)
  cfo@datansh.local / demo     (rank 70  — has one approval escalated to CEO)
  lead@datansh.local / demo    (project_admin, no rank)

Try:  log in as CFO → Founder's Office → the pending approval → Escalate ↑
      log in as CEO → the same approval is now yours to decide

Reset:  hermes pmo demo --reset
```

The "Try" lines are worth writing carefully. A demo that shows a populated screen is
mildly interesting; one that tells you the single most interesting thing to click is what
gets the product understood.

### Credentials across a profile fleet (V-42)

Bootstrap has an unplanned problem. Credentials live in each profile's own
`HERMES_HOME/.env`, and profiles are fully separate filesystem namespaces (`13` §3). Our
design is 3–5 profiles per project — **twenty projects is ~80 `.env` files**, and nothing
in 003 said how a key reaches them or what happens when they all rotate the same one.

`CredentialPool` already guards part of this: `mark_exhausted_and_rotate()` marks *sibling
entries sharing the same underlying runtime token* exhausted, so one dead key does not get
re-served under a different alias. But 80 profiles hammering one exhausted key is the
shape of incident #70401 — an infinite 401-retry loop starving the event loop, fixed with
a *"defensive streak counter, not a structural guarantee."*

So `pmo project bootstrap` must not copy literal keys:

- **Preferred:** write `op://` 1Password references. `CredentialPool` resolves them
  (`05` §3), so there is one secret and N references, and rotation is one edit.
- **Otherwise:** a single shared read-only `.env` symlinked into each profile — verify
  Hermes follows the symlink before relying on it (grade D, needs a runtime check).
- **Never:** N copies of a literal key. Rotation then means editing 80 files, and the
  window where some are stale is exactly when the retry storm happens.

`pmo doctor` reports profile count and flags shared-credential fan-out above ~20.

### Seeding without model calls

All content is fixture text. `agent_identities` exist and `task_runs` are historical
rows — no agent runs. Deterministic (seeded RNG), so screenshots and tests are stable.

`--with-agents` optionally runs one real PM turn for a live demo. Off by default: a
one-command demo must not require API keys or spend money.

---

## 3. `hermes pmo setup`

Interactive, for a first real project. Follows the pattern in `hermes_cli/setup.py`.

```
1. Where does PM-OS store state?              [~/.hermes]
2. Create the first admin              email + name + password
3. Founder's office — who else?        email, name, rank (repeat, skippable)
4. First project                       name, path to an existing git repo
5. Agent roster                        [pm + dev-1 + qa]  (edit / accept)
6. Models                              [detected from existing Hermes auth]
7. Budget                              monthly cap [none]
8. Write .datansh/ into the repo?      [yes]   ← commit it, it's project config
9. Run doctor
```

Design rules:

- **Every step has a default and every step is skippable.** A setup wizard with nine
  required answers gets abandoned at step four.
- **Step 6 reads existing state.** If Hermes already has a provider configured, use it
  and say so. Asking someone to re-enter an API key they already gave Hermes reads as
  the tool not knowing itself.
- **Step 8 explains itself**: `.datansh/` is project config, it belongs in the repo, and
  it will be reviewed like any other config change.
- Ends with `doctor` output, not a success banner. The health check is the success
  banner.

---

## 4. First-run empty states

A new project has an empty board, an empty thread and no agents that have ever run. Per
`design/design.md` §5, empty states are quiet — one muted line, no illustration. But the
**first** empty state should be instructive, because there is exactly one right next
action:

| Screen | Empty copy |
|---|---|
| Board | *"No tickets yet. Ask the PM in Founder's Office — that's the only way work starts."* |
| Founder's Office | *"Start by telling @pm what you need."* + a prefilled example |
| Approvals | *"Nothing needs your decision."* |
| Escalations | *"Nothing is blocked."* |
| Portfolio | *"No projects yet."* + `hermes pmo setup` |

The Board and Founder's Office lines do real teaching: they encode requirement #2 (work
enters through the founder's office, not by creating a ticket) in the one place a new user
is looking for a "+ New ticket" button and not finding one.

---

## 5. Docs

Three files, no more:

| File | Contents |
|---|---|
| `README.md` (repo root, PM-OS section) | What it is, `hermes pmo demo`, one screenshot |
| `docs/pmo/getting-started.md` | The setup path, the first project, the first instruction |
| `docs/pmo/concepts.md` | The mental model: founder's office → PM → tickets → agents → `@` → escalation. One diagram |

`concepts.md` is the one that matters. The product has a specific shape — instructions
only from the founder's office, agents only talk on tickets, the PM finalizes — and none
of it is guessable from the UI. Someone who has not read it will try to assign a ticket
directly and conclude the product is broken.

Link `REFERENCE-glossary.md` from both.

---

## 6. Tests

```
tests/hermes_cli/test_pmo_demo.py
  test_demo_uses_isolated_hermes_home
  test_demo_makes_no_model_calls
  test_demo_is_deterministic
  test_demo_board_has_every_status
  test_demo_has_escalated_approval        # the #8 showcase
  test_demo_reset_is_clean
  test_demo_completes_under_60s

tests/hermes_cli/test_pmo_setup.py
  test_every_step_has_a_default
  test_reuses_existing_provider_config
  test_ends_with_doctor_output
```

`test_demo_makes_no_model_calls` and `test_demo_uses_isolated_hermes_home` are the two
that keep the demo safe to run casually — which is the only kind of demo that gets run.

## P0 / P1

**P0:** `hermes pmo demo` with the mid-flight board and the escalated approval;
instructive empty states on Board and Founder's Office.

**P1:** `hermes pmo setup` wizard; the three docs; `--with-agents`; screenshots.

## Traps

- A demo that needs API keys. It stops being a one-command demo.
- A demo that writes to the real `~/.hermes`.
- An all-`todo` board. It shows nothing about the product — the interesting states are
  `draft`, `blocked` and awaiting-approval.
- No historical `task_runs`. The portfolio, the flow chart and the Spend screen all read
  empty and look broken.
- A setup wizard with required steps.
- Generic empty states on Board and Founder's Office. Those two are the only place the
  product's central rule can be taught at the moment it matters.
