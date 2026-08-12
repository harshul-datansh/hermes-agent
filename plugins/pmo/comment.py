"""Add a structured PM-OS comment to an existing Hermes Kanban task.

This edge utility only formats a comment body. ``hermes_cli.kanban_db`` remains
the comment store and emits the normal Kanban ``commented`` audit event.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from typing import Iterable, Sequence

from agent.redact import redact_sensitive_text
from hermes_cli import kanban_db


COMMENT_TYPES = ("handoff", "blocker", "review")
REVIEW_VERDICTS = ("approved", "changes-requested", "observations")


@dataclass(frozen=True)
class CommentResult:
    board_slug: str
    task_id: str
    comment_id: int
    comment_type: str
    author: str
    body: str


def _required_text(value: str | None, *, label: str) -> str:
    cleaned = str(value or "").strip()
    if not cleaned:
        raise ValueError(f"{label} must not be empty")
    return cleaned


def _items(values: Iterable[str], *, label: str) -> list[str]:
    return [_required_text(value, label=label) for value in values]


def _bullets(heading: str, values: Iterable[str], *, empty: str) -> list[str]:
    items = _items(values, label=heading.lower().rstrip(":"))
    return [f"{heading}:", *(f"- {item}" for item in items or [empty])]


def format_comment_body(
    *,
    comment_type: str,
    summary: str,
    owner: str | None = None,
    verdict: str | None = None,
    evidence: Iterable[str] = (),
    next_actions: Iterable[str] = (),
) -> str:
    """Format one documented PM-OS comment type.

    ``owner`` is the receiving owner for a handoff and the resolution owner for
    a blocker. Reviews use ``verdict`` instead. Evidence and next actions are
    optional because a short coordination update must remain easy to record.
    """

    normalized_type = str(comment_type).strip().lower()
    if normalized_type not in COMMENT_TYPES:
        raise ValueError(f"comment type must be one of: {', '.join(COMMENT_TYPES)}")
    clean_summary = _required_text(summary, label="summary")
    clean_evidence = _items(evidence, label="evidence")
    clean_actions = _items(next_actions, label="next action")

    lines = [f"[pmo:{normalized_type}]", f"Summary: {clean_summary}"]
    if normalized_type in {"handoff", "blocker"}:
        role = "Receiving owner" if normalized_type == "handoff" else "Resolution owner"
        lines.append(f"{role}: {_required_text(owner, label=role.lower())}")
        if verdict is not None:
            raise ValueError("verdict is only valid for review comments")
    else:
        clean_verdict = _required_text(verdict, label="review verdict").lower()
        if clean_verdict not in REVIEW_VERDICTS:
            raise ValueError(
                f"review verdict must be one of: {', '.join(REVIEW_VERDICTS)}"
            )
        if owner is not None:
            raise ValueError("owner is only valid for handoff and blocker comments")
        lines.append(f"Verdict: {clean_verdict}")

    lines.append("")
    lines.extend(_bullets("Evidence", clean_evidence, empty="No evidence attached."))
    lines.append("")
    lines.extend(_bullets("Next actions", clean_actions, empty="No next action recorded."))
    return "\n".join(lines)


def add_structured_comment(
    *,
    board_slug: str,
    task_id: str,
    comment_type: str,
    author: str,
    summary: str,
    owner: str | None = None,
    verdict: str | None = None,
    evidence: Iterable[str] = (),
    next_actions: Iterable[str] = (),
) -> CommentResult:
    """Add the formatted body through the existing Kanban comment API."""

    normalized_board = kanban_db._normalize_board_slug(board_slug)
    if not normalized_board:
        raise ValueError("board slug is required")
    if not kanban_db.board_exists(normalized_board):
        raise ValueError(f"board '{normalized_board}' does not exist")
    clean_task_id = _required_text(task_id, label="task id")
    clean_author = _required_text(author, label="author")
    body = format_comment_body(
        comment_type=comment_type,
        summary=summary,
        owner=owner,
        verdict=verdict,
        evidence=evidence,
        next_actions=next_actions,
    )
    # Comments are durable, user-visible Kanban records.  Apply Hermes' own
    # redaction policy at the persistence boundary so PM helpers cannot turn a
    # credential accidentally pasted into a handoff into permanent history.
    body = redact_sensitive_text(body, force=True)

    with kanban_db.connect_closing(board=normalized_board) as conn:
        if kanban_db.get_task(conn, clean_task_id) is None:
            raise ValueError(
                f"task '{clean_task_id}' does not exist on board '{normalized_board}'"
            )
        comment_id = kanban_db.add_comment(conn, clean_task_id, clean_author, body)

    return CommentResult(
        board_slug=normalized_board,
        task_id=clean_task_id,
        comment_id=comment_id,
        comment_type=str(comment_type).strip().lower(),
        author=clean_author,
        body=body,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--board", required=True, help="Existing Kanban board slug")
    parser.add_argument("--task", required=True, help="Existing task ID on that board")
    parser.add_argument("--type", required=True, choices=COMMENT_TYPES)
    parser.add_argument("--author", required=True, help="Comment author/profile")
    parser.add_argument("--summary", required=True, help="Concise durable update")
    parser.add_argument(
        "--owner",
        help="Receiving owner for a handoff or resolution owner for a blocker",
    )
    parser.add_argument("--verdict", choices=REVIEW_VERDICTS, help="Review outcome")
    parser.add_argument("--evidence", action="append", default=[], help="Repeat as needed")
    parser.add_argument(
        "--next-action", action="append", default=[], help="Repeat as needed"
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable result")
    args = parser.parse_args(argv)

    try:
        result = add_structured_comment(
            board_slug=args.board,
            task_id=args.task,
            comment_type=args.type,
            author=args.author,
            summary=args.summary,
            owner=args.owner,
            verdict=args.verdict,
            evidence=args.evidence,
            next_actions=args.next_action,
        )
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"pmo comment: {exc}", file=sys.stderr)
        return 2

    payload = {
        "author": result.author,
        "board": result.board_slug,
        "comment_id": result.comment_id,
        "task_id": result.task_id,
        "type": result.comment_type,
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(
            f"Added {result.comment_type} comment {result.comment_id} "
            f"to {result.task_id} on '{result.board_slug}'"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
