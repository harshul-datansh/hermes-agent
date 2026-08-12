"""Kanban-native blocker → PM → human → worker round trip.

Block state and attempts live in existing task events/comments.  Human input is
a native approval parent from :mod:`plugins.pmo.approval`; an approved answer
is copied back to the ticket before the native unblock transition resumes the
worker.  No parallel escalation store or message runtime is introduced.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass

from agent.redact import redact_sensitive_text
from hermes_cli import kanban_db

from plugins.pmo import approval, mentions
from plugins.pmo.project_scope import ProjectScope, ScopeViolation


ATTEMPT_MARKER = "[pmo:pm-attempt:v1]"
ESCALATION_MARKER = "[pmo:blocker-escalation:v1]"
RESOLUTION_MARKER = "[pmo:blocker-resolution:v1]"


@dataclass(frozen=True)
class EscalationView:
    task_id: str
    approval_id: str
    reason_hash: str
    required_rank: int
    needs_response_by: int


def _marker(name: str, payload: dict[str, object]) -> str:
    return name + "\n" + json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _payload(body: str, marker: str) -> dict[str, object] | None:
    first, separator, rest = body.partition("\n")
    if first.strip() != marker or not separator:
        return None
    try:
        data = json.loads(rest)
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _reason_hash(reason: str) -> str:
    normalized = " ".join(str(reason or "").casefold().split())
    if not normalized:
        raise ValueError("blocker reason is required")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def _task(scope: ProjectScope, conn, task_id: str):
    task = kanban_db.get_task(conn, task_id)
    if task is None:
        raise ValueError(f"unknown task '{task_id}'")
    if task.project_id != scope.project_id:
        raise ScopeViolation(
            f"task '{task_id}' is not bound to project '{scope.project_id}'"
        )
    return task


def block_for_pm(
    scope: ProjectScope,
    *,
    task_id: str,
    author: str,
    reason: str,
    kind: str,
) -> bool:
    """Use Hermes' typed blocker and notify the PM when human action is due."""

    clean_reason = redact_sensitive_text(str(reason or "").strip(), force=True)
    if not clean_reason:
        raise ValueError("blocker reason is required")
    if kind not in kanban_db.VALID_BLOCK_KINDS:
        raise ValueError(f"block kind must be one of {sorted(kanban_db.VALID_BLOCK_KINDS)}")
    with kanban_db.connect_closing(board=scope.board_slug) as conn:
        _task(scope, conn, task_id)
        changed = kanban_db.block_task(conn, task_id, reason=clean_reason, kind=kind)
    if not changed:
        return False
    # Dependency waits are already handled by native parent gating. Transient
    # blockers get a PM route only after the configured retry budget is spent.
    if kind in {"needs_input", "capability"}:
        mentions.post_comment(
            scope,
            task_id=task_id,
            author=author,
            body=f"@pm Blocked ({kind}): {clean_reason}",
            request_id=f"block:{task_id}:{_reason_hash(clean_reason)}",
        )
    return True


def record_pm_attempt(
    scope: ProjectScope, *, task_id: str, reason: str, note: str, author: str = "pm"
) -> int:
    reason_id = _reason_hash(reason)
    with kanban_db.connect_closing(board=scope.board_slug) as conn:
        _task(scope, conn, task_id)
        attempts = [
            data
            for comment in kanban_db.list_comments(conn, task_id)
            if (data := _payload(comment.body, ATTEMPT_MARKER))
            and data.get("reason_hash") == reason_id
        ]
        return kanban_db.add_comment(
            conn,
            task_id,
            author,
            _marker(
                ATTEMPT_MARKER,
                {
                    "attempt": len(attempts) + 1,
                    "note": redact_sensitive_text(note, force=True),
                    "reason_hash": reason_id,
                },
            ),
        )


def pm_attempt_count(scope: ProjectScope, *, task_id: str, reason: str) -> int:
    reason_id = _reason_hash(reason)
    with kanban_db.connect_closing(board=scope.board_slug) as conn:
        _task(scope, conn, task_id)
        return sum(
            1
            for comment in kanban_db.list_comments(conn, task_id)
            if (data := _payload(comment.body, ATTEMPT_MARKER))
            and data.get("reason_hash") == reason_id
        )


def escalate_to_human(
    scope: ProjectScope,
    *,
    task_id: str,
    reason: str,
    raised_by: str = "pm",
    required_rank: int = 50,
    approver_profile: str = "founders-office",
    respond_within_minutes: int | None = None,
    force: bool = False,
) -> EscalationView:
    """Create one native approval gate after the PM retry policy is met."""

    clean_reason = redact_sensitive_text(str(reason or "").strip(), force=True)
    reason_id = _reason_hash(clean_reason)
    respond_within_minutes = (
        scope.config.escalation.respond_within_minutes
        if respond_within_minutes is None
        else int(respond_within_minutes)
    )
    if respond_within_minutes < 1:
        raise ValueError("respond_within_minutes must be positive")
    with kanban_db.connect_closing(board=scope.board_slug) as conn:
        task = _task(scope, conn, task_id)
        if task.status not in {"blocked", "triage"}:
            raise RuntimeError(f"task '{task_id}' is not blocked")
        for comment in kanban_db.list_comments(conn, task_id):
            data = _payload(comment.body, ESCALATION_MARKER)
            if data and data.get("reason_hash") == reason_id:
                return EscalationView(
                    task_id,
                    str(data["approval_id"]),
                    reason_id,
                    int(data["required_rank"]),
                    int(data["needs_response_by"]),
                )
        immediate = task.block_kind in {"needs_input", "capability"}
    attempts = pm_attempt_count(scope, task_id=task_id, reason=clean_reason)
    retry_limit = scope.config.escalation.pm_retry_limit
    if not force and not immediate and attempts < retry_limit:
        raise RuntimeError(
            f"PM retry budget not exhausted ({attempts}/{retry_limit})"
        )

    created_at = int(time.time())
    request = approval.raise_approval(
        board_slug=scope.board_slug,
        target_task_id=task_id,
        title="Resolve blocked work",
        detail=clean_reason,
        raised_by=raised_by,
        required_rank=required_rank,
        approver_profile=approver_profile,
        respond_within_minutes=respond_within_minutes,
        reminder_after_minutes=min(
            scope.config.escalation.reminder_after_minutes,
            respond_within_minutes,
        ),
        escalate_rank_after_minutes=max(
            scope.config.escalation.escalate_rank_after_minutes,
            respond_within_minutes,
        ),
        now=created_at,
    )
    needs_response_by = created_at + int(respond_within_minutes) * 60
    with kanban_db.connect_closing(board=scope.board_slug) as conn:
        marker_id = kanban_db.add_comment(
            conn,
            task_id,
            raised_by,
            _marker(
                ESCALATION_MARKER,
                {
                    "approval_id": request.approval_id,
                    "needs_response_by": needs_response_by,
                    "reason_hash": reason_id,
                    "required_rank": int(required_rank),
                },
            ),
        )
    founders = next(item for item in mentions.roster(scope) if item.handle == "founders-office")
    mentions.enqueue_notification(
        scope,
        task_id=task_id,
        source_comment_id=marker_id,
        author=raised_by,
        recipient=founders,
        body=f"Human input needed: {clean_reason}",
        urgency="high",
        request_id=f"escalation:{task_id}:{reason_id}",
    )
    return EscalationView(
        task_id, request.approval_id, reason_id, int(required_rank), needs_response_by
    )


def resolve_human_input(
    scope: ProjectScope,
    *,
    escalation: EscalationView,
    answer: str,
    actor: str,
    actor_rank: int,
) -> bool:
    """Approve input, post it back to the ticket, then resume native dispatch."""

    clean_answer = redact_sensitive_text(str(answer or "").strip(), force=True)
    if not clean_answer:
        raise ValueError("human answer is required")
    approval.decide_approval(
        board_slug=scope.board_slug,
        approval_id=escalation.approval_id,
        decision="approved",
        actor=actor,
        actor_rank=actor_rank,
        note=clean_answer,
    )
    with kanban_db.connect_closing(board=scope.board_slug) as conn:
        task = _task(scope, conn, escalation.task_id)
        worker = next(
            (
                agent.handle
                for agent in scope.config.agents
                if agent.profile.casefold() == (task.assignee or "").casefold()
                or agent.handle.casefold() == (task.assignee or "").casefold()
            ),
            None,
        )
    prefix = f"@{worker} " if worker else ""
    mentions.post_comment(
        scope,
        task_id=escalation.task_id,
        author="pm",
        body=f"{prefix}Human response from {actor}: {clean_answer}",
        request_id=f"resolution:{escalation.approval_id}",
    )
    with kanban_db.connect_closing(board=scope.board_slug) as conn:
        task = _task(scope, conn, escalation.task_id)
        # Approval completion normally promotes the gated task already. If it
        # remains sticky-blocked, the explicit native unblock completes the
        # round trip without bypassing unfinished parent dependencies.
        if task.status in {"blocked", "scheduled"}:
            return kanban_db.unblock_task(conn, escalation.task_id)
        return task.status in {"ready", "todo", "running"}


def escalate_overdue(
    scope: ProjectScope,
    *,
    approver_profile: str = "founders-office",
    required_rank: int = 50,
    now: int | None = None,
    limit: int = 25,
) -> list[EscalationView]:
    """Bounded timeout safety-net for blocked tickets with no PM activity."""

    cutoff = int(now if now is not None else time.time()) - (
        scope.config.escalation.blocked_timeout_minutes * 60
    )
    candidates: list[tuple[int, str, str]] = []
    with kanban_db.connect_closing(board=scope.board_slug) as conn:
        for task in kanban_db.list_tasks(conn, status="blocked", order_by="created"):
            if task.project_id != scope.project_id:
                continue
            events = kanban_db.list_events(conn, task.id)
            blocked = next((item for item in reversed(events) if item.kind == "blocked"), None)
            if blocked is None or blocked.created_at > cutoff:
                continue
            reason = str((blocked.payload or {}).get("reason") or "Blocked without a recorded reason")
            candidates.append((blocked.created_at, task.id, reason))
    results: list[EscalationView] = []
    for _, task_id, reason in sorted(candidates)[: max(0, min(int(limit), 100))]:
        results.append(
            escalate_to_human(
                scope,
                task_id=task_id,
                reason=reason,
                approver_profile=approver_profile,
                required_rank=required_rank,
                force=True,
            )
        )
    return results
