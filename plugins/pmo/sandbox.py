"""Per-project terminal sandbox registration for PM-OS conversations.

The normal Hermes terminal remains the only terminal tool.  This module uses
its generic per-task backend overrides to select a distinct Docker environment
whose only project bind mounts are the validated folder set and this project's
native Kanban board.
"""

from __future__ import annotations

import hashlib
import threading
import time
from pathlib import Path
from typing import Any

import yaml

from hermes_cli import projects_db
from hermes_constants import get_hermes_home


_PROVISION_LOCK = threading.RLock()


def _sandbox_key(scope: Any) -> str:
    roots = "\0".join(str(path.resolve()) for path in scope.folders)
    digest = hashlib.sha256(
        f"{scope.project_id}\0{scope.board_slug}\0{roots}".encode("utf-8")
    ).hexdigest()[:16]
    return f"pmo-{digest}"


def _sandbox_home(scope: Any) -> Path:
    # The active Hermes home is the dedicated PM profile.  One PM profile may
    # own only one project, so this support state cannot be shared by another
    # project even when several profiles run in one gateway process.
    return Path(get_hermes_home()) / "pmo-sandbox" / _sandbox_key(scope)


def _write_sandbox_config(scope: Any, home: Path) -> None:
    model = (
        str(scope.config.models.pm or "").strip()
        or str(scope.config.orchestrator.model or "").strip()
        or "gpt-5.6-luna"
    )
    payload = yaml.safe_dump(
        {"model": {"default": model}},
        sort_keys=False,
        allow_unicode=True,
    )
    path = home / "config.yaml"
    if not path.exists() or path.read_text(encoding="utf-8") != payload:
        path.write_text(payload, encoding="utf-8")


def _write_filtered_project_db(scope: Any, home: Path) -> None:
    db_path = home / "projects.db"
    now = int(time.time())
    with projects_db.connect_closing(db_path=db_path) as conn:
        with conn:
            conn.execute("DELETE FROM project_folders")
            conn.execute("DELETE FROM projects")
            conn.execute("DELETE FROM discovered_repos")
            conn.execute("DELETE FROM project_meta")
            conn.execute(
                "INSERT INTO projects "
                "(id, slug, name, description, icon, color, board_slug, "
                "primary_path, created_at, archived) "
                "VALUES (?, ?, ?, ?, NULL, NULL, ?, ?, ?, 0)",
                (
                    scope.project_id,
                    scope.slug,
                    scope.name,
                    "PM-OS project sandbox",
                    scope.board_slug,
                    "/workspace",
                    now,
                ),
            )
            for index, _folder in enumerate(scope.folders):
                container_path = "/workspace" if index == 0 else f"/workspaces/{index}"
                conn.execute(
                    "INSERT INTO project_folders "
                    "(project_id, path, label, is_primary, added_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        scope.project_id,
                        container_path,
                        "Primary" if index == 0 else f"Folder {index + 1}",
                        1 if index == 0 else 0,
                        now,
                    ),
                )


def _volume(source: Path, target: str, *, read_only: bool = False) -> str:
    resolved = source.resolve(strict=True)
    suffix = ":ro" if read_only else ""
    return f"{resolved}:{target}{suffix}"


def terminal_sandbox_overrides(
    scope: Any, *, board_dir: Path, read_only: bool = False
) -> dict[str, Any]:
    """Build a fail-closed Docker override for one validated project scope."""

    board_root = board_dir.resolve(strict=True)
    expected_db = board_root / "kanban.db"
    if not expected_db.is_file():
        raise RuntimeError(f"project board database is missing: {expected_db}")

    home = _sandbox_home(scope)
    with _PROVISION_LOCK:
        home.mkdir(parents=True, exist_ok=True)
        _write_sandbox_config(scope, home)
        _write_filtered_project_db(scope, home)

    from plugins.pmo.repositories import repository_for_path

    primary_repository = repository_for_path(scope, scope.primary_path)
    volumes = [
        _volume(
            scope.primary_path,
            "/workspace",
            read_only=read_only
            or bool(primary_repository and primary_repository.access == "read"),
        ),
        _volume(home, "/opt/data"),
        _volume(
            board_root,
            f"/opt/data/kanban/boards/{scope.board_slug}",
            read_only=read_only,
        ),
    ]
    for index, folder in enumerate(scope.folders[1:], start=1):
        repository = repository_for_path(scope, folder)
        volumes.append(
            _volume(
                folder,
                f"/workspaces/{index}",
                read_only=read_only
                or bool(repository and repository.access == "read"),
            )
        )

    workdir_mappings = [
        (str(scope.primary_path.resolve()), "/workspace"),
        *(
            (str(folder.resolve()), f"/workspaces/{index}")
            for index, folder in enumerate(scope.folders[1:], start=1)
        ),
    ]

    container_board_db = f"/opt/data/kanban/boards/{scope.board_slug}/kanban.db"
    return {
        "env_type": "docker",
        "sandbox_key": _sandbox_key(scope) + ("-readonly" if read_only else ""),
        "docker_image": str(scope.config.scope.terminal_image).strip(),
        "cwd": "/workspace",
        "workdir_mappings": workdir_mappings,
        "host_cwd": None,
        "docker_mount_cwd_to_workspace": False,
        "docker_volumes": volumes,
        "docker_forward_env": [],
        "docker_env": {
            "HERMES_HOME": "/opt/data",
            "HERMES_KANBAN_HOME": "/opt/data",
            "HERMES_KANBAN_DB": container_board_db,
            "HOME": "/opt/data",
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        "docker_extra_args": [],
        "docker_run_as_host_user": False,
        "docker_network": True,
        "docker_strict_mounts": True,
        "container_persistent": False,
        "docker_persist_across_processes": False,
    }


def register_terminal_sandbox(
    scope: Any, *, board_dir: Path, environment_id: str, read_only: bool = False
) -> dict[str, Any]:
    """Register and return the normal terminal's project-specific override."""

    from tools.terminal_tool import register_task_env_overrides

    overrides = terminal_sandbox_overrides(
        scope, board_dir=board_dir, read_only=read_only
    )
    register_task_env_overrides(environment_id, overrides)
    return overrides


__all__ = ["register_terminal_sandbox", "terminal_sandbox_overrides"]
