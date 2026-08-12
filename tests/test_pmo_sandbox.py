"""Behavior tests for PM-OS per-project terminal sandbox registration."""

from __future__ import annotations

from pathlib import Path

from hermes_cli import kanban_db, projects_db
from plugins.pmo import bootstrap, project_scope
from plugins.pmo.sandbox import terminal_sandbox_overrides


def _scope(tmp_path, monkeypatch, slug: str):
    home = tmp_path / ".hermes"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.delenv("HERMES_KANBAN_HOME", raising=False)
    monkeypatch.delenv("HERMES_KANBAN_DB", raising=False)
    workspace = tmp_path / slug
    workspace.mkdir()
    result = bootstrap.bootstrap_project(
        slug=slug,
        name=slug.replace("-", " ").title(),
        workspace=workspace,
    )
    return project_scope.resolve(result.project_id, board_slug=result.board_slug)


def test_project_sandbox_mounts_only_its_project_and_board(tmp_path, monkeypatch):
    scope = _scope(tmp_path, monkeypatch, "alpha")
    board_dir = kanban_db.board_dir(scope.board_slug).resolve()

    overrides = terminal_sandbox_overrides(scope, board_dir=board_dir)

    assert overrides["env_type"] == "docker"
    assert overrides["docker_strict_mounts"] is True
    assert overrides["cwd"] == "/workspace"
    assert overrides["docker_extra_args"] == []
    assert overrides["docker_forward_env"] == []
    sources = [item.rsplit(":/", 1)[0] for item in overrides["docker_volumes"]]
    assert str(scope.primary_path.resolve()) in sources
    assert str(board_dir) in sources
    assert all("alpha" in source or "pmo-sandbox" in source for source in sources)

    sandbox_home = next(
        item.rsplit(":/", 1)[0]
        for item in overrides["docker_volumes"]
        if item.endswith(":/opt/data")
    )
    with projects_db.connect_closing(db_path=Path(sandbox_home) / "projects.db") as conn:
        rows = conn.execute(
            "SELECT id, slug, board_slug, primary_path FROM projects"
        ).fetchall()
        folders = conn.execute(
            "SELECT project_id, path, is_primary FROM project_folders"
        ).fetchall()
    assert [tuple(row) for row in rows] == [
        (scope.project_id, "alpha", scope.board_slug, "/workspace")
    ]
    assert [tuple(row) for row in folders] == [(scope.project_id, "/workspace", 1)]


def test_projects_receive_distinct_sandbox_keys(tmp_path, monkeypatch):
    first = _scope(tmp_path, monkeypatch, "alpha")
    first_overrides = terminal_sandbox_overrides(
        first, board_dir=kanban_db.board_dir(first.board_slug).resolve()
    )
    second = _scope(tmp_path, monkeypatch, "beta")
    second_overrides = terminal_sandbox_overrides(
        second, board_dir=kanban_db.board_dir(second.board_slug).resolve()
    )

    assert first_overrides["sandbox_key"] != second_overrides["sandbox_key"]
    assert not any(
        str(first.primary_path) in volume
        for volume in second_overrides["docker_volumes"]
    )
