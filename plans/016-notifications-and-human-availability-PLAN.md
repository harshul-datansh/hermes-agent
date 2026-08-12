# 016 — Notifications & Human Availability

**Why this plan exists:** requirements #6 and #8 both terminate at a human — an
escalation needs an answer, an approval needs a decision. Plans 005 and 006 route both
to "a bell icon in the dashboard". Nobody sits in the dashboard. A blocked ticket that
waits eight hours for someone to happen to log in is, from the project's point of view,
the same as no escalation path at all.

**Day-1 touchpoint:** record `needs_response_by` on escalations and approvals when they
are created. The reminder machinery can come later, but only if the deadline was
captured at the moment the item was raised.
**Full build:** 2 h.

---

## 1. The hard constraint

Requirement #7 says agents communicate **only** via `@` on task comments, and #2 says
instructions reach the PM **only** from the founder's-office thread. So any notification
bridge must be **structurally one-directional**.

> Notifications are an **egress-only** projection of state that already exists in the
> product. No notification channel may create a `messages` row, a `task_comments` row,
> or wake an agent.

Concretely: no "reply to this Slack message to answer the escalation." A reply-to-act
bridge is genuinely tempting and it silently reintroduces a second inbound channel,
breaking both #2 and #7. The notification carries a **deep link** back into the
dashboard, and the action happens there.

State this in the code, not only here — the delivery module gets no write access to
`messages` or `task_comments`, so the shortcut is not available to a future
contributor in a hurry.

---

## 2. What warrants a notification

Be strict. A system that notifies on everything gets muted in a week, and then the
escalation path is broken again but invisibly.

| Event | Who | Urgency |
|---|---|---|
| Escalation raised (#6) | project members with rank ≥ 50 | **High** |
| Approval raised | everyone at or above `required_rank` | **High** |
| Approval escalated to your rank (#8) | the new rank cohort | **High** |
| Approval decided | the requester | Normal |
| `@mention` of a human on a ticket | that person | Normal |
| `@mention` of a human in a thread | that person | Normal |
| Ticket assigned to you | assignee | Normal |
| Budget cap 80% / 100% (012) | rank ≥ 70 | Normal / High |
| Agent quarantined (014) | project_admin | Normal |
| Circuit breaker paused the PM (014) | project_admin | **High** |

Everything else — status changes, comments you are not mentioned in, run completions —
is in-app only. It goes on the Activity screen (013 §5) and nowhere else.

---

## 3. Channels

```yaml
notifications:
  channels:
    in_app:  { enabled: true }
    email:   { enabled: true,  min_urgency: "normal" }
    push:    { enabled: false }
    webhook: { enabled: false, url: "" }
  quiet_hours: { start: "20:00", end: "08:00", timezone: "Asia/Kolkata" }
  digest: { enabled: true, at: "09:00" }
```

**In-app** is the source of truth: a `notifications` table in `pmo.db`, bell with unread
count, click marks read and deep-links.

```sql
CREATE TABLE IF NOT EXISTS notifications (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT NOT NULL,
    project_id  TEXT,
    kind        TEXT NOT NULL,
    urgency     TEXT NOT NULL,          -- high | normal
    title       TEXT NOT NULL,
    body        TEXT,
    link        TEXT NOT NULL,          -- deep link into the dashboard
    created_at  INTEGER NOT NULL,
    read_at     INTEGER,
    sent_at     INTEGER                 -- when the egress bridge delivered it
);
CREATE INDEX IF NOT EXISTS idx_notif_unread ON notifications(user_id, read_at);
```

Every other channel is a projection of this table. That means a delivery failure is
recoverable (the row is still there, `sent_at IS NULL`) and the same event can never
arrive twice through two independent code paths.

**Email** via the existing gateway platform integrations. High urgency sends
immediately; normal batches into the digest.

**Webhook** — a generic POST, so a team can wire Slack/Teams themselves without PM-OS
growing a platform integration per vendor. Sign the payload (HMAC, shared secret) so
the receiver can verify it.

**Push** — P2. Not worth it before there is a mobile surface.

---

## 4. Quiet hours and urgency

Quiet hours suppress **normal**, never **high**. A production escalation at 2am is
exactly what the escalation path is for; suppressing it teaches people to route around
the system.

Timezone is per user (`users.timezone`), defaulting to the workspace timezone. This is
the one place a per-user setting is legitimate — and it is **not** a violation of #11,
because #11 is about *configuration of behaviour*, not about the recipient's own contact
preferences. Note the distinction in the code comment; someone will otherwise "fix" it
by deleting the column.

Digest at 09:00 local: everything normal-urgency from the last 24 h, one email,
grouped by project.

---

## 5. Escalation SLA

This is the part that makes #6 actually close.

```yaml
escalation:
  respond_within_minutes: 120
  reminder_after_minutes: 60
  escalate_rank_after_minutes: 180
```

At creation, an escalation or approval gets `needs_response_by = now + respond_within`.
**This is the day-1 touchpoint** — it is one column write, and without it the whole
ladder below is unbuildable retroactively.

Then:

```
raised ──60m no response──► reminder to the same cohort
       ──120m──────────────► marked overdue, --danger pill on the card,
                             system message in the founder's-office thread
       ──180m──────────────► auto-escalate to the next rank up
                             (same mechanism as 006 §3, actor = "system")
```

Auto-escalation reusing the manual `pmo_approval_escalate` path is deliberate: one code
path, one audit shape, and the `approval_escalations` row records `by_user='system'` so
it is distinguishable from a human decision.

An escalation that has climbed to CEO rank and is still unanswered stops climbing and
posts a daily reminder. There is nowhere above CEO, and a system that keeps escalating
into a void is just generating noise.

---

## 6. Availability

Lightweight — enough to route around an absent approver, no more.

```sql
CREATE TABLE IF NOT EXISTS availability (
    user_id   TEXT NOT NULL,
    from_ts   INTEGER NOT NULL,
    to_ts     INTEGER NOT NULL,
    status    TEXT NOT NULL,         -- away | limited
    delegate  TEXT,                  -- user id who may act at this user's rank
    PRIMARY KEY (user_id, from_ts)
);
```

Delegation rules, and they need to be tight because this is a way to hand out authority:

- A delegate acts **at the delegator's rank**, for the window only.
- Delegation is recorded on every decision: `approvals.decided_by = "u_dev (as u_ceo)"`.
- A delegate may not sub-delegate.
- **Only the CEO may delegate CEO rank.** Nobody can grant authority they do not hold.
- Every delegation writes to `audit` (013 §4).

The Members screen (002 §6) shows an "Away until…" chip so a requester can see why an
approval is sitting.

---

## 7. UI

- **Bell** in the topbar with an unread count, per `design/design.md` §4 (`.bell-dot`).
- Dropdown: grouped by project, high-urgency items pinned with a `--danger` dot, each
  row deep-linking to the approval, escalation, ticket or thread.
- **Mark all read**, and a filter for unread only.
- Overdue escalations and approvals carry a `--danger` "Overdue 2h" pill, matching the
  ERP's own overdue treatment (design.md §6a — the "Upcoming actions" widget already
  does exactly this).
- Notification preferences live on a **Profile** page: channel toggles, quiet hours,
  timezone. Contact preferences only — no behavioural config (#11).

---

## 8. Tests

```
tests/hermes_cli/test_pmo_notifications.py
  test_high_urgency_ignores_quiet_hours
  test_normal_urgency_batched_into_digest
  test_notification_row_precedes_any_egress
  test_delivery_failure_leaves_sent_at_null_for_retry
  test_no_duplicate_across_channels

tests/hermes_cli/test_pmo_egress_only.py
  test_notification_module_cannot_write_messages
  test_notification_module_cannot_write_comments
  test_webhook_payload_is_signed

tests/hermes_cli/test_pmo_sla.py
  test_needs_response_by_set_at_creation
  test_reminder_at_threshold
  test_auto_escalate_uses_manual_escalation_path
  test_auto_escalate_records_system_actor
  test_ceo_rank_stops_climbing

tests/hermes_cli/test_pmo_delegation.py
  test_delegate_acts_at_delegator_rank
  test_delegate_cannot_subdelegate
  test_only_ceo_can_delegate_ceo_rank
  test_decision_records_delegation
```

`test_notification_module_cannot_write_messages` is the one that protects #2 and #7.

## P0 (day-1 touchpoint) / P1

**Day 1:** `needs_response_by` written when an escalation or approval is created;
`notifications` table exists; in-app bell with unread count.

**P1:** email + digest; quiet hours; the SLA ladder with auto-escalation; availability
and delegation; webhook with signing; the Profile preferences page.

## Traps

- A reply-to-act bridge. It reintroduces an inbound channel and breaks #2 and #7. Deny
  the delivery module write access so the shortcut is unavailable.
- Suppressing high-urgency in quiet hours.
- Notifying on everything. The bell gets muted and the escalation path silently dies.
- Sending from the event rather than from the `notifications` row. Two code paths means
  duplicate delivery and unrecoverable failures.
- Delegation without an audit trail, or allowing sub-delegation.
