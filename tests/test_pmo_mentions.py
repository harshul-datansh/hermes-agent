"""Project-scoped mention routing over native Kanban comments."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from hermes_cli import kanban_db, projects_db
from plugins.pmo import mentions
from plugins.pmo.project_scope import (
    AgentConfig,
    OrchestratorConfig,
    ProjectConfig,
    ProjectIdentity,
    ProjectScope,
)


@pytest.fixture()
def mention_scope(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.delenv("HERMES_KANBAN_DB", raising=False)
    monkeypatch.delenv("HERMES_KANBAN_HOME", raising=False)
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
        agents=(
            AgentConfig(handle="dev-1", profile="dev-acme", role="developer"),
            AgentConfig(handle="qa", profile="qa-acme", role="quality"),
        ),
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
        task_id = kanban_db.create_task(
            conn,
            title="Ship it",
            assignee="dev-acme",
            board="acme",
            project_id=project_id,
        )
        other = kanban_db.create_task(conn, title="Other", board="acme")
    return scope, task_id, other


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("Ask @pm and @dev-1, then @pm again", ["pm", "dev-1"]),
        ("mail user@example.com but ask @qa", ["qa"]),
        ("inline `@pm` then @qa", ["qa"]),
        ("```python\n@pm\n```\n@dev-1", ["dev-1"]),
        ("~~~\n@qa\n~~~\n@pm", ["pm"]),
        ("install @types/node then ping @pm", ["pm"]),
        ("@@pm @a @valid.handle", ["valid.handle"]),
    ],
)
def test_parse_property_cases(body, expected):
    assert mentions.parse(body) == expected


def test_resolve_and_autocomplete_are_current_project_only(mention_scope):
    scope, _, _ = mention_scope
    resolved, unknown = mentions.resolve(scope, ["dev-1", "other-project-dev"])

    assert [item.profile for item in resolved] == ["dev-acme"]
    assert unknown == ["other-project-dev"]
    assert [item["handle"] for item in mentions.autocomplete(scope, "d")] == ["dev-1"]


def test_unknown_rejects_whole_comment_with_valid_list(mention_scope):
    scope, task_id, _ = mention_scope

    with pytest.raises(mentions.UnknownMention) as error:
        mentions.post_comment(
            scope, task_id=task_id, author="dev-1", body="@pm and @ghost help"
        )

    assert error.value.as_dict() == {
        "error": "unknown_mention",
        "unknown": ["ghost"],
        "valid_handles": ["pm", "founders-office", "dev-1", "qa"],
    }
    with kanban_db.connect_closing(board="acme") as conn:
        assert kanban_db.list_comments(conn, task_id) == []


def test_task_from_another_scope_is_rejected(mention_scope):
    scope, _, other = mention_scope
    foreign_scope = replace(scope, project_id="p-foreign")
    with pytest.raises(Exception, match="not bound to project"):
        mentions.post_comment(
            foreign_scope, task_id=other, author="dev-1", body="@pm help"
        )


def test_fanout_retry_is_idempotent_and_backlog_is_bounded(mention_scope):
    scope, task_id, _ = mention_scope
    first = mentions.post_comment(
        scope,
        task_id=task_id,
        author="qa",
        body="@pm @dev-1 please review",
        request_id="req-1",
    )
    second = mentions.post_comment(
        scope,
        task_id=task_id,
        author="qa",
        body="@pm @dev-1 please review",
        request_id="req-1",
    )
    assert first == second

    calls = []
    delivered = mentions.drain_pending(
        scope, lambda recipient, context: calls.append((recipient, context)), limit=1
    )
    assert len(delivered) == 1
    assert calls[0][1]["reply_via"] == "task_comment"
    assert len(mentions.pending_routes(scope)) == 1

    mentions.drain_pending(scope, lambda recipient, context: calls.append((recipient, context)))
    assert mentions.pending_routes(scope) == []


def test_failed_wake_remains_pending_for_restart(mention_scope):
    scope, task_id, _ = mention_scope
    mentions.post_comment(scope, task_id=task_id, author="qa", body="@pm help")

    def fail(_recipient, _context):
        raise RuntimeError("gateway unavailable")

    with pytest.raises(RuntimeError, match="gateway unavailable"):
        mentions.drain_pending(scope, fail)
    assert len(mentions.pending_routes(scope)) == 1


def test_human_notification_has_unread_read_projection(mention_scope):
    scope, task_id, _ = mention_scope
    result = mentions.post_comment(
        scope, task_id=task_id, author="pm", body="@founders-office input needed"
    )
    notification_id = result.notification_ids[0]

    assert [item["id"] for item in mentions.inbox(scope, user="founders-office", unread_only=True)] == [notification_id]
    assert mentions.mark_notification_read(
        scope, notification_id=notification_id, user="founders-office"
    )
    assert mentions.inbox(scope, user="founders-office", unread_only=True) == []
    assert mentions.inbox(scope, user="dev-1") == []
