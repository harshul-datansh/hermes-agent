"""Behavior contracts for Kanban-native PM-OS approvals."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from hermes_cli import kanban_db


REPO_ROOT = Path(__file__).resolve().parents[1]
APPROVAL_PATH = REPO_ROOT / "plugins" / "pmo" / "approval.py"


def _load_approval():
    spec = importlib.util.spec_from_file_location("pmo_approval", APPROVAL_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def approval_board(tmp_path, monkeypatch):
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.delenv("HERMES_KANBAN_HOME", raising=False)
    monkeypatch.delenv("HERMES_KANBAN_DB", raising=False)
    kanban_db.create_board("company", name="Company")
    with kanban_db.connect_closing(board="company") as conn:
        target = kanban_db.create_task(
            conn,
            title="Commit quarterly spend",
            assignee="pm",
            board="company",
        )
    return "company", target


def _raise(approval, board, target):
    return approval.raise_approval(
        board_slug=board,
        target_task_id=target,
        title="Approve quarterly spend",
        detail="Spend exceeds the team-level threshold.",
        raised_by="cfo",
        required_rank=70,
        approver_profile="cfo",
    )


def test_raise_uses_native_parent_dependency_to_gate_target(approval_board):
    approval = _load_approval()
    board, target = approval_board

    request = _raise(approval, board, target)

    assert request.status == "pending"
    assert request.approver_profile == "cfo"
    with kanban_db.connect_closing(board=board) as conn:
        approval_task = kanban_db.get_task(conn, request.approval_id)
        target_task = kanban_db.get_task(conn, target)
        assert approval_task is not None and approval_task.status == "triage"
        assert target_task is not None and target_task.status == "todo"
        assert request.approval_id in kanban_db.parent_ids(conn, target)
        assert {event.kind for event in kanban_db.list_events(conn, target)} >= {
            "linked",
            "commented",
        }


def test_approval_redacts_secret_before_native_persistence(approval_board):
    approval = _load_approval()
    board, target = approval_board
    secret = "sk-proj-abcdefghijklmnopqrstuvwxyz0123456789"

    request = approval.raise_approval(
        board_slug=board,
        target_task_id=target,
        title="Approve credential rotation",
        detail=f"The accidentally pasted credential is {secret}",
        raised_by="cfo",
        required_rank=70,
        approver_profile="cfo",
    )

    with kanban_db.connect_closing(board=board) as conn:
        persisted = kanban_db.get_task(conn, request.approval_id)
    assert persisted is not None
    assert secret not in request.detail
    assert secret not in persisted.body


def test_cfo_escalates_to_ceo_and_ceo_approval_unblocks_target(approval_board):
    approval = _load_approval()
    board, target = approval_board
    request = _raise(approval, board, target)

    escalated = approval.escalate_approval(
        board_slug=board,
        approval_id=request.approval_id,
        to_rank=100,
        to_approver_profile="ceo",
        reason="Over the quarterly cap; CEO sign-off is required.",
        actor="cfo",
        actor_rank=70,
    )

    assert escalated.required_rank == 100
    assert escalated.approver_profile == "ceo"
    with pytest.raises(PermissionError, match="requires rank 100"):
        approval.decide_approval(
            board_slug=board,
            approval_id=request.approval_id,
            decision="approved",
            actor="cfo",
            actor_rank=70,
        )

    decided = approval.decide_approval(
        board_slug=board,
        approval_id=request.approval_id,
        decision="approved",
        actor="ceo",
        actor_rank=100,
        note="Approved within the revised annual plan.",
    )

    assert decided.status == "approved"
    assert decided.decision_actor == "ceo"
    with kanban_db.connect_closing(board=board) as conn:
        assert kanban_db.get_task(conn, request.approval_id).status == "done"
        assert kanban_db.get_task(conn, target).status == "ready"
        comments = kanban_db.list_comments(conn, request.approval_id)
        assert any(approval.ESCALATION_MARKER in item.body for item in comments)
        assert any(approval.DECISION_MARKER in item.body for item in comments)
        assert {event.kind for event in kanban_db.list_events(conn, request.approval_id)} >= {
            "assigned",
            "commented",
            "specified",
            "completed",
        }


def test_escalation_is_strictly_upward_and_requires_founders_office_rank(
    approval_board,
):
    approval = _load_approval()
    board, target = approval_board
    request = _raise(approval, board, target)

    with pytest.raises(ValueError, match="above current required rank"):
        approval.escalate_approval(
            board_slug=board,
            approval_id=request.approval_id,
            to_rank=70,
            to_approver_profile="cfo",
            reason="Keep it at CFO.",
            actor="cfo",
            actor_rank=70,
        )
    with pytest.raises(ValueError, match="above current required rank"):
        approval.escalate_approval(
            board_slug=board,
            approval_id=request.approval_id,
            to_rank=50,
            to_approver_profile="founders-office",
            reason="Lower the bar.",
            actor="cfo",
            actor_rank=70,
        )
    with pytest.raises(PermissionError, match="rank 50"):
        approval.escalate_approval(
            board_slug=board,
            approval_id=request.approval_id,
            to_rank=100,
            to_approver_profile="ceo",
            reason="Escalate.",
            actor="staff-member",
            actor_rank=10,
        )


def test_decision_rechecks_rank_and_rejects_agent_actor(approval_board):
    approval = _load_approval()
    board, target = approval_board
    request = _raise(approval, board, target)

    with pytest.raises(PermissionError, match="requires rank 70"):
        approval.decide_approval(
            board_slug=board,
            approval_id=request.approval_id,
            decision="approved",
            actor="staff-member",
            actor_rank=10,
        )
    with pytest.raises(PermissionError, match="only a human"):
        approval.decide_approval(
            board_slug=board,
            approval_id=request.approval_id,
            decision="approved",
            actor="agent:company/pm",
            actor_rank=100,
            actor_kind="agent",
        )


def test_same_rank_peer_cannot_decide_another_approvers_request(approval_board):
    approval = _load_approval()
    board, target = approval_board
    request = _raise(approval, board, target)

    with pytest.raises(PermissionError, match="peer 'cto'.*routed to 'cfo'"):
        approval.decide_approval(
            board_slug=board,
            approval_id=request.approval_id,
            decision="approved",
            actor="cto",
            actor_rank=70,
        )

    # A higher-ranked human may still satisfy the gate without reassignment.
    decided = approval.decide_approval(
        board_slug=board,
        approval_id=request.approval_id,
        decision="approved",
        actor="ceo",
        actor_rank=100,
    )
    assert decided.status == "approved"


def test_rejection_is_terminal_and_keeps_target_gated(approval_board):
    approval = _load_approval()
    board, target = approval_board
    request = _raise(approval, board, target)

    rejected = approval.decide_approval(
        board_slug=board,
        approval_id=request.approval_id,
        decision="rejected",
        actor="cfo",
        actor_rank=70,
        note="Revise the vendor scope.",
    )

    assert rejected.status == "rejected"
    with kanban_db.connect_closing(board=board) as conn:
        assert kanban_db.get_task(conn, request.approval_id).status == "blocked"
        assert kanban_db.get_task(conn, target).status == "todo"
        assert request.approval_id in kanban_db.parent_ids(conn, target)

    with pytest.raises(RuntimeError, match="immutable"):
        approval.decide_approval(
            board_slug=board,
            approval_id=request.approval_id,
            decision="approved",
            actor="ceo",
            actor_rank=100,
        )
    with pytest.raises(RuntimeError, match="cannot escalate rejected"):
        approval.escalate_approval(
            board_slug=board,
            approval_id=request.approval_id,
            to_rank=100,
            to_approver_profile="ceo",
            reason="Try after rejection.",
            actor="cfo",
            actor_rank=70,
        )


def test_withdraw_is_raiser_only_terminal_and_releases_dependency(approval_board):
    approval = _load_approval()
    board, target = approval_board
    request = _raise(approval, board, target)

    with pytest.raises(PermissionError, match="original raiser"):
        approval.withdraw_approval(
            board_slug=board,
            approval_id=request.approval_id,
            actor="ceo",
            actor_rank=100,
            reason="Not my request.",
        )

    withdrawn = approval.withdraw_approval(
        board_slug=board,
        approval_id=request.approval_id,
        actor="cfo",
        actor_rank=70,
        reason="The purchase is no longer needed.",
    )

    assert withdrawn.status == "withdrawn"
    with kanban_db.connect_closing(board=board) as conn:
        assert kanban_db.get_task(conn, request.approval_id).status == "archived"
        assert kanban_db.get_task(conn, target).status == "ready"
        assert request.approval_id in kanban_db.parent_ids(conn, target)
    with pytest.raises(RuntimeError, match="immutable"):
        approval.withdraw_approval(
            board_slug=board,
            approval_id=request.approval_id,
            actor="cfo",
            actor_rank=70,
            reason="Second withdrawal.",
        )


@pytest.mark.parametrize("terminal", ["approved", "rejected"])
def test_decided_approval_cannot_be_mutated(approval_board, terminal):
    approval = _load_approval()
    board, target = approval_board
    request = _raise(approval, board, target)
    approval.decide_approval(
        board_slug=board,
        approval_id=request.approval_id,
        decision=terminal,
        actor="cfo",
        actor_rank=70,
    )

    with pytest.raises(RuntimeError):
        approval.decide_approval(
            board_slug=board,
            approval_id=request.approval_id,
            decision="rejected" if terminal == "approved" else "approved",
            actor="ceo",
            actor_rank=100,
        )
