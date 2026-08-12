"""Tests for the read-only PM-OS status-report utility."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from hermes_cli import kanban_db


REPO_ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = REPO_ROOT / "plugins" / "pmo" / "report.py"


def _load_report():
    spec = importlib.util.spec_from_file_location("pmo_report", REPORT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def report_board(tmp_path, monkeypatch):
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.delenv("HERMES_KANBAN_HOME", raising=False)
    monkeypatch.delenv("HERMES_KANBAN_DB", raising=False)
    kanban_db.create_board("delivery", name="Delivery")

    with kanban_db.connect_closing(board="delivery") as conn:
        ready = kanban_db.create_task(
            conn,
            title="Ship release",
            assignee="Alice",
            priority=4,
            initial_status="running",
            board="delivery",
        )
        blocked = kanban_db.create_task(
            conn,
            title="Approve copy",
            assignee="Bob",
            priority=3,
            initial_status="running",
            board="delivery",
        )
        todo = kanban_db.create_task(
            conn,
            title="Unowned follow-up",
            priority=1,
            initial_status="running",
            board="delivery",
        )
        kanban_db.create_task(
            conn,
            title="Founder's Office",
            body='{"schema":"datansh-pmo-conversation.v1"}',
            initial_status="blocked",
            board="delivery",
        )
        conn.execute("UPDATE tasks SET status = 'ready' WHERE id = ?", (ready,))
        conn.execute("UPDATE tasks SET status = 'todo' WHERE id = ?", (todo,))
        conn.commit()
        assert kanban_db.block_task(
            conn,
            blocked,
            reason="Legal approval is pending",
            kind="needs_input",
        )

    return {
        "slug": "delivery",
        "db_path": kanban_db.kanban_db_path(board="delivery"),
        "ready": ready,
        "blocked": blocked,
        "todo": todo,
    }


def test_report_contains_counts_work_lists_block_reason_and_assignee_load(report_board):
    report_module = _load_report()

    result = report_module.build_report(board_slug=report_board["slug"], now=2_000_000_000)

    assert result["board"]["slug"] == "delivery"
    assert result["status_counts"]["ready"] == 1
    assert result["status_counts"]["blocked"] == 1
    assert result["status_counts"]["todo"] == 1
    assert result["status_counts"]["done"] == 0
    assert sum(result["status_counts"].values()) == 3
    assert all(item["title"] != "Founder's Office" for item in result["blocked_work"])
    assert result["ready_work"][0]["id"] == report_board["ready"]
    assert result["blocked_work"] == [
        {
            "id": report_board["blocked"],
            "title": "Approve copy",
            "assignee": "bob",
            "priority": 3,
            "created_at": result["blocked_work"][0]["created_at"],
            "age_seconds": result["blocked_work"][0]["age_seconds"],
            "block_kind": "needs_input",
            "reason": "Legal approval is pending",
        }
    ]
    by_assignee = {entry["assignee"]: entry for entry in result["assignee_load"]}
    assert by_assignee["alice"]["active_total"] == 1
    assert by_assignee["alice"]["by_status"]["ready"] == 1
    assert by_assignee["bob"]["by_status"]["blocked"] == 1
    assert by_assignee[None]["by_status"]["todo"] == 1


def test_report_connection_does_not_modify_the_board_database(report_board):
    report_module = _load_report()
    before = report_board["db_path"].read_bytes()

    report_module.build_report(board_slug="delivery")

    assert report_board["db_path"].read_bytes() == before


def test_report_requires_an_existing_explicit_board_slug(tmp_path, monkeypatch):
    report_module = _load_report()
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.delenv("HERMES_KANBAN_HOME", raising=False)
    monkeypatch.delenv("HERMES_KANBAN_DB", raising=False)

    with pytest.raises(ValueError, match="board slug is required"):
        report_module.build_report(board_slug="")
    with pytest.raises(ValueError, match="does not exist"):
        report_module.build_report(board_slug="missing")

    assert not kanban_db.kanban_db_path(board="missing").exists()


def test_human_and_json_cli_outputs_are_usable(report_board, capsys):
    report_module = _load_report()

    assert report_module.main(["--board", "delivery"]) == 0
    human = capsys.readouterr().out
    assert "PM-OS status: Delivery (delivery)" in human
    assert "Blocked work (1)" in human
    assert "Legal approval is pending" in human
    assert "Ready work (1)" in human
    assert "Assignee load" in human

    assert report_module.main(["--board", "delivery", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["board"]["slug"] == "delivery"
    assert payload["status_counts"]["blocked"] == 1
