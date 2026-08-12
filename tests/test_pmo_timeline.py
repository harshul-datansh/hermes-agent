"""Behavior tests for the native PM-OS ticket timeline."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from hermes_cli import kanban_db


REPO_ROOT = Path(__file__).resolve().parents[1]
TIMELINE_PATH = REPO_ROOT / "plugins" / "pmo" / "timeline.py"


def _load_timeline():
    spec = importlib.util.spec_from_file_location("pmo_timeline", TIMELINE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def board(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.delenv("HERMES_KANBAN_HOME", raising=False)
    monkeypatch.delenv("HERMES_KANBAN_DB", raising=False)
    kanban_db.create_board("delivery", name="Delivery")
    return "delivery"


def test_timeline_merges_events_comments_runs_and_native_approval(board):
    timeline = _load_timeline()
    with kanban_db.connect_closing(board=board) as conn:
        target = kanban_db.create_task(conn, title="Ship feature", assignee="worker", board=board)
        approval = kanban_db.create_task(
            conn,
            title="Approve ship",
            body="[pmo:approval:v1]\nTitle: Ship\n",
            assignee="cfo",
            triage=True,
            board=board,
        )
        kanban_db.link_tasks(conn, approval, target)
        kanban_db.add_comment(conn, target, "pm", "Ready for review")
        kanban_db.add_comment(conn, approval, "cfo", "Approved")
        assert kanban_db.archive_task(conn, approval)
        # Closing the native parent dependency recomputes the child to ready.
        assert kanban_db.get_task(conn, target).status == "ready"
        claimed = kanban_db.claim_task(conn, target, claimer="worker")
        assert claimed is not None
        assert kanban_db.complete_task(conn, target, summary="Shipped")

    items = timeline.build_timeline(board_slug=board, task_id=target)
    assert items == sorted(
        items, key=lambda item: (item.timestamp, item.source, item.subject_id, item.kind)
    )
    assert {item.source for item in items} >= {
        "task_event",
        "comment",
        "task_run",
        "approval",
    }
    assert any(item.kind == "run_ended" and item.summary == "Shipped" for item in items)


def test_timeline_redacts_secrets_and_is_bounded(board):
    timeline = _load_timeline()
    secret = "sk-proj-abcdefghijklmnopqrstuvwxyz0123456789"
    with kanban_db.connect_closing(board=board) as conn:
        task = kanban_db.create_task(conn, title="Audit", board=board)
        kanban_db.add_comment(conn, task, "pm", f"Credential: {secret}")
        kanban_db.add_comment(conn, task, "pm", "Second")

    items = timeline.build_timeline(board_slug=board, task_id=task, limit=1)
    assert len(items) == 1
    assert secret not in repr(items)


def test_timeline_requires_explicit_existing_board_and_task(board):
    timeline = _load_timeline()
    with pytest.raises(ValueError, match="board does not exist"):
        timeline.build_timeline(board_slug="missing", task_id="t_missing")
    with pytest.raises(ValueError, match="does not exist"):
        timeline.build_timeline(board_slug=board, task_id="t_missing")
