"""Approval SLA contracts over native Kanban comments."""

from __future__ import annotations

from dataclasses import replace

import pytest

from hermes_cli import kanban_db
from plugins.pmo import approval, approval_sla
from plugins.pmo.project_scope import EscalationConfig, EscalationRankTarget


pytest_plugins = ("tests.pmo_fixtures",)


def _scope(pmo_project, *, targets=((100, "ceo"),)):
    config = pmo_project.scope.config.model_copy(
        update={
            "escalation": EscalationConfig(
                pm_retry_limit=2,
                blocked_timeout_minutes=30,
                respond_within_minutes=2,
                reminder_after_minutes=1,
                escalate_rank_after_minutes=3,
                rank_targets=tuple(
                    EscalationRankTarget(rank=rank, approver_profile=profile)
                    for rank, profile in targets
                ),
            )
        }
    )
    return replace(pmo_project.scope, config=config)


def _raise(pmo_project, *, rank=70, approver="cfo", now=1_000):
    with kanban_db.connect_closing(board=pmo_project.board_slug) as conn:
        target = kanban_db.create_task(
            conn,
            title="Approve launch",
            assignee="pm",
            board=pmo_project.board_slug,
            project_id=pmo_project.project_id,
        )
    return approval.raise_approval(
        board_slug=pmo_project.board_slug,
        target_task_id=target,
        title="Launch",
        detail="Approve launch window.",
        raised_by="cfo",
        required_rank=rank,
        approver_profile=approver,
        respond_within_minutes=2,
        reminder_after_minutes=1,
        escalate_rank_after_minutes=3,
        now=now,
    )


def test_needs_response_by_set_at_creation(pmo_project):
    item = _raise(pmo_project)
    assert item.reminder_at == 1_060
    assert item.needs_response_by == 1_120
    assert item.escalate_rank_at == 1_180


def test_reminder_at_threshold_is_idempotent(pmo_project):
    scope = _scope(pmo_project)
    item = _raise(pmo_project)
    first = approval_sla.process_approval_slas(scope, now=1_060)
    second = approval_sla.process_approval_slas(scope, now=1_060)
    assert [action.action for action in first] == ["reminder"]
    assert second == []
    with kanban_db.connect_closing(board=pmo_project.board_slug) as conn:
        comments = kanban_db.list_comments(conn, item.approval_id)
    assert sum(approval_sla.REMINDER_MARKER in comment.body for comment in comments) == 1


def test_auto_escalate_reuses_manual_path_and_records_system_actor(
    pmo_project, monkeypatch
):
    scope = _scope(pmo_project)
    item = _raise(pmo_project)
    original = approval.escalate_approval
    calls = []

    def spy(**kwargs):
        calls.append(kwargs)
        return original(**kwargs)

    monkeypatch.setattr(approval_sla.approval, "escalate_approval", spy)
    actions = approval_sla.process_approval_slas(scope, now=1_180)
    current = approval.get_approval(
        board_slug=pmo_project.board_slug, approval_id=item.approval_id
    )
    assert "escalated" in [action.action for action in actions]
    assert calls and calls[0]["actor"] == "system"
    assert calls[0]["actor_kind"] == "system"
    assert current.required_rank == 100
    assert current.needs_response_by == 1_300
    with kanban_db.connect_closing(board=pmo_project.board_slug) as conn:
        comments = kanban_db.list_comments(conn, item.approval_id)
    escalation = next(c.body for c in comments if approval.ESCALATION_MARKER in c.body)
    assert "Actor: system" in escalation
    with pytest.raises(PermissionError, match="only a human"):
        approval.decide_approval(
            board_slug=pmo_project.board_slug,
            approval_id=item.approval_id,
            decision="approved",
            actor="system",
            actor_rank=100,
            actor_kind="system",
        )


def test_ceo_rank_stops_climbing_and_gets_one_daily_reminder(pmo_project):
    scope = _scope(pmo_project, targets=())
    item = _raise(pmo_project, rank=100, approver="ceo")
    first = approval_sla.process_approval_slas(scope, now=1_180)
    second = approval_sla.process_approval_slas(scope, now=1_181)
    current = approval.get_approval(
        board_slug=pmo_project.board_slug, approval_id=item.approval_id
    )
    assert current.required_rank == 100
    assert "ceo_reminder" in [action.action for action in first]
    assert "ceo_reminder" not in [action.action for action in second]
