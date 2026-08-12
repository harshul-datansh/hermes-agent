"""Multi-repository contracts for one isolated PM-OS project."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hermes_cli import projects_db
from plugins.pmo import bootstrap, github_app, project_scope, repositories
from plugins.pmo.dashboard.plugin_api import router
from plugins.pmo.sandbox import terminal_sandbox_overrides


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _init_repo(path: Path, *, filename: str = "README.md") -> None:
    path.mkdir()
    result = subprocess.run(
        ["git", "init", "--initial-branch=main", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    (path / filename).write_text(f"# {path.name}\n", encoding="utf-8")
    _git(path, "add", filename)
    _git(
        path,
        "-c",
        "user.name=PMO Test",
        "-c",
        "user.email=pmo@example.invalid",
        "commit",
        "-m",
        "initial",
    )


@pytest.fixture()
def project(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.delenv("HERMES_KANBAN_HOME", raising=False)
    monkeypatch.delenv("HERMES_KANBAN_DB", raising=False)
    primary = tmp_path / "primary"
    _init_repo(primary)
    result = bootstrap.bootstrap_project(slug="acme", name="Acme", workspace=primary)
    return project_scope.resolve(result.project_id, board_slug=result.board_slug), tmp_path


def test_template_declares_one_primary_repository(project):
    scope, _ = project

    assert len(scope.config.repositories) == 1
    primary = scope.config.repositories[0]
    assert primary.name == "acme"
    assert primary.path == "."
    assert primary.primary is True
    assert primary.access == "write"


def test_connect_and_disconnect_secondary_repo_updates_both_stores(project):
    scope, root = project
    secondary = root / "service"
    _init_repo(secondary)

    connected = repositories.connect_repository(
        scope,
        name="service",
        path=secondary,
        url="https://github.com/example/service.git",
        default_branch="main",
        access="read",
    )
    refreshed = project_scope.resolve(scope.project_id, board_slug=scope.board_slug)

    assert connected.path == "../service"
    assert len(refreshed.folders) == 2
    assert {item.name for item in refreshed.config.repositories} == {"acme", "service"}
    status = {item.name: item for item in repositories.list_repository_status(refreshed)}
    assert status["service"].valid is True
    assert status["service"].head
    assert repositories.remove_repository(refreshed, "service") is True
    assert not secondary.exists(), "disconnect must delete the configured local checkout"
    final = project_scope.resolve(scope.project_id, board_slug=scope.board_slug)
    assert [item.name for item in final.config.repositories] == ["acme"]
    assert final.folders == (scope.primary_path,)


def test_promote_secondary_then_disconnect_former_primary_keeps_checkouts(project):
    scope, root = project
    secondary = root / "service"
    _init_repo(secondary)
    repositories.connect_repository(
        scope,
        name="service",
        path=secondary,
        url="https://github.com/example/service.git",
    )
    connected = project_scope.resolve(scope.project_id, board_slug=scope.board_slug)

    promoted = repositories.promote_repository(connected, "service")
    assert promoted.name == "service"
    assert promoted.primary is True

    after_promotion = project_scope.resolve(scope.project_id, board_slug=scope.board_slug)
    assert after_promotion.primary_path == secondary.resolve()
    rows = {item.name: item for item in after_promotion.config.repositories}
    assert rows["service"].primary is True
    assert rows["service"].path == "."
    assert rows["acme"].primary is False
    assert rows["acme"].path == "../primary"
    assert (secondary / ".datansh" / "project.yaml").is_file()

    assert repositories.remove_repository(after_promotion, "acme") is True
    final = project_scope.resolve(scope.project_id, board_slug=scope.board_slug)
    assert [item.name for item in final.config.repositories] == ["service"]
    assert final.primary_path == secondary.resolve()
    assert not (root / "primary").exists(), "disconnect must delete the former checkout"


def test_connection_rejects_repo_owned_by_another_active_project(project):
    first, root = project
    other_path = root / "other"
    _init_repo(other_path)
    bootstrap.bootstrap_project(slug="other", name="Other", workspace=other_path)

    with pytest.raises(project_scope.ProjectConfigError, match="folder overlap"):
        repositories.connect_repository(first, name="other", path=other_path)


@pytest.mark.parametrize(
    "url",
    [
        "https://token@github.com/example/private.git",
        "https://user:secret@github.com/example/private.git",
        "http://github.com/example/repo.git",
        "--upload-pack=malicious",
        "https://github.com/example/repo.git\n--config=x",
    ],
)
def test_remote_url_never_accepts_embedded_credentials_or_unsafe_values(url):
    with pytest.raises(repositories.RepositoryError):
        repositories.validate_remote_url(url)


def test_system_git_runner_preserves_normal_credential_environment(monkeypatch, tmp_path):
    marker = "system-helper-remains-visible"
    monkeypatch.setenv("GIT_ASKPASS", marker)
    captured = {}

    def fake_run(*args, **kwargs):
        captured.update(kwargs)
        return subprocess.CompletedProcess(args[0], 0, "", "")

    monkeypatch.setattr(repositories.subprocess, "run", fake_run)
    repositories._git(["version"], cwd=tmp_path)

    assert captured["env"]["GIT_ASKPASS"] == marker
    assert captured["env"]["GIT_TERMINAL_PROMPT"] == "0"
    assert captured["stdin"] is subprocess.DEVNULL


def test_read_only_repository_is_a_read_only_sandbox_mount(project, tmp_path):
    scope, root = project
    secondary = root / "reference"
    _init_repo(secondary)
    repositories.connect_repository(
        scope,
        name="reference",
        path=secondary,
        access="read",
    )
    refreshed = project_scope.resolve(scope.project_id, board_slug=scope.board_slug)
    board_dir = tmp_path / "board"
    board_dir.mkdir()
    (board_dir / "kanban.db").write_bytes(b"db")

    overrides = terminal_sandbox_overrides(refreshed, board_dir=board_dir)

    assert any(str(secondary.resolve()) in volume and volume.endswith(":ro") for volume in overrides["docker_volumes"])
    assert not overrides["docker_volumes"][0].endswith(":ro")


def test_api_lists_and_connects_repositories_for_selected_project(project):
    scope, root = project
    secondary = root / "api-service"
    _init_repo(secondary)
    app = FastAPI()
    app.state.auth_required = False
    app.include_router(router)
    client = TestClient(app)
    query = f"?board={scope.board_slug}"

    response = client.post(
        f"/projects/{scope.project_id}/repositories/connect{query}",
        json={
            "name": "api-service",
            "path": str(secondary),
            "url": "https://github.com/example/api-service.git",
            "default_branch": "main",
            "access": "write",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["valid"] is True
    listed = client.get(f"/projects/{scope.project_id}/repositories{query}")
    assert listed.status_code == 200
    assert {item["name"] for item in listed.json()["repositories"]} == {"acme", "api-service"}

    promoted = client.post(
        f"/projects/{scope.project_id}/repositories/api-service/primary{query}"
    )
    assert promoted.status_code == 200, promoted.text
    assert promoted.json()["name"] == "api-service"
    assert promoted.json()["primary"] is True

    disconnected = client.delete(f"/projects/{scope.project_id}/repositories/acme{query}")
    assert disconnected.status_code == 200, disconnected.text
    assert disconnected.json()["files_deleted"] is True


def test_project_github_install_poll_detects_consent_without_redirect(project, monkeypatch):
    scope, _root = project
    captured = {}

    def fake_poll_install(**kwargs):
        captured.update(kwargs)
        return {
            "status": "available",
            "project_id": scope.project_id,
            "installation": {"installation_id": 42},
            "repository_count": 3,
            "poll_after_seconds": 0,
        }

    monkeypatch.setattr(github_app, "poll_install", fake_poll_install)
    app = FastAPI()
    app.state.auth_required = False
    app.include_router(router)

    response = TestClient(app).post(
        f"/projects/{scope.project_id}/github-app/poll?board={scope.board_slug}",
        json={"state": "project-consent-state-value"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "available"
    assert captured["state"] == "project-consent-state-value"
    assert captured["expected_project_id"] == scope.project_id


def test_api_global_admin_can_idempotently_onboard_existing_primary(project):
    scope, _root = project
    app = FastAPI()
    app.state.auth_required = False
    app.include_router(router)
    response = TestClient(app).post(
        "/repositories/onboard",
        json={
            "slug": scope.slug,
            "name": scope.name,
            "path": str(scope.primary_path),
            "url": "https://github.com/example/acme.git",
            "default_branch": "main",
            "board_slug": scope.board_slug,
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["project_id"] == scope.project_id
    refreshed = project_scope.resolve(scope.project_id, board_slug=scope.board_slug)
    assert refreshed.config.repositories[0].url == "https://github.com/example/acme.git"


def test_config_rejects_unregistered_or_duplicate_repository_metadata(project):
    scope, root = project
    config_path = root / "primary" / ".datansh" / "project.yaml"
    document = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    document["repositories"].append(
        {
            "name": "ghost",
            "path": "../ghost",
            "url": None,
            "default_branch": "main",
            "access": "write",
            "primary": False,
        }
    )
    config_path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")

    with pytest.raises(project_scope.ProjectConfigError, match="unavailable"):
        project_scope.resolve(scope.project_id, board_slug=scope.board_slug)


def test_projects_db_contains_no_repository_credentials(project):
    scope, root = project
    secondary = root / "worker"
    _init_repo(secondary)
    repositories.connect_repository(
        scope,
        name="worker",
        path=secondary,
        url="git@github.com:example/worker.git",
    )

    with projects_db.connect_closing() as conn:
        stored = projects_db.get_project(conn, scope.project_id)
    assert stored is not None
    assert all("github" not in folder.path for folder in stored.folders)


def test_onboard_existing_checkout_records_primary_remote_without_touching_head(project):
    _scope, root = project
    checkout = root / "new-product"
    _init_repo(checkout)
    before = _git(checkout, "rev-parse", "HEAD")

    result = repositories.onboard_project(
        slug="new-product",
        name="New Product",
        path=checkout,
        url="https://github.com/example/new-product.git",
        default_branch="main",
    )

    assert result.cloned is False
    assert _git(checkout, "rev-parse", "HEAD") == before
    scope = project_scope.resolve(result.project_id, board_slug=result.board_slug)
    assert scope.config.repositories[0].url == "https://github.com/example/new-product.git"
    assert scope.config.repositories[0].primary is True
