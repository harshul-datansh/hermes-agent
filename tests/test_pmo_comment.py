"""Behavior tests for structured PM-OS comments."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from hermes_cli import kanban_db


REPO_ROOT = Path(__file__).resolve().parents[1]
COMMENT_PATH = REPO_ROOT / "plugins" / "pmo" / "comment.py"


def _load_comment():
    spec = importlib.util.spec_from_file_location("pmo_comment", COMMENT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def task_board(tmp_path, monkeypatch):
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.delenv("HERMES_KANBAN_HOME", raising=False)
    monkeypatch.delenv("HERMES_KANBAN_DB", raising=False)
    kanban_db.create_board("delivery", name="Delivery")
    with kanban_db.connect_closing(board="delivery") as conn:
        task_id = kanban_db.create_task(conn, title="Ship", board="delivery")
    return "delivery", task_id


@pytest.mark.parametrize(
    ("comment_type", "owner", "verdict", "marker"),
    [
        ("handoff", "reviewer", None, "Receiving owner: reviewer"),
        ("blocker", "release-manager", None, "Resolution owner: release-manager"),
        ("review", None, "approved", "Verdict: approved"),
    ],
)
def test_formats_documented_comment_types(comment_type, owner, verdict, marker):
    comment = _load_comment()

    body = comment.format_comment_body(
        comment_type=comment_type,
        summary="The durable update.",
        owner=owner,
        verdict=verdict,
        evidence=["Focused tests pass"],
        next_actions=["Record the decision"],
    )

    assert body.startswith(f"[pmo:{comment_type}]\nSummary: The durable update.")
    assert marker in body
    assert "- Focused tests pass" in body
    assert "- Record the decision" in body


def test_add_uses_native_comment_and_event_behavior(task_board):
    comment = _load_comment()
    board, task_id = task_board

    result = comment.add_structured_comment(
        board_slug=board,
        task_id=task_id,
        comment_type="handoff",
        author="builder",
        summary="Implementation is ready for review.",
        owner="reviewer",
        evidence=["pytest tests/test_feature.py"],
    )

    with kanban_db.connect_closing(board=board) as conn:
        comments = kanban_db.list_comments(conn, task_id)
        events = kanban_db.list_events(conn, task_id)
    assert result.comment_id == comments[-1].id
    assert comments[-1].author == "builder"
    assert comments[-1].body == result.body
    assert events[-1].kind == "commented"
    assert events[-1].payload["author"] == "builder"


def test_add_redacts_secrets_before_persisting(task_board):
    comment = _load_comment()
    board, task_id = task_board
    secret = "sk-proj-abcdefghijklmnopqrstuvwxyz0123456789"

    result = comment.add_structured_comment(
        board_slug=board,
        task_id=task_id,
        comment_type="handoff",
        author="builder",
        summary=f"Use API token {secret}",
        owner="reviewer",
    )

    with kanban_db.connect_closing(board=board) as conn:
        persisted = kanban_db.list_comments(conn, task_id)[-1].body
    assert secret not in result.body
    assert secret not in persisted


def test_requires_explicit_existing_board_and_task(task_board, tmp_path, monkeypatch):
    comment = _load_comment()
    board, task_id = task_board
    kwargs = {
        "task_id": task_id,
        "comment_type": "blocker",
        "author": "builder",
        "summary": "Approval is missing.",
        "owner": "maintainer",
    }
    with pytest.raises(ValueError, match="board slug is required"):
        comment.add_structured_comment(board_slug="", **kwargs)
    with pytest.raises(ValueError, match="does not exist"):
        comment.add_structured_comment(board_slug="missing", **kwargs)
    with pytest.raises(ValueError, match="does not exist on board"):
        comment.add_structured_comment(
            board_slug=board, **{**kwargs, "task_id": "t_missing"}
        )

    with kanban_db.connect_closing(board=board) as conn:
        assert kanban_db.list_comments(conn, task_id) == []


def test_type_specific_fields_are_required_and_rejected():
    comment = _load_comment()
    with pytest.raises(ValueError, match="receiving owner"):
        comment.format_comment_body(comment_type="handoff", summary="Ready")
    with pytest.raises(ValueError, match="review verdict"):
        comment.format_comment_body(comment_type="review", summary="Reviewed")
    with pytest.raises(ValueError, match="owner is only valid"):
        comment.format_comment_body(
            comment_type="review", summary="Reviewed", owner="x", verdict="approved"
        )


def test_json_cli_returns_native_comment_identity(task_board, capsys):
    comment = _load_comment()
    board, task_id = task_board

    exit_code = comment.main(
        [
            "--board", board,
            "--task", task_id,
            "--type", "review",
            "--author", "reviewer",
            "--summary", "Acceptance criteria pass.",
            "--verdict", "approved",
            "--json",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["board"] == board
    assert payload["task_id"] == task_id
    assert payload["type"] == "review"
    assert payload["comment_id"] > 0
