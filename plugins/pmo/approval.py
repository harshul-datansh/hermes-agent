"""Project approvals backed entirely by native Hermes Kanban records.

An approval is a Kanban task linked as a parent of the work it gates.  Native
statuses carry the terminal outcome while comments carry the human-readable
audit trail::

    triage   pending (never dispatched)
    done     approved (dependency satisfied)
    blocked  rejected (dependency remains unsatisfied)
    archived withdrawn (dependency is terminal and no longer gates work)

The module adds no database, authentication runtime, dispatcher, or model tool.
Callers must pass the authenticated actor identity and rank obtained by their
existing trusted surface; this edge enforces the hierarchy at mutation time.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass
from typing import Sequence

from agent.redact import redact_sensitive_text
from hermes_cli import kanban_db


APPROVAL_MARKER = "[pmo:approval:v1]"
ESCALATION_MARKER = "[pmo:approval-escalated:v1]"
DECISION_MARKER = "[pmo:approval-decision:v1]"
TARGET_MARKER = "[pmo:approval-gate:v1]"
SLA_MARKER = "[pmo:approval-sla:v1]"
TERMINAL_STATUSES = {"approved", "rejected", "withdrawn"}
ESCALATE_MIN_RANK = 50


@dataclass(frozen=True)
class ApprovalView:
    board_slug: str
    approval_id: str
    target_task_id: str
    title: str
    detail: str
    raised_by: str
    required_rank: int
    approver_profile: str
    status: str
    decision_actor: str | None = None
    decision_note: str | None = None
    needs_response_by: int | None = None
    reminder_at: int | None = None
    escalate_rank_at: int | None = None


def _required_text(value: str | None, *, label: str) -> str:
    cleaned = str(value or "").strip()
    if not cleaned:
        raise ValueError(f"{label} must not be empty")
    return redact_sensitive_text(cleaned, force=True)


def _rank(value: int, *, label: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a non-negative integer")
    try:
        rank = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a non-negative integer") from exc
    if rank < 0 or rank > 100:
        raise ValueError(f"{label} must be an integer between 0 and 100")
    return rank


def _board(board_slug: str) -> str:
    board = kanban_db._normalize_board_slug(board_slug)
    if not board:
        raise ValueError("board slug is required")
    if not kanban_db.board_exists(board):
        raise ValueError(f"board '{board}' does not exist")
    return board


def _human_actor(actor: str, actor_rank: int, actor_kind: str) -> tuple[str, int]:
    clean_actor = _required_text(actor, label="actor")
    if str(actor_kind).strip().lower() != "human":
        raise PermissionError("only a human actor may decide, escalate, or withdraw")
    return clean_actor, _rank(actor_rank, label="actor rank")


def _escalation_actor(actor: str, actor_rank: int, actor_kind: str) -> tuple[str, int]:
    """Accept a human or the narrow SLA system principal for escalation only."""

    kind = str(actor_kind).strip().lower()
    if kind == "human":
        return _human_actor(actor, actor_rank, actor_kind)
    clean_actor = _required_text(actor, label="actor")
    if kind != "system" or clean_actor != "system":
        raise PermissionError("only a human or the PMO SLA system may escalate")
    return clean_actor, _rank(actor_rank, label="actor rank")


def _single_line(value: str | None) -> str:
    cleaned = " ".join(str(value or "").splitlines()).strip()
    return redact_sensitive_text(cleaned, force=True)


def _fields(text: str, marker: str) -> dict[str, str] | None:
    lines = text.splitlines()
    if not lines or lines[0].strip() != marker:
        return None
    result: dict[str, str] = {}
    for line in lines[1:]:
        key, separator, value = line.partition(":")
        if separator:
            result[key.strip().lower().replace(" ", "_")] = value.strip()
    return result


def _format_approval_body(
    *, title: str, detail: str, raised_by: str, required_rank: int, target_task_id: str
) -> str:
    return "\n".join(
        [
            APPROVAL_MARKER,
            f"Title: {title}",
            f"Raised by: {raised_by}",
            f"Initial required rank: {required_rank}",
            f"Target task: {target_task_id}",
            "Detail:",
            detail,
        ]
    )


def _approval_from_conn(
    conn, *, board: str, approval_id: str
) -> ApprovalView:
    task = kanban_db.get_task(conn, approval_id)
    if task is None:
        raise ValueError(f"approval '{approval_id}' does not exist on board '{board}'")
    body_fields = _fields(task.body or "", APPROVAL_MARKER)
    if body_fields is None:
        raise ValueError(f"task '{approval_id}' is not a PM-OS approval")
    try:
        required_rank = _rank(
            int(body_fields["initial_required_rank"]), label="stored required rank"
        )
        target_task_id = body_fields["target_task"]
        raised_by = body_fields["raised_by"]
        title = body_fields["title"]
    except (KeyError, ValueError) as exc:
        raise RuntimeError(f"approval '{approval_id}' has malformed metadata") from exc

    detail = (task.body or "").partition("\nDetail:\n")[2].strip()
    decision_fields: dict[str, str] | None = None
    needs_response_by = None
    reminder_at = None
    escalate_rank_at = None
    for comment in kanban_db.list_comments(conn, approval_id):
        sla = _fields(comment.body, SLA_MARKER)
        if sla is not None:
            try:
                needs_response_by = int(sla["needs_response_by"])
                reminder_at = int(sla["reminder_at"])
                escalate_rank_at = int(sla["escalate_rank_at"])
            except (KeyError, ValueError) as exc:
                raise RuntimeError(
                    f"approval '{approval_id}' has a malformed SLA record"
                ) from exc
        escalation = _fields(comment.body, ESCALATION_MARKER)
        if escalation is not None:
            try:
                from_rank = int(escalation["from_rank"])
                to_rank = int(escalation["to_rank"])
            except (KeyError, ValueError) as exc:
                raise RuntimeError(
                    f"approval '{approval_id}' has a malformed escalation record"
                ) from exc
            if from_rank != required_rank or to_rank <= from_rank:
                raise RuntimeError(
                    f"approval '{approval_id}' has a non-monotonic escalation trail"
                )
            required_rank = to_rank

        decision = _fields(comment.body, DECISION_MARKER)
        if decision is not None:
            if decision_fields is not None:
                raise RuntimeError(
                    f"approval '{approval_id}' has more than one terminal decision"
                )
            decision_fields = decision

    native_outcomes = {
        "triage": "pending",
        "done": "approved",
        "blocked": "rejected",
        "archived": "withdrawn",
    }
    status = native_outcomes.get(task.status, "transitioning")
    decision_actor = None
    decision_note = None
    if decision_fields is not None:
        recorded_status = decision_fields.get("decision", "")
        if recorded_status not in TERMINAL_STATUSES:
            raise RuntimeError(f"approval '{approval_id}' has an invalid decision record")
        if status in TERMINAL_STATUSES and recorded_status != status:
            raise RuntimeError(
                f"approval '{approval_id}' decision disagrees with native task status"
            )
        status = recorded_status
        decision_actor = decision_fields.get("actor") or None
        decision_note = decision_fields.get("note") or None

    parents = kanban_db.parent_ids(conn, target_task_id)
    if approval_id not in parents:
        raise RuntimeError(
            f"approval '{approval_id}' is no longer linked to target '{target_task_id}'"
        )
    return ApprovalView(
        board_slug=board,
        approval_id=approval_id,
        target_task_id=target_task_id,
        title=title,
        detail=detail,
        raised_by=raised_by,
        required_rank=required_rank,
        approver_profile=task.assignee or "",
        status=status,
        decision_actor=decision_actor,
        decision_note=decision_note,
        needs_response_by=needs_response_by,
        reminder_at=reminder_at,
        escalate_rank_at=escalate_rank_at,
    )


def _add_sla_comment(
    conn,
    *,
    approval_id: str,
    author: str,
    now: int,
    respond_within_minutes: int,
    reminder_after_minutes: int,
    escalate_rank_after_minutes: int,
) -> None:
    respond = int(respond_within_minutes)
    remind = int(reminder_after_minutes)
    escalate = int(escalate_rank_after_minutes)
    if min(respond, remind, escalate) < 1:
        raise ValueError("approval SLA thresholds must be positive")
    if remind > respond:
        raise ValueError("reminder threshold cannot exceed response threshold")
    if respond > escalate:
        raise ValueError("response threshold cannot exceed rank escalation threshold")
    kanban_db.add_comment(
        conn,
        approval_id,
        author,
        "\n".join(
            [
                SLA_MARKER,
                f"Created at: {now}",
                f"Reminder at: {now + remind * 60}",
                f"Needs response by: {now + respond * 60}",
                f"Escalate rank at: {now + escalate * 60}",
            ]
        ),
    )


def get_approval(*, board_slug: str, approval_id: str) -> ApprovalView:
    board = _board(board_slug)
    clean_id = _required_text(approval_id, label="approval id")
    with kanban_db.connect_closing(board=board) as conn:
        return _approval_from_conn(conn, board=board, approval_id=clean_id)


def raise_approval(
    *,
    board_slug: str,
    target_task_id: str,
    title: str,
    detail: str,
    raised_by: str,
    required_rank: int,
    approver_profile: str,
    respond_within_minutes: int = 120,
    reminder_after_minutes: int = 60,
    escalate_rank_after_minutes: int = 180,
    now: int | None = None,
) -> ApprovalView:
    """Create a pending approval and make it a native parent dependency."""

    board = _board(board_slug)
    clean_target = _required_text(target_task_id, label="target task id")
    clean_title = _required_text(title, label="title")
    clean_detail = _required_text(detail, label="detail")
    clean_raiser = _required_text(raised_by, label="raised by")
    clean_approver = _required_text(approver_profile, label="approver profile")
    clean_rank = _rank(required_rank, label="required rank")

    with kanban_db.connect_closing(board=board) as conn:
        target = kanban_db.get_task(conn, clean_target)
        if target is None:
            raise ValueError(f"target task '{clean_target}' does not exist on board '{board}'")
        if target.status in {"done", "archived"}:
            raise ValueError(
                f"target task '{clean_target}' is already terminal ({target.status})"
            )
        approval_id = kanban_db.create_task(
            conn,
            title=f"Approval: {clean_title}",
            body=_format_approval_body(
                title=clean_title,
                detail=clean_detail,
                raised_by=clean_raiser,
                required_rank=clean_rank,
                target_task_id=clean_target,
            ),
            assignee=clean_approver,
            created_by=clean_raiser,
            triage=True,
            board=board,
        )
        kanban_db.link_tasks(conn, approval_id, clean_target)
        _add_sla_comment(
            conn,
            approval_id=approval_id,
            author=clean_raiser,
            now=int(time.time()) if now is None else int(now),
            respond_within_minutes=respond_within_minutes,
            reminder_after_minutes=reminder_after_minutes,
            escalate_rank_after_minutes=escalate_rank_after_minutes,
        )
        kanban_db.add_comment(
            conn,
            clean_target,
            clean_raiser,
            "\n".join(
                [
                    TARGET_MARKER,
                    f"Approval: {approval_id}",
                    f"Required rank: {clean_rank}",
                    f"Approver: {clean_approver}",
                    f"Summary: {clean_title}",
                ]
            ),
        )
        return _approval_from_conn(conn, board=board, approval_id=approval_id)


def escalate_approval(
    *,
    board_slug: str,
    approval_id: str,
    to_rank: int,
    to_approver_profile: str,
    reason: str,
    actor: str,
    actor_rank: int,
    actor_kind: str = "human",
    respond_within_minutes: int = 120,
    reminder_after_minutes: int = 60,
    escalate_rank_after_minutes: int = 180,
    now: int | None = None,
) -> ApprovalView:
    """Raise a pending approval's required rank; lowering it is impossible."""

    board = _board(board_slug)
    clean_actor, clean_actor_rank = _escalation_actor(actor, actor_rank, actor_kind)
    if str(actor_kind).strip().lower() == "human" and clean_actor_rank < ESCALATE_MIN_RANK:
        raise PermissionError(
            f"approval escalation requires rank {ESCALATE_MIN_RANK} or higher"
        )
    clean_to_rank = _rank(to_rank, label="target rank")
    clean_approver = _required_text(to_approver_profile, label="target approver profile")
    clean_reason = _required_text(reason, label="escalation reason")

    with kanban_db.connect_closing(board=board) as conn:
        approval = _approval_from_conn(
            conn, board=board, approval_id=_required_text(approval_id, label="approval id")
        )
        if approval.status != "pending":
            raise RuntimeError(f"cannot escalate {approval.status} approval '{approval_id}'")
        if clean_to_rank <= approval.required_rank:
            raise ValueError(
                f"target rank must be above current required rank {approval.required_rank}"
            )
        if not kanban_db.assign_task(conn, approval.approval_id, clean_approver):
            raise RuntimeError(f"could not route approval '{approval.approval_id}'")
        kanban_db.add_comment(
            conn,
            approval.approval_id,
            clean_actor,
            "\n".join(
                [
                    ESCALATION_MARKER,
                    f"From rank: {approval.required_rank}",
                    f"To rank: {clean_to_rank}",
                    f"Actor: {clean_actor}",
                    f"Actor rank: {clean_actor_rank}",
                    f"Approver: {clean_approver}",
                    f"Reason: {_single_line(clean_reason)}",
                ]
            ),
        )
        _add_sla_comment(
            conn,
            approval_id=approval.approval_id,
            author=clean_actor,
            now=int(time.time()) if now is None else int(now),
            respond_within_minutes=respond_within_minutes,
            reminder_after_minutes=reminder_after_minutes,
            escalate_rank_after_minutes=escalate_rank_after_minutes,
        )
        kanban_db.add_comment(
            conn,
            approval.target_task_id,
            clean_actor,
            f"{TARGET_MARKER}\nApproval: {approval.approval_id}\n"
            f"Escalated from rank {approval.required_rank} to {clean_to_rank}.\n"
            f"Reason: {_single_line(clean_reason)}",
        )
        return _approval_from_conn(conn, board=board, approval_id=approval.approval_id)


def _claim_pending(conn, approval: ApprovalView, *, actor: str) -> None:
    if approval.status != "pending":
        raise RuntimeError(
            f"approval '{approval.approval_id}' is already {approval.status} and immutable"
        )
    if not kanban_db.specify_triage_task(
        conn, approval.approval_id, author=actor
    ):
        raise RuntimeError(
            f"approval '{approval.approval_id}' changed while the decision was being made"
        )


def _decide_approval(
    *,
    board_slug: str,
    approval_id: str,
    decision: str,
    actor: str,
    actor_rank: int,
    note: str = "",
    actor_kind: str = "human",
    acting_as: str | None = None,
) -> ApprovalView:
    """Approve or reject a pending approval after rechecking actor rank."""

    board = _board(board_slug)
    clean_actor, clean_actor_rank = _human_actor(actor, actor_rank, actor_kind)
    clean_decision = str(decision).strip().lower()
    if clean_decision not in {"approved", "rejected"}:
        raise ValueError("decision must be 'approved' or 'rejected'")
    clean_note = _single_line(note)
    clean_acting_as = (
        _required_text(acting_as, label="acting as") if acting_as is not None else None
    )
    recorded_actor = (
        f"{clean_actor} (as {clean_acting_as})" if clean_acting_as else clean_actor
    )

    with kanban_db.connect_closing(board=board) as conn:
        approval = _approval_from_conn(
            conn, board=board, approval_id=_required_text(approval_id, label="approval id")
        )
        if clean_actor_rank < approval.required_rank:
            raise PermissionError(
                f"approval requires rank {approval.required_rank}; actor has {clean_actor_rank}"
            )
        if (
            clean_actor_rank == approval.required_rank
            and (clean_acting_as or clean_actor).lower()
            != approval.approver_profile.lower()
        ):
            raise PermissionError(
                f"rank-{approval.required_rank} peer '{clean_actor}' cannot decide an "
                f"approval routed to '{approval.approver_profile}'"
            )
        _claim_pending(conn, approval, actor=clean_actor)
        if clean_decision == "approved":
            changed = kanban_db.complete_task(
                conn,
                approval.approval_id,
                result=f"Approved by {recorded_actor}",
                summary=clean_note or f"Approved by rank {clean_actor_rank}",
            )
        else:
            changed = kanban_db.block_task(
                conn,
                approval.approval_id,
                reason=clean_note or f"Rejected by {recorded_actor}",
                kind="needs_input",
            )
        if not changed:
            raise RuntimeError(
                f"could not persist {clean_decision} state for approval '{approval_id}'"
            )
        kanban_db.add_comment(
            conn,
            approval.approval_id,
            clean_actor,
            "\n".join(
                [
                    DECISION_MARKER,
                    f"Decision: {clean_decision}",
                    f"Actor: {recorded_actor}",
                    f"Actor rank: {clean_actor_rank}",
                    f"Acting as: {clean_acting_as or ''}",
                    f"Note: {clean_note}",
                ]
            ),
        )
        kanban_db.add_comment(
            conn,
            approval.target_task_id,
            clean_actor,
            f"{TARGET_MARKER}\nApproval: {approval.approval_id}\n"
            f"Decision: {clean_decision}\nActor: {recorded_actor}\nNote: {clean_note}",
        )
        return _approval_from_conn(conn, board=board, approval_id=approval.approval_id)


def decide_approval(
    *,
    board_slug: str,
    approval_id: str,
    decision: str,
    actor: str,
    actor_rank: int,
    note: str = "",
    actor_kind: str = "human",
) -> ApprovalView:
    """Public decision edge; acting-as authority is not caller-selectable."""

    return _decide_approval(
        board_slug=board_slug,
        approval_id=approval_id,
        decision=decision,
        actor=actor,
        actor_rank=actor_rank,
        note=note,
        actor_kind=actor_kind,
    )


def _decide_approval_as_delegate(
    *,
    board_slug: str,
    approval_id: str,
    decision: str,
    actor: str,
    delegated_rank: int,
    acting_as: str,
    note: str = "",
) -> ApprovalView:
    """Package-private edge called only after availability verifies a grant."""

    return _decide_approval(
        board_slug=board_slug,
        approval_id=approval_id,
        decision=decision,
        actor=actor,
        actor_rank=delegated_rank,
        note=note,
        actor_kind="human",
        acting_as=acting_as,
    )


def withdraw_approval(
    *,
    board_slug: str,
    approval_id: str,
    actor: str,
    actor_rank: int,
    reason: str,
    actor_kind: str = "human",
) -> ApprovalView:
    """Withdraw a pending request; only its original raiser may do so."""

    board = _board(board_slug)
    clean_actor, clean_actor_rank = _human_actor(actor, actor_rank, actor_kind)
    clean_reason = _required_text(reason, label="withdrawal reason")
    with kanban_db.connect_closing(board=board) as conn:
        approval = _approval_from_conn(
            conn, board=board, approval_id=_required_text(approval_id, label="approval id")
        )
        if clean_actor != approval.raised_by:
            raise PermissionError("only the approval's original raiser may withdraw it")
        _claim_pending(conn, approval, actor=clean_actor)
        if not kanban_db.archive_task(conn, approval.approval_id):
            raise RuntimeError(f"could not withdraw approval '{approval.approval_id}'")
        kanban_db.add_comment(
            conn,
            approval.approval_id,
            clean_actor,
            "\n".join(
                [
                    DECISION_MARKER,
                    "Decision: withdrawn",
                    f"Actor: {clean_actor}",
                    f"Actor rank: {clean_actor_rank}",
                    f"Note: {_single_line(clean_reason)}",
                ]
            ),
        )
        kanban_db.add_comment(
            conn,
            approval.target_task_id,
            clean_actor,
            f"{TARGET_MARKER}\nApproval: {approval.approval_id}\n"
            f"Decision: withdrawn\nReason: {_single_line(clean_reason)}",
        )
        return _approval_from_conn(conn, board=board, approval_id=approval.approval_id)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--board", required=True, help="Existing Kanban board slug")
    sub = parser.add_subparsers(dest="command", required=True)

    show = sub.add_parser("show")
    show.add_argument("approval_id")

    create = sub.add_parser("raise")
    create.add_argument("--target", required=True)
    create.add_argument("--title", required=True)
    create.add_argument("--detail", required=True)
    create.add_argument("--raised-by", required=True)
    create.add_argument("--required-rank", required=True, type=int)
    create.add_argument("--approver", required=True)

    escalate = sub.add_parser("escalate")
    escalate.add_argument("approval_id")
    escalate.add_argument("--to-rank", required=True, type=int)
    escalate.add_argument("--to-approver", required=True)
    escalate.add_argument("--reason", required=True)
    escalate.add_argument("--actor", required=True)
    escalate.add_argument("--actor-rank", required=True, type=int)

    decide = sub.add_parser("decide")
    decide.add_argument("approval_id")
    decide.add_argument("decision", choices=("approved", "rejected"))
    decide.add_argument("--actor", required=True)
    decide.add_argument("--actor-rank", required=True, type=int)
    decide.add_argument("--note", default="")

    withdraw = sub.add_parser("withdraw")
    withdraw.add_argument("approval_id")
    withdraw.add_argument("--actor", required=True)
    withdraw.add_argument("--actor-rank", required=True, type=int)
    withdraw.add_argument("--reason", required=True)
    args = parser.parse_args(argv)

    try:
        if args.command == "show":
            result = get_approval(board_slug=args.board, approval_id=args.approval_id)
        elif args.command == "raise":
            result = raise_approval(
                board_slug=args.board,
                target_task_id=args.target,
                title=args.title,
                detail=args.detail,
                raised_by=args.raised_by,
                required_rank=args.required_rank,
                approver_profile=args.approver,
            )
        elif args.command == "escalate":
            result = escalate_approval(
                board_slug=args.board,
                approval_id=args.approval_id,
                to_rank=args.to_rank,
                to_approver_profile=args.to_approver,
                reason=args.reason,
                actor=args.actor,
                actor_rank=args.actor_rank,
            )
        elif args.command == "decide":
            result = decide_approval(
                board_slug=args.board,
                approval_id=args.approval_id,
                decision=args.decision,
                actor=args.actor,
                actor_rank=args.actor_rank,
                note=args.note,
            )
        else:
            result = withdraw_approval(
                board_slug=args.board,
                approval_id=args.approval_id,
                actor=args.actor,
                actor_rank=args.actor_rank,
                reason=args.reason,
            )
    except (OSError, PermissionError, RuntimeError, ValueError) as exc:
        print(f"pmo approval: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(asdict(result), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
