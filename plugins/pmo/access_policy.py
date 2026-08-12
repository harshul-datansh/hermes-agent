"""Project authorization for the additive Datansh PM-OS surface.

Hermes remains the identity provider: interactive callers arrive as the
dashboard ``Session`` already attached to ``request.state`` and machine
callers arrive as its existing ``TokenPrincipal``.  This module only answers
the narrower PM question: what may that authenticated principal do in one
project?

Policy is version-controlled beside the project at ``.datansh/access.yaml``.
This module does not own credentials, sessions, user preferences, database tables,
or replacement agent/tool runtimes; the optional Datansh password provider and its
project-local hash store live in ``plugins/pmo/credentials.py``.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import yaml
from agent.redact import redact_sensitive_text
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from starlette.requests import HTTPConnection

from hermes_cli import kanban_db, projects_db
from plugins.pmo.project_context import state_directory
from plugins.pmo.project_scope import ProjectScope, resolve


log = logging.getLogger(__name__)

LOCAL_OPERATOR = "dashboard:local-operator"
ACCESS_FILENAME = "access.yaml"
AUDIT_FILENAME = "access-audit.jsonl"

PROJECT_ROLES = frozenset({"viewer", "contributor", "pm", "project_admin"})

CAPABILITIES: dict[str, frozenset[str]] = {
    "project.read": PROJECT_ROLES,
    "task.read": PROJECT_ROLES,
    "comment.write": PROJECT_ROLES,
    "task.write": frozenset({"contributor", "pm", "project_admin"}),
    "task.transition": frozenset({"contributor", "pm", "project_admin"}),
    "task.delete": frozenset({"pm", "project_admin"}),
    "board.dispatch": frozenset({"pm", "project_admin"}),
    "agent.manage": frozenset({"pm", "project_admin"}),
    "config.read": PROJECT_ROLES,
    "config.write": frozenset({"pm", "project_admin"}),
    "member.read": frozenset({"pm", "project_admin"}),
    "member.write": frozenset({"project_admin"}),
    "thread.read": PROJECT_ROLES,
    "thread.post": frozenset({"contributor", "pm", "project_admin"}),
    "approval.request": frozenset({"contributor", "pm", "project_admin"}),
    # Human work is still a native Kanban task, but only a project manager or
    # project administrator may create/delegate it from the PMO surface.
    "human_task.delegate": frozenset({"pm", "project_admin"}),
}

RANK_ACTIONS: dict[str, int] = {
    "approval.decide": 50,
    "approval.escalate": 50,
    "org.manage": 100,
}

MEMBERSHIP_MANAGE_RANK = 70
MEMBERSHIP_ACTIONS = frozenset({"member.read", "member.write"})
FOUNDER_CHAT_RANK = 70
FOUNDER_CHAT_ACTIONS = frozenset({"thread.read", "thread.post"})


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class MemberRule(_StrictModel):
    principal: str = Field(min_length=1, max_length=200)
    role: Literal["viewer", "contributor", "pm", "project_admin"]

    @field_validator("principal")
    @classmethod
    def human_only(cls, value: str) -> str:
        value = value.strip()
        if value != LOCAL_OPERATOR and not value.startswith("human:"):
            raise ValueError("project members must be human principals")
        return value


class OrgRankRule(_StrictModel):
    principal: str = Field(min_length=1, max_length=200)
    rank: int = Field(ge=0, le=100)

    @field_validator("principal")
    @classmethod
    def human_only(cls, value: str) -> str:
        value = value.strip()
        if value != LOCAL_OPERATOR and not value.startswith("human:"):
            raise ValueError("org ranks may only be assigned to humans")
        return value


class AccessPolicy(_StrictModel):
    version: Literal[1]
    members: tuple[MemberRule, ...] = ()
    org_ranks: tuple[OrgRankRule, ...] = ()

    @field_validator("members")
    @classmethod
    def unique_members(cls, value: tuple[MemberRule, ...]) -> tuple[MemberRule, ...]:
        principals = [item.principal for item in value]
        if len(principals) != len(set(principals)):
            raise ValueError("duplicate project member principal")
        return value

    @field_validator("org_ranks")
    @classmethod
    def unique_ranks(cls, value: tuple[OrgRankRule, ...]) -> tuple[OrgRankRule, ...]:
        principals = [item.principal for item in value]
        if len(principals) != len(set(principals)):
            raise ValueError("duplicate org-rank principal")
        return value


@dataclass(frozen=True)
class Principal:
    id: str
    kind: Literal["human", "agent", "service"]


@dataclass(frozen=True)
class AccessDecision:
    principal: str
    action: str
    project_id: str
    allowed: bool
    reason: str
    project_role: str | None = None
    org_rank: int | None = None


class AccessPolicyError(ValueError):
    """Policy file is absent or invalid; authorization must fail closed."""


def access_policy_path(scope: ProjectScope) -> Path:
    """Return the sole project policy path (never a user/profile path)."""

    return state_directory(scope.primary_path) / ACCESS_FILENAME


def load_access_policy(scope: ProjectScope) -> AccessPolicy:
    path = access_policy_path(scope)
    if not path.is_file() or path.is_symlink():
        raise AccessPolicyError(f"project access policy does not exist: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        return AccessPolicy.model_validate(raw)
    except (OSError, UnicodeError, yaml.YAMLError, ValidationError) as exc:
        raise AccessPolicyError(f"invalid project access policy: {path}") from exc


def write_access_policy_template(
    scope: ProjectScope,
    *,
    owner_principal: str = LOCAL_OPERATOR,
) -> tuple[Path, bool]:
    """Create a deny-by-default policy with one explicit project admin.

    Existing policy is never overwritten.  Agent entries are deliberately not
    accepted here: PM/worker roles are derived structurally from project.yaml.
    """

    policy = AccessPolicy(
        version=1,
        members=(MemberRule(principal=owner_principal, role="project_admin"),),
        org_ranks=(OrgRankRule(principal=owner_principal, rank=100),),
    )
    path = access_policy_path(scope)
    if path.exists():
        load_access_policy(scope)
        return path, False
    payload = policy.model_dump(mode="json")
    text = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)
    try:
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
    except FileExistsError:
        load_access_policy(scope)
        return path, False
    return path, True


def write_access_policy(scope: ProjectScope, policy: AccessPolicy) -> Path:
    """Atomically persist a validated project policy.

    The file remains project-owned and versionable.  No user-level config or
    alternate policy store is introduced, and a symlink is never replaced.
    """

    path = access_policy_path(scope)
    if path.is_symlink():
        raise AccessPolicyError(f"refusing symlinked access policy: {path}")
    validated = AccessPolicy.model_validate(policy.model_dump(mode="python"))
    if not any(item.role == "project_admin" for item in validated.members):
        raise AccessPolicyError("project access policy must retain a project_admin")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent), text=True
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(
                yaml.safe_dump(
                    validated.model_dump(mode="json"),
                    sort_keys=False,
                    allow_unicode=True,
                )
            )
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
    return path


def _project_role(principal: Principal, scope: ProjectScope, policy: AccessPolicy) -> str | None:
    if principal.kind == "agent":
        prefix = f"agent:{scope.slug}/"
        if not principal.id.startswith(prefix):
            return None
        handle = principal.id[len(prefix):]
        if handle == "pm":
            return "pm"
        if any(agent.handle == handle for agent in scope.config.agents):
            return "contributor"
        return None
    if principal.kind != "human":
        return None
    for member in policy.members:
        if member.principal == principal.id:
            return member.role
    return None


def _org_rank(principal: Principal, policy: AccessPolicy) -> int | None:
    # Structural guarantee: no token, config row, or mistaken role mapping can
    # turn an agent/service principal into a human approver.
    if principal.kind != "human":
        return None
    for rule in policy.org_ranks:
        if rule.principal == principal.id:
            return rule.rank
    return None


GLOBAL_ADMIN_RANK = 100


def global_org_rank(principal: Principal) -> int | None:
    """Return the highest human org rank across active project policies.

    Rank 100 is the CEO/global-admin tier.  It is intentionally derived from
    project-owned policy files rather than a user-level PMO table, while still
    allowing a CEO enrolled in one project to administer every project.
    """

    if principal.kind != "human":
        return None
    highest: int | None = None
    try:
        with projects_db.connect_closing() as connection:
            projects = projects_db.list_projects(connection, include_archived=False)
    except Exception:
        return None
    for project in projects:
        if not project.primary_path:
            continue
        try:
            path = state_directory(project.primary_path, create=False) / ACCESS_FILENAME
        except (OSError, ValueError):
            continue
        if path.is_symlink() or not path.is_file():
            continue
        try:
            policy = AccessPolicy.model_validate(
                yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            )
        except (OSError, UnicodeError, yaml.YAMLError, ValidationError):
            continue
        rank = _org_rank(principal, policy)
        if rank is not None and (highest is None or rank > highest):
            highest = rank
            if highest >= GLOBAL_ADMIN_RANK:
                return highest
    return highest


def has_global_admin(principal: Principal) -> bool:
    return (global_org_rank(principal) or 0) >= GLOBAL_ADMIN_RANK


def _audit(scope: ProjectScope, decision: AccessDecision) -> None:
    """Append a redacted decision to project-owned, versionable JSONL."""

    event = {
        "version": 1,
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "principal": decision.principal,
        "action": decision.action,
        "project_id": decision.project_id,
        "allowed": decision.allowed,
        "reason": decision.reason,
        "project_role": decision.project_role,
        "org_rank": decision.org_rank,
    }
    clean = {
        key: redact_sensitive_text(value, force=True) if isinstance(value, str) else value
        for key, value in event.items()
    }
    path = state_directory(scope.primary_path) / AUDIT_FILENAME
    if path.exists() and path.is_symlink():
        raise AccessPolicyError(f"refusing symlinked access audit: {path}")
    line = json.dumps(clean, sort_keys=True, ensure_ascii=False) + "\n"
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(line)


def evaluate(
    principal: Principal,
    action: str,
    scope: ProjectScope,
    *,
    policy: AccessPolicy | None = None,
    write_audit: bool = True,
) -> AccessDecision:
    """Evaluate one action. Unknown actions and unavailable policy deny."""

    try:
        selected = policy or load_access_policy(scope)
    except AccessPolicyError:
        decision = AccessDecision(
            principal.id, action, scope.project_id, False, "policy unavailable"
        )
        if write_audit:
            _audit(scope, decision)
        return decision

    if action in RANK_ACTIONS:
        rank = _org_rank(principal, selected)
        if policy is None:
            global_rank = global_org_rank(principal)
            if global_rank is not None and (rank is None or global_rank > rank):
                rank = global_rank
        required = RANK_ACTIONS[action]
        decision = AccessDecision(
            principal.id,
            action,
            scope.project_id,
            rank is not None and rank >= required,
            "org rank sufficient" if rank is not None and rank >= required else "org rank insufficient",
            org_rank=rank,
        )
    elif action in CAPABILITIES:
        role = _project_role(principal, scope, selected)
        rank = _org_rank(principal, selected)
        if policy is None:
            global_rank = global_org_rank(principal)
            if global_rank is not None and (rank is None or global_rank > rank):
                rank = global_rank
        global_admin = principal.kind == "human" and (rank or 0) >= GLOBAL_ADMIN_RANK
        membership_manager = (
            action in MEMBERSHIP_ACTIONS
            and principal.kind == "human"
            and (rank or 0) >= MEMBERSHIP_MANAGE_RANK
        )
        founder_officer = (
            action in FOUNDER_CHAT_ACTIONS
            and principal.kind == "human"
            and (rank or 0) >= FOUNDER_CHAT_RANK
        )
        allowed = (
            global_admin
            or membership_manager
            or founder_officer
            or role in CAPABILITIES[action]
        )
        decision = AccessDecision(
            principal.id,
            action,
            scope.project_id,
            allowed,
            (
                "founder officer chat access"
                if founder_officer
                else "project capability granted" if allowed else "project capability denied"
            ),
            project_role=role,
        )
    else:
        log.error("PM-OS denied unknown capability %r", action)
        decision = AccessDecision(
            principal.id, action, scope.project_id, False, "unknown action"
        )
    if write_audit:
        _audit(scope, decision)
    return decision


def can(
    principal: Principal,
    action: str,
    scope: ProjectScope,
    *,
    policy: AccessPolicy | None = None,
    write_audit: bool = True,
) -> bool:
    return evaluate(
        principal, action, scope, policy=policy, write_audit=write_audit
    ).allowed


def describe_principal(principal: Principal, scope: ProjectScope) -> dict[str, object]:
    """Expose the effective project role/rank for a dashboard actor."""

    policy = load_access_policy(scope)
    role = _project_role(principal, scope, policy)
    rank = _org_rank(principal, policy)
    global_rank = global_org_rank(principal)
    effective_rank = rank
    if global_rank is not None and (effective_rank is None or global_rank > effective_rank):
        effective_rank = global_rank
    if effective_rank is not None and effective_rank >= GLOBAL_ADMIN_RANK:
        role = "project_admin"
    rank = effective_rank
    capabilities = sorted(
        action
        for action in CAPABILITIES
        if role in CAPABILITIES[action]
    )
    capabilities.extend(
        action
        for action, required in RANK_ACTIONS.items()
        if rank is not None and rank >= required
    )
    if principal.kind == "human" and (rank or 0) >= MEMBERSHIP_MANAGE_RANK:
        capabilities.extend(MEMBERSHIP_ACTIONS)
    return {
        "principal": principal.id,
        "kind": principal.kind,
        "project_role": role,
        "org_rank": rank,
        "capabilities": sorted(set(capabilities)),
    }


def principal_from_connection(connection: HTTPConnection) -> Principal | None:
    session = getattr(connection.state, "session", None)
    if session is not None and getattr(session, "user_id", None):
        return Principal(f"human:{session.user_id}", "human")
    token = getattr(connection.state, "token_principal", None)
    token_id = str(getattr(token, "principal", "") or "").strip()
    if token_id:
        return Principal(token_id, "agent" if token_id.startswith("agent:") else "service")
    # Gated browser WebSockets carry the existing single-use Hermes ticket.
    # Consume it here so authorization has the human identity, then mark the
    # connection so the endpoint's native WS gate does not consume it twice.
    if connection.scope.get("type") == "websocket":
        ticket = connection.query_params.get("ticket", "")
        if ticket:
            try:
                from hermes_cli.dashboard_auth.ws_tickets import consume_ticket

                info = consume_ticket(ticket)
            except Exception:
                return None
            user_id = str(info.get("user_id") or "").strip()
            if not user_id:
                return None
            connection.state.pmo_ws_authenticated = True
            return Principal(f"human:{user_id}", "human")
    # Loopback Hermes uses its process session token rather than a user
    # session. It is still an existing authenticated identity, represented by
    # one stable structural principal. Never infer this in a bare test app.
    if hasattr(connection.app.state, "auth_required") and not connection.app.state.auth_required:
        return Principal(LOCAL_OPERATOR, "human")
    return None


def infer_route_action(method: str, path_template: str) -> str:
    """Classify every inherited PMO route at one inspectable choke point."""

    method = method.upper()
    path = path_template.removeprefix("/api/plugins/pmo") or "/"
    if method == "GET" or method == "WEBSOCKET":
        if path == "/config" or path.startswith("/orchestration") or path.startswith("/profiles"):
            return "config.read"
        return "task.read"
    if path.endswith("/comments"):
        return "comment.write"
    if any(marker in path for marker in ("/dispatch", "/reclaim", "/specify", "/reassign", "/decompose", "/terminate")):
        return "board.dispatch"
    if path.startswith("/profiles"):
        return "agent.manage"
    if path.startswith("/orchestration") or path.startswith("/boards") or "/home-subscribe/" in path:
        return "config.write"
    if method == "DELETE":
        return "task.delete"
    if method in {"POST", "PUT", "PATCH"}:
        return "task.write"
    return "__unknown_route_action__"


def _scope_for_connection(connection: HTTPConnection) -> ProjectScope:
    host_scope = getattr(
        getattr(connection, "state", None), "datansh_project_scope", None
    )
    requested_project = str(connection.query_params.get("project_id") or "").strip()
    board = connection.query_params.get("board")
    route_path = str(getattr(connection.scope.get("route"), "path", ""))
    if not board and "/boards/{slug}" in route_path:
        board = connection.path_params.get("slug")
    if host_scope is not None:
        # The authenticated host project is authoritative when a copied route
        # has no native board parameter (for example /profiles and
        # /model-options).  If both selectors exist they must agree; never
        # fall back to the process-wide current-board pointer.
        if board:
            normalized = kanban_db._normalize_board_slug(board)
            if normalized != host_scope.board_slug:
                raise AccessPolicyError("board does not match the selected project")
        if requested_project and requested_project not in {
            host_scope.project_id,
            host_scope.slug,
        }:
            raise AccessPolicyError("request does not match the selected project")
        return host_scope
    board = board or kanban_db.get_current_board()
    normalized = kanban_db._normalize_board_slug(board)
    if not normalized:
        raise AccessPolicyError("a project-bound board is required")
    metadata = kanban_db.read_board_metadata(normalized)
    project_id = str(metadata.get("project_id") or "").strip()
    if not project_id:
        raise AccessPolicyError(f"board '{normalized}' is not bound to a project")
    scope = resolve(project_id, board_slug=normalized)
    if requested_project and requested_project not in {
        scope.project_id,
        scope.slug,
    }:
        raise AccessPolicyError("board does not match the requested project")
    return scope


def require(action: str):
    """FastAPI dependency factory, marked for route-coverage introspection."""

    async def dependency(connection: HTTPConnection) -> Principal:
        principal = principal_from_connection(connection)
        if principal is None:
            raise HTTPException(status_code=401, detail="Unauthorized")
        try:
            scope = _scope_for_connection(connection)
        except Exception as exc:
            log.warning(
                "PMO project scope resolution denied for %s: %s",
                connection.url.path,
                exc,
            )
            raise HTTPException(status_code=403, detail="Project access denied") from exc
        decision = evaluate(principal, action, scope)
        if not decision.allowed:
            raise HTTPException(status_code=403, detail="Project access denied")
        return principal

    dependency._pmo_guard = True  # type: ignore[attr-defined]
    dependency._pmo_action = action  # type: ignore[attr-defined]
    return dependency


def require_identity():
    """Require an existing Hermes identity without selecting a project.

    This is intentionally narrower than project authorization.  It exists for
    the portfolio bootstrap route, which must discover the caller's authorized
    projects before the browser has a board selection.  The route still
    filters every returned project through :func:`evaluate`.
    """

    async def dependency(connection: HTTPConnection) -> Principal:
        principal = principal_from_connection(connection)
        if principal is None:
            raise HTTPException(status_code=401, detail="Unauthorized")
        return principal

    dependency._pmo_guard = True  # type: ignore[attr-defined]
    dependency._pmo_action = "identity-only"  # type: ignore[attr-defined]
    return dependency


def require_route_access():
    """Dynamic dependency used by the copied router without editing endpoints."""

    async def dependency(connection: HTTPConnection) -> Principal:
        route = connection.scope.get("route")
        template = str(getattr(route, "path", connection.url.path))
        method = "WEBSOCKET" if connection.scope.get("type") == "websocket" else str(connection.scope.get("method", ""))
        normalized = template.removeprefix("/api/plugins/pmo") or "/"
        identity_scoped_gets = {
            "/portfolio",
            "/boards",
            "/projects",
            "/access/portfolio",
            "/founders-office/folders",
            "/founders-office/global",
            "/github-app/status",
            "/github-app/callback",
        }
        identity_scoped_posts = {
            "/founders-office/global/messages",
            "/repositories/onboard",
        }
        onboarding_route = normalized == "/project-onboarding" or normalized.startswith(
            "/project-onboarding/"
        )
        administration_route = normalized == "/administration" or normalized.startswith(
            "/administration/"
        )
        global_founder_route = (
            normalized == "/founders-office/global/conversations"
            or normalized.startswith("/founders-office/global/conversations/")
        )
        if onboarding_route or administration_route or global_founder_route or (
            method.upper() == "GET" and normalized in identity_scoped_gets
        ) or (
            method.upper() == "POST" and normalized in identity_scoped_posts
        ):
            # Portfolio-wide endpoints cannot authorize against one arbitrary
            # current board. Their handlers enumerate the caller's visible
            # projects and evaluate read/write access for each project before
            # returning data or dispatching a message.
            principal = await require_identity()(connection)
            connection.state.pmo_access_authorized = True
            return principal
        action = infer_route_action(method, template)
        guarded = require(action)
        principal = await guarded(connection)
        connection.state.pmo_access_authorized = True
        return principal

    dependency._pmo_guard = True  # type: ignore[attr-defined]
    dependency._pmo_action = "route-classified"  # type: ignore[attr-defined]
    return dependency
