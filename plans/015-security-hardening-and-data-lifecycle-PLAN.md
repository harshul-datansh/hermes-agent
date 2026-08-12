# 015 — Security Hardening & Data Lifecycle

**Why this plan exists:** plan 002 covers *who may call what*. That is authorization, and
it is only one of the security surfaces here. This plan covers the rest: secrets that
agents can read, content that gets rendered in a browser, files that get uploaded, and
data that has to be deleted when a client leaves.

Prompt injection via the client channel is in **010** — the boundary lives with the
channel that creates it.

**Day-1 touchpoint:** the secrets denylist in the agent toolset, and HTML escaping in
the chat/comment renderer. Both are small, and both are much harder to add once agents
have been reading `.env` files and messages have been rendering as HTML for a week.
**Full build:** 2 h.

---

## 1. Secrets

Agents have filesystem access inside project folders. Project folders contain `.env`.

### Denylist

```yaml
scope:
  deny:
    - ".env"
    - ".env.*"
    - "**/secrets/**"
    - "**/*.pem"
    - "**/*.key"
    - "**/id_rsa*"
    - "**/.aws/**"
    - "**/.ssh/**"
    - "**/.hermes/**"          # the agent's own credentials
```

Enforced in `pmo_scope.assert_path` (003), *after* the in-scope check. A denied path
returns a refusal naming the rule, not a generic error — an agent that gets "file not
found" will retry with a different path.

Note 003 marks `scope.deny` as P1 with the field parsed but unenforced. **This plan
promotes it to day 1.** Parsing a security control without enforcing it is worse than
omitting it, because the presence of the key in `project.yaml` reads as protection.

### Secrets in output

Agents paste file contents into comments and commit messages. Before any agent-authored
text is persisted:

1. Scan for high-entropy strings and known key shapes (`sk-`, `ghp_`, `AKIA`,
   `-----BEGIN`). `hermes_cli/security_audit.py` and `mcp_security.py` exist upstream —
   check for a reusable scanner before writing one.
2. On a hit: redact to `[redacted:<kind>]`, persist the redacted form, flag in `audit`.
3. Never block the write. A blocked comment means the agent retries with the secret
   reformatted, and now it evades the pattern.

The same redaction applies to run transcripts (013 §3).

### Env vars

`hermes_cli/config.py` already carries a denylist for env names that must not be written
from the dashboard (`HERMES_HOME`, `HERMES_PROFILE`, `HERMES_CONFIG`, `HERMES_ENV`).
Extend it for PM-OS rather than starting a second list.

---

## 2. Rendering — XSS

The chat (006) and the comment thread (005) render agent- and human-authored markdown
into a browser. The plugin bundle builds DOM with `React.createElement`, which escapes
text children — so the risk is concentrated in the places where you reach past it:

| Risk | Rule |
|---|---|
| `dangerouslySetInnerHTML` for markdown | Only via a sanitising renderer with an allowlist. `web/src/components/Markdown.tsx` exists — reuse the host's, do not add a second markdown path |
| Link `href` | Allowlist `http`/`https`/`mailto` only. `javascript:` and `data:` blocked |
| Images | `--warning` banner + click-to-load for remote images in client threads; a remote image URL is an exfiltration channel and a read receipt |
| `@mention` tokens | Built from resolved ids, never from the raw matched text |
| Attachment filenames | Escaped on render; never used to build a DOM string |

Test with a message body of `<img src=x onerror=alert(1)>` and
`[click](javascript:alert(1))` in the chat, the comment thread, the ticket title, and
the ticket body. All four render sites, not just the one you remember.

---

## 3. Attachments

`task_attachments` exists upstream (`plugins/kanban` has upload/download/delete routes,
and `tests/plugins/test_kanban_attachments.py`). Harden the fork:

- **Size cap** (`attachments.max_mb`, default 25) enforced streaming, not after buffering.
- **Type allowlist** by sniffed content, not by extension.
- **Store outside the web root** with generated names; serve through the guarded
  endpoint. `FileResponse` on a user-supplied path is a traversal waiting to happen.
- **`Content-Disposition: attachment`** and `X-Content-Type-Options: nosniff` on every
  download. Never render an uploaded file inline.
- **Client-thread attachments in a separate prefix** (010 §5).
- Scan filenames for traversal (`..`, absolute paths, NTFS alternate data streams `:`)
  before they touch the filesystem.

---

## 4. Transport & deployment

The kanban plugin's docstring notes `hermes dashboard --host 0.0.0.0` is "safe to run on
a LAN" because plugin routes now require the session token — but also that the auth
"isn't multi-user". Once 002 lands, PM-OS *is* multi-user, which changes the deployment
posture:

- **HTTPS required** when bound to anything but loopback. Session cookies must be
  `Secure` + `HttpOnly` + `SameSite=Lax`. Refuse to start with a non-loopback bind and
  no TLS unless `--insecure` is passed explicitly.
- **CSRF** on all state-changing routes. `SameSite=Lax` covers most of it; add a token
  for anything reachable cross-origin.
- **Rate limits**: `/auth/password-login` (5/min/IP), approval decisions (30/min/user),
  comment posts (60/min/principal). The last one is as much a runaway-agent guard as a
  security control.
- **Security headers**: CSP (`default-src 'self'`), `X-Frame-Options: DENY`,
  `Referrer-Policy: same-origin`.

The CSP matters for the plugin architecture specifically: plugin bundles are loaded as
scripts, and the manifest supports an `integrity` (SRI) field (`web/src/plugins/types.ts`).
Set it for the PM-OS bundle so a tampered plugin file will not execute.

---

## 5. Data lifecycle

Client work means client data, and client data eventually has to leave.

### Retention

```yaml
retention:
  transcripts_days: 30
  audit_days: 400          # long — this is the compliance record
  task_events_days: 180
  client_messages_days: 730
  backups_daily: 14
  backups_weekly: 8
```

A nightly cron job enforces these. Deletion is by age, per table, in batches, logged
to `audit`.

### Project deletion

`hermes pmo project delete --slug acme --confirm acme` must:

1. Refuse unless the typed confirmation matches the slug.
2. Export first — write a full archive (tickets, comments, threads, approvals, audit)
   to a file, and print the path *before* deleting anything.
3. Delete `pmo.db` rows, kanban board, `projects.db` rows, attachments, worktrees.
4. **Not** delete the project's git repository or `.datansh/` — that is the user's work,
   not our data.
5. Leave a tombstone row in `audit` naming who deleted what and when.

Archive-before-delete is not optional. A project deleted by mistake with no export is
the failure that ends trust in the tool.

### Export

`hermes pmo export --project acme --out acme.zip` — JSON per table plus attachments.
Needed for client handover, for the compliance answer to "give me everything you hold",
and as the safety net under deletion.

---

## 6. Threat model summary

| Threat | Primary control | Plan |
|---|---|---|
| Employee reads another project | capability guard + explicit board slug | 002, 003 |
| Agent reads another project's files | `path_allowed` on resolved paths | 003 |
| Agent reads secrets in scope | `scope.deny`, enforced | **015 §1** |
| Client injects instructions into the PM | restricted toolset + envelope + scanner | 010 |
| Client sees internal discussion | context builder excludes internal threads | 010 §5 |
| XSS via message/comment/title | sanitising renderer, scheme allowlist | **015 §2** |
| Agent leaks secrets into a comment | output redaction | **015 §1** |
| Malicious upload | size/type/storage/headers | **015 §3** |
| Runaway agent spend | budget caps at claim time | 012 |
| Runaway agent writes | rate limits, circuit breakers | 014, 015 §4 |
| Privilege escalation to approver | agents hold no org rank, structurally | 002 §4, 006 §2 |
| Tampered plugin bundle | SRI `integrity` in the manifest | **015 §4** |

---

## 7. Tests

```
tests/hermes_cli/test_pmo_secrets.py
  test_deny_rules_enforced_not_just_parsed
  test_denied_path_refusal_names_the_rule
  test_secret_pattern_redacted_in_comment
  test_redaction_does_not_block_the_write

tests/plugins/test_pmo_render_safety.py
  test_script_tag_escaped_in_chat
  test_script_tag_escaped_in_comment
  test_script_tag_escaped_in_ticket_title_and_body
  test_javascript_scheme_link_blocked
  test_remote_image_gated_in_client_thread

tests/plugins/test_pmo_attachments.py
  test_size_cap_enforced_streaming
  test_type_allowlist_by_sniff_not_extension
  test_traversal_filename_rejected
  test_download_sets_disposition_and_nosniff

tests/hermes_cli/test_pmo_lifecycle.py
  test_delete_requires_typed_slug
  test_delete_exports_before_removing
  test_delete_leaves_repo_untouched
  test_retention_job_batches_and_logs
```

## P0 (day-1 touchpoint) / P1

**Day 1:** `scope.deny` enforced (promoted from 003 P1); HTML escaping verified at all
four render sites; attachment size cap and storage outside the web root.

**P1:** output redaction; TLS enforcement and headers; rate limits; SRI on the bundle;
retention job; export and delete commands.

## Traps

- Shipping `scope.deny` parsed but unenforced. The key's presence reads as protection.
- Blocking a write on a secret hit instead of redacting. The agent reformats and evades.
- Testing XSS at one render site. There are at least four.
- `FileResponse` on a user-supplied path.
- Deleting a project without an export. There is no undo.
- Extension-based upload filtering. Sniff the content.
