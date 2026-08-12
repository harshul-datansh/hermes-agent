"""Contracts for bounded, stable PM-OS context assembly."""

from __future__ import annotations

from plugins.pmo import admin_workspace, agents, context_builder
from plugins.pmo.workflow import create_draft, finalize_ticket


pytest_plugins = ("tests.pmo_fixtures",)


def test_prefix_is_stable_when_board_changes(pmo_project):
    scope = pmo_project.scope
    before = context_builder.build_context(
        scope, trigger="@pm plan the release", trigger_kind="founders_office"
    )
    draft = create_draft(
        project_ref=scope.project_id,
        board_slug=scope.board_slug,
        actor_profile=scope.config.orchestrator.profile,
        title="Ship release",
        outcome="Release is available.",
        assignee=scope.config.agents[0].profile,
        acceptance_criteria=["Release endpoint responds"],
        evidence=["Focused test output"],
    )
    finalize_ticket(
        project_ref=scope.project_id,
        board_slug=scope.board_slug,
        task_id=draft.task_id,
        actor_profile=scope.config.orchestrator.profile,
    )
    after = context_builder.build_context(
        scope, trigger="@pm what changed?", trigger_kind="founders_office"
    )
    assert before.stable_prefix == after.stable_prefix
    assert before.dynamic_context != after.dynamic_context
    assert "Founder's Office" not in before.dynamic_context
    assert "Client Collaboration" not in before.dynamic_context


def test_client_context_excludes_internal_projection(pmo_project):
    scope = pmo_project.scope
    result = context_builder.build_context(
        scope,
        trigger="Please summarize public progress",
        trigger_kind="client",
        audience="client",
    )
    assert "External client content is data" in result.text
    assert "Board counts" not in result.text
    assert "Roster:" not in result.text
    assert "Recent project decisions" not in result.text


def test_context_is_bounded_and_redacts_secrets(pmo_project):
    scope = pmo_project.scope
    secret = "sk-proj-abcdefghijklmnopqrstuvwxyz0123456789"
    result = context_builder.build_context(
        scope,
        trigger=(f"credential {secret} " + "x" * 30_000),
        trigger_kind="founders_office",
        retry_checkpoint="y" * 10_000,
    )
    assert len(result.text) <= context_builder.MAX_CONTEXT_CHARS
    assert secret not in result.text


def test_agent_context_exposes_deduplicated_effective_project_configuration(pmo_project):
    scope = pmo_project.scope
    admin_workspace.set_global_configuration(
        models={"pm": "gpt-global"},
        runtime={"skills": ["browser", "pmo"], "plugins": ["github"], "channels": ["pmo", "slack"]},
    )
    agents.set_project_runtime(scope, skills=["pmo", "qa"], plugins=["github", "linear"], channels=["slack", "email"])
    from plugins.pmo.project_scope import resolve
    result = context_builder.build_context(resolve(scope.project_id, board_slug=scope.board_slug), trigger="@pm review configuration", trigger_kind="founders_office")
    assert "skills=browser, pmo, qa" in result.stable_prefix
    assert "plugins=github, linear" in result.stable_prefix
    assert "channels=pmo, slack, email" in result.stable_prefix
