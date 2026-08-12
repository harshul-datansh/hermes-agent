"""Behavior tests for structured PM-OS ticket creation."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from hermes_cli import kanban_db
from hermes_cli import projects_db


REPO_ROOT = Path(__file__).resolve().parents[1]
TICKET_PATH = REPO_ROOT / "plugins" / "pmo" / "ticket.py"


def _load_ticket():
    spec = importlib.util.spec_from_file_location("pmo_ticket", TICKET_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def ticket_board(tmp_path, monkeypatch):
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.delenv("HERMES_KANBAN_HOME", raising=False)
    monkeypatch.delenv("HERMES_KANBAN_DB", raising=False)
    kanban_db.create_board("delivery", name="Delivery")
    return "delivery"


def test_ticket_body_preserves_repeated_structured_requirements():
    ticket = _load_ticket()

    body = ticket.format_ticket_body(
        outcome="The release is available to users.",
        constraints=["Keep the public API stable", "No core tool changes"],
        acceptance_criteria=["All focused tests pass", "Upgrade path is documented"],
        dependencies=["Release approval"],
        parent_task_ids=["t_parent"],
        evidence=["Test command output", "Reviewer sign-off"],
    )

    assert body == "\n".join(
        [
            "Outcome:",
            "The release is available to users.",
            "",
            "Scope and constraints:",
            "- Keep the public API stable",
            "- No core tool changes",
            "",
            "Acceptance criteria:",
            "- [ ] All focused tests pass",
            "- [ ] Upgrade path is documented",
            "",
            "Dependencies and risks:",
            "- Kanban task `t_parent`",
            "- Release approval",
            "",
            "Evidence required to close:",
            "- [ ] Test command output",
            "- [ ] Reviewer sign-off",
        ]
    )


def test_product_ticket_body_leads_with_context_and_user_impact():
    ticket = _load_ticket()

    body = ticket.format_ticket_body(
        product_context="New customers abandon onboarding because they cannot see a clear next step.",
        user_impact="New customers can complete onboarding without support intervention.",
        outcome="The onboarding journey clearly guides customers to their first successful action.",
        constraints=["Keep existing customer data intact"],
        acceptance_criteria=["A new customer can complete onboarding from start to finish"],
        dependencies=[],
        parent_task_ids=[],
        evidence=["A recorded walkthrough of the customer journey"],
    )

    assert body.startswith(
        "Product context:\nNew customers abandon onboarding because they cannot see a clear next step.\n\n"
        "User impact:\nNew customers can complete onboarding without support intervention.\n\n"
        "Outcome:"
    )
    assert "src/" not in body


def test_create_ticket_uses_native_assignment_and_ready_status(ticket_board):
    ticket = _load_ticket()

    result = ticket.create_ticket(
        board_slug=ticket_board,
        title="Ship release",
        outcome="Users can install the release.",
        assignee="Release_Manager",
        acceptance_criteria=["Package installation succeeds"],
        evidence=["Installation transcript"],
        constraints=["Do not change Hermes core"],
    )

    assert result.board_slug == "delivery"
    assert result.status == "ready"
    assert result.assignee == "release_manager"
    with kanban_db.connect_closing(board="delivery") as conn:
        stored = kanban_db.get_task(conn, result.task_id)
    assert stored is not None
    assert stored.body == result.body
    assert stored.assignee == "release_manager"
    assert stored.status == "ready"
    assert stored.max_runtime_seconds == 3_600
    assert stored.max_retries == 3


def test_parent_dependency_uses_native_link_and_todo_status(ticket_board):
    ticket = _load_ticket()
    with kanban_db.connect_closing(board=ticket_board) as conn:
        parent_id = kanban_db.create_task(conn, title="Approve design", board=ticket_board)

    result = ticket.create_ticket(
        board_slug=ticket_board,
        title="Implement design",
        outcome="The approved design is implemented.",
        assignee="Builder",
        acceptance_criteria=["Behavior matches the design"],
        evidence=["Focused test output"],
        parent_task_ids=[parent_id],
    )

    assert result.status == "todo"
    assert f"Kanban task `{parent_id}`" in result.body
    with kanban_db.connect_closing(board=ticket_board) as conn:
        assert kanban_db.parent_ids(conn, result.task_id) == [parent_id]


def test_project_board_uses_native_deterministic_worktree(tmp_path, monkeypatch):
    ticket = _load_ticket()
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    workspace = tmp_path / "project"
    workspace.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.delenv("HERMES_KANBAN_HOME", raising=False)
    monkeypatch.delenv("HERMES_KANBAN_DB", raising=False)

    with projects_db.connect_closing() as conn:
        project_id = projects_db.create_project(
            conn,
            name="Acme",
            slug="acme",
            primary_path=str(workspace),
            board_slug="acme",
        )
    kanban_db.create_board(
        "acme",
        name="Acme",
        default_workdir=str(workspace),
        project_id=project_id,
    )

    result = ticket.create_ticket(
        board_slug="acme",
        title="Implement API",
        outcome="The API behavior is available.",
        assignee="Builder",
        acceptance_criteria=["API tests pass"],
        evidence=["Focused test output"],
    )

    with kanban_db.connect_closing(board="acme") as conn:
        stored = kanban_db.get_task(conn, result.task_id)
    assert stored is not None
    assert stored.project_id == project_id
    assert stored.workspace_kind == "worktree"
    assert stored.workspace_path == str(workspace / ".worktrees" / stored.id)
    assert stored.branch_name == projects_db.branch_name_for(
        projects_db.Project(
            id=project_id,
            slug="acme",
            name="Acme",
            primary_path=str(workspace),
            created_at=0,
        ),
        stored.id,
        title=stored.title,
    )


def test_triage_uses_existing_kanban_status(ticket_board):
    ticket = _load_ticket()

    result = ticket.create_ticket(
        board_slug=ticket_board,
        title="Specify migration",
        outcome="Migration uncertainty is resolved.",
        assignee="Specifier",
        acceptance_criteria=["Unknowns have owners"],
        evidence=["Reviewed migration note"],
        triage=True,
    )

    assert result.status == "triage"
    assert result.status in kanban_db.VALID_STATUSES


def test_ticket_requires_existing_explicit_board_and_structured_closure_fields(
    tmp_path, monkeypatch
):
    ticket = _load_ticket()
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.delenv("HERMES_KANBAN_HOME", raising=False)
    monkeypatch.delenv("HERMES_KANBAN_DB", raising=False)

    base = {
        "title": "Safe task",
        "outcome": "A result exists.",
        "assignee": "worker",
        "acceptance_criteria": ["Result is verified"],
        "evidence": ["Verification output"],
    }
    with pytest.raises(ValueError, match="board slug is required"):
        ticket.create_ticket(board_slug="", **base)
    with pytest.raises(ValueError, match="does not exist"):
        ticket.create_ticket(board_slug="missing", **base)
    with pytest.raises(ValueError, match="at least one acceptance criterion"):
        ticket.format_ticket_body(
            outcome="A result exists.",
            constraints=[],
            acceptance_criteria=[],
            dependencies=[],
            parent_task_ids=[],
            evidence=["Verification output"],
        )
    with pytest.raises(ValueError, match="at least one evidence requirement"):
        ticket.format_ticket_body(
            outcome="A result exists.",
            constraints=[],
            acceptance_criteria=["Result is verified"],
            dependencies=[],
            parent_task_ids=[],
            evidence=[],
        )
    assert not kanban_db.kanban_db_path(board="missing").exists()


def test_json_cli_returns_native_task_state(ticket_board, capsys):
    ticket = _load_ticket()

    exit_code = ticket.main(
        [
            "--board",
            ticket_board,
            "--title",
            "Document release",
            "--outcome",
            "Operators can follow the release process.",
            "--assignee",
            "Writer",
            "--acceptance",
            "Every command is documented",
            "--acceptance",
            "A reviewer can reproduce the process",
            "--evidence",
            "Reviewer sign-off",
            "--json",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["board"] == "delivery"
    assert payload["assignee"] == "writer"
    assert payload["status"] == "ready"
    assert payload["task_id"].startswith("t_")
