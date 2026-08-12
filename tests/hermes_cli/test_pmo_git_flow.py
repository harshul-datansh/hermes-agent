"""PM integration policy over native project worktrees and review cards."""

from __future__ import annotations

from dataclasses import replace

import pytest

from hermes_cli import kanban_db
from plugins.pmo import git_flow


pytest_plugins = ("tests.pmo_fixtures",)


def _completed_source(project) -> str:
    with kanban_db.connect_closing(board=project.board_slug) as conn:
        task_id = kanban_db.create_task(
            conn,
            title="Implement API",
            assignee=f"{project.slug}-dev-1",
            created_by=f"pm-{project.slug}",
            board=project.board_slug,
            project_id=project.project_id,
        )
        assert kanban_db.complete_task(conn, task_id, summary="API implemented")
        stored = kanban_db.get_task(conn, task_id)
    assert stored is not None
    assert stored.workspace_kind == "worktree"
    assert stored.workspace_path == str(project.workspace / ".worktrees" / task_id)
    assert stored.branch_name.startswith(f"{project.slug}/{task_id}")
    return task_id


def test_review_reuses_native_links_worktree_and_role_model(pmo_project):
    source_id = _completed_source(pmo_project)

    request = git_flow.request_review(
        scope=pmo_project.scope,
        source_task_id=source_id,
        raised_by=f"pm-{pmo_project.slug}",
    )

    with kanban_db.connect_closing(board=pmo_project.board_slug) as conn:
        review = kanban_db.get_task(conn, request.review_task_id)
        parents = kanban_db.parent_ids(conn, request.review_task_id)
    assert review is not None
    assert review.assignee == f"{pmo_project.slug}-qa"
    assert review.workspace_kind == "worktree"
    assert review.workspace_path == str(pmo_project.workspace / ".worktrees" / review.id)
    assert source_id in parents
    assert "sdlc-review" in review.skills
    assert request.auto_merge is False


def test_default_approval_records_human_merge_without_running_git(pmo_project):
    source_id = _completed_source(pmo_project)
    request = git_flow.request_review(
        scope=pmo_project.scope,
        source_task_id=source_id,
        raised_by=f"pm-{pmo_project.slug}",
    )

    def no_git(*args, **kwargs):  # pragma: no cover - fails immediately if called
        raise AssertionError("human-main approval must not run git")

    result = git_flow.approve_review(
        scope=pmo_project.scope,
        review_task_id=request.review_task_id,
        actor=f"{pmo_project.slug}-qa",
        runner=no_git,
    )

    assert result.outcome == "human_merge_required"
    with kanban_db.connect_closing(board=pmo_project.board_slug) as conn:
        review = kanban_db.get_task(conn, request.review_task_id)
        comments = kanban_db.list_comments(conn, source_id)
    assert review is not None and review.status == "done"
    assert any("Human merge to main is required" in comment.body for comment in comments)


def test_production_conflict_blocks_and_routes_pm_without_resolution(pmo_project):
    source_id = _completed_source(pmo_project)
    request = git_flow.request_review(
        scope=pmo_project.scope,
        source_task_id=source_id,
        raised_by=f"pm-{pmo_project.slug}",
    )
    scope = replace(
        pmo_project.scope,
        config=pmo_project.scope.config.model_copy(
            update={
                "board": pmo_project.scope.config.board.model_copy(
                    update={"auto_merge": True, "integration_branch": "integration"}
                )
            }
        ),
    )
    calls: list[tuple[str, ...]] = []

    class Result:
        def __init__(self, code=0, stdout="", stderr=""):
            self.returncode = code
            self.stdout = stdout
            self.stderr = stderr

    def fake_git(argv, **kwargs):
        args = tuple(argv[1:])
        calls.append(args)
        if args[:2] == ("rev-parse", "--verify"):
            if args[2] == "integration":
                return Result(1, stderr="unknown revision")
            return Result(stdout="a" * 40 + "\n")
        if args[0] == "merge-tree":
            return Result(
                1,
                stdout="CONFLICT (content): Merge conflict in src/public_api.py\n",
            )
        raise AssertionError(f"unexpected mutating git call: {args}")

    result = git_flow.approve_review(
        scope=scope,
        review_task_id=request.review_task_id,
        actor=f"{pmo_project.slug}-qa",
        runner=fake_git,
    )

    assert result.outcome == "blocked_conflict"
    assert not any(args[0] in {"commit-tree", "update-ref", "push"} for args in calls)
    with kanban_db.connect_closing(board=pmo_project.board_slug) as conn:
        review = kanban_db.get_task(conn, request.review_task_id)
        comments = kanban_db.list_comments(conn, request.review_task_id)
    assert review is not None and review.status == "blocked"
    assert any("@pm" in comment.body and "No automatic resolution" in comment.body for comment in comments)


def test_gc_dry_run_preserves_reclaimed_run_evidence(pmo_project):
    with kanban_db.connect_closing(board=pmo_project.board_slug) as conn:
        task_id = kanban_db.create_task(
            conn,
            title="Crashed work",
            assignee=f"{pmo_project.slug}-dev-1",
            board=pmo_project.board_slug,
            project_id=pmo_project.project_id,
        )
        assert kanban_db.claim_task(conn, task_id, ttl_seconds=1) is not None
        assert kanban_db.reclaim_task(conn, task_id, reason="simulated crash")
        assert kanban_db.archive_task(conn, task_id)
        task = kanban_db.get_task(conn, task_id)

    candidates = git_flow.gc_worktrees(
        scope=pmo_project.scope,
        now=int(task.created_at) + 8 * 86_400,
        dry_run=True,
        delete_merged=True,
    )

    candidate = next(item for item in candidates if item.task_id == task_id)
    assert candidate.crashed_evidence is True
    assert candidate.action == "preserve-crash-evidence"
