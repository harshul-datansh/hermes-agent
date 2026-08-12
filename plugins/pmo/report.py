"""Render a read-only status report for one explicit Hermes Kanban board.

PM-OS does not maintain reporting state of its own.  This utility resolves a
named board through :mod:`hermes_cli.kanban_db` and opens that board's existing
SQLite database in read-only mode.  It never selects the process-wide current
board and never initializes, migrates, or writes a database.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from hermes_cli import kanban_db
from plugins.pmo.cost import CONVERSATION_MARKER


REPORT_STATUSES = (
    "triage",
    "todo",
    "scheduled",
    "ready",
    "running",
    "blocked",
    "review",
    "done",
)
ACTIVE_STATUSES = frozenset(REPORT_STATUSES) - {"done"}


def _read_only_connection(path: Path) -> sqlite3.Connection:
    """Open an existing SQLite database without permitting any writes."""

    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _work_item(task: kanban_db.Task, *, now: int) -> dict[str, Any]:
    created_at = int(task.created_at)
    return {
        "id": task.id,
        "title": task.title,
        "assignee": task.assignee,
        "priority": int(task.priority or 0),
        "created_at": created_at,
        "age_seconds": max(0, now - created_at),
    }


def build_report(*, board_slug: str, now: int | None = None) -> dict[str, Any]:
    """Return current PM status for ``board_slug`` without changing the board.

    A slug is mandatory so automation cannot accidentally report whichever
    board happens to be selected in another terminal or dashboard session.
    Archived cards are omitted from operational counts and work lists.
    """

    normalized = kanban_db._normalize_board_slug(board_slug)
    if not normalized:
        raise ValueError("board slug is required")
    if not kanban_db.board_exists(normalized):
        raise ValueError(f"board '{normalized}' does not exist")

    db_path = kanban_db.kanban_db_path(board=normalized)
    if not db_path.is_file():
        raise ValueError(f"board '{normalized}' has no Kanban database")

    generated_at = int(time.time()) if now is None else int(now)
    metadata = kanban_db.read_board_metadata(normalized)

    with closing(_read_only_connection(db_path)) as conn:
        tasks = [
            task
            for task in kanban_db.list_tasks(
                conn, include_archived=False, order_by="priority"
            )
            if CONVERSATION_MARKER not in str(task.body or "")
        ]
        blocked_reasons: dict[str, str] = {}
        for task in tasks:
            if task.status != "blocked":
                continue
            blocked_runs = kanban_db.list_runs(
                conn,
                task.id,
                state_type="outcome",
                state_name="blocked",
            )
            for run in reversed(blocked_runs):
                if run.summary and run.summary.strip():
                    blocked_reasons[task.id] = run.summary
                    break

    status_counts = {status: 0 for status in REPORT_STATUSES}
    status_counts.update(Counter(task.status for task in tasks))

    blocked_work: list[dict[str, Any]] = []
    ready_work: list[dict[str, Any]] = []
    load_counts: dict[str | None, Counter[str]] = defaultdict(Counter)
    for task in tasks:
        status = task.status
        assignee = task.assignee
        load_counts[assignee][status] += 1
        if status == "ready":
            ready_work.append(_work_item(task, now=generated_at))
        elif status == "blocked":
            item = _work_item(task, now=generated_at)
            item.update(
                {
                    "block_kind": task.block_kind,
                    "reason": blocked_reasons.get(task.id),
                }
            )
            blocked_work.append(item)

    assignee_load = []
    for assignee, counts in load_counts.items():
        by_status = {status: int(counts.get(status, 0)) for status in REPORT_STATUSES}
        assignee_load.append(
            {
                "assignee": assignee,
                "active_total": sum(counts.get(status, 0) for status in ACTIVE_STATUSES),
                "done_total": int(counts.get("done", 0)),
                "by_status": by_status,
            }
        )
    assignee_load.sort(
        key=lambda entry: (
            -entry["active_total"],
            entry["assignee"] is None,
            entry["assignee"] or "",
        )
    )

    return {
        "board": {
            "slug": normalized,
            "name": metadata.get("name") or normalized,
            "project_id": metadata.get("project_id"),
        },
        "generated_at": generated_at,
        "status_counts": status_counts,
        "blocked_work": blocked_work,
        "ready_work": ready_work,
        "assignee_load": assignee_load,
    }


def _format_age(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3_600:
        return f"{seconds // 60}m"
    if seconds < 86_400:
        return f"{seconds // 3_600}h"
    return f"{seconds // 86_400}d"


def _format_item(item: dict[str, Any], *, blocked: bool = False) -> str:
    assignee = item["assignee"] or "unassigned"
    line = (
        f"- [{item['id']}] {item['title']} | {assignee} | "
        f"priority {item['priority']} | age {_format_age(item['age_seconds'])}"
    )
    if blocked:
        detail = item.get("reason") or "reason not recorded"
        kind = item.get("block_kind") or "unspecified"
        line += f" | {kind}: {detail}"
    return line


def format_human(report: dict[str, Any]) -> str:
    """Render ``build_report`` output for a terminal or task comment."""

    board = report["board"]
    generated = datetime.fromtimestamp(
        report["generated_at"], tz=timezone.utc
    ).isoformat().replace("+00:00", "Z")
    counts = report["status_counts"]
    lines = [
        f"PM-OS status: {board['name']} ({board['slug']})",
        f"Generated: {generated}",
        "Status: " + " | ".join(f"{status} {counts[status]}" for status in REPORT_STATUSES),
        "",
        f"Blocked work ({len(report['blocked_work'])})",
    ]
    lines.extend(
        _format_item(item, blocked=True) for item in report["blocked_work"]
    )
    if not report["blocked_work"]:
        lines.append("- none")

    lines.extend(("", f"Ready work ({len(report['ready_work'])})"))
    lines.extend(_format_item(item) for item in report["ready_work"])
    if not report["ready_work"]:
        lines.append("- none")

    lines.extend(("", "Assignee load"))
    for load in report["assignee_load"]:
        assignee = load["assignee"] or "unassigned"
        active_parts = [
            f"{status} {load['by_status'][status]}"
            for status in REPORT_STATUSES
            if status != "done" and load["by_status"][status]
        ]
        detail = ", ".join(active_parts) if active_parts else "no active work"
        lines.append(
            f"- {assignee}: {load['active_total']} active ({detail}); "
            f"done {load['done_total']}"
        )
    if not report["assignee_load"]:
        lines.append("- none")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--board",
        required=True,
        help="Explicit Hermes Kanban board slug (the current board is never inferred)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit stable machine-readable JSON instead of the human report",
    )
    args = parser.parse_args(argv)

    try:
        report = build_report(board_slug=args.board)
    except (OSError, sqlite3.Error, ValueError) as exc:
        print(f"pmo report: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(format_human(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
