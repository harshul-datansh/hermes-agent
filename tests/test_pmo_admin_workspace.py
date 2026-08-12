"""Focused contracts for the PMO administration control room."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from plugins.pmo import admin_workspace, bootstrap, project_scope
from plugins.pmo.access_policy import Principal
from plugins.pmo.dashboard.plugin_api import (
    AdministrationGlobalAgentsBody,
    AdministrationGlobalConfigurationBody,
    AdministrationRuntimeBody,
    set_administration_global_configuration,
    set_administration_global_configuration_agents,
)


def test_project_administration_chat_is_scoped_and_returns_explicit_actions(tmp_path):
    primary = tmp_path / "alpha"
    (primary / ".datansh").mkdir(parents=True)
    scope = SimpleNamespace(primary_path=primary)
    permissions = {
        "manage_agents": True,
        "connect_provider": True,
        "global": False,
    }

    saved = admin_workspace.append_message(
        scope,
        author="human:developer@example.test",
        body="Please connect OpenAI Codex for this project.",
    )
    proposed = admin_workspace.suggestion(
        saved["body"] + " Create a QA agent too.", permissions, project_name="Alpha"
    )
    assistant = admin_workspace.append_message(
        scope,
        author="Hermes Administration",
        body=proposed["body"],
        kind="assistant",
        actions=proposed["actions"],
    )

    rows = admin_workspace.messages(scope)
    assert [row["id"] for row in rows] == [saved["id"], assistant["id"]]
    assert {item["kind"] for item in assistant["actions"]} == {
        "connect_codex", "create_agent"
    }
    assert not (tmp_path / "pmo-admin-workspace" / "global.jsonl").exists()


def test_global_admin_suggestions_offer_explicit_iam_and_onboarding_actions():
    proposed = admin_workspace.suggestion(
        "I need IAM access and to onboard a project",
        {"global": True},
        project_name=None,
    )
    assert {action["kind"] for action in proposed["actions"]} == {
        "open_onboarding",
        "open_access",
    }


def test_registered_global_agent_can_edit_only_global_configuration(tmp_path, monkeypatch):
    """A global machine principal never becomes a project member by config."""

    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setattr(admin_workspace, "get_default_hermes_root", lambda: root)
    agent = Principal("agent:global/config-ops", "agent")
    saved = admin_workspace.set_global_configuration_agents(principals=[agent.id])
    assert saved["access"]["global_agents"] == [agent.id]
    assert admin_workspace.can_manage_global_configuration(agent) is True
    primary = tmp_path / "project"
    primary.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(root))
    result = bootstrap.bootstrap_project(slug="project", name="Project", workspace=primary)
    scope = project_scope.resolve(result.project_id, board_slug=result.board_slug)
    assert admin_workspace.permissions_for(agent, scope)["read"] is False
    updated = set_administration_global_configuration(
        AdministrationGlobalConfigurationBody(models={"pm": "gpt-global"}, runtime=AdministrationRuntimeBody(skills=["browser"])),
        principal=agent,
    )
    assert updated["configuration"]["models"]["pm"] == "gpt-global"
    with pytest.raises(HTTPException, match="Human global administrator"):
        set_administration_global_configuration_agents(
            AdministrationGlobalAgentsBody(principals=["agent:global/other"]), principal=agent
        )
