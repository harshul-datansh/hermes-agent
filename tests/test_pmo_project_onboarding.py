"""Staged GitHub App project onboarding contracts."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from plugins.pmo import github_app, project_onboarding, project_scope


class FakeProvider:
    settings = SimpleNamespace(app_slug="datansh-pm-os")

    def __init__(self) -> None:
        self.installations = {
            101: github_app.InstallationInfo(101, "hedgiai", "selected", "write"),
            202: github_app.InstallationInfo(202, "datansh", "selected", "read"),
        }
        self.repositories = {
            101: (
                github_app.GitHubRepositoryInfo(
                    501, "hedgiai/product", True, "main"
                ),
            ),
            202: (
                github_app.GitHubRepositoryInfo(
                    601, "datansh/reference", True, "develop"
                ),
            ),
        }

    def get_installation(self, installation_id: int):
        try:
            return self.installations[int(installation_id)]
        except KeyError as exc:
            raise github_app.GitHubAppError("unknown installation") from exc

    def list_repositories(self, installation_id: int):
        self.get_installation(installation_id)
        return self.repositories[int(installation_id)]

    def verify_repository(
        self, installation_id: int, repository_id: int, full_name: str
    ):
        installation = self.get_installation(installation_id)
        for repository in self.list_repositories(installation_id):
            if (
                repository.repository_id == int(repository_id)
                and repository.full_name.casefold() == str(full_name).casefold()
            ):
                return installation, repository
        raise github_app.GitHubAppError("repository not installed")

    def git_environment(self, binding, *, access: str):
        self.verify_repository(
            binding.installation_id, binding.repository_id, binding.full_name
        )
        return {"DATANSH_PMO_GITHUB_APP_TOKEN": "ephemeral-test-token"}


def _git(repo: Path, *args: str) -> None:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def _checkout(path: Path, remote: str) -> None:
    path.mkdir()
    _git(path, "init", "--initial-branch=main")
    _git(path, "remote", "add", "origin", remote)


@pytest.fixture()
def isolated_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.delenv("HERMES_KANBAN_HOME", raising=False)
    monkeypatch.delenv("HERMES_KANBAN_DB", raising=False)
    return tmp_path


def _grant(draft: dict, principal: str, installation_id: int, provider) -> None:
    subject = project_onboarding.consent_subject(
        draft["onboarding_id"], principal=principal
    )
    started = github_app.begin_install(subject, principal=principal, provider=provider)
    github_app.finalize_install(
        state=started["state"],
        installation_id=installation_id,
        principal=principal,
        expected_project_id=subject.project_id,
        provider=provider,
    )


def test_staged_onboarding_supports_many_installations_and_dedicated_pm(
    isolated_home,
):
    provider = FakeProvider()
    principal = "human:ceo@datansh.local"
    primary = isolated_home / "product"
    secondary = isolated_home / "reference"
    _checkout(primary, "https://github.com/hedgiai/product.git")
    _checkout(secondary, "https://github.com/datansh/reference.git")
    draft = project_onboarding.create_draft(
        principal=principal,
        slug="product",
        name="Product",
        workspace_path=primary,
    )
    _grant(draft, principal, 101, provider)
    _grant(draft, principal, 202, provider)

    project_onboarding.add_repository(
        draft["onboarding_id"],
        principal=principal,
        installation_id=101,
        repository_id=501,
        full_name="hedgiai/product",
        primary=True,
        provider=provider,
    )
    selected = project_onboarding.add_repository(
        draft["onboarding_id"],
        principal=principal,
        installation_id=202,
        repository_id=601,
        full_name="datansh/reference",
        local_path=secondary,
        access="read",
        provider=provider,
    )
    serialized = json.dumps(selected)
    assert "ephemeral-test-token" not in serialized
    assert "private_key" not in serialized.casefold()
    assert {item["github_app"]["installation_id"] for item in selected["repositories"]} == {
        101,
        202,
    }

    completed = project_onboarding.complete_draft(
        draft["onboarding_id"], principal=principal, provider=provider
    )
    scope = project_scope.resolve(
        completed["project_id"], board_slug=completed["board_slug"]
    )

    assert completed["pm_profile"] == "pm-product"
    assert scope.config.orchestrator.profile == "pm-product"
    assert sum(repository.primary for repository in scope.config.repositories) == 1
    assert {repository.github_app.installation_id for repository in scope.config.repositories} == {
        101,
        202,
    }
    with pytest.raises(project_onboarding.ProjectOnboardingError, match="invalid or expired"):
        project_onboarding.draft_view(draft["onboarding_id"], principal=principal)


def test_selection_requires_consent_and_respects_contents_permission(isolated_home):
    provider = FakeProvider()
    principal = "human:ceo@datansh.local"
    draft = project_onboarding.create_draft(
        principal=principal,
        slug="permission-test",
        name="Permission Test",
        workspace_path=isolated_home / "permission-test",
    )
    with pytest.raises(github_app.GitHubAppError, match="not been consented"):
        project_onboarding.add_repository(
            draft["onboarding_id"],
            principal=principal,
            installation_id=202,
            repository_id=601,
            full_name="datansh/reference",
            primary=True,
            provider=provider,
        )

    _grant(draft, principal, 202, provider)
    with pytest.raises(project_onboarding.ProjectOnboardingError, match="read-only"):
        project_onboarding.add_repository(
            draft["onboarding_id"],
            principal=principal,
            installation_id=202,
            repository_id=601,
            full_name="datansh/reference",
            primary=True,
            provider=provider,
        )


def test_completion_requires_exactly_one_primary(isolated_home):
    provider = FakeProvider()
    principal = "human:ceo@datansh.local"
    draft = project_onboarding.create_draft(
        principal=principal,
        slug="no-primary",
        name="No Primary",
        workspace_path=isolated_home / "no-primary",
    )

    with pytest.raises(project_onboarding.ProjectOnboardingError, match="exactly one"):
        project_onboarding.complete_draft(
            draft["onboarding_id"], principal=principal, provider=provider
        )
    # Validation failures reset the completion latch so the draft stays usable.
    assert project_onboarding.draft_view(
        draft["onboarding_id"], principal=principal
    )["repositories"] == []
