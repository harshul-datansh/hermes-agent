"""Bounded SLA maintenance for Kanban-native PMO approvals.

Deadlines and every scheduler action are native approval comments. Automatic
rank escalation delegates to :func:`plugins.pmo.approval.escalate_approval`,
so manual and scheduled escalation share one audit shape. The system principal
is never accepted by the decision path.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass
from typing import Sequence

from hermes_cli import kanban_db

from plugins.pmo import approval
from plugins.pmo.project_scope import ProjectScope, resolve


REMINDER_MARKER = "[pmo:approval-reminder:v1]"
OVERDUE_MARKER = "[pmo:approval-overdue:v1]"
CEO_REMINDER_MARKER = "[pmo:approval-ceo-reminder:v1]"
MAX_ACTIONS = 500
MAX_SCAN = 2_000


@dataclass(frozen=True)
class SlaAction:
    approval_id: str
    action: str
    at: int
    required_rank: int


def _fields(body: str, marker: str) -> dict[str, str] | None:
    lines = body.splitlines()
    if not lines or lines[0].strip() != marker:
        return None
    fields: dict[str, str] = {}
    for line in lines[1:]:
        key, separator, value = line.partition(":")
        if separator:
            fields[key.strip().casefold().replace(" ", "_")] = value.strip()
    return fields


def _record_once(
    conn,
    *,
    task_id: str,
    marker: str,
    key: str,
    value: int,
    body: str,
) -> bool:
    for comment in kanban_db.list_comments(conn, task_id):
        fields = _fields(comment.body, marker)
        if fields is not None and fields.get(key) == str(value):
            return False
    kanban_db.add_comment(conn, task_id, "system", f"{marker}\n{body}")
    return True


def _next_target(scope: ProjectScope, current_rank: int):
    return next(
        (
            target
            for target in scope.config.escalation.rank_targets
            if current_rank < target.rank <= 100
        ),
        None,
    )


def process_approval_slas(
    scope: ProjectScope,
    *,
    now: int | None = None,
    limit: int = 100,
) -> list[SlaAction]:
    """Apply a bounded, idempotent reminder/overdue/escalation pass."""

    bound = int(limit)
    if not 1 <= bound <= MAX_ACTIONS:
        raise ValueError(f"limit must be between 1 and {MAX_ACTIONS}")
    timestamp = int(time.time()) if now is None else int(now)
    with kanban_db.connect_closing(board=scope.board_slug) as conn:
        candidates = [
            task.id
            for task in kanban_db.list_tasks(
                conn, status="triage", limit=MAX_SCAN, order_by="created"
            )
            if (task.body or "").splitlines()[:1] == [approval.APPROVAL_MARKER]
        ]

    actions: list[SlaAction] = []
    for approval_id in candidates:
        if len(actions) >= bound:
            break
        item = approval.get_approval(
            board_slug=scope.board_slug, approval_id=approval_id
        )
        if item.status != "pending" or item.needs_response_by is None:
            continue

        with kanban_db.connect_closing(board=scope.board_slug) as conn:
            if (
                item.reminder_at is not None
                and timestamp >= item.reminder_at
                and len(actions) < bound
                and _record_once(
                    conn,
                    task_id=item.approval_id,
                    marker=REMINDER_MARKER,
                    key="needs_response_by",
                    value=item.needs_response_by,
                    body=(
                        f"Needs response by: {item.needs_response_by}\n"
                        f"Required rank: {item.required_rank}\n"
                        f"Approver: {item.approver_profile}"
                    ),
                )
            ):
                actions.append(
                    SlaAction(item.approval_id, "reminder", timestamp, item.required_rank)
                )
            if (
                timestamp >= item.needs_response_by
                and len(actions) < bound
                and _record_once(
                    conn,
                    task_id=item.approval_id,
                    marker=OVERDUE_MARKER,
                    key="needs_response_by",
                    value=item.needs_response_by,
                    body=(
                        f"Needs response by: {item.needs_response_by}\n"
                        f"Required rank: {item.required_rank}"
                    ),
                )
            ):
                actions.append(
                    SlaAction(item.approval_id, "overdue", timestamp, item.required_rank)
                )

        if item.escalate_rank_at is None or timestamp < item.escalate_rank_at:
            continue
        if item.required_rank >= 100:
            day = timestamp // 86_400
            with kanban_db.connect_closing(board=scope.board_slug) as conn:
                if len(actions) < bound and _record_once(
                    conn,
                    task_id=item.approval_id,
                    marker=CEO_REMINDER_MARKER,
                    key="day",
                    value=day,
                    body=(
                        f"Day: {day}\nRequired rank: 100\n"
                        "CEO response is still required; rank cannot climb further."
                    ),
                ):
                    actions.append(
                        SlaAction(item.approval_id, "ceo_reminder", timestamp, 100)
                    )
            continue

        target = _next_target(scope, item.required_rank)
        if target is None or len(actions) >= bound:
            continue
        escalated = approval.escalate_approval(
            board_slug=scope.board_slug,
            approval_id=item.approval_id,
            to_rank=target.rank,
            to_approver_profile=target.approver_profile,
            reason="Approval SLA expired without a human decision.",
            actor="system",
            actor_rank=item.required_rank,
            actor_kind="system",
            respond_within_minutes=scope.config.escalation.respond_within_minutes,
            reminder_after_minutes=scope.config.escalation.reminder_after_minutes,
            escalate_rank_after_minutes=scope.config.escalation.escalate_rank_after_minutes,
            now=timestamp,
        )
        actions.append(
            SlaAction(escalated.approval_id, "escalated", timestamp, escalated.required_rank)
        )
    return actions


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--board", required=True)
    parser.add_argument("--workspace")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--now", type=int)
    args = parser.parse_args(argv)
    try:
        scope = resolve(args.project, board_slug=args.board, workspace=args.workspace)
        actions = process_approval_slas(scope, now=args.now, limit=args.limit)
    except (OSError, PermissionError, RuntimeError, ValueError) as exc:
        print(f"pmo sla: {exc}", file=sys.stderr)
        return 2
    print(json.dumps([asdict(item) for item in actions], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
