"""Read-only project and portfolio projections over native Hermes records."""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable, Sequence

from hermes_cli import kanban_db, projects_db


APPROVAL_MARKER = "[pmo:approval:v1]"
CONVERSATION_SCHEMA = "datansh-pmo-conversation.v1"
_CACHE_SECONDS = 30.0
_CACHE: dict[tuple[tuple[str, ...], int | None], tuple[float, list["ProjectRollup"]]] = {}


@dataclass(frozen=True)
class ProjectRollup:
    project_id: str
    slug: str
    name: str
    board_slug: str
    status: str
    total: int
    done: int
    in_flight: int
    blocked: int
    awaiting_approval: int
    needs_you: int
    spend_usd: str
    last_activity: int

    @property
    def progress(self) -> str:
        return f"{self.done}/{self.total}"


def clear_cache() -> None:
    _CACHE.clear()


def _approval_rank(body: str | None) -> int | None:
    text = str(body or "")
    if not text.startswith(APPROVAL_MARKER):
        return None
    for line in text.splitlines():
        if line.startswith("Initial required rank:"):
            try:
                return int(line.partition(":")[2].strip())
            except ValueError:
                return None
    return None


def _is_conversation(body: str | None) -> bool:
    lines = str(body or "").splitlines()
    if not lines:
        return False
    first_line = lines[0].strip()
    if not (first_line.startswith("<!-- ") and first_line.endswith(" -->")):
        return False
    try:
        payload = json.loads(first_line[5:-4])
    except (TypeError, ValueError, json.JSONDecodeError):
        return False
    return isinstance(payload, dict) and payload.get("schema") == CONVERSATION_SCHEMA


def _run_cost(metadata: dict | None) -> Decimal:
    usage = (metadata or {}).get("usage")
    if not isinstance(usage, dict):
        return Decimal("0")
    try:
        return Decimal(str(usage.get("cost_usd") or "0"))
    except InvalidOperation:
        return Decimal("0")


def _project_rollup(project: projects_db.Project, *, actor_rank: int | None) -> ProjectRollup:
    board = projects_db.normalize_slug(project.board_slug)
    if not board or not kanban_db.board_exists(board):
        return ProjectRollup(
            project.id, project.slug, project.name, board or "", "Unavailable",
            0, 0, 0, 0, 0, 0, "0.00", project.created_at,
        )

    with kanban_db.connect_closing(board=board) as conn:
        # Archived cards (including lifecycle cancellations) are retained but
        # deliberately excluded from the progress denominator.
        tasks = [
            task
            for task in kanban_db.list_tasks(conn, include_archived=False, order_by="created")
            if task.project_id == project.id
        ]
        approvals = [
            task for task in tasks
            if _approval_rank(task.body) is not None and task.status == "triage"
        ]
        delivery = [
            task
            for task in tasks
            if _approval_rank(task.body) is None and not _is_conversation(task.body)
        ]
        done = sum(task.status == "done" for task in delivery)
        blocked = sum(task.status == "blocked" for task in delivery)
        in_flight = sum(task.status in {"ready", "running", "review"} for task in delivery)
        needs_you = sum(
            actor_rank is not None and actor_rank >= int(_approval_rank(task.body) or 0)
            for task in approvals
        )
        spend = Decimal("0")
        last_activity = project.created_at
        for task in tasks:
            last_activity = max(
                last_activity,
                task.created_at,
                task.started_at or 0,
                task.completed_at or 0,
            )
            for event in kanban_db.list_events(conn, task.id):
                last_activity = max(last_activity, event.created_at)
            for comment in kanban_db.list_comments(conn, task.id):
                last_activity = max(last_activity, comment.created_at)
            for run in kanban_db.list_runs(conn, task.id):
                last_activity = max(last_activity, run.ended_at or run.started_at)
                spend += _run_cost(run.metadata)

    status = "At risk" if blocked else "Active"
    return ProjectRollup(
        project.id,
        project.slug,
        project.name,
        board,
        status,
        len(delivery),
        done,
        in_flight,
        blocked,
        len(approvals),
        needs_you,
        str(spend.quantize(Decimal("0.01"))),
        last_activity,
    )


def portfolio(
    *,
    visible_project_ids: Iterable[str],
    actor_rank: int | None = None,
    use_cache: bool = True,
) -> list[ProjectRollup]:
    """Return only explicitly authorized projects, ordered by actionability.

    The caller must supply the membership/rank-filtered ids from the access
    policy. There is intentionally no "all projects" default on a server-side
    read path.
    """

    visible = tuple(sorted({str(value) for value in visible_project_ids if str(value)}))
    key = (visible, actor_rank)
    now = time.monotonic()
    cached = _CACHE.get(key)
    if use_cache and cached and now - cached[0] < _CACHE_SECONDS:
        return list(cached[1])
    with projects_db.connect_closing() as conn:
        projects = [
            project for project in projects_db.list_projects(conn)
            if project.id in visible or project.slug in visible
        ]
    rows = [_project_rollup(project, actor_rank=actor_rank) for project in projects]
    rows.sort(key=lambda row: (-row.needs_you, -row.last_activity, row.slug))
    _CACHE[key] = (now, rows)
    return list(rows)


def project_overview(
    *, project_ref: str, visible_project_ids: Iterable[str], actor_rank: int | None = None
) -> ProjectRollup:
    with projects_db.connect_closing() as conn:
        project = projects_db.get_project(conn, project_ref)
    if project is None:
        raise ValueError(f"unknown project: {project_ref}")
    visible = {str(value) for value in visible_project_ids}
    if project.id not in visible and project.slug not in visible:
        raise PermissionError(f"project is not visible to this principal: {project_ref}")
    return _project_rollup(project, actor_rank=actor_rank)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", nargs="*", help="Authorized project ids/slugs")
    parser.add_argument("--rank", type=int)
    args = parser.parse_args(argv)
    rows = portfolio(visible_project_ids=args.project, actor_rank=args.rank, use_cache=False)
    print(json.dumps([asdict(row) | {"progress": row.progress} for row in rows], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
