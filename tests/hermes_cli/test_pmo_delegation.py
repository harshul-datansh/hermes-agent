"""Append-only temporary human delegation contracts."""

from __future__ import annotations

import json

import pytest

from hermes_cli import kanban_db
from plugins.pmo import approval, availability


pytest_plugins = ("tests.pmo_fixtures",)


def _grant(pmo_project, *, delegator="cfo", delegate="finance-lead", rank=70):
    return availability.grant_delegation(
        pmo_project.scope,
        delegator=delegator,
        delegate=delegate,
        delegator_rank=rank,
        delegated_rank=rank,
        from_ts=1_000,
        to_ts=2_000,
        actor=delegator,
    )


def test_delegate_acts_at_delegator_rank_and_decision_records_acting_as(pmo_project):
    _grant(pmo_project)
    with kanban_db.connect_closing(board=pmo_project.board_slug) as conn:
        target = kanban_db.create_task(
            conn,
            title="Approve spend",
            assignee="pm",
            board=pmo_project.board_slug,
            project_id=pmo_project.project_id,
        )
    request = approval.raise_approval(
        board_slug=pmo_project.board_slug,
        target_task_id=target,
        title="Spend",
        detail="Approve vendor spend.",
        raised_by="pm",
        required_rank=70,
        approver_profile="cfo",
        now=1_000,
    )
    result = availability.decide_with_delegation(
        pmo_project.scope,
        approval_id=request.approval_id,
        decision="approved",
        actor="finance-lead",
        at=1_500,
    )
    assert result.status == "approved"
    assert result.decision_actor == "finance-lead (as cfo)"
    records = [
        json.loads(line)
        for line in (
            pmo_project.workspace / ".datansh" / availability.AVAILABILITY_FILE
        ).read_text(encoding="utf-8").splitlines()
    ]
    audit = records[-1]
    assert audit["record_type"] == "delegated_decision"
    assert audit["acting_as"] == "cfo"


def test_delegate_cannot_subdelegate(pmo_project):
    _grant(pmo_project)
    with pytest.raises(PermissionError, match="sub-delegate"):
        availability.grant_delegation(
            pmo_project.scope,
            delegator="finance-lead",
            delegate="analyst",
            delegator_rank=70,
            delegated_rank=70,
            from_ts=1_200,
            to_ts=1_800,
            actor="finance-lead",
        )


def test_only_ceo_can_delegate_ceo_rank(pmo_project):
    with pytest.raises(PermissionError, match="only the CEO"):
        availability.grant_delegation(
            pmo_project.scope,
            delegator="cfo",
            delegate="finance-lead",
            delegator_rank=70,
            delegated_rank=100,
            from_ts=1_000,
            to_ts=2_000,
            actor="cfo",
        )
    ceo = availability.grant_delegation(
        pmo_project.scope,
        delegator="ceo",
        delegate="chief-of-staff",
        delegator_rank=100,
        delegated_rank=100,
        from_ts=1_000,
        to_ts=2_000,
        actor="ceo",
    )
    assert ceo.rank == 100


def test_expired_or_agent_delegation_cannot_decide(pmo_project):
    _grant(pmo_project)
    with pytest.raises(PermissionError, match="no active"):
        availability.decide_with_delegation(
            pmo_project.scope,
            approval_id="missing",
            decision="approved",
            actor="finance-lead",
            at=2_001,
        )
    with pytest.raises(PermissionError, match="only a human"):
        availability.grant_delegation(
            pmo_project.scope,
            delegator="cfo",
            delegate="agent:pm",
            delegator_rank=70,
            delegated_rank=70,
            from_ts=3_000,
            to_ts=4_000,
            actor="cfo",
            actor_kind="agent",
        )


def test_availability_is_temporary_append_only_and_self_set(pmo_project):
    availability.set_availability(
        pmo_project.scope,
        user_id="cfo",
        from_ts=1_000,
        to_ts=2_000,
        status="limited",
        actor="cfo",
    )
    assert [item.user_id for item in availability.list_availability(pmo_project.scope, at=1_500)] == [
        "cfo"
    ]
    assert availability.list_availability(pmo_project.scope, at=2_001) == []
    with pytest.raises(PermissionError, match="own availability"):
        availability.set_availability(
            pmo_project.scope,
            user_id="ceo",
            from_ts=1_000,
            to_ts=2_000,
            status="away",
            actor="cfo",
        )
