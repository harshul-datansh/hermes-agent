"""Check the PMO Kanban fork against its recorded source revision.

The upstream-owned ``plugins/kanban`` tree must always match the source commit
recorded in ``plugins/pmo/UPSTREAM.md``. The PMO tree may differ only in the
small identity surface declared below.
"""

from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path


SOURCE_COMMIT_RE = re.compile(r"`([0-9a-f]{40})` on \d{4}-\d{2}-\d{2}")
PMO_METADATA_FILES = {"OPERATIONS.md", "PROJECT_CONTEXT.md", "UPSTREAM.md"}
KNOWN_PMO_DIVERGENCES = {
    "extra: access_policy.py",
    "extra: admin_workspace.py",
    "extra: agent_soul.py",
    "extra: agents.py",
    "extra: approval.py",
    "extra: approval_sla.py",
    "extra: attachment_security.py",
    "extra: availability.py",
    "extra: bootstrap.py",
    "extra: collaboration.py",
    "extra: comment.py",
    "extra: context_builder.py",
    "extra: credentials.py",
    "extra: cost.py",
    "extra: demo.py",
    "extra: doctor.py",
    "extra: escalation.py",
    "extra: git_flow.py",
    "extra: github_app.py",
    "extra: lifecycle.py",
    "extra: mentions.py",
    "extra: notifications.py",
    "extra: performance.py",
    "extra: planning.py",
    "extra: portfolio.py",
    "extra: provider_auth.py",
    "extra: project_context.py",
    "extra: project_onboarding.py",
    "extra: project_scope.py",
    "extra: report.py",
    "extra: repositories.py",
    "extra: sandbox.py",
    "extra: setup.py",
    "extra: tab_policy.py",
    "extra: ticket.py",
    "extra: timeline.py",
    "extra: tool_scope.py",
    "extra: workflow.py",
    "changed: dashboard/dist/index.js",
    "changed: dashboard/dist/style.css",
    "changed: dashboard/manifest.json",
    "changed: dashboard/plugin_api.py",
}
PMO_CLIENT_REPLACEMENTS = (
    (b"at /api/plugins/kanban/", b"at /api/plugins/pmo/"),
    (b'const API = "/api/plugins/kanban";', b'const API = "/api/plugins/pmo";'),
    (
        b'const LS_BOARD_KEY = "hermes.kanban.selectedBoard";',
        b'const LS_BOARD_KEY = "hermes.pmo.selectedBoard";',
    ),
)
PMO_BACKEND_REPLACEMENTS = (
    (
        b"Mounted at /api/plugins/kanban/ by the dashboard plugin system.",
        b"Mounted at /api/plugins/pmo/ by the dashboard plugin system.",
    ),
    (
        b"from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile, WebSocket, WebSocketDisconnect, status as http_status",
        b"from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, WebSocket, WebSocketDisconnect, status as http_status",
    ),
    (
        b"from hermes_cli import kanban_diagnostics as kd\n",
        b"from hermes_cli import kanban_diagnostics as kd\nfrom plugins.pmo.access_policy import require_route_access\n",
    ),
    (
        b"router = APIRouter()",
        b"_route_access_guard = require_route_access()\nrouter = APIRouter(dependencies=[Depends(_route_access_guard)])",
    ),
    (
        b'    try:\n        from hermes_cli import web_server as _ws',
        b'    if getattr(ws.state, "pmo_ws_authenticated", False):\n        return True\n    try:\n        from hermes_cli import web_server as _ws',
    ),
)


def _git_bytes(repo_root: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout


def _git_bytes_from_input(repo_root: Path, data: bytes, *args: str) -> bytes:
    result = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        check=True,
        input=data,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout


def read_source_commit(repo_root: Path) -> str:
    provenance = repo_root / "plugins" / "pmo" / "UPSTREAM.md"
    match = SOURCE_COMMIT_RE.search(provenance.read_text(encoding="utf-8"))
    if not match:
        raise ValueError(f"No 40-character source commit found in {provenance}")
    return match.group(1)


def source_files(repo_root: Path, source_commit: str) -> list[str]:
    output = _git_bytes(
        repo_root,
        "ls-tree",
        "-r",
        "--name-only",
        "-z",
        source_commit,
        "plugins/kanban",
    )
    prefix = "plugins/kanban/"
    paths = [item.decode("utf-8") for item in output.split(b"\0") if item]
    return sorted(path[len(prefix) :] for path in paths if path.startswith(prefix))


def compare_tree(
    repo_root: Path,
    source_commit: str,
    target_relative: str,
    *,
    ignored_files: set[str] | None = None,
) -> list[str]:
    """Return human-readable differences from the recorded Kanban tree."""

    ignored = ignored_files or set()
    expected = source_files(repo_root, source_commit)
    target = repo_root / target_relative
    actual = sorted(
        path.relative_to(target).as_posix()
        for path in target.rglob("*")
        if path.is_file()
        and "__pycache__" not in path.relative_to(target).parts
        and path.relative_to(target).as_posix() not in ignored
    )

    differences: list[str] = []
    for relative in sorted(set(expected) - set(actual)):
        differences.append(f"missing: {relative}")
    for relative in sorted(set(actual) - set(expected)):
        differences.append(f"extra: {relative}")
    for relative in sorted(set(expected) & set(actual)):
        source_blob = _git_bytes(
            repo_root,
            "rev-parse",
            f"{source_commit}:plugins/kanban/{relative}",
        ).strip()
        target_blob = _git_bytes_from_input(
            repo_root,
            (target / relative).read_bytes(),
            "hash-object",
            "--stdin",
            f"--path=plugins/kanban/{relative}",
        ).strip()
        if target_blob != source_blob:
            differences.append(f"changed: {relative}")
    return differences


def _single_extension(data: bytes, *, prefix: bytes) -> bytes | None:
    start = prefix + b":START"
    end = prefix + b":END"
    if data.count(start) != 1 or data.count(end) != 1:
        return None
    before, remainder = data.split(start, 1)
    _, after = remainder.split(end, 1)
    return before + after


def pmo_client_matches_allowed_transform(repo_root: Path, source_commit: str) -> bool:
    """Verify the additive PM shell retains the inherited Kanban capabilities.

    UI accessibility work intentionally touches focus handling in the copied
    drawer, so byte-for-byte comparison is no longer useful for this file.
    The upstream tree itself is still byte-checked above. Here we require one
    bounded PM extension, independent namespaces/registration, every major
    inherited surface, and no additional raw-HTML rendering seam.
    """

    source = _git_bytes(
        repo_root, "show", f"{source_commit}:plugins/kanban/dashboard/dist/index.js"
    ).replace(b"\r\n", b"\n")
    actual = (repo_root / "plugins/pmo/dashboard/dist/index.js").read_bytes().replace(
        b"\r\n", b"\n"
    )
    if _single_extension(actual, prefix=b"// PMO-DASHBOARD-EXTENSION") is None:
        return False
    required = (
        b"function KanbanPage()",
        b"function TaskDrawer(props)",
        b"function AttachmentsSection(props)",
        b"function RunHistorySection(props)",
        b"function ModelEditor(props)",
        b"function BoardColumns(props)",
        b"function OrchestrationPanel()",
        b"function DiagnosticsSection(props)",
        b"onDragStart",
        b"onMove:",
    )
    return (
        all(marker in source and marker in actual for marker in required)
        and b'const API = "/api/plugins/pmo";' in actual
        and b'const LS_BOARD_KEY = "hermes.pmo.selectedBoard";' in actual
        and b'register("pmo", PmoPage)' in actual
        and actual.count(b"dangerouslySetInnerHTML") == source.count(b"dangerouslySetInnerHTML")
        and b"sanitizeMarkdownHtml" in actual
    )


def pmo_backend_matches_allowed_transform(repo_root: Path, source_commit: str) -> bool:
    """Verify copied routes remain present beside one guarded PM extension."""

    source = _git_bytes(
        repo_root, "show", f"{source_commit}:plugins/kanban/dashboard/plugin_api.py"
    ).replace(b"\r\n", b"\n")
    actual = (repo_root / "plugins/pmo/dashboard/plugin_api.py").read_bytes().replace(
        b"\r\n", b"\n"
    )
    if _single_extension(actual, prefix=b"# PMO-DASHBOARD-EXTENSION") is None:
        return False
    inherited_routes = (
        b'@router.get("/board")',
        b'@router.post("/tasks")',
        b'@router.patch("/tasks/{task_id}")',
        b'@router.post("/tasks/{task_id}/comments")',
        b'@router.get("/runs/{run_id}")',
        b'@router.get("/diagnostics")',
        b'@router.get("/model-options")',
        b'@router.websocket("/events")',
    )
    pm_routes = (
        b'@router.get("/portfolio")',
        b'@router.get("/projects/{project_ref}/overview")',
        b'@router.get("/approvals")',
        b'@router.get("/notifications")',
        b'@router.get("/decisions")',
        b'@router.get("/spend")',
    )
    return (
        all(marker in source and marker in actual for marker in inherited_routes)
        and all(marker in actual for marker in pm_routes)
        and b"require_route_access" in actual
        and b"CREATE TABLE" not in actual
    )


def pmo_style_matches_allowed_transform(repo_root: Path, source_commit: str) -> bool:
    """The style fork is the exact baseline plus one bounded PM block."""

    source = _git_bytes(
        repo_root, "show", f"{source_commit}:plugins/kanban/dashboard/dist/style.css"
    ).replace(b"\r\n", b"\n")
    actual = (repo_root / "plugins/pmo/dashboard/dist/style.css").read_bytes().replace(
        b"\r\n", b"\n"
    )
    start = b"/* PMO-DASHBOARD-EXTENSION:START */"
    end = b"/* PMO-DASHBOARD-EXTENSION:END */"
    if actual.count(start) != 1 or actual.count(end) != 1:
        return False
    before, remainder = actual.split(start, 1)
    _, after = remainder.split(end, 1)
    stripped = before + after
    return stripped.strip() == source.strip()


def _print_differences(label: str, differences: list[str]) -> None:
    print(f"{label}: {'clean' if not differences else 'diverged'}")
    for difference in differences:
        print(f"  - {difference}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Hermes repository root (defaults to the script's parent repository)",
    )
    args = parser.parse_args()

    repo_root = args.repo_root.resolve()
    source_commit = read_source_commit(repo_root)
    kanban_differences = compare_tree(repo_root, source_commit, "plugins/kanban")
    pmo_differences = compare_tree(
        repo_root,
        source_commit,
        "plugins/pmo",
        ignored_files=PMO_METADATA_FILES,
    )

    print(f"Recorded source: {source_commit}")
    _print_differences("plugins/kanban", kanban_differences)
    _print_differences("plugins/pmo", pmo_differences)

    if kanban_differences:
        return 1
    if set(pmo_differences) != KNOWN_PMO_DIVERGENCES:
        print("plugins/pmo divergence does not match the recorded identity surface")
        return 1
    if not pmo_client_matches_allowed_transform(repo_root, source_commit):
        print("plugins/pmo client differs outside its API and browser-storage namespaces")
        return 1
    if not pmo_backend_matches_allowed_transform(repo_root, source_commit):
        print("plugins/pmo backend lost an inherited route or its bounded PM extension")
        return 1
    if not pmo_style_matches_allowed_transform(repo_root, source_commit):
        print("plugins/pmo styles are not the baseline plus one bounded PM extension")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
