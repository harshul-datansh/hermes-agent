from __future__ import annotations

import pytest

from plugins.pmo import project_scope


pytest_plugins = ("tests.pmo_fixtures",)


def test_deny_rules_are_enforced_and_refusal_names_rule(pmo_project):
    secret = pmo_project.workspace / ".env"
    secret.write_text("EXAMPLE=not-a-real-secret", encoding="utf-8")
    assert project_scope.path_allowed(pmo_project.scope, secret) is False
    with pytest.raises(project_scope.ScopeViolation, match=r"denied.*\.env"):
        project_scope.assert_path(pmo_project.scope, secret)


def test_default_project_template_has_day_one_secret_rules():
    deny = set(project_scope._template_data(slug="acme", name="Acme")["scope"]["deny"])
    assert {
        ".env",
        ".env.*",
        "**/secrets/**",
        "**/*.pem",
        "**/*.key",
        "**/id_rsa*",
        "**/.aws/**",
        "**/.ssh/**",
        "**/.hermes/**",
    } <= deny
