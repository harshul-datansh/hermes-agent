from plugins.pmo.access_policy import Principal
from plugins.pmo.tab_policy import ROLE_TABS, TAB_ROLES, policy_for, resolve_tab_role


def test_wrapper_defines_requested_personas():
    assert {
        "manager",
        "backend-developer",
        "client",
        "ceo",
        "cfo",
        "qa",
        "frontend-developer",
        "hr",
        "data-analytics",
        "maintainer",
        "admin",
    }.issubset(TAB_ROLES)
    assert set(ROLE_TABS["client"]) == {"/chat", "/files", "/pmo"}
    assert "/config" in ROLE_TABS["admin"]


def test_principal_aliases_resolve_without_user_config():
    assert resolve_tab_role(
        Principal("human:cfo@datansh.local", "human"),
        project_role="viewer",
        org_rank=70,
    ) == "cfo"
    assert resolve_tab_role(
        Principal("human:qa@datansh.local", "human"),
        project_role="viewer",
        org_rank=0,
    ) == "qa"
    assert resolve_tab_role(
        Principal("agent:acme/backend-developer", "agent"),
        project_role="contributor",
        org_rank=None,
    ) == "backend-developer"


def test_policy_is_project_role_and_rank_aware():
    result = policy_for(
        Principal("human:lead@datansh.local", "human"),
        project_role="project_admin",
        org_rank=0,
    )
    assert result["role"] == "admin"
    assert "/config" in result["allowed_paths"]
    assert result["project_role"] == "project_admin"


def test_admin_substring_in_email_does_not_elevate_contributor():
    result = policy_for(
        Principal("human:testAdmin2@datansh.local", "human"),
        project_role="contributor",
        org_rank=None,
    )
    assert result["role"] == "manager"
    assert "/config" not in result["allowed_paths"]


def test_ceo_persona_keeps_every_hermes_tab():
    result = policy_for(
        Principal("human:ceo@datansh.local", "human"),
        project_role=None,
        org_rank=100,
    )
    assert result["role"] == "ceo"
    assert set(result["allowed_paths"]) == set(result["all_paths"])
