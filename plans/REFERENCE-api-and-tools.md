# Reference — API Surface & Agent Tools

Everything PM-OS exposes, in one place. Assembled from plans 002–016 so an implementer
can see the whole surface without reading nine documents.

All HTTP routes mount at `/api/plugins/pmo/`. **Every route carries a
`require(...)` guard** (002 §2d); the coverage test (002 §2e) fails the build otherwise.
`/health` is the only public path.

Legend: 🆕 new in PM-OS · ♻️ inherited from the kanban fork · ⏳ P1

---

## 1. HTTP — board & tickets

| Method | Path | Capability | Plan |
|---|---|---|---|
| ♻️ GET | `/board?project_id=` | `task.read` | 003 |
| ♻️ GET | `/tasks/{id}` | `task.read` | |
| ♻️ POST | `/tasks` | `task.write` | 009 |
| ♻️ PATCH | `/tasks/{id}` | `task.write` | |
| ♻️ DELETE | `/tasks/{id}` | `task.delete` | |
| ♻️ POST | `/tasks/bulk` | `task.write` | |
| 🆕 POST | `/tasks/{id}/transition` | per `TRANSITIONS` table | 009 §1 |
| 🆕 POST | `/tasks/finalize` | `board.dispatch` | 004 §4 |
| 🆕 POST | `/tasks/{id}/review/reject` | `task.transition` | 009 §4 |
| ♻️ POST | `/tasks/{id}/comments` | `comment.write` | 005 |
| ♻️ GET/POST/DELETE | `/links` | `task.write` | |
| ♻️ POST | `/tasks/{id}/decompose` | `board.dispatch` | 004 |
| ♻️ POST | `/tasks/{id}/specify` | `board.dispatch` | |
| ♻️ POST | `/tasks/{id}/reassign` | `board.dispatch` | |
| ♻️ POST | `/tasks/{id}/reclaim` | `board.dispatch` | 014 |
| 🆕 GET | `/tasks/{id}/timeline` | `task.read` | 013 §2 |
| ♻️ GET/POST | `/tasks/{id}/attachments` | `task.read` / `task.write` | 015 §3 |
| ♻️ GET/DELETE | `/attachments/{id}` | `task.read` / `task.delete` | |
| ♻️ GET | `/stats`, `/assignees`, `/diagnostics` | `task.read` | |
| ♻️ GET | `/workers/active` | `task.read` | 013 §5 |
| ♻️ GET | `/runs/{id}`, `/runs/{id}/inspect` | `task.read` | |
| 🆕 GET | `/runs/{id}/transcript` | `task.read` (redacted) | 013 §3 |
| ♻️ POST | `/runs/{id}/terminate` | `board.dispatch` | |
| ♻️ POST | `/dispatch` | `board.dispatch` | |

## 2. HTTP — projects, members, config

| Method | Path | Capability | Plan |
|---|---|---|---|
| 🆕 GET | `/projects` | (membership-filtered) | 003 |
| 🆕 POST | `/projects` | `org.manage` | |
| 🆕 GET | `/projects/{pid}` | `project.read` | |
| 🆕 GET/PUT | `/projects/{pid}/orchestration` | `config.read` / `config.write` | 003 §3 |
| 🆕 GET/PUT | `/projects/{pid}/config` | `config.read` / `config.write` | 003 |
| 🆕 GET | `/projects/{pid}/members` | `member.read` | 002 §6 |
| 🆕 POST/PATCH/DELETE | `/projects/{pid}/members[/{uid}]` | `member.write` | |
| 🆕 PATCH | `/users/{uid}/org-role` | `org.manage` (CEO) | 002 |
| 🆕 GET | `/projects/{pid}/agents` | `project.read` | 004 |
| 🆕 PATCH | `/projects/{pid}/agents/{handle}` | `agent.manage` | 014 §3 |
| ⏳🆕 POST | `/projects/{pid}/export` | `project_admin` | 015 §5 |
| ⏳🆕 DELETE | `/projects/{pid}` | `org.manage` + typed slug | 015 §5 |

## 3. HTTP — chat, approvals, escalations

| Method | Path | Capability | Plan |
|---|---|---|---|
| 🆕 GET | `/projects/{pid}/threads` | `thread.read` | 006 |
| 🆕 GET | `/threads/{tid}/messages?after=&limit=` | `thread.read` + membership | |
| 🆕 POST | `/threads/{tid}/messages` | `thread.post` + membership | |
| 🆕 GET | `/projects/{pid}/approvals?status=` | `thread.read` | 006 §3 |
| 🆕 POST | `/projects/{pid}/approvals` | `approval.request` | |
| 🆕 POST | `/approvals/{aid}/decide` | rank ≥ `required_rank`, **re-checked at decision time** | |
| 🆕 POST | `/approvals/{aid}/escalate` | `approval.escalate`, up-only | |
| 🆕 POST | `/approvals/{aid}/withdraw` | requester or `project_admin` | |
| 🆕 GET | `/projects/{pid}/escalations` | `project.read` | 005 §6 |
| 🆕 POST | `/escalations/{eid}/resolve` | rank ≥ 50 | |
| ⏳🆕 GET/POST | `/threads/{tid}/client-messages` | `thread.read` (client kind) | 010 |

## 4. HTTP — cross-cutting

| Method | Path | Capability | Plan |
|---|---|---|---|
| 🆕 GET | `/notifications?unread=` | self only | 016 |
| 🆕 POST | `/notifications/read` | self only | |
| ⏳🆕 GET | `/projects/{pid}/spend` | rank ≥ 50 | 012 §5 |
| ⏳🆕 GET | `/projects/{pid}/audit` | `project_admin` or rank ≥ 70 | 013 §4 |
| ⏳🆕 POST | `/projects/{pid}/pause` / `/resume` | `project_admin` | 014 §5 |
| 🆕 GET | `/health` | **public** | 002 §2d |
| 🆕 WS | `/events?project_id=` | membership checked on subscribe | 006 §4 |

The WebSocket multiplexes `task_events`, thread messages, approval changes and
notifications on one socket. **Subscribe is membership-checked** — 006's most
consequential trap.

---

## 5. Agent tools

Every tool takes its project from the run context, never from an argument (004 §5).
An agent cannot name another project.

### Available to all agents

| Tool | Capability | Plan |
|---|---|---|
| `pmo_task_show(task_id)` | `task.read` | |
| `pmo_task_list(status=, assignee=)` | `task.read` | |
| `pmo_comment(task_id, body)` | `comment.write` — **the only emit path** | 005 |
| `pmo_block(task_id, reason)` | agent | 005 §6 |
| `pmo_complete(task_id, summary)` | agent | 009 |
| `pmo_heartbeat(task_id)` | agent | 014 |
| `pmo_attach(task_id, path)` | `task.write`, path scope-checked | 003, 015 |
| `pmo_attachments(task_id)` | `task.read` | |

### PM only

| Tool | Capability | Plan |
|---|---|---|
| `pmo_ticket_create(title, body, assignee, priority, labels, touches, idem_key)` | `task.write` | 009, 011 |
| `pmo_ticket_update(id, ...)` | `task.write` (draft only) | |
| `pmo_ticket_decompose(id)` | `board.dispatch` | 004 |
| `pmo_ticket_finalize(ids)` | `board.dispatch` + 4 checks | 004 §4 |
| `pmo_assign(id, handle)` | `board.dispatch` | |
| `pmo_unblock(id, resolution)` | `board.dispatch` | 005 |
| `pmo_escalate(task_id, reason)` | PM | 005 §6 |
| `pmo_thread_post(thread_id, body)` | `thread.post` | 006 |
| `pmo_approval_request(title, detail, required_rank, task_id)` | `approval.request` | 006 |

### Reviewer only (`qa` handle)

| Tool | Capability | Plan |
|---|---|---|
| `pmo_review_accept(task_id, note)` | `task.transition` | 009 §1 |
| `pmo_review_reject(task_id, reason)` | `task.transition` | 009 §4 |

### Structurally absent

Not "denied" — **not in any PM-OS toolset**, so no prompt can reach them:

- every messaging/platform tool (`send_message`, Slack/WhatsApp/Telegram) — #2, #7
- `pmo_approval_decide` — agents hold no org rank, so it cannot exist for them (002 §4)
- source-write tools for the `qa` handle (009 §6)
- repo-write tools for the `research` handle
- **on a client-thread turn**, everything except `pmo_thread_post` and
  `pmo_approval_request` (010 §1)

---

## 6. CLI

```
hermes pmo init
hermes pmo doctor                                    # the standing health check  013 §6
hermes pmo user add --email --name --role
hermes pmo user passwd --email
hermes pmo project bootstrap --slug --name --path
hermes pmo project list
hermes pmo pause   --project --reason                # 014 §5   ⏳
hermes pmo resume  --project                         # ⏳
hermes pmo gc [--dry-run]                            # stale worktrees  011 §6   ⏳
hermes pmo backup                                    # VACUUM INTO      014 §6   ⏳
hermes pmo restore --from --dry-run                  # ⏳
hermes pmo export  --project --out                   # 015 §5   ⏳
hermes pmo project delete --slug --confirm <slug>    # 015 §5   ⏳
```

---

## 7. Standard error shapes

Consistency here is worth more than it looks — agents recover from structured errors and
flail on prose.

```json
{ "error": "unknown_mention",
  "unknown": ["dev-7"],
  "valid_handles": ["pm", "dev-1", "qa"] }

{ "error": "illegal_transition",
  "from": "draft", "to": "done",
  "legal": ["todo", "cancelled"] }

{ "error": "finalize_refused",
  "reasons": ["missing_acceptance_criteria", "approval_pending"],
  "approval_id": "ap_3f21" }

{ "error": "scope_violation",
  "path": "C:\\work\\beta\\README.md",
  "reason": "outside project folders" }

{ "error": "denied_path",
  "path": ".env",
  "rule": "scope.deny[0]" }

{ "error": "forbidden",
  "action": "approval.decide",
  "required_rank": 100, "your_rank": 70 }
```

The **reject-with-valid-list** pattern (first example) is used everywhere an agent must
pick from a vocabulary — mentions (005), labels (009), transitions (009), handles.
It is the single highest-leverage convention in the API: an agent that receives the
legal set self-corrects on the next call instead of guessing.
