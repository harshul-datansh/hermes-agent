"""Full native blocker escalation round trip."""

from __future__ import annotations

import time

import pytest

from hermes_cli import kanban_db, projects_db
from plugins.pmo import escalation, mentions
from plugins.pmo.project_scope import (
    AgentConfig,
    EscalationConfig,
    OrchestratorConfig,
    ProjectConfig,
    ProjectIdentity,
    ProjectScope,
)


@pytest.fixture()
def escalation_scope(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.delenv("HERMES_KANBAN_DB", raising=False)
    with projects_db.connect_closing() as conn:
        project_id = projects_db.create_project(
            conn,
            name="Acme",
            slug="acme",
            primary_path=str(workspace),
            board_slug="acme",
        )
    kanban_db.create_board("acme", name="Acme", project_id=project_id)
    config = ProjectConfig(
        version=1,
        project=ProjectIdentity(name="Acme", slug="acme"),
        orchestrator=OrchestratorConfig(profile="pm-acme"),
        agents=(AgentConfig(handle="dev-1", profile="dev-acme", role="developer"),),
        escalation=EscalationConfig(pm_retry_limit=2, blocked_timeout_minutes=30),
    )
    scope = ProjectScope(
        project_id=project_id,
        slug="acme",
        name="Acme",
        board_slug="acme",
        primary_path=workspace.resolve(),
        folders=(workspace.resolve(),),
        workspace=workspace.resolve(),
        config=config,
    )
    with kanban_db.connect_closing(board="acme") as conn:
        task = kanban_db.create_task(
            conn,
            title="Deploy",
            assignee="dev-acme",
            board="acme",
            project_id=project_id,
        )
    return scope, task


def test_block_autocomments_pm_and_wakes_through_callback(escalation_scope):
    scope, task = escalation_scope
    assert escalation.block_for_pm(
        scope,
        task_id=task,
        author="dev-1",
        reason="Need the production credential",
        kind="capability",
    )
    pending = mentions.pending_routes(scope)
    assert len(pending) == 1 and pending[0].recipient.handle == "pm"
    with kanban_db.connect_closing(board="acme") as conn:
        assert kanban_db.get_task(conn, task).status == "blocked"


def test_transient_requires_pm_retry_budget(escalation_scope):
    scope, task = escalation_scope
    escalation.block_for_pm(
        scope, task_id=task, author="dev-1", reason="Provider is flaky", kind="transient"
    )
    escalation.record_pm_attempt(scope, task_id=task, reason="Provider is flaky", note="retry 1")
    with pytest.raises(RuntimeError, match="1/2"):
        escalation.escalate_to_human(scope, task_id=task, reason="Provider is flaky")
    escalation.record_pm_attempt(scope, task_id=task, reason="Provider is flaky", note="retry 2")
    raised = escalation.escalate_to_human(scope, task_id=task, reason="Provider is flaky")
    assert raised.required_rank == 50
    assert raised.needs_response_by > int(time.time())


def test_blocked_pm_human_answer_worker_unblock_round_trip(escalation_scope):
    scope, task = escalation_scope
    escalation.block_for_pm(
        scope,
        task_id=task,
        author="dev-1",
        reason="Which region should receive the deployment?",
        kind="needs_input",
    )
    raised = escalation.escalate_to_human(
        scope,
        task_id=task,
        reason="Which region should receive the deployment?",
        required_rank=50,
        approver_profile="cto",
    )
    assert mentions.inbox(scope, user="founders-office", unread_only=True)

    assert escalation.resolve_human_input(
        scope,
        escalation=raised,
        answer="Deploy to ap-south-1.",
        actor="cto",
        actor_rank=70,
    )
    with kanban_db.connect_closing(board="acme") as conn:
        native_task = kanban_db.get_task(conn, task)
        comments = kanban_db.list_comments(conn, task)
        assert native_task.status in {"ready", "todo", "running"}
        assert any("@dev-1 Human response" in comment.body for comment in comments)
        assert {event.kind for event in kanban_db.list_events(conn, task)} >= {
            "blocked",
            "commented",
        }


def test_escalation_is_idempotent_per_task_and_reason(escalation_scope):
    scope, task = escalation_scope
    reason = "Need customer confirmation"
    escalation.block_for_pm(
        scope, task_id=task, author="dev-1", reason=reason, kind="needs_input"
    )
    first = escalation.escalate_to_human(scope, task_id=task, reason=reason)
    second = escalation.escalate_to_human(scope, task_id=task, reason=reason)
    assert second == first
