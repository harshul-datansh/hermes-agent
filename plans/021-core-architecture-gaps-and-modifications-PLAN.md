# 021 — Core Architecture Gaps & Required Hermes Modifications ⭐

**Read this before 004, 006 and 010. It amends all three.**

**Why this plan exists:** plans 001–020 describe what to build assuming Hermes can host
it. Several things we need are **not things Hermes does**, and each one is a fork in the
road: work around it, extend through a supported seam, or modify core and pay for it on
every upstream merge forever (018).

The founder's-office group chat is the clearest case. Hermes has 1:1 agent sessions and
platform bridges. It does not have "a durable conversation with several humans and an
agent that wakes on events." Plan 006 quietly assumed we'd build that ourselves. We
should not — and this plan explains what to do instead.

**Time budget:** the decisions cost nothing; the platform adapter is 2 h and replaces
roughly 3 h of bespoke work in 006.

---

## 1. How to read this

For each gap:

| Field | Meaning |
|---|---|
| **Hermes does** | What exists today, verified in this checkout |
| **We need** | The product requirement |
| **Options** | The real choices, not a survey |
| **Decision** | What to do, and why |
| **Core cost** | Lines of upstream code we must touch (018 §2 budget: three) |

---

## GAP 1 — Multi-participant conversation with an agent ⭐

> *"a dashboard + chat for conversation between project manager and founder's office.
> Can be a group chat"* — requirement #8

**Hermes does:**
- 1:1 agent sessions (dashboard `/chat`, CLI, TUI).
- Platform bridges: WhatsApp, Signal, Discord, Slack, weixin, etc., where **one agent
  participates in an external group chat**. `gateway/platforms/base.py` `BasePlatformAdapter`
  is ~6,700 lines of handled edge cases — typing indicators, message splitting, mention
  parsing, ephemeral messages, media, session binding.
- `gateway/profile_routing.py` — hierarchical routing of `platform + chat_id + thread_id`
  → profile, with specificity scoring.
- `gateway/turn_lease.py` — serialises turns per resolved session id.
- `gateway/wake.py` — wakes an existing session from a background event.

**We need:** a durable, in-product thread with several humans and the PM agent, where the
PM's turns are triggered by events (a message, a mention, a blocker) rather than by
someone holding a chat window open.

**Options:**

1. **Build a parallel conversation runtime inside the plugin** — own `messages` table,
   own agent invocation, own concurrency control. *This is what plan 006 implied.*
2. **Register PM-OS as a Hermes gateway platform.** Our threads become "chats" on a
   platform named `pmo`; the PM is routed to them by `profile_routes`; gateway does
   session binding, turn leasing, delivery, and wake.
3. Hybrid — own the storage and UI, call gateway for turn execution.

**Decision: option 2.**

`gateway/platforms/ADDING_A_PLATFORM.md` states the plugin path plainly:

> *"The adapter inherits from `BasePlatformAdapter` and registers via
> `ctx.register_platform()` in the `register(ctx)` entry point. This requires **zero
> changes to core Hermes code**."*

and lists what you inherit: *"adapter creation, config parsing, user authorization, cron
delivery, send_message routing, system prompt hints, status display, gateway setup."*

Option 1 means reimplementing `turn_lease.py` — whose docstring is a 20-line account of a
concurrency bug (#64934) involving interleaved transcript flushes and a permanent
`user;user` alternation wedge. That is not a bug we want to rediscover.

### The realisation that makes this clean

**We never need multiple agents in one conversation.** Requirement #7 says agents talk
only through task comments. So the founder's-office thread is *N humans + exactly one
agent (the PM)*. That is precisely the shape `profile_routing` already supports:
`platform=pmo, chat_id=<project>, thread_id=<thread>` → profile `pm-acme`.

The constraint that looked limiting is the one that makes the whole thing fit.

### Architecture

```
Dashboard composer (human types "@pm add SSO")
   │  POST /api/plugins/pmo/threads/{tid}/messages     [guarded, 002]
   ▼
pmo_chat.post()  ──► INSERT messages (durable, ours — the record)
   │
   ├─ mentions @pm ? ──no──► done. Humans talking to humans.
   │                   yes
   ▼
PmoPlatformAdapter.handle_message(MessageEvent(
      platform="pmo", chat_id=project_id, thread_id=thread_id,
      chat_type="group", user_id=<user>, text=<body>))
   │
   ▼  gateway
profile_routing  →  pm-acme
turn_lease       →  serialised per session          ← solves 004 §3 single-flight
session binding  →  durable transcript
   │
   ▼
agent turn runs  (context from 019, tools from 004 §5)
   │
   ▼
PmoPlatformAdapter.send(chat_id, thread_id, text)
   │
   ▼
INSERT messages (author='agent:acme/pm')  ──► WebSocket ──► UI
```

Our `messages` table stays the product record and the UI's source. The gateway session is
the *agent's* memory of the conversation. Both exist; they are not duplicates, they are
two views with different lifetimes — ours is permanent and auditable, the session's is
compressible and disposable.

**Blocker and mention wakes** (005) use `gateway/wake.py` on the same adapter. Set
`supports_async_delivery = True` so we get the simple synthetic-`MessageEvent` path —
`wake.py`'s docstring names "plugin platforms" as push-capable. The self-POST workaround
it describes exists for the stateless API-server adapter and we should not inherit it.

### Adapter placement

`plugins/platforms/pmo/` — `plugin.yaml` + `adapter.py`, per the guide's bundled-plugin
path. Reference implementations to read first: `plugins/platforms/{line,irc,teams,google_chat}/`.
Separate from `plugins/pmo/` (the dashboard plugin) because they are different extension
points with different lifecycles.

### Two hard constraints on the adapter

**1. Append only — never edit, merge or strip a message (V-40).** `anthropic_adapter.py`
has a tracked failure mode where *"message editing/stripping/merging invalidates an
extended-thinking signature and causes an outright HTTP 400"*
(`_thinking_signature_invalidated`). And `prompt_caching.py`'s markers are re-applied after
failover because *"caching state is treated as fragile and re-derivable, not a stable
invariant."*

We supply an **event**; the gateway owns transcript shape. The `user;user` alternation
wedge that motivated `turn_lease.py` came from exactly this class of problem
(`14` §6: transcript shape is a reliability boundary). Add the
extended-thinking-plus-injected-wake case to the 017 fixtures — it fails as a 400, not as
a degraded response, so it will look like an outage.

**2. Do not let `pmo-*` toolsets into a gateway composite (V-37).** `17` §8's checklist
says it directly: *"Verify gateway-facing tools do not inherit local-terminal capability
through an overly broad composite."* Our PM **is** gateway-facing. If `pmo-orchestrator`
lands inside `hermes-gateway`, or the PM profile enables a broad composite for
convenience, the PM gains `terminal` — and requirement #7's "no other way to communicate"
is false, because a shell is a channel. PM-OS toolsets are **leaf sets**: no `includes`,
and nothing includes them. Test the transitive closure.

**Core cost: zero.**

---

## GAP 2 — Per-turn toolset variation

**Hermes does:** toolsets are per-**profile** (`toolsets.py`, `toolset_distributions.py`,
`hermes_cli/tools_config.py`). A profile has one tool allowlist.

**We need (010 §1):** the PM holds full orchestrator tools on a founder's-office turn, and
a *restricted* set (`pmo_thread_post` + `pmo_approval_request` only) on a client-thread
turn. That restriction is the enforcement boundary for untrusted client input — it cannot
be a prompt instruction.

**Options:**

1. Add per-turn toolset override to the agent runtime. **Core modification**, and a deep
   one — the toolset is bound at session construction.
2. **Two profiles, one agent.** `pm-acme` (full) and `pm-acme-client` (restricted), routed
   by `profile_routes` on `thread_id`.
3. Runtime guard inside each tool that checks the calling context. Enforcement inside the
   thing being enforced.

**Decision: option 2.**

`profile_routing` already routes at thread granularity with specificity 14 — the most
specific match. A client thread routes to the restricted profile; everything else falls
through to the full one. The restriction becomes a *configuration* fact rather than a
runtime check, which is exactly the property 010 needs: the tool is not in the profile,
so no prompt can reach it.

Cost: `pmo project bootstrap` creates two profiles instead of one, and the two share a
soul file with a mode header. Cheap.

Option 3 is worth naming as rejected: a guard inside `pmo_ticket_create` that refuses when
the turn came from a client thread is enforcement *inside* the boundary rather than at it,
and it fails open on any tool added later.

**Core cost: zero.**

---

## GAP 3 — Structured messages (approval cards) in a conversation

**Hermes does:** messages are text + media. Some adapters have richer affordances (the
guide mentions LINE Template Buttons), but there is no cross-platform structured-message
type.

**We need:** an approval card rendered inline in the thread with Approve / Reject /
Escalate ↑ buttons (006 §5).

**Decision: keep it out of the message body entirely.**

`messages.kind='approval_ref'` with `ref_id=<approval_id>` — already in the 001 schema.
The **UI** joins `approvals` and renders the card. The **agent** sees a plain-text
rendering in its context ("Approval ap_3f21 raised: … Needs: CFO"). Two renderings of one
row.

This is why the approval lives in its own table rather than as a message payload: humans
need an interactive card, the agent needs prose, and the audit needs a typed record. A
JSON blob inside a message body serves none of the three well.

**Core cost: zero.**

---

## GAP 4 — Multi-user authorization

**Hermes does:** pluggable **authentication** (`hermes_cli/dashboard_auth/`), and — in the
kanban plugin's own words — *"The auth still isn't multi-user — anyone who can read the
printed URL+token gets full dashboard access."*

**We need:** per-project roles and an org rank hierarchy (002, 006).

**Decision: build authorization for PM-OS routes only. Do not attempt to make core Hermes
dashboard pages multi-user.**

This is an architectural boundary worth stating explicitly, because it is easy to drift
across:

> **Core Hermes dashboard pages (Config, Env, Skills, MCP, Profiles, Sessions, Logs,
> Files, Plugins) are single-tenant admin surfaces. PM-OS is the multi-user surface.**

A CFO with a PM-OS login must not reach `/env` or `/files` — those pages expose the whole
machine. So deployment must gate the core pages to admins, either by running PM-OS on a
dashboard whose non-PM-OS tabs are hidden for non-admins, or by fronting it with a reverse
proxy that only exposes `/pmo` and `/api/plugins/pmo/*` to non-admin sessions.

**Ship the proxy rule on day 1 if the dashboard is reachable by more than one person.**
Guarding our own routes while leaving `/api/env` open to any authenticated session is not
a partial win; it is the same hole with more steps.

**Core cost: one line** — registering the auth provider (018 §2 budget).

---

## GAP 5 — Project as the primary scope

**Hermes does:** scope is the **profile** (`HERMES_HOME`, `HERMES_PROFILE`). Config,
memory, channel directory, and the kanban "active board" are all profile-global.
`projects.db` exists but is a lightweight association, not an isolation boundary.

**We need:** project as the primary scope for config, board, folders, agents and chat
(#3, #9, #10).

**Options:**

1. Make Hermes project-scoped. Deep core modification; effectively a fork.
2. **Map project → profile-set, and never touch global state.**

**Decision: option 2**, with three standing rules:

- **Never read "active" anything.** No active board, no active profile, no active project.
  Every call passes explicit scope (003 §2b). Grep for `connect_closing()` without
  `board=` as a build step.
- **One profile per (project, agent role).** `pm-acme`, `pm-acme-client`, `acme-dev-1`,
  `acme-qa`. Profile-global state then *becomes* project-scoped for free — including
  memory (020 §1), which is why per-agent memory needed no work.
- **Project config is a third layer**, never written back to `~/.hermes/config.yaml`
  (003 §3). The upstream kanban plugin's `PUT /orchestration` writes global config; our
  fork must not.

The residual mismatch: a Hermes install running twenty projects has ~60 profiles. That is
a scale question, not a correctness one — 024 sizes it.

**Core cost: zero.**

---

## GAP 6 — Dispatch gating on `draft`

**Hermes does:** the kanban dispatcher claims tasks by status. `draft` is not a concept.

**We need:** `draft` tickets invisible to the dispatcher until the PM finalizes (#5, 009).

**Options:**

1. Patch the core dispatcher's claim query. **Core modification** to `hermes_cli/kanban*.py`
   — the files most likely to change upstream.
2. **Run our own dispatcher** in `plugins/pmo/`, claiming only `status='todo'` on our
   boards.

**Decision: option 2.** The claim logic is small; the merge cost of touching `kanban_db.py`
is not. It also lets us apply budget gating at claim time (012 §3) and the
`max_concurrent_workers` cap (011 §5) without pushing PM-OS concepts into upstream code.

Reuse `kanban_db`'s claim-lock primitives — do not reimplement locking (014 §1).

**Core cost: zero.**

---

## GAP 7 — Approvals gating an agent action

**Hermes does:** `hermes_cli/approval_mode.py` and `approvals_suggest.py` gate *tool
calls* interactively (the human-in-the-loop confirm-before-acting pattern). That is a
different thing from an org-hierarchy sign-off on a work item.

**We need:** a ticket that cannot be finalized until someone of sufficient rank approves
(006 §3).

**Decision: keep them separate and say so.** PM-OS approvals gate **ticket transitions**,
in our tables, checked in `pmo_ticket_finalize`. Hermes approval-mode gates **tool calls**
and stays available for its own purpose. Do not attempt to express one in the other; the
lifetimes (seconds vs days) and the deciders (the operator vs the CEO) have nothing in
common.

**Core cost: zero.**

---

## GAP 8 — i18n

**Hermes does:** 20 locales in `web/src/i18n/`, and the kanban plugin bundle carries a
`useI18n` shim with an English fallback.

**We need:** a decision, not a feature.

**Decision: English-only on day 1, with strings centralised at the top of the bundle** so
extraction is mechanical later. Record it here rather than discovering in month two that
the product regressed a capability the host has. See 025.

**Core cost: zero.**

---

## 2. Summary

| # | Gap | Decision | Core cost |
|---|---|---|---|
| 1 | Group chat with an agent | Register PM-OS as a **gateway platform plugin** | 0 |
| 2 | Per-turn toolset | **Two profiles**, routed by thread | 0 |
| 3 | Approval cards | `kind='approval_ref'` + UI join; prose for the agent | 0 |
| 4 | Multi-user authz | Ours only; **core pages stay admin-only** | 0 |
| 5 | Project scope | Project → profile-set; never read "active" state | 0 |
| 6 | Draft gating | Our own dispatcher — but upstream already hides `triage` | 0 |
| 7 | Approvals | Separate concept from Hermes approval-mode | 0 |
| 8 | i18n | English-only, strings centralised | 0 |

**Total upstream modification: zero.**

> **Revised 2026-08-05.** This plan first estimated one line, for registering the auth
> provider. Verification found `PluginContext` exposes all three registrations we need:
>
> ```python
> ctx.register_platform(name, label, adapter_factory, check_fn, ...)   # GAP 1
> ctx.register_dashboard_auth_provider(provider)                        # GAP 4
> ctx.register_cli_command(name, help, setup_fn, handler_fn)            # `hermes pmo ...`
> ```
>
> So the auth provider and the `hermes pmo` subcommand both register through the plugin
> context. **No upstream file is touched at all.** 018's three-touchpoint budget is
> unspent; keep it that way. See `VERIFIED.md` V-01.

That is the headline result of this plan: **everything requirement #8 needs is reachable
through supported extension points, with no upstream modification whatsoever.** The
instinct that a multi-human/agent group chat must mean forking Hermes was right about the
requirement and wrong about the repo.

---

## 3. What this amends

| Plan | Amendment |
|---|---|
| **004 §3** | Single-flight per project is `turn_lease.py`, not a new lock. Wake is `gateway/wake.py`, not a bespoke watcher for thread messages. The mention/blocker watchers (005) still tail our tables, but they *deliver* through the adapter. |
| **006 §1, §4** | Threads are a gateway platform. `pmo_chat.post()` writes `messages` **and** emits a `MessageEvent` when `@pm` is present. Live updates still ours (WebSocket over our tables). |
| **006 §5** | Approval cards render from `approvals`, referenced by `kind='approval_ref'`. Unchanged, but now the reason is written down. |
| **010 §1** | The restricted client toolset is a **second profile** (`pm-acme-client`) selected by `profile_routes`, not a runtime toolset override. |
| **009 §1** | The dispatcher that honours `draft` is ours, in `plugins/pmo/`. |
| **002 §6** | Add the deployment rule: core dashboard pages are admin-only. |

---

## 4. Tests

```
tests/plugins/test_pmo_platform_adapter.py
  test_adapter_registers_via_plugin_context
  test_supports_async_delivery_is_true
  test_at_pm_message_emits_message_event
  test_plain_human_message_emits_nothing
  test_agent_reply_persists_to_messages_table
  test_profile_route_resolves_thread_to_pm_profile
  test_client_thread_routes_to_restricted_profile     # GAP 2
  test_concurrent_posts_serialised_by_turn_lease      # GAP 1

tests/test_pmo_core_boundary.py
  test_upstream_touchpoints_are_one_line_each         # GAP 4, 018 §2
  test_no_active_board_or_profile_reads               # GAP 5
  test_pmo_dispatcher_claims_only_todo                # GAP 6
```

`test_no_active_board_or_profile_reads` is a source grep asserted in CI. GAP 5's rule is
the kind that erodes one convenient call at a time.

---

## 5. Traps

- Building a conversation runtime. `turn_lease.py`'s docstring is a warning written in
  the blood of bug #64934. Read it before deciding you can do better.
- Setting `supports_async_delivery = False` by copy-paste from `api_server.py`. That
  drops you onto the self-POST path, which exists for a stateless adapter and is wrong
  for a push-capable one.
- Letting a non-admin PM-OS user reach `/env`, `/files` or `/config`. Guarding our routes
  while leaving core routes open is the same hole with more steps.
- Reading "active board" or "active profile" anywhere. It is the single-tenant assumption
  leaking back in.
- Patching `hermes_cli/kanban_db.py` for `draft`. It is the file most likely to move
  upstream and the one with `DROP TABLE`-on-drift machinery.
- Expressing PM-OS approvals in Hermes approval-mode. Different lifetime, different
  decider, different table.
