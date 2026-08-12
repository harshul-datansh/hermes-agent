"""Agent-facing collaboration tools for Datansh PM-OS.

``plugins/pmo/collaboration.py`` gave the dashboard a way to ask, hand over
and escalate. Agents could not reach any of it: they only have the upstream
``kanban_*`` tools, so an agent that needed a specialist had no move except
writing a plain comment nobody is notified about. A live worker proved it —
it did the work, wrote a good comment, and mentioned no one.

These are thin wrappers. The board stays the only channel (requirement #7),
Kanban stays the only task store, and every action still writes an ``@``
comment plus whatever state change it implies.

Founder\'s Office planning, specialist sub-chats, delegated ticket creation,
and project-agent provisioning live here too.  Those operations are narrow
PMO mutations; the PM\'s generic repository and shell surfaces remain
read-only.

Registered in the dedicated ``pmo-collab`` toolset. It is a **leaf set**: it
includes nothing and nothing includes it, so a PM-OS agent cannot pick up
``terminal`` or messaging through a broad composite.
"""

from __future__ import annotations

import json
import os
import subprocess
import uuid
from pathlib import Path
from typing import Any, Mapping

from tools.registry import registry


def _env_task_and_board() -> tuple[str, str]:
    """The task and board this worker was dispatched for.

    Taken from the environment rather than a tool argument: a worker must not
    be able to act on another project's ticket by naming it, and the
    dispatcher already pins both (``HERMES_KANBAN_TASK`` /
    ``HERMES_KANBAN_BOARD``).
    """
    task_id = str(os.environ.get("HERMES_KANBAN_TASK") or "").strip()
    board = str(os.environ.get("HERMES_KANBAN_BOARD") or "").strip()
    if not board:
        try:
            from gateway.session_context import get_session_env

            if get_session_env("HERMES_SESSION_PLATFORM", "") == "pmo":
                board = str(
                    get_session_env("HERMES_SESSION_CHAT_ID", "") or ""
                ).strip()
        except Exception:  # noqa: BLE001 - worker env remains the fallback
            pass
    return task_id, board


def _scope():
    from hermes_cli import kanban_db
    from plugins.pmo.project_scope import resolve

    _task_id, board = _env_task_and_board()
    if not board:
        board = kanban_db.get_current_board()
    meta = kanban_db.read_board_metadata(board)
    project_id = str(meta.get("project_id") or "").strip()
    if not project_id:
        raise RuntimeError(
            f"board {board!r} is not bound to a PM-OS project; "
            "collaboration tools need a project-scoped board"
        )
    return resolve(project_id, board_slug=board)


def _author() -> str:
    """The profile this worker runs as — the comment's author.

    The task's ``assignee`` is preferred over ``get_active_profile_name()``:
    inside a dispatched worker the latter returns the generic ``worker``, so
    every agent's comment was signed identically and you could not tell who
    had spoken on the board.
    """
    # A routed collaboration turn (for example a PM answering a developer)
    # intentionally runs under a profile different from the ticket assignee.
    # Prefer that explicit session identity. Legacy Kanban workers may still
    # expose only ``worker`` and fall back to the assignee below.
    try:
        from gateway.session_context import get_session_env
        from hermes_cli.profiles import normalize_profile_name

        session_profile = normalize_profile_name(
            get_session_env("HERMES_SESSION_PROFILE", "")
        )
        if session_profile and session_profile not in {"default", "worker"}:
            return session_profile
    except Exception:  # noqa: BLE001 - fall through to task ownership
        pass

    task_id, board = _env_task_and_board()
    if task_id:
        try:
            from hermes_cli import kanban_db

            with kanban_db.connect_closing(board=board or None) as conn:
                task = kanban_db.get_task(conn, task_id)
            assignee = str(getattr(task, "assignee", "") or "").strip()
            if assignee:
                return assignee
        except Exception:  # noqa: BLE001 - fall through to the profile name
            pass
    from hermes_cli import profiles as profiles_mod

    try:
        from gateway.session_context import get_session_env

        return (
            get_session_env("HERMES_SESSION_PROFILE", "")
            or profiles_mod.get_active_profile_name()
            or "unknown-agent"
        )
    except Exception:  # noqa: BLE001 - a tool must not die on profile lookup
        return "unknown-agent"


def _active_profile() -> str:
    from gateway.session_context import get_session_env
    from hermes_cli.profiles import normalize_profile_name

    value = get_session_env("HERMES_SESSION_PROFILE", "")
    if value:
        return normalize_profile_name(value)
    return normalize_profile_name(_author())


def _founder_context(scope) -> tuple[str, str]:
    """Return the active PM profile and Founder\'s Office thread."""

    from gateway.session_context import get_session_env
    from hermes_cli.profiles import normalize_profile_name
    from plugins.platforms.pmo.adapter import resolve_conversation

    profile = _active_profile()
    expected = normalize_profile_name(scope.config.orchestrator.profile)
    if profile != expected:
        raise PermissionError(
            f"only project PM profile '{expected}' may use Founder\'s Office planning tools"
        )
    thread_id = str(get_session_env("HERMES_SESSION_THREAD_ID", "") or "").strip()
    conversation = resolve_conversation(
        board_slug=scope.board_slug, thread_id=thread_id
    )
    if conversation.project_id != scope.project_id or conversation.kind not in {
        "founders_office", "global_founders_office"
    }:
        raise PermissionError("this action is available only inside Founder's Office")
    return profile, conversation.thread_id


def _target_task(args: Mapping[str, Any]) -> str:
    explicit = str(args.get("task_id") or "").strip()
    env_task, _ = _env_task_and_board()
    if explicit or env_task:
        return explicit or env_task
    try:
        from gateway.session_context import get_session_env

        if get_session_env("HERMES_SESSION_PLATFORM", "") == "pmo":
            return str(
                get_session_env("HERMES_SESSION_THREAD_ID", "") or ""
            ).strip()
    except Exception:  # noqa: BLE001 - handler reports the empty target
        pass
    return ""


def _fail(message: str) -> str:
    return json.dumps({"ok": False, "error": message})


def _check_pmo_collab() -> bool:
    """Available only inside a PM-OS project board."""
    try:
        from hermes_cli import kanban_db

        _task, board = _env_task_and_board()
        if not board:
            return False
        meta = kanban_db.read_board_metadata(board)
        return bool(str(meta.get("project_id") or "").strip())
    except Exception:  # noqa: BLE001 - availability checks must never raise
        return False


def _pm_scope():
    """Return the active scope only for its dedicated project manager."""

    from hermes_cli.profiles import normalize_profile_name

    scope = _scope()
    if _active_profile() != normalize_profile_name(scope.config.orchestrator.profile):
        raise PermissionError("read-only repository analysis is available only to the project PM")
    return scope


def _check_pmo_pm() -> bool:
    try:
        _pm_scope()
        return True
    except Exception:  # noqa: BLE001 - availability checks must never raise
        return False


def _repo_path(scope, raw_path: Any) -> Path:
    """Resolve one inspection path through the normal project-scope guard."""

    from plugins.pmo.project_scope import assert_path

    value = str(raw_path or ".").strip() or "."
    return assert_path(scope, value)


def _relative_repo_path(scope, path: Path) -> str:
    for root in scope.folders:
        try:
            return path.resolve(strict=False).relative_to(root.resolve(strict=False)).as_posix()
        except ValueError:
            continue
    return path.name


def _handle_repo_read(args: Mapping[str, Any], **_kw: Any) -> str:
    """Read a bounded text file without exposing a generic write-capable tool."""

    try:
        scope = _pm_scope()
        path = _repo_path(scope, args.get("path"))
        if not path.is_file():
            raise ValueError(f"not a file: {_relative_repo_path(scope, path)}")
        max_chars = max(1_000, min(120_000, int(args.get("max_chars") or 24_000)))
        text = path.read_text(encoding="utf-8", errors="replace")
        return json.dumps({
            "ok": True,
            "path": _relative_repo_path(scope, path),
            "content": text[:max_chars],
            "truncated": len(text) > max_chars,
        })
    except Exception as exc:  # noqa: BLE001
        return _fail(str(exc))


def _handle_repo_search(args: Mapping[str, Any], **_kw: Any) -> str:
    """Perform a bounded literal repository search for planning evidence."""

    try:
        scope = _pm_scope()
        needle = str(args.get("query") or "").strip()
        if not needle:
            raise ValueError("search query is required")
        if len(needle) > 500:
            raise ValueError("search query exceeds 500 characters")
        root = _repo_path(scope, args.get("path"))
        if not root.is_dir():
            raise ValueError(f"not a directory: {_relative_repo_path(scope, root)}")
        max_results = max(1, min(100, int(args.get("max_results") or 30)))
        ignored = {".git", "node_modules", ".venv", "venv", "dist", "build", "coverage"}
        rows: list[dict[str, Any]] = []
        needle_folded = needle.casefold()
        for candidate in root.rglob("*"):
            if any(part in ignored for part in candidate.parts):
                continue
            if not candidate.is_file() or candidate.stat().st_size > 1_000_000:
                continue
            try:
                lines = candidate.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for number, line in enumerate(lines, start=1):
                if needle_folded in line.casefold():
                    rows.append({
                        "path": _relative_repo_path(scope, candidate),
                        "line": number,
                        "text": line[:500],
                    })
                    if len(rows) >= max_results:
                        return json.dumps({"ok": True, "matches": rows, "truncated": True})
        return json.dumps({"ok": True, "matches": rows, "truncated": False})
    except Exception as exc:  # noqa: BLE001
        return _fail(str(exc))


def _handle_repo_status(args: Mapping[str, Any], **_kw: Any) -> str:
    """Return a read-only Git status snapshot for planning and triage."""

    try:
        scope = _pm_scope()
        completed = subprocess.run(
            ["git", "status", "--short", "--branch"],
            cwd=scope.primary_path,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
            check=False,
        )
        if completed.returncode:
            raise ValueError(completed.stderr.strip() or "git status failed")
        return json.dumps({"ok": True, "status": completed.stdout[:24_000]})
    except Exception as exc:  # noqa: BLE001
        return _fail(str(exc))


def _handle_context(args: Mapping[str, Any], **_kw: Any) -> str:
    """Return bounded project memory, configuration, skills, and board context."""

    try:
        from plugins.pmo.context_builder import build_context
        from plugins.pmo import planning

        scope = _pm_scope()
        profile, thread_id = _founder_context(scope)
        request = str(args.get("request") or "Founder request planning")
        envelope = build_context(
            scope,
            trigger=request,
            trigger_kind="founders_office_planning",
            audience="internal",
        )
        review_id = planning.record_context_review(
            scope,
            thread_id=thread_id,
            author=f"agent:{profile}",
            request=request,
            context=envelope.text,
        )
        return json.dumps({
            "ok": True,
            "project_id": scope.project_id,
            "context": envelope.text,
            "context_review_id": review_id,
            "truncated": envelope.truncated,
        })
    except Exception as exc:  # noqa: BLE001
        return _fail(str(exc))


# --- handlers ---------------------------------------------------------------


def _handle_handles(args: Mapping[str, Any], **_kw: Any) -> str:
    """Who can be addressed on this project."""
    from plugins.pmo import mentions

    try:
        scope = _scope()
    except Exception as exc:  # noqa: BLE001
        return _fail(str(exc))
    rows = [
        {"handle": r.handle, "kind": r.kind, "role": r.role}
        for r in mentions.roster(scope)
    ]
    return json.dumps({"ok": True, "handles": rows})


def _handle_ask(args: Mapping[str, Any], **_kw: Any) -> str:
    from plugins.pmo import collaboration

    handles = args.get("handles") or []
    if isinstance(handles, str):
        handles = [handles]
    try:
        scope = _scope()
        result = collaboration.request_info(
            scope, task_id=_target_task(args), author=_author(),
            handles=handles, question=str(args.get("question") or ""),
            pause=True,
        )
    except Exception as exc:  # noqa: BLE001 - report, never crash the turn
        return _fail(str(exc))
    return json.dumps({
        "ok": True, "action": result.action, "comment_id": result.comment_id,
        "notified": list(result.handles),
        "state": "waiting_for_answer",
    })


def _handle_answer(args: Mapping[str, Any], **_kw: Any) -> str:
    from plugins.pmo import collaboration

    try:
        scope = _scope()
        result = collaboration.answer_info(
            scope,
            task_id=_target_task(args),
            author=_author(),
            answer=str(args.get("answer") or ""),
            question_id=str(args.get("question_id") or "") or None,
        )
    except Exception as exc:  # noqa: BLE001
        return _fail(str(exc))
    return json.dumps({
        "ok": True,
        "action": result.action,
        "comment_id": result.comment_id,
        "notified": list(result.handles),
        "state": "requesting_worker_resumed",
        "assignee": result.new_assignee,
    })


def _handle_transfer(args: Mapping[str, Any], **_kw: Any) -> str:
    from plugins.pmo import collaboration

    try:
        scope = _scope()
        result = collaboration.transfer_task(
            scope, task_id=_target_task(args), author=_author(),
            to_handle=str(args.get("to_handle") or ""),
            reason=str(args.get("reason") or ""),
        )
    except Exception as exc:  # noqa: BLE001
        return _fail(str(exc))
    return json.dumps({
        "ok": True, "action": result.action, "comment_id": result.comment_id,
        "previous_assignee": result.previous_assignee,
        "new_assignee": result.new_assignee,
    })


def _handle_escalate(args: Mapping[str, Any], **_kw: Any) -> str:
    from plugins.pmo import collaboration

    handles = args.get("handles") or []
    if isinstance(handles, str):
        handles = [handles]
    try:
        scope = _scope()
        if _active_profile() == scope.config.orchestrator.profile:
            from plugins.pmo import planning

            _profile, thread_id = _founder_context(scope)
            plan = planning.require_plan(
                scope, thread_id=thread_id, plan_id=str(args.get("plan_id") or "")
            )
            planning.require_consultation_response(scope, plan=plan)
        result = collaboration.escalate_to_human(
            scope, task_id=_target_task(args), author=_author(),
            handles=handles, reason=str(args.get("reason") or ""),
            block=bool(args.get("block", True)),
        )
    except Exception as exc:  # noqa: BLE001
        return _fail(str(exc))
    return json.dumps({
        "ok": True, "action": result.action, "comment_id": result.comment_id,
        "new_assignee": result.new_assignee,
    })


def _handle_plan(args: Mapping[str, Any], **_kw: Any) -> str:
    from plugins.pmo import planning

    try:
        scope = _scope()
        profile, thread_id = _founder_context(scope)
        plan = planning.record_plan(
            scope,
            thread_id=thread_id,
            author=f"agent:{profile}",
            objective=str(args.get("objective") or ""),
            discussion=str(args.get("discussion") or ""),
        )
    except Exception as exc:  # noqa: BLE001
        return _fail(str(exc))
    return json.dumps({
        "ok": True,
        "plan_id": plan.plan_id,
        "thread_id": plan.thread_id,
        "objective": plan.objective,
    })


def _handle_consult(args: Mapping[str, Any], **_kw: Any) -> str:
    from hermes_cli import kanban_db
    from plugins.platforms.pmo.adapter import (
        create_project_conversation,
        enqueue_persisted_message,
    )
    from plugins.pmo import mentions, planning

    try:
        scope = _scope()
        profile, thread_id = _founder_context(scope)
        plan = planning.require_plan(
            scope, thread_id=thread_id, plan_id=str(args.get("plan_id") or "")
        )
        handle = str(args.get("handle") or "").strip().lstrip("@").lower()
        entry = next((item for item in mentions.roster(scope) if item.handle == handle), None)
        if entry is None or entry.kind != "agent" or not entry.profile:
            raise ValueError(f"@{handle or '?'} is not a project agent")
        if entry.profile == scope.config.orchestrator.profile:
            raise ValueError("the PM cannot consult itself; choose a specialist agent")
        if str(entry.role or "").strip().casefold() not in {"dev", "developer", "engineer", "backend-developer", "frontend-developer"}:
            raise ValueError("the mandatory plan consultation must use a project developer")
        pending = planning.pending_consultation(scope, plan=plan, handle=handle)
        if pending:
            return json.dumps({
                "ok": True,
                "plan_id": plan.plan_id,
                "handle": handle,
                "profile": pending.get("profile"),
                "thread_id": pending.get("consultation_thread_id"),
                "request_comment_id": pending.get("request_comment_id"),
                "queue_id": None,
                "status": "already_queued",
                "awaiting_specialist": True,
                "continue_current_turn": False,
                "next_action": (
                    "Stop this turn. Do not ask the founder, call another collaboration "
                    "tool, or open a duplicate consultation. The specialist response will "
                    "automatically wake the project manager in this conversation."
                ),
            })
        question = str(args.get("question") or "").strip()
        topic = " ".join(question.split())[:80] or "Specialist review"
        conversation = create_project_conversation(
            scope,
            title=f"Plan {plan.plan_id} · @{handle} · {topic}",
        )
        request_id = planning.record_consultation(
            scope,
            plan=plan,
            consultation_thread_id=conversation.thread_id,
            author=f"agent:{profile}",
            handle=handle,
            profile=entry.profile,
            question=question,
        )
        queue_id = enqueue_persisted_message(
            board_slug=scope.board_slug,
            thread_id=conversation.thread_id,
            comment_id=request_id,
            actor_id=f"agent:{profile}",
            actor_name="Project PM",
            project_slug=scope.slug,
            target_profile=entry.profile,
        )
    except Exception as exc:  # noqa: BLE001
        return _fail(str(exc))
    return json.dumps({
        "ok": True,
        "plan_id": plan.plan_id,
        "handle": handle,
        "profile": entry.profile,
        "thread_id": conversation.thread_id,
        "request_comment_id": request_id,
        "queue_id": queue_id,
        "status": "queued",
        "awaiting_specialist": True,
        "continue_current_turn": False,
        "next_action": (
            "Stop this turn. Do not ask the founder or continue planning yet. The "
            "specialist response will automatically wake the project manager in this "
            "conversation, and that recommendation must be used in the next decision."
        ),
    })


def _handle_peer_pm(args: Mapping[str, Any], **_kw: Any) -> str:
    """Route a Global Founder message to one peer PM, never its agents."""

    from hermes_cli import kanban_db
    from hermes_cli.profiles import normalize_profile_name
    from plugins.platforms.pmo.adapter import (
        enqueue_persisted_message,
        list_project_conversations,
        resolve_conversation,
    )
    from plugins.pmo.project_scope import registered_projects, resolve

    try:
        scope = _scope()
        source_profile, thread_id = _founder_context(scope)
        source_conversation = resolve_conversation(
            board_slug=scope.board_slug, thread_id=thread_id
        )
        if source_conversation.kind != "global_founders_office":
            raise PermissionError("peer PM messages are available only in Global Founder's Office")
        target_profile = normalize_profile_name(
            str(args.get("target_pm") or "").strip().lstrip("@")
        )
        if not target_profile:
            raise ValueError("target_pm is required")
        if target_profile == normalize_profile_name(source_profile):
            raise ValueError("choose another project's PM")
        request = " ".join(str(args.get("request") or "").split())
        if not request:
            raise ValueError("request is required")
        if len(request) > 8_000:
            raise ValueError("request exceeds 8000 characters")

        target_project = None
        target_scope = None
        for project in registered_projects():
            if not project.board_slug or project.id == scope.project_id:
                continue
            try:
                candidate = resolve(project.id, board_slug=project.board_slug)
            except (OSError, RuntimeError, ValueError):
                continue
            if normalize_profile_name(candidate.config.orchestrator.profile) == target_profile:
                target_project = project
                target_scope = candidate
                break
        if target_project is None or target_scope is None:
            raise ValueError(f"@{target_profile} is not a peer project manager")

        global_id = source_conversation.global_conversation_id or "global"
        target_conversation = next(
            (
                item
                for item in list_project_conversations(target_scope)
                if item.kind == "global_founders_office"
                and (item.global_conversation_id or "global") == global_id
            ),
            None,
        )
        if target_conversation is None:
            raise ValueError("the peer PM does not share this global conversation")

        marker_id = uuid.uuid4().hex
        body = (
            f"<!-- datansh-pmo-global-message:{marker_id} -->\n"
            f"@{target_profile} Peer PM request from @{source_profile} "
            f"({scope.name}):\n\n{request}\n\n"
            "Reply in this Global Founder's Office conversation."
        )
        with kanban_db.connect_closing(board=target_scope.board_slug) as conn:
            comment_id = kanban_db.add_comment(
                conn,
                target_conversation.thread_id,
                author=f"agent:{source_profile}",
                body=body,
            )
        queue_id = enqueue_persisted_message(
            board_slug=target_scope.board_slug,
            thread_id=target_conversation.thread_id,
            comment_id=int(comment_id),
            actor_id=f"agent:{source_profile}",
            actor_name=source_profile,
            project_slug=target_scope.slug,
            target_profile=target_profile,
        )
    except Exception as exc:  # noqa: BLE001
        return _fail(str(exc))
    return json.dumps({
        "ok": True,
        "source_project_id": scope.project_id,
        "target_project_id": target_scope.project_id,
        "target_pm": target_profile,
        "global_conversation_id": global_id,
        "comment_id": int(comment_id),
        "queue_id": queue_id,
        "status": "queued",
        "continue_current_turn": False,
        "next_action": "Stop this turn and wait for the peer PM to reply in Global Founder's Office.",
    })


def _handle_create_agent(args: Mapping[str, Any], **_kw: Any) -> str:
    from plugins.pmo import agents, planning

    try:
        scope = _scope()
        _profile, thread_id = _founder_context(scope)
        planning.require_plan(
            scope, thread_id=thread_id, plan_id=str(args.get("plan_id") or "")
        )
        role = str(args.get("role") or "").strip().lower()
        count = int(args.get("count") or 1)
        if count < 1 or count > 8:
            raise ValueError("count must be between 1 and 8")
        model = str(args.get("model") or "").strip()
        provisioned = agents.provision(
            scope,
            roles=(role,),
            counts={role: count},
            models={role: model} if model else None,
            include_orchestrator=False,
        )
        roster = agents.write_roster(scope, provisioned)
    except Exception as exc:  # noqa: BLE001
        return _fail(str(exc))
    return json.dumps({
        "ok": True,
        "role": role,
        "count": len(provisioned),
        "agents": [
            {
                "handle": item.handle,
                "profile": item.profile,
                "created": item.profile_created,
            }
            for item in provisioned
        ],
        "roster_size": len(roster),
    })


def _handle_request_details(args: Mapping[str, Any], **_kw: Any) -> str:
    from plugins.pmo import planning

    try:
        scope = _scope()
        profile, thread_id = _founder_context(scope)
        plan = planning.require_plan(
            scope, thread_id=thread_id, plan_id=str(args.get("plan_id") or "")
        )
        # Founder questions must be informed by the specialist run.  A model
        # may ignore the consultation tool's "stop this turn" guidance, so
        # enforce the sequencing here instead of relying on prompt compliance.
        planning.require_consultation_response(scope, plan=plan)
        request = planning.record_detail_request(
            scope,
            plan=plan,
            author=f"agent:{profile}",
            questions=args.get("questions"),
        )
    except Exception as exc:  # noqa: BLE001
        return _fail(str(exc))
    return json.dumps({
        "ok": True,
        "plan_id": plan.plan_id,
        "request_id": request.request_id,
        "question_count": len(request.questions),
        "status": "waiting_for_founder_details",
    })


def _handle_request_ticket_confirmation(args: Mapping[str, Any], **_kw: Any) -> str:
    from plugins.pmo import mentions, planning

    try:
        scope = _scope()
        profile, thread_id = _founder_context(scope)
        plan = planning.require_plan(
            scope, thread_id=thread_id, plan_id=str(args.get("plan_id") or "")
        )
        proposal = {key: value for key, value in args.items() if key != "plan_id"}
        raw_assignee = str(proposal.get("assignee") or "").strip().lstrip("@").lower()
        entry = next((item for item in mentions.roster(scope)
            if item.handle == raw_assignee or str(item.profile or "").lower() == raw_assignee
            or str(item.principal or "").lower() == raw_assignee), None)
        if entry is None:
            raise ValueError(f"@{raw_assignee or '?'} is not a project worker or project human")
        if entry.kind == "agent" and entry.profile == scope.config.orchestrator.profile:
            raise ValueError(
                "the PM cannot own an implementation ticket. Select the appropriate "
                "project worker from the completed specialist consultation and submit "
                "a revised ticket confirmation; do not ask the founder to choose a developer."
            )
        if entry.kind == "agent" and entry.profile:
            proposal["assignee"] = entry.profile
        elif entry.kind == "human" and entry.principal:
            proposal["assignee"] = entry.principal
        else:
            raise ValueError(f"@{raw_assignee or '?'} cannot own a project ticket")
        confirmation = planning.record_ticket_confirmation(
            scope,
            plan=plan,
            author=f"agent:{profile}",
            proposal=proposal,
        )
    except Exception as exc:  # noqa: BLE001
        return _fail(str(exc))
    return json.dumps({
        "ok": True,
        "plan_id": plan.plan_id,
        "confirmation_id": confirmation.confirmation_id,
        "status": "waiting_for_founder_approval",
    })


def _handle_create_ticket(args: Mapping[str, Any], **_kw: Any) -> str:
    from plugins.pmo import mentions, planning, workflow

    try:
        scope = _scope()
        profile, thread_id = _founder_context(scope)
        plan = planning.require_plan(
            scope, thread_id=thread_id, plan_id=str(args.get("plan_id") or "")
        )
        confirmation = planning.require_ticket_approval(
            scope,
            plan=plan,
            confirmation_id=str(args.get("confirmation_id") or ""),
        )
        proposal = confirmation.proposal
        raw_assignee = str(proposal.get("assignee") or "").strip().lstrip("@").lower()
        entry = next((item for item in mentions.roster(scope)
            if item.handle == raw_assignee or str(item.profile or "").lower() == raw_assignee
            or str(item.principal or "").lower() == raw_assignee), None)
        if entry is None:
            raise ValueError(f"@{raw_assignee or '?'} is not a project worker or project human")
        if entry.kind == "agent" and entry.profile == scope.config.orchestrator.profile:
            raise ValueError(
                "the approved proposal incorrectly names the PM as owner. Do not ask "
                "the founder to choose a developer: use the completed specialist "
                "consultation to select the appropriate project worker, then submit a "
                "revised confirmation for approval."
            )

        def _items(name: str) -> list[str]:
            value = proposal.get(name) or []
            return [str(value)] if isinstance(value, str) else [str(item) for item in value]

        common = {
            "project_ref": scope.project_id, "board_slug": scope.board_slug,
            "actor_profile": profile, "title": str(proposal.get("title") or ""),
            "product_context": str(proposal.get("product_context") or ""),
            "user_impact": str(proposal.get("user_impact") or ""),
            "outcome": str(proposal.get("outcome") or ""),
            "acceptance_criteria": _items("acceptance_criteria"), "evidence": _items("evidence"),
            "constraints": _items("constraints"), "dependencies": _items("dependencies"),
            "parent_task_ids": _items("parent_task_ids"), "labels": _items("labels"),
            "touches": _items("touches"), "priority": int(proposal.get("priority") or 0),
            "idempotency_key": f"pmo-founder-confirmation:{scope.project_id}:{confirmation.confirmation_id}",
        }
        human_owned = entry.kind == "human" and entry.principal
        if human_owned:
            draft = workflow.create_human_draft(
                **common, created_by=f"agent:{profile}", assignee=entry.principal,
                human_steps=_items("human_steps"),
            )
            final_status = draft.status
            ticket_assignee = entry.principal
        elif entry.kind == "agent" and entry.profile:
            draft = workflow.create_draft(
                **common, assignee=entry.profile,
                estimate_hours=(float(proposal["estimate_hours"])
                    if proposal.get("estimate_hours") is not None else None),
            )
            final_status = workflow.finalize_ticket(
                project_ref=scope.project_id, board_slug=scope.board_slug,
                task_id=draft.task_id, actor_profile=profile,
            ).status
            ticket_assignee = entry.profile
        else:
            raise ValueError(f"@{raw_assignee or '?'} cannot own a project ticket")
        planning.record_ticket(
            scope,
            plan=plan,
            author=f"agent:{profile}",
            task_id=draft.task_id,
            title=str(proposal.get("title") or ""),
        )
        context_comment_id = planning.attach_ticket_context(
            scope,
            task_id=draft.task_id,
            author=f"agent:{profile}",
            context=planning.ticket_context(scope, plan=plan, confirmation=confirmation),
        )
    except Exception as exc:  # noqa: BLE001
        return _fail(str(exc))
    return json.dumps({
        "ok": True,
        "plan_id": plan.plan_id,
        "task_id": draft.task_id,
        "confirmation_id": confirmation.confirmation_id,
        "context_comment_id": context_comment_id,
        "assignee": ticket_assignee,
        "status": final_status,
        "kind": "human_task" if human_owned else "agent_ticket",
            "warnings": list(draft.warnings),
    })


def _handle_ask_human(args: Mapping[str, Any], **_kw: Any) -> str:
    from plugins.pmo import mentions, planning

    handles = args.get("handles") or []
    if isinstance(handles, str):
        handles = [handles]
    try:
        scope = _scope()
        profile, thread_id = _founder_context(scope)
        plan = planning.require_plan(
            scope, thread_id=thread_id, plan_id=str(args.get("plan_id") or "")
        )
        planning.require_consultation_response(scope, plan=plan)
        clean_handles = [str(item).strip().lstrip("@").lower() for item in handles]
        roster = {item.handle: item for item in mentions.roster(scope)}
        invalid = [h for h in clean_handles if h not in roster or roster[h].kind != "human"]
        if invalid:
            raise ValueError("human handle(s) required: " + ", ".join(invalid))
        question = str(args.get("question") or "").strip()
        result = mentions.post_comment(
            scope,
            task_id=thread_id,
            author=f"agent:{profile}",
            body=(
                " ".join(f"@{handle}" for handle in clean_handles)
                + f" Human input requested after specialist consultation for plan {plan.plan_id}: "
                + question
            ),
        )
    except Exception as exc:  # noqa: BLE001
        return _fail(str(exc))
    return json.dumps({
        "ok": True,
        "plan_id": plan.plan_id,
        "comment_id": result.comment_id,
        "notified": list(result.handles),
    })


# --- schemas ----------------------------------------------------------------

_REPO_READ_SCHEMA = {
    "name": "pmo_repo_read",
    "description": "Read a bounded UTF-8 repository file for project planning. Project PM only; read-only.",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Project-relative file path"},
            "max_chars": {"type": "integer", "minimum": 1000, "maximum": 120000},
        },
        "required": ["path"],
    },
}

_REPO_SEARCH_SCHEMA = {
    "name": "pmo_repo_search",
    "description": "Bounded literal text search over the project repository. Project PM only; read-only.",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Literal text to find"},
            "path": {"type": "string", "description": "Project-relative directory, default root"},
            "max_results": {"type": "integer", "minimum": 1, "maximum": 100},
        },
        "required": ["query"],
    },
}

_REPO_STATUS_SCHEMA = {
    "name": "pmo_repo_status",
    "description": "Read the current Git branch and working-tree status. Project PM only; read-only.",
    "parameters": {"type": "object", "properties": {}, "required": []},
}

_CONTEXT_SCHEMA = {
    "name": "pmo_context",
    "description": (
        "Load this project's bounded memory/knowledge, effective skills and configuration, "
        "roster, and current board context before planning. Project PM only; read-only."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "request": {"type": "string", "description": "Current founder request"},
        },
        "required": ["request"],
    },
}

_HANDLES_SCHEMA = {
    "name": "pmo_handles",
    "description": (
        "List every @handle you may address on this project — teammate agents "
        "and humans. Call this first if you are unsure who to contact; an "
        "unknown handle is refused."
    ),
    "parameters": {"type": "object", "properties": {}, "required": []},
}

_ASK_SCHEMA = {
    "name": "pmo_ask",
    "description": (
        "Ask teammates for information you need to finish this ticket. Posts "
        "an @mention comment and notifies them. Ownership does not change — "
        "use this when you are blocked on an answer, not on a decision."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "handles": {
                "type": "array", "items": {"type": "string"},
                "description": "Handles to ask, e.g. ['ops','qa']",
            },
            "question": {"type": "string", "description": "What you need to know"},
            "task_id": {
                "type": "string",
                "description": "Defaults to the ticket you are working on",
            },
        },
        "required": ["handles", "question"],
    },
}

_ASK_SCHEMA["description"] = (
    "Ask teammates for information you need to finish this ticket. Posts an "
    "@mention comment, pauses the ticket, and preserves your ownership. The "
    "recipient must reply with pmo_answer, which resumes your ticket."
)

_ANSWER_SCHEMA = {
    "name": "pmo_answer",
    "description": (
        "Answer the pending question addressed to you on this ticket. Posts "
        "the answer on the same ticket and resumes the original worker. Never "
        "repeat or re-ask the question; provide the concrete fact or direction."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "answer": {
                "type": "string",
                "description": "Concrete answer or direction for the requesting agent",
            },
            "question_id": {
                "type": "string",
                "description": "Optional when exactly one question is pending",
            },
            "task_id": {
                "type": "string",
                "description": "Defaults to the ticket carrying the question",
            },
        },
        "required": ["answer"],
    },
}

_TRANSFER_SCHEMA = {
    "name": "pmo_transfer",
    "description": (
        "Hand this ticket to another AGENT who is better suited to finish it "
        "(for example to @qa for verification). Moves ownership and releases "
        "your claim. For humans use pmo_escalate instead."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "to_handle": {"type": "string", "description": "Agent handle, e.g. 'qa'"},
            "reason": {
                "type": "string",
                "description": "Why they should take it — appears on the board",
            },
            "task_id": {"type": "string"},
        },
        "required": ["to_handle", "reason"],
    },
}

_ESCALATE_SCHEMA = {
    "name": "pmo_escalate",
    "description": (
        "Escalate to named HUMANS when you cannot proceed without a decision, "
        "credential or approval. Tags them, assigns the ticket to them and "
        "blocks it so no agent picks it back up and loops."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "handles": {
                "type": "array", "items": {"type": "string"},
                "description": "Human handles, e.g. ['ceo','cfo']",
            },
            "reason": {"type": "string", "description": "What you need from them"},
            "block": {
                "type": "boolean",
                "description": "Block the ticket (default true)",
            },
            "task_id": {"type": "string"},
            "plan_id": {
                "type": "string",
                "description": "Required when the project PM escalates from Founder's Office",
            },
        },
        "required": ["handles", "reason"],
    },
}

_ESCALATE_SCHEMA["description"] = (
    "Escalate to named HUMANS when you cannot proceed without a decision, "
    "credential, approval, or review. Tags them and hands them an actionable "
    "human-owned ticket; it does not leave their work parked as blocked."
)
_ESCALATE_SCHEMA["parameters"]["properties"]["block"]["description"] = (
    "Pause agent execution while handing off (default true)"
)

_PLAN_SCHEMA = {
    "name": "pmo_plan",
    "description": (
        "Record the visible Founder's Office plan and discussion that must exist "
        "before creating tickets, consulting specialists, or provisioning agents."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "objective": {"type": "string", "description": "Outcome the founder requested"},
            "discussion": {
                "type": "string",
                "description": "Options, repo findings, trade-offs, sequencing, and open questions",
            },
        },
        "required": ["objective", "discussion"],
    },
}

_CONSULT_SCHEMA = {
    "name": "pmo_consult",
    "description": (
        "Open a real Founder's Office sub-chat with a roster specialist. The "
        "specialist is routed there for read-only analysis and its reply is linked to the plan."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "plan_id": {"type": "string"},
            "handle": {"type": "string", "description": "Specialist handle, e.g. dev or qa"},
            "question": {"type": "string", "description": "Focused question for the specialist"},
        },
        "required": ["plan_id", "handle", "question"],
    },
}

_PEER_PM_SCHEMA = {
    "name": "pmo_peer_pm",
    "description": (
        "Send a bounded request from this project's PM to one other project's PM "
        "inside the active Global Founder's Office conversation. This routes only "
        "to the peer PM; it never exposes or addresses the peer project's agents."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "target_pm": {
                "type": "string",
                "description": "Exact peer PM profile, for example pm-project-two",
            },
            "request": {
                "type": "string",
                "description": "Bounded question or coordination request for the peer PM",
            },
        },
        "required": ["target_pm", "request"],
    },
}

_CREATE_AGENT_SCHEMA = {
    "name": "pmo_create_agent",
    "description": (
        "Provision one or more project-scoped worker agents when the current plan "
        "needs a missing specialty. The new profiles are added to this project's roster."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "plan_id": {"type": "string"},
            "role": {
                "type": "string",
                "enum": ["dev", "qa", "ops", "research", "design", "data"],
            },
            "count": {"type": "integer", "minimum": 1, "maximum": 8, "default": 1},
            "model": {"type": "string", "description": "Optional provider/model route"},
        },
        "required": ["plan_id", "role"],
    },
}

_CREATE_TICKET_SCHEMA = {
    "name": "pmo_create_ticket",
    "description": (
        "Create the exact worker-owned ticket explicitly approved by the founder. "
        "The proposal is loaded from the confirmation record, so approved fields cannot drift."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "plan_id": {"type": "string"},
            "confirmation_id": {"type": "string"},
        },
        "required": ["plan_id", "confirmation_id"],
    },
}

_REQUEST_DETAILS_SCHEMA = {
    "name": "pmo_request_details",
    "description": (
        "After a developer has replied, ask the founder 1–3 short multiple-choice "
        "ticket-detail questions. Provide 2–3 exclusive choices; the UI adds Other automatically."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "plan_id": {"type": "string"},
            "questions": {
                "type": "array", "minItems": 1, "maxItems": 3,
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "question": {"type": "string"},
                        "options": {
                            "type": "array", "minItems": 2, "maxItems": 3,
                            "items": {
                                "type": "object",
                                "properties": {
                                    "id": {"type": "string"},
                                    "label": {"type": "string"},
                                    "description": {"type": "string"},
                                },
                                "required": ["id", "label", "description"],
                            },
                        },
                    },
                    "required": ["id", "question", "options"],
                },
            },
        },
        "required": ["plan_id", "questions"],
    },
}

_REQUEST_CONFIRMATION_SCHEMA = {
    "name": "pmo_request_ticket_confirmation",
    "description": (
        "After the founder answers the structured questions, show the exact contextual ticket "
        "proposal for approval. Do not call pmo_create_ticket until this confirmation is approved."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "plan_id": {"type": "string"},
            "title": {"type": "string"},
            "product_context": {
                "type": "string",
                "description": "The current product problem, affected journey, and business context",
            },
            "user_impact": {
                "type": "string",
                "description": "What a user, client, or operator will gain or avoid after delivery",
            },
            "outcome": {"type": "string"},
            "assignee": {"type": "string", "description": "Project worker handle or a project human handle / human:<email> principal"},
            "human_steps": {"type": "array", "items": {"type": "string"}, "description": "Required 3-7 concrete hand-off steps when assignee is human"},
            "acceptance_criteria": {"type": "array", "items": {"type": "string"}},
            "evidence": {"type": "array", "items": {"type": "string"}},
            "constraints": {"type": "array", "items": {"type": "string"}},
            "dependencies": {"type": "array", "items": {"type": "string"}},
            "parent_task_ids": {"type": "array", "items": {"type": "string"}},
            "labels": {"type": "array", "items": {"type": "string"}},
            "touches": {"type": "array", "items": {"type": "string"}},
            "estimate_hours": {"type": "number", "minimum": 0},
            "priority": {"type": "integer", "default": 0},
            "context_summary": {
                "type": "string",
                "description": "Relevant skills, repository/memory findings, developer advice, and founder choices",
            },
        },
        "required": [
            "plan_id", "title", "product_context", "user_impact", "outcome", "assignee", "acceptance_criteria", "evidence", "context_summary"
        ],
    },
}

_ASK_HUMAN_SCHEMA = {
    "name": "pmo_ask_human",
    "description": (
        "Ask named humans in Founder's Office only after a roster specialist has "
        "replied in the plan's consultation sub-chat."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "plan_id": {"type": "string"},
            "handles": {"type": "array", "items": {"type": "string"}},
            "question": {"type": "string"},
        },
        "required": ["plan_id", "handles", "question"],
    },
}


# Registered as four explicit top-level calls, not a loop. Registry discovery
# is AST-assisted (``tools/registry.py`` only imports modules containing a
# top-level ``registry.register(...)`` call), and a call nested inside a
# ``for`` body is not detected — the module simply never loads and the tools
# silently do not exist.
registry.register(
    name="pmo_repo_read",
    toolset="pmo-collab",
    schema=_REPO_READ_SCHEMA,
    handler=_handle_repo_read,
    check_fn=_check_pmo_pm,
    emoji="read",
)

registry.register(
    name="pmo_repo_search",
    toolset="pmo-collab",
    schema=_REPO_SEARCH_SCHEMA,
    handler=_handle_repo_search,
    check_fn=_check_pmo_pm,
    emoji="search",
)

registry.register(
    name="pmo_repo_status",
    toolset="pmo-collab",
    schema=_REPO_STATUS_SCHEMA,
    handler=_handle_repo_status,
    check_fn=_check_pmo_pm,
    emoji="status",
)

registry.register(
    name="pmo_context",
    toolset="pmo-collab",
    schema=_CONTEXT_SCHEMA,
    handler=_handle_context,
    check_fn=_check_pmo_pm,
    emoji="context",
)

registry.register(
    name="pmo_plan",
    toolset="pmo-collab",
    schema=_PLAN_SCHEMA,
    handler=_handle_plan,
    check_fn=_check_pmo_pm,
    emoji="plan",
)

registry.register(
    name="pmo_consult",
    toolset="pmo-collab",
    schema=_CONSULT_SCHEMA,
    handler=_handle_consult,
    check_fn=_check_pmo_pm,
    emoji="chat",
)

registry.register(
    name="pmo_peer_pm",
    toolset="pmo-collab",
    schema=_PEER_PM_SCHEMA,
    handler=_handle_peer_pm,
    check_fn=_check_pmo_pm,
    emoji="peers",
)

registry.register(
    name="pmo_create_agent",
    toolset="pmo-collab",
    schema=_CREATE_AGENT_SCHEMA,
    handler=_handle_create_agent,
    check_fn=_check_pmo_pm,
    emoji="agent",
)

registry.register(
    name="pmo_request_details",
    toolset="pmo-collab",
    schema=_REQUEST_DETAILS_SCHEMA,
    handler=_handle_request_details,
    check_fn=_check_pmo_pm,
    emoji="question",
)

registry.register(
    name="pmo_request_ticket_confirmation",
    toolset="pmo-collab",
    schema=_REQUEST_CONFIRMATION_SCHEMA,
    handler=_handle_request_ticket_confirmation,
    check_fn=_check_pmo_pm,
    emoji="approval",
)

registry.register(
    name="pmo_create_ticket",
    toolset="pmo-collab",
    schema=_CREATE_TICKET_SCHEMA,
    handler=_handle_create_ticket,
    check_fn=_check_pmo_pm,
    emoji="ticket",
)

registry.register(
    name="pmo_ask_human",
    toolset="pmo-collab",
    schema=_ASK_HUMAN_SCHEMA,
    handler=_handle_ask_human,
    check_fn=_check_pmo_pm,
    emoji="human",
)

registry.register(
    name="pmo_handles",
    toolset="pmo-collab",
    schema=_HANDLES_SCHEMA,
    handler=_handle_handles,
    check_fn=_check_pmo_collab,
    emoji="🤝",
)

registry.register(
    name="pmo_ask",
    toolset="pmo-collab",
    schema=_ASK_SCHEMA,
    handler=_handle_ask,
    check_fn=_check_pmo_collab,
    emoji="🤝",
)

registry.register(
    name="pmo_answer",
    toolset="pmo-collab",
    schema=_ANSWER_SCHEMA,
    handler=_handle_answer,
    check_fn=_check_pmo_collab,
    emoji="answer",
)

registry.register(
    name="pmo_transfer",
    toolset="pmo-collab",
    schema=_TRANSFER_SCHEMA,
    handler=_handle_transfer,
    check_fn=_check_pmo_collab,
    emoji="🤝",
)

registry.register(
    name="pmo_escalate",
    toolset="pmo-collab",
    schema=_ESCALATE_SCHEMA,
    handler=_handle_escalate,
    check_fn=_check_pmo_collab,
    emoji="🤝",
)
