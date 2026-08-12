"""Project-aware guards for normal Hermes file and terminal tool calls.

PM-OS retains Hermes' normal tools, skills, memory, learning, and delegation.
This hook only binds path-bearing calls made inside a PMO conversation to the
conversation's explicit project folder set.  It reuses the standard plugin
``pre_tool_call`` veto seam instead of replacing core tools or creating a
parallel tool runtime.

The terminal guard validates its explicit ``workdir`` and rejects lexical
absolute paths or parent traversal that leave the project.  The session itself
is also pinned to the project workspace by the PMO adapter.  As documented in
the project-scope plan, this is a best-effort host-shell guard rather than a
filesystem jail; container isolation remains the boundary for adversarial shell
commands.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from contextvars import ContextVar, Token
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hermes_cli import kanban_db
from hermes_cli.profiles import normalize_profile_name
from plugins.pmo.project_scope import (
    ScopeViolation,
    assert_path,
    resolve,
    task_agent_profiles,
)


_PATCH_PATH = re.compile(
    r"^(?P<prefix>\*\*\*\s*(?:Add|Update|Delete)\s+File:\s*)"
    r"(?P<path>.+?)\s*$",
    re.MULTILINE,
)
_PATCH_MOVE = re.compile(
    r"^(?P<prefix>\*\*\*\s*Move\s+File:\s*)"
    r"(?P<source>.+?)\s*->\s*(?P<destination>.+?)\s*$",
    re.MULTILINE,
)
_SHELL_TOKEN = re.compile(r'''"[^"\r\n]*"|'[^'\r\n]*'|[^\s|;&<>]+''')
_WINDOWS_ABSOLUTE = re.compile(r"(?:[A-Za-z]:[\\/]|\\\\)")
_URI_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")
_POSIX_SANDBOX_ROOTS = frozenset({
    "bin", "dev", "etc", "home", "lib", "lib64", "opt", "proc", "root",
    "run", "sbin", "sys", "tmp", "usr", "var", "workspace", "workspaces",
})
@dataclass(frozen=True)
class _BoundScope:
    scope: Any
    board_dir: Path | None = None


_ACTIVE_SCOPE: ContextVar[_BoundScope | None] = ContextVar(
    "DATANSH_PMO_ACTIVE_SCOPE", default=None
)


def bind_active_scope(scope: Any, *, board_dir: str | Path | None = None) -> Token:
    """Bind an adapter-validated project scope to the current agent turn."""

    return _ACTIVE_SCOPE.set(
        _BoundScope(scope=scope, board_dir=Path(board_dir) if board_dir else None)
    )


def reset_active_scope(token: Token) -> None:
    _ACTIVE_SCOPE.reset(token)


def _active_pmo_scope():
    from gateway.session_context import get_session_env
    from plugins.platforms.pmo.adapter import resolve_conversation

    if get_session_env("HERMES_SESSION_PLATFORM") != "pmo":
        return None, None
    try:
        board = get_session_env("HERMES_SESSION_CHAT_ID").strip()
        thread_id = get_session_env("HERMES_SESSION_THREAD_ID").strip()
        if not board or not thread_id:
            return None, "PM-OS project identity is incomplete for this session"
        trusted = _ACTIVE_SCOPE.get()
        if trusted is not None:
            trusted_scope = trusted.scope
            if getattr(trusted_scope, "board_slug", "") != board:
                return None, "PM-OS project scope does not match this conversation"
            return trusted_scope, None
        conversation = resolve_conversation(board_slug=board, thread_id=thread_id)
        return resolve(conversation.project_id, board_slug=board), None
    except Exception as exc:  # noqa: BLE001 - policy hooks must fail closed
        return None, f"PM-OS could not validate this project's scope: {exc}"


def _is_pm_session(scope: Any) -> bool:
    """Whether the active turn is the project's orchestrator profile."""

    from gateway.session_context import get_session_env

    active = normalize_profile_name(
        get_session_env("HERMES_SESSION_PROFILE", "") or "default"
    )
    expected = normalize_profile_name(scope.config.orchestrator.profile)
    return active == expected


def _normalize_shell_path(value: str) -> str | None:
    """Return a path-bearing shell token in host form, if it has one.

    This deliberately recognizes only syntax that can name a filesystem path;
    ordinary switches such as ``cmd /c`` and network URLs are not paths.  It is
    not a shell parser and does not claim to make the host shell a sandbox.
    """

    token = value.strip().strip('"\'`').strip("()[]{} ,")
    if not token or _URI_SCHEME.match(token):
        return None
    # Handle assignments and function-like PowerShell tokens without trying to
    # evaluate the shell language.  The first absolute marker is the relevant
    # portion for containment.
    match = _WINDOWS_ABSOLUTE.search(token)
    if match:
        return token[match.start():].rstrip("),]")
    lower = token.casefold()
    if lower.startswith("/mnt/") and len(token) > 7 and token[5].isalpha() and token[6] == "/":
        return f"{token[5].upper()}:{token[6:]}"
    if (
        len(token) > 3
        and token[0] == "/"
        and token[1].isalpha()
        and token[2] == "/"
        and token[1:].split("/", 1)[0].casefold() not in _POSIX_SANDBOX_ROOTS
    ):
        return f"{token[1].upper()}:{token[2:]}"
    # A leading slash with more path structure is a POSIX absolute path.  A
    # one-segment value such as `/c` is commonly a Windows CLI switch.
    if token.startswith("/") and (token == "/" or "/" in token[1:]):
        return token.rstrip("),]")
    if token == "~" or token.startswith(("~/", "~\\")):
        return token
    if token in {"$HOME", "${HOME}", "%USERPROFILE%", "%HOMEDRIVE%"}:
        return "~"
    segments = re.split(r"[\\/]", token)
    if ".." in segments:
        return token.rstrip("),]")
    return None


def _terminal_paths(command: str) -> tuple[str, ...]:
    paths: list[str] = []
    for match in _SHELL_TOKEN.finditer(command):
        candidate = _normalize_shell_path(match.group(0))
        if candidate and candidate not in paths:
            paths.append(candidate)
    return tuple(paths)


def _paths_for_call(tool_name: str, args: Mapping[str, Any]) -> tuple[str, ...]:
    if tool_name in {"read_file", "write_file", "search_files"}:
        value = args.get("path", "." if tool_name == "search_files" else "")
        return (str(value),) if value else ()
    if tool_name == "patch":
        if str(args.get("mode") or "replace") == "patch":
            patch_text = str(args.get("patch") or "")
            paths = [match.group("path") for match in _PATCH_PATH.finditer(patch_text)]
            for match in _PATCH_MOVE.finditer(patch_text):
                paths.extend((match.group("source"), match.group("destination")))
            return tuple(paths)
        value = args.get("path")
        return (str(value),) if value else ()
    if tool_name == "terminal":
        value = args.get("workdir")
        command_paths = _terminal_paths(str(args.get("command") or ""))
        return ((str(value),) if value else ()) + command_paths
    return ()


def _container_path_for_host(scope: Any, path: Path) -> str:
    """Map one validated project path to its private sandbox mount."""

    resolved = path.resolve(strict=False)
    for index, root in enumerate(scope.folders):
        if resolved == root or resolved.is_relative_to(root):
            mount = "/workspace" if index == 0 else f"/workspaces/{index}"
            relative = resolved.relative_to(root).as_posix()
            return mount if relative == "." else f"{mount}/{relative}"
    raise ScopeViolation(f"path is outside project '{scope.slug}': {resolved}")


def _project_tool_path(scope: Any, path: str, *, container_paths: bool) -> str:
    validated = assert_path(scope, path)
    if container_paths:
        return _container_path_for_host(scope, validated)
    return str(validated)


def _rewrite_patch_paths(
    scope: Any, patch_text: str, *, container_paths: bool = False
) -> str:
    """Resolve every V4A file header against the project's primary folder."""

    def replace(match: re.Match[str]) -> str:
        return match.group("prefix") + _project_tool_path(
            scope, match.group("path"), container_paths=container_paths
        )

    def replace_move(match: re.Match[str]) -> str:
        source = _project_tool_path(
            scope, match.group("source"), container_paths=container_paths
        )
        destination = _project_tool_path(
            scope, match.group("destination"), container_paths=container_paths
        )
        return f"{match.group('prefix')}{source} -> {destination}"

    return _PATCH_MOVE.sub(replace_move, _PATCH_PATH.sub(replace, patch_text))


def tool_request_scope_middleware(
    *,
    tool_name: str,
    args: Mapping[str, Any] | None = None,
    task_id: str = "",
    session_id: str = "",
    **_kwargs: Any,
) -> dict[str, Any] | None:
    """Make PMO file-tool paths absolute before Hermes executes the tool.

    Hermes file tools normally resolve relative paths from the process/task
    working directory. A PMO conversation instead resolves them from its
    validated project root. The pre-tool hook independently requires these
    rewritten absolute paths, so a missing or failed middleware pass is denied
    rather than falling back to the host working directory.
    """

    if tool_name not in {"read_file", "write_file", "patch", "search_files"}:
        return None
    scope, scope_error = _active_pmo_scope()
    if scope_error or scope is None:
        return None

    rewritten = dict(args or {})
    try:
        container_paths = scope.config.scope.terminal_isolation == "docker"
        if container_paths:
            environment_id = str(task_id or session_id).strip()
            trusted = _ACTIVE_SCOPE.get()
            if not environment_id or trusted is None or trusted.board_dir is None:
                return None
            from plugins.pmo.sandbox import register_terminal_sandbox

            register_terminal_sandbox(
                scope,
                board_dir=trusted.board_dir,
                environment_id=environment_id,
                read_only=_is_pm_session(scope),
            )
        if tool_name == "search_files":
            raw_path = str(rewritten.get("path") or ".").strip() or "."
            rewritten["path"] = _project_tool_path(
                scope, raw_path, container_paths=container_paths
            )
        elif tool_name in {"read_file", "write_file"}:
            raw_path = str(rewritten.get("path") or "").strip()
            if raw_path:
                rewritten["path"] = _project_tool_path(
                    scope, raw_path, container_paths=container_paths
                )
        elif str(rewritten.get("mode") or "replace") == "patch":
            patch_text = str(rewritten.get("patch") or "")
            rewritten["patch"] = _rewrite_patch_paths(
                scope, patch_text, container_paths=container_paths
            )
        else:
            raw_path = str(rewritten.get("path") or "").strip()
            if raw_path:
                rewritten["path"] = _project_tool_path(
                    scope, raw_path, container_paths=container_paths
                )
    except (OSError, RuntimeError, ScopeViolation):
        # Preserve the original request. The pre-tool hook below will emit the
        # policy veto, including when middleware failures are isolated by the
        # plugin manager.
        return None

    if rewritten == dict(args or {}):
        return None
    return {
        "args": rewritten,
        "source": "datansh_pmo_project_scope",
        "reason": "resolve relative file-tool paths inside the active project",
    }


def _kanban_scope_block(
    scope: Any, tool_name: str, args: Mapping[str, Any]
) -> dict[str, str] | None:
    """Keep every Kanban tool call on the active project's board and roster."""

    raw_board = str(args.get("board") or "").strip()
    if raw_board:
        try:
            requested_board = kanban_db._normalize_board_slug(raw_board)
        except ValueError:
            requested_board = None
        if requested_board != scope.board_slug:
            return {
                "action": "block",
                "message": (
                    f"PM-OS project scope blocked {tool_name}: board {raw_board!r} "
                    f"is not this project's board '{scope.board_slug}'."
                ),
            }

    raw_project = str(args.get("project") or args.get("project_id") or "").strip()
    if raw_project and raw_project not in {scope.project_id, scope.slug}:
        return {
            "action": "block",
            "message": (
                f"PM-OS project scope blocked {tool_name}: project {raw_project!r} "
                f"does not match '{scope.slug}'."
            ),
        }

    pm_session = _is_pm_session(scope)
    if pm_session and tool_name == "kanban_create":
        return {
            "action": "block",
            "message": (
                "Project PMs must plan in Founder's Office and create delegated "
                "work with pmo_create_ticket; direct kanban_create is disabled."
            ),
        }

    if tool_name in {"kanban_create", "kanban_reassign"}:
        raw_assignee = str(args.get("assignee") or args.get("profile") or "").strip()
        if raw_assignee and not raw_assignee.startswith(("human:", "dashboard:")):
            assignee = normalize_profile_name(raw_assignee)
            if assignee == normalize_profile_name(scope.config.orchestrator.profile):
                return {
                    "action": "block",
                    "message": (
                        "The project PM is an orchestrator and cannot be assigned "
                        "implementation work; choose a worker agent."
                    ),
                }
            if assignee not in task_agent_profiles(scope):
                return {
                    "action": "block",
                    "message": (
                        f"PM-OS project scope blocked {tool_name}: assignee "
                        f"'{assignee}' is not in project '{scope.slug}' roster."
                    ),
                }

    task_ids: list[str] = []
    for key in ("task_id", "parent_id", "child_id"):
        value = str(args.get(key) or "").strip()
        if value:
            task_ids.append(value)
    parents = args.get("parents")
    if isinstance(parents, str):
        task_ids.append(parents)
    elif isinstance(parents, (list, tuple)):
        task_ids.extend(str(value).strip() for value in parents if str(value).strip())
    if task_ids:
        with kanban_db.connect_closing(board=scope.board_slug) as conn:
            for task_id in dict.fromkeys(task_ids):
                task = kanban_db.get_task(conn, task_id)
                if task is None or task.project_id != scope.project_id:
                    return {
                        "action": "block",
                        "message": (
                            f"PM-OS project scope blocked {tool_name}: task "
                            f"'{task_id}' is not part of project '{scope.slug}'."
                        ),
                    }
    return None


def _host_path_for_terminal(scope: Any, path: str) -> str:
    """Map the sandbox's public mount paths back to validated host roots.

    Agents naturally reuse ``/workspace`` after the first container command.
    The pre-hook still applies the same project and deny-rule validation by
    translating that path, rather than rejecting a valid in-sandbox workdir or
    blindly trusting every POSIX absolute path.
    """

    normalized = path.replace("\\", "/")
    mounts = [("/workspace", scope.folders[0])]
    mounts.extend(
        (f"/workspaces/{index}", folder)
        for index, folder in enumerate(scope.folders[1:], start=1)
    )
    for container_root, host_root in mounts:
        if normalized == container_root:
            return str(host_root)
        prefix = container_root + "/"
        if normalized.startswith(prefix):
            relative = normalized[len(prefix):]
            return str(Path(host_root, *relative.split("/")))
    return path


def _is_project_mount_path(scope: Any, path: str) -> bool:
    normalized = path.replace("\\", "/")
    if normalized == "/workspace" or normalized.startswith("/workspace/"):
        return True
    return any(
        normalized == f"/workspaces/{index}"
        or normalized.startswith(f"/workspaces/{index}/")
        for index in range(1, len(scope.folders))
    )


def _is_container_internal_path(scope: Any, path: str) -> bool:
    """Return whether an absolute POSIX path is internal to the hard sandbox.

    Project mount paths are deliberately excluded so they still pass through
    the host-side project/deny validation. Other paths such as ``/dev/null``
    and ``/tmp`` name only the container filesystem and cannot escape Docker.
    """

    if scope.config.scope.terminal_isolation != "docker":
        return False
    normalized = path.replace("\\", "/")
    if not normalized.startswith("/"):
        return False
    if normalized == "/workspace" or normalized.startswith("/workspace/"):
        return False
    for index in range(1, len(scope.folders)):
        root = f"/workspaces/{index}"
        if normalized == root or normalized.startswith(root + "/"):
            return False
    return True


def pre_tool_scope_hook(
    *,
    tool_name: str,
    args: Mapping[str, Any] | None = None,
    task_id: str = "",
    session_id: str = "",
    **_kwargs: Any,
) -> dict[str, str] | None:
    """Block path-bearing normal tools when they leave the PM's project."""

    file_tools = {"read_file", "write_file", "patch", "search_files", "terminal"}
    pm_forbidden = {
        "terminal",
        "process",
        "write_file",
        "patch",
        "execute_code",
        "delegate_task",
        "clarify",
        "process",
    }
    is_kanban = tool_name.startswith("kanban_")
    if tool_name not in file_tools and tool_name not in pm_forbidden and not is_kanban:
        return None
    scope, scope_error = _active_pmo_scope()
    if scope_error:
        return {"action": "block", "message": scope_error}
    if scope is None:
        return None
    if _is_pm_session(scope) and tool_name in pm_forbidden:
        replacement = {
            "delegate_task": "create/consult roster agents with pmo_create_agent and pmo_consult",
            "clarify": "consult a roster agent first, then use pmo_ask_human with that plan",
            "terminal": "use pmo_repo_read, pmo_repo_search, or pmo_repo_status for read-only repository analysis",
        }.get(tool_name, "use read-only repository inspection and delegate work through PMO tickets")
        return {
            "action": "block",
            "message": (
                f"PM-OS blocked {tool_name}: the project PM is read-only and does not "
                f"implement work; {replacement}."
            ),
        }
    if is_kanban:
        return _kanban_scope_block(scope, tool_name, args or {})
    for path in _paths_for_call(tool_name, args or {}):
        if tool_name == "terminal" and _is_container_internal_path(scope, path):
            continue
        project_mount = (
            tool_name != "terminal"
            and scope.config.scope.terminal_isolation == "docker"
            and _is_project_mount_path(scope, path)
        )
        if (
            tool_name != "terminal"
            and not project_mount
            and not Path(path).expanduser().is_absolute()
        ):
            return {
                "action": "block",
                "message": (
                    f"PM-OS project scope blocked {tool_name}: relative path "
                    f"{path!r} was not resolved by project middleware. Retry "
                    f"with an absolute path inside {scope.primary_path}."
                ),
            }
        checked_path = (
            _host_path_for_terminal(scope, path)
            if tool_name == "terminal" or project_mount
            else path
        )
        try:
            validated_path = assert_path(scope, checked_path)
        except ScopeViolation as exc:
            return {
                "action": "block",
                "message": (
                    f"PM-OS project scope blocked {tool_name}: {exc}. "
                    f"Work inside {scope.primary_path} or use a profile-scoped "
                    "Hermes skill/config tool for profile state."
                ),
            }
        if tool_name in {"write_file", "patch"}:
            from plugins.pmo.repositories import repository_for_path

            repository = repository_for_path(scope, validated_path)
            if repository is not None and repository.access == "read":
                return {
                    "action": "block",
                    "message": (
                        f"PM-OS repository '{repository.name}' is read-only for this project"
                    ),
                }
    if tool_name == "terminal" and scope.config.scope.terminal_isolation == "docker":
        environment_id = str(task_id or session_id).strip()
        if not environment_id:
            return {
                "action": "block",
                "message": "PM-OS project sandbox requires an internal session identity",
            }
        trusted = _ACTIVE_SCOPE.get()
        if trusted is None or trusted.board_dir is None:
            return {
                "action": "block",
                "message": "PM-OS project sandbox is unavailable for this conversation",
            }
        try:
            from plugins.pmo.sandbox import register_terminal_sandbox

            register_terminal_sandbox(
                scope,
                board_dir=trusted.board_dir,
                environment_id=environment_id,
                read_only=_is_pm_session(scope),
            )
        # PluginManager intentionally isolates hook failures so one broken
        # plugin cannot take down the agent loop.  This hook is a security
        # boundary, though: any provisioning failure must become an explicit
        # veto or the swallowed exception would let the terminal run with the
        # process-global backend configuration.
        except Exception as exc:  # noqa: BLE001 - fail closed at policy seam
            return {
                "action": "block",
                "message": f"PM-OS project sandbox is unavailable: {exc}",
            }
    return None


__all__ = [
    "bind_active_scope",
    "pre_tool_scope_hook",
    "reset_active_scope",
    "tool_request_scope_middleware",
]
