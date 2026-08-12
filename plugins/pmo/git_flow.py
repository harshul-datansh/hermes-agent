"""Git review/integration policy around Hermes' native project worktrees.

Hermes remains responsible for worktree creation, deterministic paths, branches,
claims, and review workers. PM-OS adds the human-main policy, advisory overlap
hints, conflict routing, and an opt-in integration-ref update. It never attempts
to resolve a conflict automatically.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

from agent.redact import redact_sensitive_text
from hermes_cli import kanban_db

from . import cost, project_scope


REVIEW_MARKER = "[pmo:git-review:v1]"
CONFLICT_MARKER = "[pmo:git-conflict:v1]"
GC_MARKER = "[pmo:worktree-stale:v1]"
HEX_OID = re.compile(r"^[0-9a-f]{40,64}$")


@dataclass(frozen=True)
class ConflictCheck:
    target_branch: str
    source_branch: str
    conflicted: bool
    files: tuple[str, ...]
    merge_tree: str | None
    detail: str


@dataclass(frozen=True)
class ReviewRequest:
    source_task_id: str
    review_task_id: str
    reviewer_profile: str
    source_branch: str
    auto_merge: bool
    integration_branch: str | None


@dataclass(frozen=True)
class IntegrationResult:
    review_task_id: str
    source_task_id: str
    outcome: str
    target_branch: str
    commit: str | None = None
    conflict_files: tuple[str, ...] = ()


@dataclass(frozen=True)
class GcCandidate:
    task_id: str
    path: str
    branch: str | None
    reason: str
    crashed_evidence: bool
    action: str


Runner = Callable[..., Any]


def _run_git(
    repo: Path,
    args: Sequence[str],
    *,
    runner: Runner = subprocess.run,
    env_overrides: dict[str, str] | None = None,
) -> Any:
    kwargs: dict[str, Any] = {
        "cwd": str(repo),
        "capture_output": True,
        "text": True,
        "check": False,
    }
    if env_overrides:
        environment = os.environ.copy()
        environment.update(env_overrides)
        environment["GIT_TERMINAL_PROMPT"] = "0"
        kwargs["env"] = environment
    return runner(["git", *args], **kwargs)


def _require_ok(result: Any, operation: str) -> str:
    if int(getattr(result, "returncode", 1)) != 0:
        detail = str(getattr(result, "stderr", "") or getattr(result, "stdout", "")).strip()
        raise RuntimeError(f"{operation} failed: {detail or 'git returned an error'}")
    return str(getattr(result, "stdout", "") or "").strip()


def _project_task(conn, scope: project_scope.ProjectScope, task_id: str):
    task = kanban_db.get_task(conn, str(task_id).strip())
    if task is None or task.project_id != scope.project_id:
        raise ValueError(f"task '{task_id}' is not in project '{scope.slug}'")
    return task


def _reviewer(scope: project_scope.ProjectScope) -> str:
    for agent in scope.config.agents:
        if "review" in agent.role.casefold() or agent.handle.casefold() in {"qa", "reviewer"}:
            return agent.profile
    raise ValueError("project roster has no reviewer profile")


def request_review(
    *,
    scope: project_scope.ProjectScope,
    source_task_id: str,
    raised_by: str,
) -> ReviewRequest:
    """Create one native reviewer card linked after a completed worker card."""

    with kanban_db.connect_closing(board=scope.board_slug) as conn:
        source = _project_task(conn, scope, source_task_id)
        if source.status != "done":
            raise ValueError(f"source task must be done before review; got {source.status}")
        if source.workspace_kind != "worktree" or not source.branch_name:
            raise ValueError("source task has no native worktree branch to review")
        reviewer = _reviewer(scope)
        body = "\n".join(
            [
                REVIEW_MARKER,
                f"Source task: {source.id}",
                f"Source branch: {source.branch_name}",
                f"Base branch: {scope.config.board.base_branch}",
                f"Integration branch: {scope.config.board.integration_branch or ''}",
                f"Auto merge: {str(scope.config.board.auto_merge).lower()}",
                "Review the diff and acceptance evidence. Never resolve merge conflicts automatically.",
            ]
        )
        review_id = kanban_db.create_task(
            conn,
            title=f"Review: {source.title}",
            body=body,
            assignee=reviewer,
            created_by=str(raised_by).strip() or scope.config.orchestrator.profile,
            parents=(source.id,),
            idempotency_key=f"pmo-review:{scope.project_id}:{source.id}",
            max_runtime_seconds=source.max_runtime_seconds or 3_600,
            max_retries=source.max_retries or 3,
            skills=("sdlc-review",),
            model_override=cost.model_route_for_assignee(scope, reviewer),
            board=scope.board_slug,
            project_id=scope.project_id,
        )
    return ReviewRequest(
        source.id,
        review_id,
        reviewer,
        source.branch_name,
        scope.config.board.auto_merge,
        scope.config.board.integration_branch,
    )


def detect_conflicts(
    *,
    scope: project_scope.ProjectScope,
    source_task_id: str,
    target_branch: str | None = None,
    runner: Runner = subprocess.run,
) -> ConflictCheck:
    """Use ``git merge-tree`` without touching any working tree."""

    with kanban_db.connect_closing(board=scope.board_slug) as conn:
        source = _project_task(conn, scope, source_task_id)
    if not source.branch_name:
        raise ValueError(f"task '{source.id}' has no branch")
    target = target_branch or scope.config.board.integration_branch or scope.config.board.base_branch
    repo = scope.primary_path
    source_oid = _require_ok(
        _run_git(repo, ["rev-parse", "--verify", source.branch_name], runner=runner),
        "resolve source branch",
    ).splitlines()[0]
    target_result = _run_git(repo, ["rev-parse", "--verify", target], runner=runner)
    if int(getattr(target_result, "returncode", 1)) != 0 and target == scope.config.board.integration_branch:
        target = scope.config.board.base_branch
        target_result = _run_git(repo, ["rev-parse", "--verify", target], runner=runner)
    target_oid = _require_ok(target_result, "resolve target branch").splitlines()[0]
    result = _run_git(
        repo,
        ["merge-tree", "--write-tree", "--messages", target_oid, source_oid],
        runner=runner,
    )
    output = str(getattr(result, "stdout", "") or "")
    error = str(getattr(result, "stderr", "") or "")
    conflicted = int(getattr(result, "returncode", 1)) != 0
    files = tuple(
        sorted(
            {
                match.group(1).strip()
                for match in re.finditer(r"CONFLICT \([^)]*\):.*? in (.+)$", output + "\n" + error, re.MULTILINE)
            }
        )
    )
    tree = output.splitlines()[0].strip() if output.splitlines() else ""
    if not HEX_OID.fullmatch(tree):
        tree = ""
    return ConflictCheck(
        target_branch=target_branch or scope.config.board.integration_branch or scope.config.board.base_branch,
        source_branch=source.branch_name,
        conflicted=conflicted,
        files=files,
        merge_tree=tree or None,
        detail=redact_sensitive_text((error or output).strip()[:1000], force=True),
    )


def _block_conflict(
    conn,
    *,
    review_task_id: str,
    source_branch: str,
    check: ConflictCheck,
) -> None:
    files = ", ".join(check.files) or "files reported by git"
    reason = f"merge conflict with {check.target_branch} from {source_branch} in {files}"
    kanban_db.add_comment(
        conn,
        review_task_id,
        "pmo-git",
        f"{CONFLICT_MARKER}\n@pm {reason}\nNo automatic resolution was attempted.",
    )
    task = kanban_db.get_task(conn, review_task_id)
    kanban_db.block_task(
        conn,
        review_task_id,
        reason=reason,
        kind="needs_input",
        expected_run_id=task.current_run_id if task else None,
    )


def approve_review(
    *,
    scope: project_scope.ProjectScope,
    review_task_id: str,
    actor: str,
    runner: Runner = subprocess.run,
) -> IntegrationResult:
    """Approve review; only an opt-in integration branch can be auto-merged."""

    with kanban_db.connect_closing(board=scope.board_slug) as conn:
        review = _project_task(conn, scope, review_task_id)
        fields = {}
        for line in str(review.body or "").splitlines()[1:]:
            key, sep, value = line.partition(":")
            if sep:
                fields[key.strip().casefold().replace(" ", "_")] = value.strip()
        source_id = fields.get("source_task", "")
        source = _project_task(conn, scope, source_id)
        if not str(review.body or "").startswith(REVIEW_MARKER):
            raise ValueError(f"task '{review.id}' is not a PMO git review")

    if not scope.config.board.auto_merge:
        with kanban_db.connect_closing(board=scope.board_slug) as conn:
            kanban_db.add_comment(
                conn,
                source.id,
                str(actor).strip() or "reviewer",
                f"{REVIEW_MARKER}\nApproved. Human merge to {scope.config.board.base_branch} is required.",
            )
            kanban_db.complete_task(
                conn,
                review.id,
                summary="Review approved; main remains human-owned",
                metadata={"source_task_id": source.id, "merge_policy": "human-main"},
                expected_run_id=review.current_run_id,
            )
        return IntegrationResult(review.id, source.id, "human_merge_required", scope.config.board.base_branch)

    target = scope.config.board.integration_branch
    if not target or target == scope.config.board.base_branch:
        raise ValueError("automatic merge may target only a separate integration branch")
    check = detect_conflicts(
        scope=scope,
        source_task_id=source.id,
        target_branch=target,
        runner=runner,
    )
    if check.conflicted or not check.merge_tree:
        with kanban_db.connect_closing(board=scope.board_slug) as conn:
            _block_conflict(
                conn,
                review_task_id=review.id,
                source_branch=source.branch_name or "unknown",
                check=check,
            )
        return IntegrationResult(review.id, source.id, "blocked_conflict", target, conflict_files=check.files)

    repo = scope.primary_path
    target_result = _run_git(repo, ["rev-parse", "--verify", target], runner=runner)
    if int(getattr(target_result, "returncode", 1)) != 0:
        base_oid = _require_ok(
            _run_git(repo, ["rev-parse", "--verify", scope.config.board.base_branch], runner=runner),
            "resolve base branch",
        ).splitlines()[0]
        _require_ok(
            _run_git(repo, ["update-ref", f"refs/heads/{target}", base_oid], runner=runner),
            "initialize integration branch",
        )
        target_oid = base_oid
    else:
        target_oid = _require_ok(target_result, "resolve integration branch").splitlines()[0]
    source_oid = _require_ok(
        _run_git(repo, ["rev-parse", "--verify", source.branch_name], runner=runner),
        "resolve source branch",
    ).splitlines()[0]
    merge_commit = _require_ok(
        _run_git(
            repo,
            [
                "commit-tree",
                check.merge_tree,
                "-p",
                target_oid,
                "-p",
                source_oid,
                "-m",
                f"PMO integration of {source.id}",
            ],
            runner=runner,
        ),
        "create integration commit",
    ).splitlines()[0]
    if not HEX_OID.fullmatch(merge_commit):
        raise RuntimeError("git commit-tree returned an invalid commit id")
    _require_ok(
        _run_git(
            repo,
            ["update-ref", f"refs/heads/{target}", merge_commit, target_oid],
            runner=runner,
        ),
        "atomically update integration branch",
    )
    if scope.config.board.push_remote:
        from plugins.pmo.repositories import git_environment_for_repository

        git_environment = git_environment_for_repository(
            scope, repo, access="write"
        )
        _require_ok(
            _run_git(
                repo,
                ["push", "origin", f"{target}:{target}"],
                runner=runner,
                env_overrides=dict(git_environment or {}),
            ),
            "push integration branch",
        )
    with kanban_db.connect_closing(board=scope.board_slug) as conn:
        kanban_db.add_comment(
            conn,
            source.id,
            str(actor).strip() or "reviewer",
            f"{REVIEW_MARKER}\nIntegrated as {merge_commit} on {target}; main remains human-owned.",
        )
        kanban_db.complete_task(
            conn,
            review.id,
            summary=f"Review approved and integrated to {target}",
            metadata={"source_task_id": source.id, "integration_commit": merge_commit},
            expected_run_id=review.current_run_id,
        )
    return IntegrationResult(review.id, source.id, "integrated", target, commit=merge_commit)


def gc_worktrees(
    *,
    scope: project_scope.ProjectScope,
    dry_run: bool = True,
    delete_merged: bool = False,
    now: int | None = None,
    runner: Runner = subprocess.run,
) -> tuple[GcCandidate, ...]:
    """Report stale native worktrees; preserve crash evidence by default."""

    cutoff = int(now or time.time()) - scope.config.board.worktree_stale_days * 86_400
    candidates: list[GcCandidate] = []
    with kanban_db.connect_closing(board=scope.board_slug) as conn:
        for task in kanban_db.list_tasks(conn, include_archived=True, order_by="created"):
            if task.project_id != scope.project_id or task.workspace_kind != "worktree" or not task.workspace_path:
                continue
            runs = kanban_db.list_runs(conn, task.id, include_active=False)
            crashed = any(run.outcome in {"crashed", "timed_out", "spawn_failed", "gave_up", "reclaimed"} for run in runs)
            last = max([task.completed_at or task.created_at, *(run.ended_at or run.started_at for run in runs)])
            if task.status not in {"done", "archived", "blocked"} or last > cutoff:
                continue
            path = Path(task.workspace_path).resolve(strict=False)
            expected_root = (scope.primary_path / ".worktrees").resolve(strict=False)
            if path.parent != expected_root:
                candidates.append(GcCandidate(task.id, str(path), task.branch_name, "non-native worktree path", crashed, "preserve"))
                continue
            action = "preserve-crash-evidence" if crashed else ("remove" if delete_merged and not dry_run else "dry-run")
            if action == "remove":
                result = _run_git(scope.primary_path, ["worktree", "remove", str(path)], runner=runner)
                if int(getattr(result, "returncode", 1)) != 0:
                    action = "stale-remove-failed"
            candidates.append(
                GcCandidate(
                    task.id,
                    str(path),
                    task.branch_name,
                    "stale terminal worktree",
                    crashed,
                    action,
                )
            )
            marker = f"{GC_MARKER}\nPath: {path}\nAction: {action}"
            if not dry_run and not any(GC_MARKER in comment.body for comment in kanban_db.list_comments(conn, task.id)):
                kanban_db.add_comment(conn, task.id, "pmo-gc", marker)
    return tuple(candidates)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--board", required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    review = sub.add_parser("review")
    review.add_argument("task_id")
    review.add_argument("--actor", required=True)
    approve = sub.add_parser("approve")
    approve.add_argument("task_id")
    approve.add_argument("--actor", required=True)
    gc = sub.add_parser("gc")
    gc.add_argument("--apply", action="store_true")
    gc.add_argument("--delete-merged", action="store_true")
    args = parser.parse_args(argv)
    try:
        scope = project_scope.resolve(args.project, board_slug=args.board)
        if args.command == "review":
            payload = asdict(request_review(scope=scope, source_task_id=args.task_id, raised_by=args.actor))
        elif args.command == "approve":
            payload = asdict(approve_review(scope=scope, review_task_id=args.task_id, actor=args.actor))
        else:
            payload = [asdict(item) for item in gc_worktrees(scope=scope, dry_run=not args.apply, delete_merged=args.delete_merged)]
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"pmo git: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
