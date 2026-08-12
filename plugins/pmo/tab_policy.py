"""Role-to-tab policy for the Datansh Hermes wrapper.

This is deliberately a PMO-owned policy seam.  It does not edit Hermes' core
navigation or duplicate the core pages; the wrapper uses the result to reduce
navigation noise and redirect a signed-in user away from tabs outside their
project role.  The underlying Hermes routes remain the upstream surface and
keep their normal authentication.
"""

from __future__ import annotations

import re
from typing import Final

from plugins.pmo.access_policy import Principal


TAB_ROLES: Final[tuple[str, ...]] = (
    "manager",
    "backend-developer",
    "client",
    "ceo",
    "cfo",
    "qa",
    "frontend-developer",
    "hr",
    "data-analytics",
    "maintainer",
    "admin",
)

# The paths are the existing Hermes dashboard routes.  PMO only owns this
# allowlist and never copies the route implementation, which keeps upstream
# syncs conflict-light.
ALL_TABS: Final[frozenset[str]] = frozenset(
    {
        "/chat",
        "/sessions",
        "/files",
        "/analytics",
        "/models",
        "/logs",
        "/cron",
        "/skills",
        "/plugins",
        "/mcp",
        "/channels",
        "/webhooks",
        "/pairing",
        "/profiles",
        "/config",
        "/env",
        "/system",
        "/docs",
        # Keep the copied/inherited Kanban available alongside PM-OS. This is
        # where the shared dashboard project context is carried into the
        # original board rather than treating it as a removed Hermes feature.
        "/kanban",
        "/pmo",
    }
)

ROLE_TABS: Final[dict[str, frozenset[str]]] = {
    "manager": frozenset({"/chat", "/sessions", "/files", "/logs", "/skills", "/kanban", "/pmo"}),
    "backend-developer": frozenset(
        {"/chat", "/sessions", "/files", "/models", "/logs", "/skills", "/mcp", "/kanban", "/pmo"}
    ),
    "client": frozenset({"/chat", "/files", "/pmo"}),
    # CEO is the global PMO administrator: every project and every existing
    # Hermes tab remains available to the identity, subject to normal Hermes
    # authentication and host safety gates.
    "ceo": ALL_TABS,
    "cfo": frozenset({"/chat", "/sessions", "/files", "/analytics", "/kanban", "/pmo"}),
    "qa": frozenset({"/chat", "/sessions", "/files", "/logs", "/skills", "/kanban", "/pmo"}),
    "frontend-developer": frozenset(
        {"/chat", "/sessions", "/files", "/models", "/logs", "/skills", "/kanban", "/pmo"}
    ),
    "hr": frozenset({"/chat", "/sessions", "/files", "/kanban", "/pmo"}),
    "data-analytics": frozenset(
        {"/chat", "/sessions", "/files", "/analytics", "/models", "/logs", "/kanban", "/pmo"}
    ),
    "maintainer": frozenset(
        {
            "/chat",
            "/sessions",
            "/files",
            "/models",
            "/logs",
            "/cron",
            "/skills",
            "/mcp",
            "/channels",
            "/webhooks",
            "/kanban",
            "/pmo",
        }
    ),
    "admin": ALL_TABS,
}


def _normalized(value: str) -> str:
    return "".join(char for char in value.casefold() if char.isalnum() or char == "-")


def _principal_hint(principal: Principal) -> str:
    """Return only the identity's local-part, without substring elevation."""

    value = principal.id.rsplit("/", 1)[-1].rsplit(":", 1)[-1]
    return value.split("@", 1)[0].casefold()


def _matches_role_hint(role: str, hint: str) -> bool:
    normalized_role = _normalized(role)
    normalized_hint = _normalized(hint)
    if normalized_role == normalized_hint:
        return True
    tokens = {_normalized(token) for token in re.split(r"[^a-z0-9]+", hint) if token}
    return normalized_role in tokens


def resolve_tab_role(
    principal: Principal,
    *,
    project_role: str | None,
    org_rank: int | None,
) -> str:
    """Resolve a stable wrapper persona without adding user-level config.

    Explicit role words in a human principal (``human:cfo@…``) win first so
    deployments can use normal identity claims/email aliases.  Project PM and
    membership roles are safe fallbacks for existing policies that predate the
    wrapper persona list.  Agents are kept on the manager/developer surface,
    never elevated to an administrative human role.
    """

    hint = _principal_hint(principal)
    aliases = {
        "backenddeveloper": "backend-developer",
        "frontenddeveloper": "frontend-developer",
        "dataanalytics": "data-analytics",
    }
    for role in TAB_ROLES:
        if _matches_role_hint(role, hint):
            return aliases.get(_normalized(role), role)
    if principal.kind == "agent":
        if any(token in hint for token in ("backend", "api", "server")):
            return "backend-developer"
        if any(token in hint for token in ("frontend", "ui", "web")):
            return "frontend-developer"
        if "qa" in hint or "test" in hint:
            return "qa"
        return "manager" if project_role == "pm" else "maintainer"
    if org_rank is not None and org_rank >= 100:
        return "ceo"
    if org_rank is not None and org_rank >= 70:
        return "cfo"
    if project_role == "project_admin":
        return "admin"
    if project_role == "pm":
        return "manager"
    if project_role == "viewer":
        return "client"
    return "manager"


def policy_for(
    principal: Principal,
    *,
    project_role: str | None,
    org_rank: int | None,
) -> dict[str, object]:
    role = resolve_tab_role(principal, project_role=project_role, org_rank=org_rank)
    return {
        "role": role,
        "roles": list(TAB_ROLES),
        "allowed_paths": sorted(ROLE_TABS[role]),
        "all_paths": sorted(ALL_TABS),
        "project_role": project_role,
        "org_rank": org_rank,
    }
