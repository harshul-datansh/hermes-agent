"""Plan 003 project-scope and per-project config contracts."""

from __future__ import annotations

import importlib
import importlib.util
import os
import sys
from pathlib import Path

import pytest
import yaml

from hermes_cli import kanban_db


REPO_ROOT = Path(__file__).resolve().parents[1]
PMO_ROOT = REPO_ROOT / "plugins" / "pmo"


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, PMO_ROOT / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def modules():
    # Bootstrap imports the namespace-package spelling, so use that same module
    # identity here; exception classes from two spec-loaded copies are distinct.
    scope_module = importlib.import_module("plugins.pmo.project_scope")
    return scope_module, _load(
        "pmo_bootstrap_scope_test", "bootstrap.py"
    )


@pytest.fixture()
def isolated_hermes_home(tmp_path, monkeypatch):
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    return hermes_home


def _scope_for(scope_module, root: Path):
    config = scope_module.ProjectConfig(
        version=1,
        project=scope_module.ProjectIdentity(name="Acme", slug="acme"),
        orchestrator=scope_module.OrchestratorConfig(profile="pm-acme"),
    )
    resolved = root.resolve(strict=True)
    return scope_module.ProjectScope(
        project_id="test-project",
        slug="acme",
        name="Acme",
        board_slug="acme-board",
        primary_path=resolved,
        folders=(resolved,),
        workspace=resolved,
        config=config,
    )


def test_path_allowed_rejects_parent_traversal(tmp_path, modules):
    scope_module, _ = modules
    root = tmp_path / "acme"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("private", encoding="utf-8")
    scope = _scope_for(scope_module, root)

    assert not scope_module.path_allowed(scope, "../outside.txt")
    with pytest.raises(scope_module.ScopeViolation):
        scope_module.assert_path(scope, "../outside.txt")


def test_path_allowed_rejects_symlink_escape(tmp_path, modules):
    scope_module, _ = modules
    root = tmp_path / "acme"
    outside = tmp_path / "elsewhere"
    root.mkdir()
    outside.mkdir()
    link = root / "escape"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlinks are unavailable for this account: {exc}")
    scope = _scope_for(scope_module, root)

    assert not scope_module.path_allowed(scope, link / "new-file.txt")


def test_path_allowed_rejects_sibling_prefix(tmp_path, modules):
    scope_module, _ = modules
    root = tmp_path / "acme"
    sibling = tmp_path / "acme-secrets"
    root.mkdir()
    sibling.mkdir()
    scope = _scope_for(scope_module, root)

    assert not scope_module.path_allowed(scope, sibling)


def test_path_allowed_accepts_new_file_in_scope(tmp_path, modules):
    scope_module, _ = modules
    root = tmp_path / "acme"
    root.mkdir()
    scope = _scope_for(scope_module, root)

    expected = (root / "new" / "file.txt").resolve(strict=False)
    assert scope_module.path_allowed(scope, "new/file.txt")
    assert scope_module.assert_path(scope, "new/file.txt") == expected


def test_path_allowed_enforces_deny_globs_after_resolution(tmp_path, modules):
    scope_module, _ = modules
    root = tmp_path / "acme"
    root.mkdir()
    config = scope_module.ProjectConfig(
        version=1,
        project=scope_module.ProjectIdentity(name="Acme", slug="acme"),
        orchestrator=scope_module.OrchestratorConfig(profile="pm-acme"),
        scope=scope_module.ScopeConfig(deny=(".env", "**/secrets/**")),
    )
    resolved = root.resolve(strict=True)
    scope = scope_module.ProjectScope(
        project_id="test-project",
        slug="acme",
        name="Acme",
        board_slug="acme",
        primary_path=resolved,
        folders=(resolved,),
        workspace=resolved,
        config=config,
    )

    assert not scope_module.path_allowed(scope, ".env")
    assert not scope_module.path_allowed(scope, "secrets/new.txt")
    assert not scope_module.path_allowed(scope, "nested/secrets/new.txt")
    assert scope_module.path_allowed(scope, "src/new.txt")


def test_path_allowed_rejects_broken_symlink_parent(tmp_path, modules):
    scope_module, _ = modules
    root = tmp_path / "acme"
    root.mkdir()
    link = root / "broken"
    try:
        link.symlink_to(tmp_path / "missing-target", target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlinks are unavailable for this account: {exc}")
    scope = _scope_for(scope_module, root)

    assert not scope_module.path_allowed(scope, link / "new-file.txt")


@pytest.mark.skipif(os.name != "nt", reason="Windows device path contract")
def test_path_allowed_windows_unc_device_path(tmp_path, modules):
    scope_module, _ = modules
    root = tmp_path / "acme"
    root.mkdir()
    scope = _scope_for(scope_module, root)
    device_path = Path("\\\\?\\") / root.resolve()

    # Device/UNC spelling must never bypass containment. Depending on the
    # Python build it either resolves to the same directory or is rejected.
    allowed = scope_module.path_allowed(scope, device_path)
    if allowed:
        assert scope_module.assert_path(scope, device_path) == root.resolve()


def test_invalid_project_config_raises_without_fallback(
    tmp_path, isolated_hermes_home, modules
):
    scope_module, bootstrap = modules
    workspace = tmp_path / "project"
    workspace.mkdir()
    result = bootstrap.bootstrap_project(
        slug="acme", name="Acme", workspace=workspace, board_slug="delivery"
    )
    result.config_path.write_text("version: nope\nproject: [", encoding="utf-8")

    with pytest.raises(scope_module.ProjectConfigError, match="invalid project config"):
        scope_module.resolve("acme", board_slug="delivery")


def test_no_user_config_layer(tmp_path, isolated_hermes_home, modules):
    scope_module, bootstrap = modules
    workspace = tmp_path / "project"
    workspace.mkdir()
    result = bootstrap.bootstrap_project(slug="acme", name="Acme", workspace=workspace)
    (isolated_hermes_home / "config.yaml").write_text(
        "project:\n  slug: another-project\nversion: 999\n",
        encoding="utf-8",
    )

    resolved = scope_module.resolve(result.project_id, board_slug="acme")

    assert resolved.config.project.slug == "acme"
    assert resolved.config.version == 1


def test_resolve_requires_explicit_matching_board_and_project_binding(
    tmp_path, isolated_hermes_home, modules
):
    scope_module, bootstrap = modules
    workspace = tmp_path / "project"
    workspace.mkdir()
    result = bootstrap.bootstrap_project(
        slug="acme", name="Acme", workspace=workspace, board_slug="delivery"
    )

    resolved = scope_module.resolve(
        result.project_id,
        board_slug="delivery",
        workspace=workspace,
    )
    assert resolved.project_id == result.project_id
    assert resolved.board_slug == "delivery"
    assert resolved.workspace == workspace.resolve()
    assert resolved.config.project.slug == "acme"

    with pytest.raises(scope_module.BoardBindingError):
        scope_module.resolve(result.project_id, board_slug="another-board")
    with pytest.raises(TypeError):
        scope_module.resolve(result.project_id)


def test_resolve_rejects_board_metadata_bound_to_another_project(
    tmp_path, isolated_hermes_home, modules
):
    scope_module, bootstrap = modules
    workspace = tmp_path / "project"
    workspace.mkdir()
    result = bootstrap.bootstrap_project(slug="acme", name="Acme", workspace=workspace)
    kanban_db.write_board_metadata("acme", project_id="different-project")

    with pytest.raises(scope_module.BoardBindingError, match="not bound"):
        scope_module.resolve(result.project_id, board_slug="acme")


def test_config_scope_cannot_expand_registered_project_folders(
    tmp_path, isolated_hermes_home, modules
):
    scope_module, bootstrap = modules
    workspace = tmp_path / "project"
    outside = tmp_path / "other-project"
    workspace.mkdir()
    outside.mkdir()
    result = bootstrap.bootstrap_project(slug="acme", name="Acme", workspace=workspace)
    document = yaml.safe_load(result.config_path.read_text(encoding="utf-8"))
    document["scope"]["folders"] = [".", "../other-project"]
    result.config_path.write_text(
        yaml.safe_dump(document, sort_keys=False), encoding="utf-8"
    )

    with pytest.raises(scope_module.ScopeViolation):
        scope_module.resolve(result.project_id, board_slug="acme")


def test_resolve_rejects_workspace_from_another_project(
    tmp_path, isolated_hermes_home, modules
):
    scope_module, bootstrap = modules
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    result = bootstrap.bootstrap_project(slug="first", name="First", workspace=first)
    bootstrap.bootstrap_project(slug="second", name="Second", workspace=second)

    with pytest.raises(scope_module.ScopeViolation):
        scope_module.resolve(result.project_id, board_slug="first", workspace=second)


def test_project_folders_cannot_overlap_between_projects(
    tmp_path, isolated_hermes_home, modules
):
    scope_module, bootstrap = modules
    parent = tmp_path / "portfolio"
    nested = parent / "nested-project"
    nested.mkdir(parents=True)
    first = bootstrap.bootstrap_project(
        slug="portfolio", name="Portfolio", workspace=parent
    )
    with pytest.raises(scope_module.ProjectConfigError, match="folder overlap"):
        bootstrap.bootstrap_project(
            slug="nested-project", name="Nested Project", workspace=nested
        )

    # The original project remains valid; registration of the unsafe nested
    # project is the operation that must fail closed.
    assert scope_module.resolve(first.project_id, board_slug="portfolio")


def test_project_manager_profile_cannot_be_shared_between_projects(
    tmp_path, isolated_hermes_home, modules
):
    scope_module, bootstrap = modules
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    bootstrap.bootstrap_project(slug="first", name="First", workspace=first)
    second_result = bootstrap.bootstrap_project(slug="second", name="Second", workspace=second)
    document = yaml.safe_load(second_result.config_path.read_text(encoding="utf-8"))
    document["orchestrator"]["profile"] = "pm-first"
    second_result.config_path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")

    with pytest.raises(scope_module.ProjectConfigError, match="already owned"):
        scope_module.resolve(second_result.project_id, board_slug="second")


def test_worker_profile_cannot_be_shared_between_projects(
    tmp_path, isolated_hermes_home, modules
):
    scope_module, bootstrap = modules
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    first_result = bootstrap.bootstrap_project(slug="first", name="First", workspace=first)
    second_result = bootstrap.bootstrap_project(slug="second", name="Second", workspace=second)
    for result in (first_result, second_result):
        document = yaml.safe_load(result.config_path.read_text(encoding="utf-8"))
        document["agents"] = [
            {"handle": "builder", "profile": "shared-builder", "role": "developer"}
        ]
        result.config_path.write_text(
            yaml.safe_dump(document, sort_keys=False), encoding="utf-8"
        )

    with pytest.raises(scope_module.ProjectConfigError, match="already owned"):
        scope_module.resolve(second_result.project_id, board_slug="second")


def test_project_config_rejects_duplicate_profiles_and_reserved_handles(modules):
    scope_module, _ = modules
    base = {
        "version": 1,
        "project": {"name": "Acme", "slug": "acme"},
        "orchestrator": {"profile": "pm-acme"},
    }

    with pytest.raises(ValueError, match="more than one project role"):
        scope_module.ProjectConfig.model_validate(
            {
                **base,
                "agents": [
                    {"handle": "lead", "profile": "pm-acme", "role": "developer"}
                ],
            }
        )
    with pytest.raises(ValueError, match="reserved"):
        scope_module.ProjectConfig.model_validate(
            {
                **base,
                "agents": [
                    {"handle": "pm", "profile": "worker-acme", "role": "developer"}
                ],
            }
        )


def test_dispatch_never_claims_task_assigned_to_another_projects_agent(
    tmp_path, isolated_hermes_home, modules
):
    scope_module, bootstrap = modules
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    first_result = bootstrap.bootstrap_project(slug="first", name="First", workspace=first)
    bootstrap.bootstrap_project(slug="second", name="Second", workspace=second)
    scope = scope_module.resolve(first_result.project_id, board_slug="first")
    with kanban_db.connect_closing(board=scope.board_slug) as conn:
        task_id = kanban_db.create_task(
            conn,
            title="Do not leak",
            assignee="pm-second",
            project_id=scope.project_id,
            workspace_kind="dir",
            workspace_path=str(scope.primary_path),
            board=scope.board_slug,
        )
        spawned: list[str] = []
        result = kanban_db.dispatch_once(
            conn,
            board=scope.board_slug,
            spawn_fn=lambda task, _workspace: spawned.append(task.id),
        )
        task = kanban_db.get_task(conn, task_id)

    assert spawned == []
    assert task is not None and task.status == "ready"
    assert task_id in result.skipped_nonspawnable


def test_bootstrap_writes_template_once_and_preserves_user_edits(
    tmp_path, isolated_hermes_home, modules
):
    _, bootstrap = modules
    workspace = tmp_path / "project"
    workspace.mkdir()

    first = bootstrap.bootstrap_project(slug="acme", name="Acme", workspace=workspace)
    document = yaml.safe_load(first.config_path.read_text(encoding="utf-8"))
    document["project"]["client"] = "Acme Corp"
    first.config_path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    second = bootstrap.bootstrap_project(slug="acme", name="Acme", workspace=workspace)

    assert first.created_config is True
    assert second.created_config is False
    preserved = yaml.safe_load(second.config_path.read_text(encoding="utf-8"))
    assert preserved["project"]["client"] == "Acme Corp"


def test_bootstrap_refuses_config_symlink_escape(
    tmp_path, isolated_hermes_home, modules
):
    scope_module, bootstrap = modules
    workspace = tmp_path / "project"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    datansh = workspace / ".datansh"
    try:
        datansh.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlinks are unavailable for this account: {exc}")

    with pytest.raises(scope_module.ScopeViolation):
        bootstrap.bootstrap_project(slug="acme", name="Acme", workspace=workspace)


def test_board_git_and_recovery_defaults_are_backward_compatible(modules):
    scope_module, _ = modules

    config = scope_module.ProjectConfig.model_validate(
        {
            "version": 1,
            "project": {"name": "Acme", "slug": "acme"},
            "orchestrator": {"profile": "pm-acme"},
            "agents": [],
            "board": {},
            "scope": {"folders": ["."], "deny": []},
        }
    )

    assert config.board.base_branch == "main"
    assert config.board.integration_branch is None
    assert config.board.auto_merge is False
    assert config.board.worktree_stale_days == 7
    assert config.board.restart_drain_limit == 25


def test_auto_merge_requires_separate_integration_branch(modules):
    scope_module, _ = modules

    with pytest.raises(ValueError, match="auto_merge requires"):
        scope_module.BoardConfig.model_validate({"auto_merge": True})
    with pytest.raises(ValueError, match="must differ"):
        scope_module.BoardConfig.model_validate(
            {"base_branch": "main", "integration_branch": "main"}
        )
