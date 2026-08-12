"""GitHub App consent and ephemeral authentication security contracts."""

from __future__ import annotations

import base64
import json
from types import SimpleNamespace

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import ValidationError

from plugins.pmo import github_app
from plugins.pmo.dashboard.plugin_api import (
    RepositoryCloneBody,
    RepositoryConnectBody,
    RepositoryOnboardBody,
    _github_app_installation_authorized,
    _project_github_installations,
)
from plugins.pmo.project_scope import (
    GitHubAppRepositoryBinding,
    OrchestratorConfig,
    ProjectConfig,
    ProjectIdentity,
    RepositoryConfig,
)


class FakeProvider:
    settings = SimpleNamespace(app_slug="datansh-pm-os")

    def __init__(self):
        self.installations = {
            101: github_app.InstallationInfo(101, "hedgiai", "selected", "write"),
            202: github_app.InstallationInfo(202, "datansh", "selected", "read"),
        }
        self.repositories = {
            101: (
                github_app.GitHubRepositoryInfo(501, "hedgiai/hedgi_app", True, "main"),
            ),
            202: (
                github_app.GitHubRepositoryInfo(601, "datansh/shared-api", True, "develop"),
            ),
        }

    def get_installation(self, installation_id):
        try:
            return self.installations[int(installation_id)]
        except KeyError as exc:
            raise github_app.GitHubAppError("unknown installation") from exc

    def list_installations(self):
        return tuple(
            github_app.GitHubInstallationCandidate(item, updated_at=1_000)
            for item in self.installations.values()
        )

    def list_repositories(self, installation_id):
        self.get_installation(installation_id)
        return self.repositories[int(installation_id)]

    def verify_repository(self, installation_id, repository_id, full_name):
        installation = self.get_installation(installation_id)
        for repository in self.list_repositories(installation_id):
            if repository.repository_id == int(repository_id) and repository.full_name.casefold() == str(full_name).casefold():
                return installation, repository
        raise github_app.GitHubAppError("repository not installed")

    def git_environment(self, binding, *, access):
        self.verify_repository(binding.installation_id, binding.repository_id, binding.full_name)
        return {"DATANSH_PMO_GITHUB_APP_TOKEN": "ephemeral-test-token", "ACCESS": access}


def _scope():
    return SimpleNamespace(project_id="p_hedgi", board_slug="hedgi-app")


def test_single_use_install_state_is_bound_to_principal_and_project():
    provider = FakeProvider()
    started = github_app.begin_install(
        _scope(), principal="human:ceo@datansh.local", provider=provider, now=1_000
    )
    assert started["consent_url"].startswith(
        "https://github.com/apps/datansh-pm-os/installations/new?state="
    )

    with pytest.raises(github_app.GitHubAppError, match="invalid"):
        github_app.finalize_install(
            state=started["state"],
            installation_id=101,
            principal="human:attacker@datansh.local",
            provider=provider,
            expected_project_id="p_hedgi",
            now=1_001,
        )

    pending, installation = github_app.finalize_install(
        state=started["state"],
        installation_id=101,
        principal="human:ceo@datansh.local",
        provider=provider,
        expected_project_id="p_hedgi",
        now=1_002,
    )
    assert pending.project_id == "p_hedgi"
    assert installation.installation_account == "hedgiai"
    with pytest.raises(github_app.GitHubAppError, match="already used"):
        github_app.finalize_install(
            state=started["state"],
            installation_id=101,
            principal="human:ceo@datansh.local",
            provider=provider,
            now=1_003,
        )


def test_install_poll_grants_one_recent_account_with_repositories():
    provider = FakeProvider()
    started = github_app.begin_install(
        _scope(), principal="human:ceo@datansh.local", provider=provider, now=1_000
    )

    result = github_app.poll_install(
        state=started["state"],
        principal="human:ceo@datansh.local",
        provider=provider,
        expected_project_id="p_hedgi",
        now=1_001,
    )

    assert result["status"] == "needs_selection"
    assert {item["installation_account"] for item in result["candidates"]} == {"hedgiai", "datansh"}

    provider.installations.pop(202)
    provider.repositories.pop(202)
    result = github_app.poll_install(
        state=started["state"],
        principal="human:ceo@datansh.local",
        provider=provider,
        expected_project_id="p_hedgi",
        now=1_002,
    )

    assert result["status"] == "available"
    assert result["installation"].installation_account == "hedgiai"
    assert result["repository_count"] == 1


def test_one_project_can_hold_repositories_from_different_installations():
    provider = FakeProvider()
    hedgi = github_app.authorize_repository(
        installation_id=101,
        repository_id=501,
        full_name="hedgiai/hedgi_app",
        provider=provider,
    )
    shared = github_app.authorize_repository(
        installation_id=202,
        repository_id=601,
        full_name="datansh/shared-api",
        provider=provider,
    )
    config = ProjectConfig(
        version=1,
        project=ProjectIdentity(name="Hedgi", slug="hedgi-app"),
        orchestrator=OrchestratorConfig(profile="pm-hedgi-app"),
        repositories=(
            RepositoryConfig(name="hedgi-app", path=".", primary=True, github_app=hedgi),
            RepositoryConfig(name="shared-api", path="../shared-api", github_app=shared),
        ),
    )

    payload = config.model_dump(mode="json")["repositories"]
    assert payload[0]["github_app"]["installation_id"] == 101
    assert payload[1]["github_app"]["installation_id"] == 202
    serialized = json.dumps(payload)
    assert "token" not in serialized.casefold()
    assert "private_key" not in serialized.casefold()
    assert "client_secret" not in serialized.casefold()


def test_existing_project_repository_keeps_its_github_installation_available():
    binding = GitHubAppRepositoryBinding(
        installation_id=101,
        installation_account="hedgiai",
        repository_id=501,
        full_name="hedgiai/hedgi_app",
    )
    scope = SimpleNamespace(
        project_id="p_hedgi",
        config=SimpleNamespace(
            repositories=(
                SimpleNamespace(github_app=binding, access="write"),
            )
        ),
    )
    principal = SimpleNamespace(id="human:project-owner@datansh.local")

    installations = _project_github_installations(scope)

    assert installations == {
        101: {
            "installation_id": 101,
            "installation_account": "hedgiai",
            "repository_selection": "selected",
            "contents_permission": "write",
            "expires_at": None,
        }
    }
    assert _github_app_installation_authorized(scope, principal, 101) == installations[101]
    with pytest.raises(github_app.GitHubAppError, match="has not been consented"):
        _github_app_installation_authorized(scope, principal, 202)


def test_repository_api_models_reject_raw_credentials_and_partial_selection():
    with pytest.raises(ValidationError):
        RepositoryCloneBody.model_validate(
            {
                "url": "https://github.com/hedgiai/hedgi_app.git",
                "token": "must-not-be-accepted",
            }
        )
    with pytest.raises(ValidationError, match="provided together"):
        RepositoryConnectBody.model_validate(
            {"path": "C:/repo", "installation_id": 101}
        )
    with pytest.raises(ValidationError):
        RepositoryOnboardBody.model_validate(
            {
                "slug": "private-project",
                "name": "Private Project",
                "path": "C:/repo",
                "installation_id": 101,
                "repository_id": 501,
                "full_name": "hedgiai/hedgi_app",
            }
        )


def test_public_status_never_returns_secret_or_secret_reference(monkeypatch):
    monkeypatch.setattr(
        github_app,
        "load_settings",
        lambda: github_app.GitHubAppSettings(
            app_slug="datansh-pm-os",
            app_id=42,
            client_id="Iv1.public",
            callback_url="https://example.test/callback",
            api_url="https://api.github.com",
            private_key="super-secret-private-key",
            client_secret="super-secret-client-secret",
        ),
    )
    status = github_app.public_status()
    serialized = json.dumps(status)

    assert status["configured"] is True
    assert status["required_permission"] == "contents"
    assert "secret" not in serialized.casefold()
    assert "private_key" not in serialized.casefold()


def test_api_provider_verifies_app_and_mints_repository_scoped_token():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_key = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    token_requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert "super-secret-token" not in str(request.url)
        if request.method == "GET" and request.url.path == "/app/installations/101":
            return httpx.Response(
                200,
                json={
                    "app_id": 42,
                    "account": {"login": "hedgiai"},
                    "repository_selection": "selected",
                    "permissions": {"contents": "write"},
                },
            )
        if request.method == "POST" and request.url.path.endswith("/access_tokens"):
            token_requests.append(json.loads(request.content))
            return httpx.Response(201, json={"token": "super-secret-token", "expires_at": "soon"})
        if request.method == "GET" and request.url.path == "/installation/repositories":
            assert request.headers["Authorization"] == "Bearer super-secret-token"
            return httpx.Response(
                200,
                json={
                    "repositories": [
                        {
                            "id": 501,
                            "full_name": "hedgiai/hedgi_app",
                            "private": True,
                            "default_branch": "main",
                        }
                    ]
                },
            )
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    settings = github_app.GitHubAppSettings(
        app_slug="datansh-pm-os",
        app_id=42,
        client_id=None,
        callback_url=None,
        api_url="https://api.github.com",
        private_key=private_key,
    )
    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = github_app.GitHubApiProvider(settings, client=client)
    binding = github_app.authorize_repository(
        installation_id=101,
        repository_id=501,
        full_name="hedgiai/hedgi_app",
        provider=provider,
    )
    environment = provider.git_environment(binding, access="write")

    assert environment["DATANSH_PMO_GITHUB_APP_TOKEN"] == "super-secret-token"
    assert environment["GIT_CONFIG_VALUE_0"] == ""
    assert "super-secret-token" not in environment["GIT_CONFIG_VALUE_1"]
    assert any(
        request.get("repository_ids") == [501]
        and request.get("permissions") == {"contents": "write"}
        for request in token_requests
    )
    client.close()


def test_api_provider_prefers_client_id_as_jwt_issuer():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_key = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    settings = github_app.GitHubAppSettings(
        app_slug="datansh-pm-os",
        app_id=42,
        client_id="Iv23test-client-id",
        callback_url=None,
        api_url="https://api.github.com",
        private_key=private_key,
    )
    payload_segment = github_app.GitHubApiProvider(settings)._app_jwt().split(".")[1]
    payload = json.loads(
        base64.urlsafe_b64decode(payload_segment + "=" * (-len(payload_segment) % 4))
    )

    assert payload["iss"] == "Iv23test-client-id"


def test_binding_requires_identifier_only_shape():
    binding = GitHubAppRepositoryBinding(
        installation_id=101,
        installation_account="hedgiai",
        repository_id=501,
        full_name="hedgiai/hedgi_app",
    )
    assert set(binding.model_dump()) == {
        "provider",
        "installation_id",
        "installation_account",
        "repository_id",
        "full_name",
    }


def test_read_only_installation_cannot_mint_write_git_credentials():
    provider = FakeProvider()
    binding = GitHubAppRepositoryBinding(
        installation_id=202,
        installation_account="datansh",
        repository_id=601,
        full_name="datansh/shared-api",
    )
    # The production provider enforces permission before token minting. This
    # assertion exercises the same public contract with its actual method.
    class ReadOnlyProvider(github_app.GitHubApiProvider):
        def __init__(self):
            pass

        def verify_repository(self, installation_id, repository_id, full_name):
            return provider.verify_repository(installation_id, repository_id, full_name)

    with pytest.raises(github_app.GitHubAppError, match="read-only"):
        ReadOnlyProvider().git_environment(binding, access="write")
