"""Read-only health projection and bounded native restart recovery."""

from __future__ import annotations

from hermes_cli import kanban_db
from plugins.pmo import doctor


pytest_plugins = ("tests.pmo_fixtures",)


def test_doctor_projects_health_without_mutating_native_history(pmo_project):
    with kanban_db.connect_closing(board=pmo_project.board_slug) as conn:
        before = sum(
            len(kanban_db.list_events(conn, task.id)) + len(kanban_db.list_comments(conn, task.id))
            for task in kanban_db.list_tasks(conn, include_archived=True)
        )

    report = doctor.build_report(pmo_project.scope, run_contract_checks=False)

    with kanban_db.connect_closing(board=pmo_project.board_slug) as conn:
        after = sum(
            len(kanban_db.list_events(conn, task.id)) + len(kanban_db.list_comments(conn, task.id))
            for task in kanban_db.list_tasks(conn, include_archived=True)
        )
    names = {check.name for check in report.checks}
    assert before == after
    assert {
        "binding",
        "scope",
        "profiles",
        "threads",
        "worktrees",
        "stale_claims",
        "retries",
        "budget",
        "reconciliation",
    } <= names


def test_restart_drain_is_bounded_and_uses_native_reclaim(pmo_project):
    task_ids: list[str] = []
    with kanban_db.connect_closing(board=pmo_project.board_slug) as conn:
        for index in range(2):
            task_id = kanban_db.create_task(
                conn,
                title=f"Stale worker {index}",
                assignee=f"{pmo_project.slug}-dev-1",
                board=pmo_project.board_slug,
                project_id=pmo_project.project_id,
            )
            claimed = kanban_db.claim_task(conn, task_id, ttl_seconds=1)
            assert claimed is not None
            task_ids.append(task_id)
            expires = max(expires if index else 0, int(claimed.claim_expires))

    projection = doctor.restart_drain(
        pmo_project.scope,
        limit=1,
        apply=False,
        now=expires + 1,
    )
    assert len(projection.stale_claims_seen) == 1
    assert projection.reclaimed == ()
    assert projection.truncated is True

    applied = doctor.restart_drain(
        pmo_project.scope,
        limit=1,
        apply=True,
        now=expires + 1,
    )
    assert len(applied.reclaimed) == 1
    with kanban_db.connect_closing(board=pmo_project.board_slug) as conn:
        states = {task_id: kanban_db.get_task(conn, task_id).status for task_id in task_ids}
    assert sorted(states.values()) == ["ready", "running"]


def test_doctor_contract_checks_are_reported(pmo_project, monkeypatch):
    monkeypatch.setattr(
        doctor,
        "_contract_check",
        lambda script: doctor.DoctorCheck(script, "ok", "passed"),
    )

    report = doctor.build_report(pmo_project.scope, run_contract_checks=True)

    contract_names = {item.name for item in report.checks if item.name.startswith("check_pmo_")}
    assert contract_names == {
        "check_pmo_capability_contract.py",
        "check_pmo_kanban_sync.py",
    }

