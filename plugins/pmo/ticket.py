"""Create one structured PM-OS ticket on an explicit Hermes Kanban board.

The utility is deliberately a thin edge around :mod:`hermes_cli.kanban_db`.
Kanban remains the only task store and owns status derivation, dependency
links, assignment normalization, project/worktree routing, and audit events.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from typing import Iterable, Sequence

from hermes_cli import kanban_db


@dataclass(frozen=True)
class TicketResult:
    """The Kanban record created (or reused through idempotency)."""

    board_slug: str
    task_id: str
    status: str
    assignee: str
    body: str


def _required_text(value: str, *, label: str) -> str:
    cleaned = str(value).strip()
    if not cleaned:
        raise ValueError(f"{label} must not be empty")
    return cleaned


def _items(values: Iterable[str], *, label: str, required: bool = False) -> list[str]:
    cleaned = [_required_text(value, label=label) for value in values]
    if required and not cleaned:
        raise ValueError(f"at least one {label} is required")
    return cleaned


def _bullet_section(heading: str, items: Sequence[str], *, empty: str) -> list[str]:
    lines = [f"{heading}:"]
    lines.extend(f"- {item}" for item in items)
    if not items:
        lines.append(f"- {empty}")
    return lines


def format_ticket_body(
    *,
    outcome: str,
    product_context: str | None = None,
    user_impact: str | None = None,
    constraints: Iterable[str],
    acceptance_criteria: Iterable[str],
    dependencies: Iterable[str],
    parent_task_ids: Iterable[str],
    evidence: Iterable[str],
) -> str:
    """Build the standard ticket body used by PM-OS operators and agents."""

    clean_outcome = _required_text(outcome, label="outcome")
    clean_constraints = _items(constraints, label="constraint")
    clean_acceptance = _items(
        acceptance_criteria,
        label="acceptance criterion",
        required=True,
    )
    clean_dependencies = _items(dependencies, label="dependency")
    clean_parents = _items(parent_task_ids, label="parent task id")
    clean_evidence = _items(evidence, label="evidence requirement", required=True)

    dependency_items = [f"Kanban task `{task_id}`" for task_id in clean_parents]
    dependency_items.extend(clean_dependencies)

    clean_product_context = (
        _required_text(product_context, label="product context")
        if product_context is not None else ""
    )
    clean_user_impact = (
        _required_text(user_impact, label="user impact")
        if user_impact is not None else ""
    )
    lines: list[str] = []
    if clean_product_context:
        lines.extend(["Product context:", clean_product_context, ""])
    if clean_user_impact:
        lines.extend(["User impact:", clean_user_impact, ""])
    lines.extend(["Outcome:", clean_outcome, ""])
    lines.extend(
        _bullet_section(
            "Scope and constraints",
            clean_constraints,
            empty="No additional constraints recorded.",
        )
    )
    lines.extend(("", "Acceptance criteria:"))
    lines.extend(f"- [ ] {criterion}" for criterion in clean_acceptance)
    lines.append("")
    lines.extend(
        _bullet_section(
            "Dependencies and risks",
            dependency_items,
            empty="No dependencies or risks identified.",
        )
    )
    lines.extend(("", "Evidence required to close:"))
    lines.extend(f"- [ ] {item}" for item in clean_evidence)
    return "\n".join(lines)


def create_ticket(
    *,
    board_slug: str,
    title: str,
    outcome: str,
    product_context: str | None = None,
    user_impact: str | None = None,
    assignee: str,
    acceptance_criteria: Iterable[str],
    evidence: Iterable[str],
    constraints: Iterable[str] = (),
    dependencies: Iterable[str] = (),
    parent_task_ids: Iterable[str] = (),
    priority: int = 0,
    triage: bool = False,
    max_runtime_seconds: int = 3_600,
    max_retries: int = 3,
    created_by: str = "datansh-pm-os",
    idempotency_key: str | None = None,
) -> TicketResult:
    """Create a structured ticket using only the named board's Kanban API.

    Status is never invented or patched here. ``kanban_db.create_task`` places
    the ticket in ``triage`` when requested, ``todo`` while a parent is open,
    or ``ready`` when its parent dependencies are complete.
    """

    normalized_board = kanban_db._normalize_board_slug(board_slug)
    if not normalized_board:
        raise ValueError("board slug is required")
    if not kanban_db.board_exists(normalized_board):
        raise ValueError(f"board '{normalized_board}' does not exist")

    clean_title = _required_text(title, label="title")
    clean_assignee = _required_text(assignee, label="assignee")
    clean_created_by = _required_text(created_by, label="created-by")
    if int(max_runtime_seconds) < 1:
        raise ValueError("max runtime seconds must be at least 1")
    if int(max_retries) < 1:
        raise ValueError("max retries must be at least 1")
    parents = tuple(_items(parent_task_ids, label="parent task id"))
    body = format_ticket_body(
        outcome=outcome,
        product_context=product_context,
        user_impact=user_impact,
        constraints=constraints,
        acceptance_criteria=acceptance_criteria,
        dependencies=dependencies,
        parent_task_ids=parents,
        evidence=evidence,
    )

    with kanban_db.connect_closing(board=normalized_board) as conn:
        task_id = kanban_db.create_task(
            conn,
            title=clean_title,
            body=body,
            assignee=clean_assignee,
            created_by=clean_created_by,
            priority=int(priority),
            max_runtime_seconds=int(max_runtime_seconds),
            max_retries=int(max_retries),
            parents=parents,
            triage=bool(triage),
            idempotency_key=idempotency_key,
            board=normalized_board,
        )
        task = kanban_db.get_task(conn, task_id)

    if task is None:  # Defensive: create_task returning no row is a DB contract breach.
        raise RuntimeError(f"Kanban did not return created task '{task_id}'")
    return TicketResult(
        board_slug=normalized_board,
        task_id=task.id,
        status=task.status,
        assignee=task.assignee or "",
        body=task.body or "",
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--board", required=True, help="Existing Kanban board slug")
    parser.add_argument("--title", required=True, help="Short ticket title")
    parser.add_argument("--outcome", required=True, help="Observable desired outcome")
    parser.add_argument("--assignee", required=True, help="Hermes profile assigned to the ticket")
    parser.add_argument(
        "--acceptance",
        action="append",
        required=True,
        help="Acceptance criterion; repeat for every criterion",
    )
    parser.add_argument(
        "--evidence",
        action="append",
        required=True,
        help="Evidence required before closure; repeat as needed",
    )
    parser.add_argument(
        "--constraint",
        action="append",
        default=[],
        help="Scope constraint; repeat as needed",
    )
    parser.add_argument(
        "--dependency",
        action="append",
        default=[],
        help="Non-ticket dependency or risk; repeat as needed",
    )
    parser.add_argument(
        "--parent",
        action="append",
        default=[],
        help="Parent Kanban task id; repeat to create native dependency links",
    )
    parser.add_argument("--priority", type=int, default=0, help="Kanban priority tiebreaker")
    parser.add_argument(
        "--max-runtime-seconds",
        type=int,
        default=3_600,
        help="Native Kanban worker runtime cap (default: 3600)",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=3,
        help="Native Kanban consecutive-failure limit (default: 3)",
    )
    parser.add_argument(
        "--triage",
        action="store_true",
        help="Use Kanban's existing triage status before the ticket is executable",
    )
    parser.add_argument("--created-by", default="datansh-pm-os", help="Audit author")
    parser.add_argument("--idempotency-key", default=None, help="Optional Kanban idempotency key")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable result")
    args = parser.parse_args(argv)

    try:
        result = create_ticket(
            board_slug=args.board,
            title=args.title,
            outcome=args.outcome,
            assignee=args.assignee,
            acceptance_criteria=args.acceptance,
            evidence=args.evidence,
            constraints=args.constraint,
            dependencies=args.dependency,
            parent_task_ids=args.parent,
            priority=args.priority,
            max_runtime_seconds=args.max_runtime_seconds,
            max_retries=args.max_retries,
            triage=args.triage,
            created_by=args.created_by,
            idempotency_key=args.idempotency_key,
        )
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"pmo ticket: {exc}", file=sys.stderr)
        return 2

    payload = {
        "assignee": result.assignee,
        "board": result.board_slug,
        "status": result.status,
        "task_id": result.task_id,
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(
            f"Created {result.task_id} on '{result.board_slug}' "
            f"({result.status}, assignee={result.assignee})"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
