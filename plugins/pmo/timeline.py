"""Build a redacted, chronological PM-OS ticket timeline from native Kanban data."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from typing import Any, Sequence

from agent.redact import redact_sensitive_text
from hermes_cli import kanban_db


APPROVAL_MARKER = "[pmo:approval:v1]"


@dataclass(frozen=True)
class TimelineItem:
    timestamp: int
    source: str
    kind: str
    subject_id: str
    actor: str | None
    summary: str
    details: dict[str, Any]


def _redact(value: Any) -> Any:
    if isinstance(value, str):
        return redact_sensitive_text(value, force=True)
    if isinstance(value, dict):
        return {str(key): _redact(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(item) for item in value]
    return value


def _event_items(conn, task_id: str) -> list[TimelineItem]:
    items: list[TimelineItem] = []
    for event in kanban_db.list_events(conn, task_id):
        payload = _redact(event.payload or {})
        actor = payload.get("actor") or payload.get("author")
        items.append(
            TimelineItem(
                timestamp=event.created_at,
                source="task_event",
                kind=event.kind,
                subject_id=task_id,
                actor=str(actor) if actor else None,
                summary=str(payload.get("reason") or payload.get("summary") or event.kind),
                details={**payload, "run_id": event.run_id},
            )
        )
    return items


def _comment_items(conn, task_id: str, *, source: str = "comment") -> list[TimelineItem]:
    return [
        TimelineItem(
            timestamp=comment.created_at,
            source=source,
            kind="commented",
            subject_id=task_id,
            actor=comment.author,
            summary=_redact(comment.body.splitlines()[0] if comment.body else "comment"),
            details={"body": _redact(comment.body), "comment_id": comment.id},
        )
        for comment in kanban_db.list_comments(conn, task_id)
    ]


def _run_items(conn, task_id: str) -> list[TimelineItem]:
    items: list[TimelineItem] = []
    for run in kanban_db.list_runs(conn, task_id):
        items.append(
            TimelineItem(
                timestamp=run.started_at,
                source="task_run",
                kind="run_started",
                subject_id=task_id,
                actor=run.profile,
                summary=f"Run {run.id} started",
                details={"run_id": run.id, "profile": run.profile, "step_key": run.step_key},
            )
        )
        if run.ended_at is not None:
            items.append(
                TimelineItem(
                    timestamp=run.ended_at,
                    source="task_run",
                    kind="run_ended",
                    subject_id=task_id,
                    actor=run.profile,
                    summary=_redact(run.summary or run.outcome or "run ended"),
                    details=_redact(
                        {
                            "run_id": run.id,
                            "outcome": run.outcome,
                            "duration_seconds": max(0, run.ended_at - run.started_at),
                            "metadata": run.metadata or {},
                            "error": run.error,
                        }
                    ),
                )
            )
    return items


def build_timeline(*, board_slug: str, task_id: str, limit: int = 500) -> list[TimelineItem]:
    """Return one bounded timeline for a task and its native approval parents."""

    board = kanban_db._normalize_board_slug(board_slug)
    if not board or not kanban_db.board_exists(board):
        raise ValueError(f"board does not exist: {board_slug!r}")
    clean_task = str(task_id).strip()
    if not clean_task:
        raise ValueError("task id is required")
    if not 1 <= int(limit) <= 2_000:
        raise ValueError("limit must be between 1 and 2000")

    with kanban_db.connect_closing(board=board) as conn:
        if kanban_db.get_task(conn, clean_task) is None:
            raise ValueError(f"task '{clean_task}' does not exist on board '{board}'")
        items = [
            *_event_items(conn, clean_task),
            *_comment_items(conn, clean_task),
            *_run_items(conn, clean_task),
        ]
        for parent_id in kanban_db.parent_ids(conn, clean_task):
            parent = kanban_db.get_task(conn, parent_id)
            if parent is None or not (parent.body or "").startswith(APPROVAL_MARKER):
                continue
            items.extend(_event_items(conn, parent_id))
            items.extend(_comment_items(conn, parent_id, source="approval"))

    # Native timestamps are second-granularity, so deterministic source/id keys
    # make repeated reads stable without inventing a second sequence store.
    items.sort(key=lambda item: (item.timestamp, item.source, item.subject_id, item.kind))
    return items[-int(limit) :]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--board", required=True)
    parser.add_argument("--task", required=True)
    parser.add_argument("--limit", type=int, default=500)
    args = parser.parse_args(argv)
    try:
        timeline = build_timeline(board_slug=args.board, task_id=args.task, limit=args.limit)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"pmo timeline: {exc}", file=sys.stderr)
        return 2
    print(json.dumps([asdict(item) for item in timeline], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
