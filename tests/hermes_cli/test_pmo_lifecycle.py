from __future__ import annotations

import json
import time
import zipfile

import pytest

from hermes_cli import kanban_db
from plugins.pmo import lifecycle


pytest_plugins = ("tests.pmo_fixtures",)


def test_export_contains_native_records_and_project_context(pmo_project, tmp_path):
    with kanban_db.connect_closing(board=pmo_project.board_slug) as conn:
        task_id = kanban_db.create_task(
            conn,
            title="Export me",
            body="portable data",
            board=pmo_project.board_slug,
            project_id=pmo_project.project_id,
        )
        kanban_db.add_comment(conn, task_id, "tester", "audit comment")
    out = tmp_path / "handover.zip"
    result = lifecycle.export_project(pmo_project.slug, out=out)
    assert result.path == out.resolve()
    with zipfile.ZipFile(out) as archive:
        assert {"manifest.json", "tasks.json", "project-state/project.yaml", "project-state/access.yaml"} <= set(
            archive.namelist()
        )
        tasks = json.loads(archive.read("tasks.json"))
    exported = next(task for task in tasks if task["id"] == task_id)
    assert exported["comments"][0]["body"] == "audit comment"


def test_delete_requires_exact_typed_slug_and_does_not_export(pmo_project, tmp_path):
    out = tmp_path / "must-not-exist.zip"
    with pytest.raises(ValueError, match="exactly match"):
        lifecycle.delete_project(
            slug=pmo_project.slug,
            confirm=pmo_project.slug.upper(),
            out=out,
            actor="tester",
        )
    assert not out.exists()
    assert kanban_db.board_exists(pmo_project.board_slug)


def test_delete_exports_before_removing_and_leaves_repo_untouched(pmo_project, tmp_path):
    sentinel = pmo_project.workspace / "keep-me.txt"
    sentinel.write_text("repository data", encoding="utf-8")
    project_config = pmo_project.workspace / ".datansh" / "project.yaml"
    out = tmp_path / "delete-safety.zip"
    observed = []

    def notice(path):
        observed.append((path.is_file(), kanban_db.board_exists(pmo_project.board_slug)))

    result = lifecycle.delete_project(
        slug=pmo_project.slug,
        confirm=pmo_project.slug,
        out=out,
        actor="human:owner@example.test",
        export_notice=notice,
    )
    assert observed == [(True, True)]
    assert result.board_action == "deleted"
    assert not kanban_db.board_exists(pmo_project.board_slug)
    assert sentinel.read_text(encoding="utf-8") == "repository data"
    assert project_config.is_file()
    records = [json.loads(line) for line in result.tombstone_path.read_text(encoding="utf-8").splitlines()]
    assert records[-1]["kind"] == "project_deleted"
    assert records[-1]["slug"] == pmo_project.slug


def test_retention_job_batches_and_logs(pmo_project, fake_clock):
    with kanban_db.connect_closing(board=pmo_project.board_slug) as conn:
        task_ids = []
        for index in range(3):
            task_id = kanban_db.create_task(
                conn,
                title=f"Old archived {index}",
                board=pmo_project.board_slug,
                project_id=pmo_project.project_id,
                triage=True,
            )
            kanban_db.archive_task(conn, task_id)
            task_ids.append(task_id)
    fake_clock.advance(hours=24 * 200)
    result = lifecycle.enforce_retention(
        pmo_project.slug,
        archived_days=180,
        batch_size=2,
        now=int(fake_clock.time()),
    )
    assert result.examined == 2
    assert result.deleted == 2
    assert result.audit_path.is_file()
    with kanban_db.connect_closing(board=pmo_project.board_slug) as conn:
        remaining = [task_id for task_id in task_ids if kanban_db.get_task(conn, task_id)]
    assert len(remaining) == 1


def test_lifecycle_cli_actions_registered():
    from plugins.platforms.pmo.cli import COMMAND_MODULES

    assert COMMAND_MODULES["export"] == "plugins.pmo.lifecycle"
    assert COMMAND_MODULES["retention"] == "plugins.pmo.lifecycle"
    assert COMMAND_MODULES["project"] == "plugins.pmo.lifecycle"
