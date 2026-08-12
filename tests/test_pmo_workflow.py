"""PM-OS lifecycle contracts over native Hermes Kanban records."""

from __future__ import annotations

from pathlib import Path

import pytest

from hermes_cli import kanban_db, projects_db
from plugins.pmo import approval, workflow


@pytest.fixture()
def pmo_project(tmp_path, monkeypatch):
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    workspace = tmp_path / "acme"
    workspace.mkdir()
    config_dir = workspace / ".datansh"
    config_dir.mkdir()
    (config_dir / "project.yaml").write_text(
        """version: 1
project:
  name: Acme
  slug: acme
orchestrator:
  profile: pm-acme
agents:
  - handle: dev-1
    profile: acme-dev-1
    role: Engineer
board:
  default_assignee: dev-1
  labels: [spend, release, bug]
approvals:
  rules:
    - when: {label: spend}
      required_rank: 70
scope:
  folders: ['.']
  deny: ['.env']
""",
        encoding="utf-8",
    )
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
    return {
        "project": "acme",
        "project_id": project_id,
        "board": "acme",
        "actor": "pm-acme",
        "workspace": workspace,
    }


def _draft(pmo_project, **overrides):
    values = {
        "project_ref": pmo_project["project"],
        "board_slug": pmo_project["board"],
        "actor_profile": pmo_project["actor"],
        "title": "Implement billing API",
        "outcome": "The billing API is available.",
        "assignee": "acme-dev-1",
        "acceptance_criteria": ["Billing tests pass"],
        "evidence": ["Focused pytest output"],
        "constraints": ["Preserve public routes"],
    }
    values.update(overrides)
    return workflow.create_draft(**values)


def _call(project, task_id):
    return {
        "project_ref": project["project"],
        "board_slug": project["board"],
        "task_id": task_id,
        "actor_profile": project["actor"],
    }


def test_create_draft_is_native_triage_and_retry_safe(pmo_project):
    first = _draft(pmo_project)
    second = _draft(pmo_project)

    assert first.task_id == second.task_id
    with kanban_db.connect_closing(board="acme") as conn:
        stored = kanban_db.get_task(conn, first.task_id)
        matches = [item for item in kanban_db.list_tasks(conn) if item.title == stored.title]
    assert stored is not None
    assert stored.status == "triage"
    assert stored.project_id == pmo_project["project_id"]
    assert stored.workspace_kind == "worktree"
    assert len(matches) == 1


def test_finalize_validates_then_uses_native_promotion_and_is_idempotent(pmo_project):
    draft = _draft(pmo_project)

    promoted = workflow.finalize_ticket(**_call(pmo_project, draft.task_id))
    repeated = workflow.finalize_ticket(**_call(pmo_project, draft.task_id))

    assert promoted.previous_status == "triage"
    assert promoted.status == "ready"
    assert repeated.idempotent is True
    assert repeated.status == "ready"


def test_finalize_refuses_missing_contract_roster_and_wrong_project(pmo_project):
    with kanban_db.connect_closing(board="acme") as conn:
        bad_id = kanban_db.create_task(
            conn,
            title="Ambiguous work",
            body="No checkable contract.",
            assignee="outsider",
            triage=True,
            board="acme",
            project_id=pmo_project["project_id"],
        )

    with pytest.raises(workflow.FinalizeRefused) as exc_info:
        workflow.finalize_ticket(**_call(pmo_project, bad_id))
    assert "acceptance checkbox" in str(exc_info.value)
    assert "closure-evidence checkbox" in str(exc_info.value)
    assert "not in the project roster" in str(exc_info.value)
    with pytest.raises(PermissionError, match="only project PM"):
        workflow.finalize_ticket(
            **{**_call(pmo_project, bad_id), "actor_profile": "acme-dev-1"}
        )


def test_pending_and_rejected_approvals_block_promotion_until_approved(pmo_project):
    draft = _draft(pmo_project, labels=["spend"])
    gate = approval.raise_approval(
        board_slug="acme",
        target_task_id=draft.task_id,
        title="Approve spend",
        detail="Vendor spend requires CFO approval.",
        raised_by="cfo",
        required_rank=70,
        approver_profile="cfo",
    )

    with pytest.raises(workflow.FinalizeRefused, match="not approved"):
        workflow.finalize_ticket(**_call(pmo_project, draft.task_id))
    approval.decide_approval(
        board_slug="acme",
        approval_id=gate.approval_id,
        decision="approved",
        actor="cfo",
        actor_rank=70,
    )
    finalized = workflow.finalize_ticket(**_call(pmo_project, draft.task_id))
    assert finalized.status == "ready"


def test_matching_approval_rule_refuses_when_no_gate_exists(pmo_project):
    draft = _draft(pmo_project, labels=["spend"])

    with pytest.raises(workflow.FinalizeRefused, match="rank-70"):
        workflow.finalize_ticket(**_call(pmo_project, draft.task_id))


def test_unknown_label_lists_project_vocabulary(pmo_project):
    with pytest.raises(workflow.WorkflowError, match=r"unknown label.*spend.*release.*bug"):
        _draft(pmo_project, labels=["unbounded-free-text"])


def test_touches_overlap_is_advisory_and_preserves_promotion(pmo_project):
    first = _draft(
        pmo_project,
        title="Change public API",
        touches=["src/api/**"],
        idempotency_key="touch-first",
    )
    workflow.finalize_ticket(**_call(pmo_project, first.task_id))
    second = _draft(
        pmo_project,
        title="Change API serializer",
        touches=["src/api/serializers.py"],
        idempotency_key="touch-second",
    )

    result = workflow.finalize_ticket(**_call(pmo_project, second.task_id))

    assert result.status == "ready"
    assert any("touches overlap" in warning for warning in result.warnings)


def test_cancel_preserves_comments_links_and_is_idempotent(pmo_project):
    with kanban_db.connect_closing(board="acme") as conn:
        parent_id = kanban_db.create_task(
            conn,
            title="Parent",
            board="acme",
            project_id=pmo_project["project_id"],
        )
    draft = _draft(pmo_project, parent_task_ids=[parent_id])
    workflow.cancel_ticket(**_call(pmo_project, draft.task_id), reason="No longer needed")
    repeated = workflow.cancel_ticket(
        **_call(pmo_project, draft.task_id), reason="No longer needed"
    )

    with kanban_db.connect_closing(board="acme") as conn:
        stored = kanban_db.get_task(conn, draft.task_id)
        comments = kanban_db.list_comments(conn, draft.task_id)
        parents = kanban_db.parent_ids(conn, draft.task_id)
    assert stored is not None and stored.status == "archived"
    assert parent_id in parents
    assert any(workflow.LIFECYCLE_MARKER in item.body for item in comments)
    assert repeated.idempotent is True


def test_reopen_retains_predecessor_and_returns_one_draft_successor(pmo_project):
    draft = _draft(pmo_project)
    workflow.cancel_ticket(**_call(pmo_project, draft.task_id), reason="Paused")

    reopened = workflow.reopen_ticket(
        **_call(pmo_project, draft.task_id), reason="Priority restored"
    )
    repeated = workflow.reopen_ticket(
        **_call(pmo_project, draft.task_id), reason="Priority restored"
    )

    assert reopened.successor_task_id == repeated.successor_task_id
    assert repeated.idempotent is True
    with kanban_db.connect_closing(board="acme") as conn:
        predecessor = kanban_db.get_task(conn, draft.task_id)
        successor = kanban_db.get_task(conn, reopened.successor_task_id)
        parents = kanban_db.parent_ids(conn, successor.id)
    assert predecessor is not None and predecessor.status == "archived"
    assert successor is not None and successor.status == "triage"
    assert successor.workspace_path != predecessor.workspace_path
    assert draft.task_id in parents


def test_illegal_transition_names_legal_actions(pmo_project):
    draft = _draft(pmo_project)
    workflow.cancel_ticket(**_call(pmo_project, draft.task_id), reason="Cancelled")

    with pytest.raises(workflow.IllegalTransition) as exc_info:
        workflow.finalize_ticket(**_call(pmo_project, draft.task_id))
    assert exc_info.value.legal_actions == ("reopen",)
    assert "legal actions: reopen" in str(exc_info.value)


def test_third_rework_escalates_to_pm_without_overwriting_history(pmo_project):
    current = _draft(pmo_project).task_id
    predecessors = []
    for number in range(1, 4):
        workflow.cancel_ticket(**_call(pmo_project, current), reason=f"Review {number}")
        reopened = workflow.reopen_ticket(
            **_call(pmo_project, current), reason=f"Revise specification {number}"
        )
        predecessors.append(current)
        current = reopened.successor_task_id

    with kanban_db.connect_closing(board="acme") as conn:
        successor = kanban_db.get_task(conn, current)
        comments = kanban_db.list_comments(conn, current)
        assert all(kanban_db.get_task(conn, item).status == "archived" for item in predecessors)
    assert successor is not None and "Rework count: 3" in successor.body
    assert any("@pm Rework escalation" in item.body for item in comments)


def test_validate_body_accepts_both_supported_contract_shapes():
    markdown = """## Context
Why.
## Acceptance
- [ ] Works
## Out of scope
No UI.
## Closure Evidence
- [ ] Test output
"""
    result = workflow.validate_body("Implement behavior", markdown)
    assert result.acceptance_count == 1
    assert result.evidence_count == 1
