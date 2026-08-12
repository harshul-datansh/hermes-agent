"""Seed and measure an isolated PM-OS six-month target-scale fixture."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Iterator

from hermes_constants import reset_hermes_home_override, set_hermes_home_override
from hermes_cli import kanban_db
from plugins.pmo import bootstrap
from plugins.pmo.performance import measure_board


MARKER = ".datansh-pmo-loadgen.json"


@contextmanager
def isolated_home(root: Path) -> Iterator[None]:
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    marker = root / MARKER
    if marker.exists():
        payload = json.loads(marker.read_text(encoding="utf-8"))
        if payload != {"root": str(root), "schema": "datansh-pmo-loadgen.v1"}:
            raise ValueError(f"invalid PMO loadgen marker at {marker}")
    elif any(root.iterdir()):
        raise ValueError("loadgen root must be empty or marker-verified")
    else:
        marker.write_text(
            json.dumps({"root": str(root), "schema": "datansh-pmo-loadgen.v1"}) + "\n",
            encoding="utf-8",
        )

    keys = ("HERMES_HOME", "HERMES_KANBAN_HOME", "HERMES_KANBAN_DB", "HERMES_KANBAN_BOARD")
    previous = {key: os.environ.get(key) for key in keys}
    os.environ["HERMES_HOME"] = str(root / "hermes")
    os.environ["HERMES_KANBAN_HOME"] = str(root / "hermes")
    os.environ.pop("HERMES_KANBAN_DB", None)
    os.environ.pop("HERMES_KANBAN_BOARD", None)
    token = set_hermes_home_override(root / "hermes")
    try:
        yield
    finally:
        reset_hermes_home_override(token)
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def run_load(root: Path, *, tickets: int, messages: int, repeats: int = 5) -> dict:
    with isolated_home(root):
        workspace = root / "workspace"
        workspace.mkdir(exist_ok=True)
        boot = bootstrap.bootstrap_project(
            slug="scale", name="PMO scale fixture", workspace=workspace
        )
        task_ids: list[str] = []
        with kanban_db.connect_closing(board=boot.board_slug) as conn:
            existing = kanban_db.list_tasks(conn, include_archived=True)
            task_ids.extend(task.id for task in existing)
            for index in range(len(existing), max(0, tickets)):
                task_ids.append(
                    kanban_db.create_task(
                        conn,
                        title=f"Scale ticket {index:04d}",
                        body="Synthetic release-scale ticket.",
                        created_by="pmo-loadgen",
                        workspace_kind="scratch",
                        project_id=boot.project_id,
                        idempotency_key=f"pmo-loadgen-task-{index}",
                        board=boot.board_slug,
                    )
                )
            if not task_ids:
                task_ids.append(
                    kanban_db.create_task(
                        conn,
                        title="Scale thread",
                        body="Synthetic release-scale thread.",
                        created_by="pmo-loadgen",
                        workspace_kind="scratch",
                        project_id=boot.project_id,
                        idempotency_key="pmo-loadgen-thread",
                        board=boot.board_slug,
                    )
                )
            thread_id = task_ids[0]
            existing_messages = len(kanban_db.list_comments(conn, thread_id))
            for index in range(existing_messages, max(0, messages)):
                kanban_db.add_comment(
                    conn,
                    thread_id,
                    author="pmo-loadgen",
                    body=f"Synthetic thread message {index:05d}",
                )
        snapshot = measure_board(
            boot.board_slug, thread_task_id=thread_id, repeats=repeats
        )
        return {
            "schema": "datansh-pmo-loadgen-result.v1",
            "board_query_p50_ms": round(snapshot.board_query_p50_ms, 3),
            "board_query_p95_ms": round(snapshot.board_query_p95_ms, 3),
            "thread_load_p50_ms": round(snapshot.thread_load_p50_ms, 3),
            "ticket_count": snapshot.ticket_count,
            "largest_thread_messages": snapshot.largest_thread_messages,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, help="Empty or marker-verified persistent fixture root")
    parser.add_argument("--tickets", type=int, default=2_000)
    parser.add_argument("--messages", type=int, default=5_000)
    parser.add_argument("--repeats", type=int, default=5)
    args = parser.parse_args()
    if args.root:
        result = run_load(
            args.root, tickets=args.tickets, messages=args.messages, repeats=args.repeats
        )
    else:
        with TemporaryDirectory(prefix="datansh-pmo-loadgen-") as temporary:
            result = run_load(
                Path(temporary),
                tickets=args.tickets,
                messages=args.messages,
                repeats=args.repeats,
            )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

