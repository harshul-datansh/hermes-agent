from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from plugins.pmo.access_policy import (
    ACCESS_FILENAME,
    AUDIT_FILENAME,
    AccessPolicy,
    MemberRule,
    OrgRankRule,
    Principal,
    AccessPolicyError,
    _scope_for_connection,
    can,
    evaluate,
    infer_route_action,
)
from plugins.pmo.project_scope import (
    AgentConfig,
    OrchestratorConfig,
    ProjectConfig,
    ProjectIdentity,
    ProjectScope,
)


def _scope(tmp_path: Path) -> ProjectScope:
    config = ProjectConfig(
        version=1,
        project=ProjectIdentity(name="Acme", slug="acme"),
        orchestrator=OrchestratorConfig(profile="pm-acme"),
        agents=(AgentConfig(handle="dev-1", profile="developer", role="developer"),),
    )
    return ProjectScope(
        project_id="project-acme",
        slug="acme",
        name="Acme",
        board_slug="acme",
        primary_path=tmp_path,
        folders=(tmp_path,),
        workspace=tmp_path,
        config=config,
    )


def _policy() -> AccessPolicy:
    return AccessPolicy(
        version=1,
        members=(
            MemberRule(principal="human:viewer", role="viewer"),
            MemberRule(principal="human:contributor", role="contributor"),
            MemberRule(principal="human:pm", role="pm"),
            MemberRule(principal="human:admin", role="project_admin"),
            MemberRule(principal="human:cfo", role="viewer"),
            MemberRule(principal="human:ceo", role="viewer"),
        ),
        org_ranks=(
            OrgRankRule(principal="human:cfo", rank=50),
            OrgRankRule(principal="human:ceo", rank=100),
        ),
    )


@pytest.mark.parametrize(
    ("role", "read", "comment", "write", "config_write", "member_write"),
    [
        ("viewer", True, True, False, False, False),
        ("contributor", True, True, True, False, False),
        ("pm", True, True, True, True, False),
        ("admin", True, True, True, True, True),
    ],
)
def test_viewer_contributor_pm_admin_matrix(
    tmp_path, role, read, comment, write, config_write, member_write
):
    scope = _scope(tmp_path)
    principal = Principal(f"human:{role}", "human")
    policy = _policy()
    assert can(principal, "task.read", scope, policy=policy) is read
    assert can(principal, "comment.write", scope, policy=policy) is comment
    assert can(principal, "task.write", scope, policy=policy) is write
    assert can(principal, "config.write", scope, policy=policy) is config_write
    assert can(principal, "member.write", scope, policy=policy) is member_write


def test_org_rank_is_separate_from_project_membership(tmp_path):
    scope = _scope(tmp_path)
    policy = _policy()
    assert can(Principal("human:cfo", "human"), "approval.decide", scope, policy=policy)
    assert not can(Principal("human:cfo", "human"), "org.manage", scope, policy=policy)
    assert can(Principal("human:ceo", "human"), "org.manage", scope, policy=policy)
    assert not can(Principal("human:admin", "human"), "approval.decide", scope, policy=policy)


def test_cfo_rank_can_read_and_post_founder_threads_without_membership(tmp_path):
    scope = _scope(tmp_path)
    policy = AccessPolicy(
        version=1,
        members=(),
        org_ranks=(OrgRankRule(principal="human:cfo", rank=70),),
    )
    principal = Principal("human:cfo", "human")

    assert can(principal, "thread.read", scope, policy=policy)
    assert can(principal, "thread.post", scope, policy=policy)
    assert not can(principal, "task.write", scope, policy=policy)


def test_global_ceo_rank_grants_project_admin_capabilities(monkeypatch, tmp_path):
    import plugins.pmo.access_policy as access_policy

    scope = _scope(tmp_path)
    policy = AccessPolicy(version=1)
    monkeypatch.setattr(access_policy, "load_access_policy", lambda _scope: policy)
    monkeypatch.setattr(access_policy, "global_org_rank", lambda _principal: 100)
    ceo = Principal("human:ceo@example.com", "human")

    assert can(ceo, "task.read", scope)
    assert can(ceo, "member.write", scope)
    assert can(ceo, "org.manage", scope)


def test_cfo_rank_can_manage_project_members_without_project_admin_role(tmp_path):
    scope = _scope(tmp_path)
    policy = AccessPolicy(
        version=1,
        members=(MemberRule(principal="human:cfo", role="contributor"),),
        org_ranks=(OrgRankRule(principal="human:cfo", rank=70),),
    )
    cfo = Principal("human:cfo", "human")
    assert can(cfo, "member.read", scope, policy=policy)
    assert can(cfo, "member.write", scope, policy=policy)
    assert not can(cfo, "config.write", scope, policy=policy)


def test_agents_are_structural_and_can_never_hold_org_rank(tmp_path):
    scope = _scope(tmp_path)
    policy = _policy()
    assert can(Principal("agent:acme/pm", "agent"), "config.write", scope, policy=policy)
    assert can(Principal("agent:acme/dev-1", "agent"), "task.write", scope, policy=policy)
    assert not can(Principal("agent:acme/dev-1", "agent"), "config.write", scope, policy=policy)
    assert not can(Principal("agent:acme/pm", "agent"), "approval.decide", scope, policy=policy)
    with pytest.raises(ValidationError):
        OrgRankRule(principal="agent:acme/pm", rank=100)


def test_service_principal_cannot_impersonate_human_membership(tmp_path):
    scope = _scope(tmp_path)
    policy = _policy()
    assert not can(
        Principal("human:admin", "service"), "member.write", scope, policy=policy
    )
    assert not can(
        Principal("human:ceo", "service"), "org.manage", scope, policy=policy
    )


def test_unknown_action_denies_and_audits_allowed_and_denied(tmp_path):
    scope = _scope(tmp_path)
    policy = _policy()
    allowed = evaluate(Principal("human:viewer", "human"), "task.read", scope, policy=policy)
    denied = evaluate(Principal("human:viewer", "human"), "secret.power", scope, policy=policy)
    assert allowed.allowed is True
    assert denied.allowed is False
    assert denied.reason == "unknown action"
    events = [
        json.loads(line)
        for line in (tmp_path / ".datansh" / AUDIT_FILENAME).read_text(encoding="utf-8").splitlines()
    ]
    assert [event["allowed"] for event in events] == [True, False]
    assert [event["action"] for event in events] == ["task.read", "secret.power"]


def test_audit_redacts_sensitive_text(tmp_path):
    scope = _scope(tmp_path)
    secret = "sk-proj-123456789012345678901234567890"
    policy = AccessPolicy(
        version=1,
        members=(MemberRule(principal=f"human:{secret}", role="viewer"),),
    )
    evaluate(Principal(f"human:{secret}", "human"), "task.read", scope, policy=policy)
    audit = (tmp_path / ".datansh" / AUDIT_FILENAME).read_text(encoding="utf-8")
    assert secret not in audit


def test_no_user_level_config_or_agent_memberships(tmp_path):
    scope = _scope(tmp_path)
    from plugins.pmo.access_policy import access_policy_path

    assert access_policy_path(scope) == tmp_path / ".datansh" / ACCESS_FILENAME
    with pytest.raises(ValidationError):
        MemberRule(principal="agent:acme/dev-1", role="contributor")

    from plugins.pmo.dashboard.plugin_api import router

    assert not any("/me/config" in route.path for route in router.routes)


def test_every_pmo_route_is_guarded():
    from plugins.pmo.dashboard.plugin_api import router

    unguarded = []
    for route in router.routes:
        dependant = getattr(route, "dependant", None)
        dependencies = getattr(dependant, "dependencies", ())
        if not any(getattr(dep.call, "_pmo_guard", False) for dep in dependencies):
            unguarded.append(route.path)
    assert unguarded == []


def test_route_scope_uses_authenticated_host_project_without_board(tmp_path):
    scope = _scope(tmp_path)
    connection = SimpleNamespace(
        state=SimpleNamespace(datansh_project_scope=scope),
        query_params={},
        scope={"route": SimpleNamespace(path="/api/plugins/pmo/model-options")},
        path_params={},
    )

    assert _scope_for_connection(connection) is scope


def test_route_scope_rejects_board_that_disagrees_with_host_project(tmp_path):
    scope = _scope(tmp_path)
    connection = SimpleNamespace(
        state=SimpleNamespace(datansh_project_scope=scope),
        query_params={"board": "another-project"},
        scope={"route": SimpleNamespace(path="/api/plugins/pmo/tasks")},
        path_params={},
    )

    with pytest.raises(AccessPolicyError, match="selected project"):
        _scope_for_connection(connection)


def test_route_scope_rejects_project_id_that_disagrees_with_board(tmp_path):
    scope = _scope(tmp_path)
    connection = SimpleNamespace(
        state=SimpleNamespace(datansh_project_scope=scope),
        query_params={"board": scope.board_slug, "project_id": "project-other"},
        scope={"route": SimpleNamespace(path="/api/plugins/pmo/events")},
        path_params={},
    )

    with pytest.raises(AccessPolicyError, match="selected project"):
        _scope_for_connection(connection)


@pytest.mark.parametrize(
    ("method", "path", "action"),
    [
        ("GET", "/tasks/{task_id}", "task.read"),
        ("POST", "/tasks", "task.write"),
        ("POST", "/tasks/{task_id}/comments", "comment.write"),
        ("DELETE", "/tasks/{task_id}", "task.delete"),
        ("POST", "/dispatch", "board.dispatch"),
        ("PUT", "/orchestration", "config.write"),
        ("PATCH", "/profiles/{profile_name}", "agent.manage"),
        ("WEBSOCKET", "/events", "task.read"),
    ],
)
def test_route_action_coverage(method, path, action):
    assert infer_route_action(method, path) == action
