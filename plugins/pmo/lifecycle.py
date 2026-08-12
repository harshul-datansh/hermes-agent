"""Export, retain, and delete PM-OS project state through native stores."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

from agent.redact import redact_sensitive_text
from hermes_constants import get_hermes_home
from hermes_cli import kanban_db, projects_db
from plugins.pmo import project_scope


EXPORT_SCHEMA = "datansh-pmo-export.v1"
AUDIT_SCHEMA = "datansh-pmo-lifecycle-audit.v1"


@dataclass(frozen=True)
class ExportResult:
    project_id: str
    slug: str
    board_slug: str
    path: Path
    task_count: int
    attachment_count: int


@dataclass(frozen=True)
class DeleteResult:
    slug: str
    export_path: Path
    board_action: str
    tombstone_path: Path


@dataclass(frozen=True)
class RetentionResult:
    project_id: str
    slug: str
    examined: int
    deleted: int
    attachment_count: int
    audit_path: Path


def _scope(project_ref: str, board_slug: str | None = None):
    selected_board = board_slug
    if selected_board is None:
        with projects_db.connect_closing() as conn:
            project = projects_db.get_project(conn, project_ref)
        if project is None or not project.board_slug:
            raise ValueError(f"project has no explicit board binding: {project_ref}")
        selected_board = project.board_slug
    return project_scope.resolve(project_ref, board_slug=selected_board)


def _json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, indent=2, sort_keys=True, default=str
    ) + "\n"


def _audit_path() -> Path:
    root = get_hermes_home().resolve() / "pmo-audit"
    if root.is_symlink():
        raise ValueError(f"refusing symlinked PMO audit directory: {root}")
    root.mkdir(mode=0o750, parents=True, exist_ok=True)
    if root.is_symlink():
        raise ValueError(f"refusing symlinked PMO audit directory: {root}")
    return root / "lifecycle.jsonl"


def _audit(event: dict[str, Any]) -> Path:
    path = _audit_path()
    if path.is_symlink():
        raise ValueError(f"refusing symlinked PMO audit file: {path}")
    payload = dict(event)
    payload["schema"] = AUDIT_SCHEMA
    payload["recorded_at"] = int(time.time())
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return path


def _safe_attachment(att: kanban_db.Attachment, root: Path) -> Path | None:
    try:
        stored = Path(att.stored_path)
        if stored.is_symlink():
            return None
        resolved = stored.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError):
        return None
    return resolved if resolved.is_file() else None


def export_project(
    project_ref: str,
    *,
    out: str | Path,
    board_slug: str | None = None,
) -> ExportResult:
    """Write a full portable archive without mutating project or board state."""

    scope = _scope(project_ref, board_slug)
    destination = Path(out).expanduser().resolve()
    if destination.exists() and destination.is_symlink():
        raise ValueError(f"refusing symlinked export destination: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)

    with projects_db.connect_closing() as project_conn:
        project = projects_db.get_project(project_conn, scope.project_id)
    if project is None:
        raise ValueError(f"project does not exist: {project_ref}")

    root = kanban_db.attachments_root(board=scope.board_slug).resolve()
    tasks_payload: list[dict[str, Any]] = []
    attachment_files: list[tuple[kanban_db.Attachment, Path]] = []
    with kanban_db.connect_closing(board=scope.board_slug) as conn:
        tasks = kanban_db.list_tasks(conn, include_archived=True, order_by="created")
        for task in tasks:
            attachments = kanban_db.list_attachments(conn, task.id)
            task_data = asdict(task)
            task_data["comments"] = [asdict(item) for item in kanban_db.list_comments(conn, task.id)]
            task_data["events"] = [asdict(item) for item in kanban_db.list_events(conn, task.id)]
            task_data["runs"] = [asdict(item) for item in kanban_db.list_runs(conn, task_id=task.id)]
            task_data["attachments"] = [asdict(item) for item in attachments]
            tasks_payload.append(task_data)
            for item in attachments:
                safe = _safe_attachment(item, root)
                if safe is not None:
                    attachment_files.append((item, safe))

    manifest = {
        "schema": EXPORT_SCHEMA,
        "project": asdict(project),
        "board": kanban_db.read_board_metadata(scope.board_slug),
        "workspace": str(scope.primary_path),
        "task_count": len(tasks_payload),
        "attachment_count": len(attachment_files),
    }
    state_files = (
        "project.yaml",
        "access.yaml",
        "decisions.jsonl",
        "knowledge.jsonl",
        "availability.jsonl",
        "context.md",
    )
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", _json(manifest))
            archive.writestr("tasks.json", _json(tasks_payload))
            for filename in state_files:
                source = scope.primary_path / ".datansh" / filename
                if source.is_file() and not source.is_symlink():
                    archive.write(source, f"project-state/{filename}")
            for attachment, source in attachment_files:
                archive.write(
                    source,
                    f"attachments/{attachment.id}/{Path(attachment.filename).name}",
                )
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return ExportResult(
        project_id=scope.project_id,
        slug=scope.slug,
        board_slug=scope.board_slug,
        path=destination,
        task_count=len(tasks_payload),
        attachment_count=len(attachment_files),
    )


def delete_project(
    *,
    slug: str,
    confirm: str,
    out: str | Path,
    actor: str,
    export_notice: Callable[[Path], None] | None = None,
) -> DeleteResult:
    """Export first, then remove native PM state while preserving the repo."""

    normalized = projects_db.normalize_slug(slug)
    if str(confirm).strip() != normalized or str(slug).strip() != normalized:
        raise ValueError("typed confirmation must exactly match the normalized project slug")
    scope = _scope(normalized)
    workspace = scope.primary_path.resolve(strict=True)
    state_dir = workspace / ".datansh"
    exported = export_project(normalized, out=out, board_slug=scope.board_slug)
    if export_notice is not None:
        export_notice(exported.path)

    clean_actor = redact_sensitive_text(str(actor or "local-operator").strip(), force=True)
    tombstone = _audit(
        {
            "kind": "project_delete_started",
            "actor": clean_actor,
            "project_id": scope.project_id,
            "slug": normalized,
            "board_slug": scope.board_slug,
            "export_path": str(exported.path),
        }
    )
    board_result = kanban_db.remove_board(scope.board_slug, archive=False)
    with projects_db.connect_closing() as conn:
        if not projects_db.delete_project(conn, scope.project_id):
            raise RuntimeError(f"could not delete native project record '{normalized}'")
    if not workspace.is_dir():
        raise RuntimeError("project repository changed during deletion")
    if state_dir.exists() and not state_dir.is_dir():
        raise RuntimeError("project .datansh path changed during deletion")
    _audit(
        {
            "kind": "project_deleted",
            "actor": clean_actor,
            "project_id": scope.project_id,
            "slug": normalized,
            "board_slug": scope.board_slug,
            "export_path": str(exported.path),
        }
    )
    return DeleteResult(normalized, exported.path, str(board_result["action"]), tombstone)


def enforce_retention(
    project_ref: str,
    *,
    archived_days: int = 180,
    batch_size: int = 100,
    now: int | None = None,
    dry_run: bool = False,
) -> RetentionResult:
    """Delete old archived native cards in a bounded, auditable batch."""

    if archived_days < 1:
        raise ValueError("archived-days must be at least 1")
    if not 1 <= batch_size <= 1000:
        raise ValueError("batch-size must be between 1 and 1000")
    scope = _scope(project_ref)
    cutoff = int(now if now is not None else time.time()) - archived_days * 86400
    deleted = 0
    attachment_count = 0
    examined = 0
    with kanban_db.connect_closing(board=scope.board_slug) as conn:
        candidates = [
            task
            for task in kanban_db.list_tasks(
                conn, status="archived", include_archived=True, order_by="created"
            )
            if task.created_at < cutoff
        ][:batch_size]
        examined = len(candidates)
        if not dry_run:
            for task in candidates:
                attachments = kanban_db.list_attachments(conn, task.id)
                for attachment in attachments:
                    if kanban_db.delete_attachment(conn, attachment.id) is not None:
                        attachment_count += 1
                if kanban_db.delete_archived_task(conn, task.id):
                    deleted += 1
    audit_path = _audit(
        {
            "kind": "retention_batch",
            "project_id": scope.project_id,
            "slug": scope.slug,
            "cutoff": cutoff,
            "examined": examined,
            "deleted": deleted,
            "attachments_deleted": attachment_count,
            "dry_run": bool(dry_run),
        }
    )
    return RetentionResult(
        scope.project_id, scope.slug, examined, deleted, attachment_count, audit_path
    )


def main_for_action(action: str, argv: Sequence[str] | None = None) -> int:
    argv = list(argv or ())
    if action == "project":
        parser = argparse.ArgumentParser(description="Delete PMO project state safely")
        sub = parser.add_subparsers(dest="command", required=True)
        delete = sub.add_parser("delete")
        delete.add_argument("--slug", required=True)
        delete.add_argument("--confirm", required=True)
        delete.add_argument("--out", required=True, type=Path)
        delete.add_argument("--actor", default="local-operator")
        args = parser.parse_args(argv)
        try:
            result = delete_project(
                slug=args.slug,
                confirm=args.confirm,
                out=args.out,
                actor=args.actor,
                export_notice=lambda path: print(f"Export ready: {path}"),
            )
        except (OSError, RuntimeError, ValueError) as exc:
            print(f"pmo project: {exc}", file=sys.stderr)
            return 2
        print(f"Deleted project '{result.slug}'; tombstone: {result.tombstone_path}")
        return 0
    if action == "retention":
        parser = argparse.ArgumentParser(description="Run one bounded PMO retention batch")
        parser.add_argument("--project", required=True)
        parser.add_argument("--archived-days", type=int, default=180)
        parser.add_argument("--batch-size", type=int, default=100)
        parser.add_argument("--dry-run", action="store_true")
        args = parser.parse_args(argv)
        try:
            result = enforce_retention(
                args.project,
                archived_days=args.archived_days,
                batch_size=args.batch_size,
                dry_run=args.dry_run,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            print(f"pmo retention: {exc}", file=sys.stderr)
            return 2
        print(_json(asdict(result)), end="")
        return 0

    parser = argparse.ArgumentParser(description="Export all PMO-held project data")
    parser.add_argument("--project", required=True)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = export_project(args.project, out=args.out)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"pmo export: {exc}", file=sys.stderr)
        return 2
    print(_json(asdict(result)), end="")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return main_for_action("export", argv)


if __name__ == "__main__":
    raise SystemExit(main())
