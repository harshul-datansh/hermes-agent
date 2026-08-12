"""Role-aware administration workspace for the Datansh PM-OS plugin.

This module deliberately keeps operational chat separate from Founder’s Office.
Founder’s Office is a trusted delivery-instruction channel for a project PM;
the administration workspace is an auditable operator record for people who
provision agents, adjust model routing, connect provider credentials, and
check project health.
"""

from __future__ import annotations

import json
import re
import secrets
import time
from pathlib import Path
from typing import Any

import yaml
from agent.redact import redact_sensitive_text
from hermes_constants import get_default_hermes_root, get_hermes_home
from plugins.pmo.project_context import state_directory


class AdministrationError(ValueError):
    """A requested administrator operation is not safe or not allowed."""


_GLOBAL_AGENT_PRINCIPAL = re.compile(r"^agent:global/[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def _global_configuration_path() -> Path:
    """Return the root-owned PMO settings path, including for worker profiles."""

    directory = get_default_hermes_root() / "pmo-admin-workspace"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "configuration.yaml"
    if path.is_symlink():
        raise AdministrationError("refusing to use a symlinked global configuration")
    return path


def _global_agent_access(raw: Any) -> list[str]:
    """Validate global configuration-agent identities without broadening RBAC.

    These are machine principals issued by Hermes' existing token flow. They
    are deliberately distinct from ``agent:<project>/<handle>``: registering a
    global configuration agent must never make it a member of a project.
    """

    if raw is None:
        return []
    if not isinstance(raw, (list, tuple)):
        raise AdministrationError("global configuration agents must be a list")
    result: list[str] = []
    seen: set[str] = set()
    for item in raw:
        principal = str(item or "").strip()
        if not _GLOBAL_AGENT_PRINCIPAL.fullmatch(principal):
            raise AdministrationError(
                "global configuration agents must use agent:global/<handle> identities"
            )
        key = principal.casefold()
        if key in seen:
            raise AdministrationError("global configuration agents must be unique")
        seen.add(key)
        result.append(principal)
    return result


def _read_global_configuration() -> dict[str, Any]:
    """Read the root-owned configuration once and validate its safe surface."""

    from plugins.pmo.project_scope import ModelsConfig, ProjectRuntimeConfig

    path = _global_configuration_path()
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise AdministrationError(f"cannot read global PMO configuration: {exc}") from exc
    if not isinstance(raw, dict):
        raise AdministrationError("global PMO configuration must be a mapping")
    try:
        models = ModelsConfig.model_validate(raw.get("models") or {})
        runtime = ProjectRuntimeConfig.model_validate(raw.get("runtime") or {})
    except ValueError as exc:
        raise AdministrationError(f"invalid global PMO configuration: {exc}") from exc
    access = raw.get("access") or {}
    if not isinstance(access, dict):
        raise AdministrationError("global PMO access configuration must be a mapping")
    return {
        "models": models.model_dump(mode="json"),
        "runtime": runtime.model_dump(mode="json"),
        "access": {"global_agents": _global_agent_access(access.get("global_agents"))},
    }


def global_configuration() -> dict[str, Any]:
    """Read validated global defaults without exposing unrelated Hermes config."""

    return _read_global_configuration()


def set_global_configuration(*, models: dict[str, Any], runtime: dict[str, Any]) -> dict[str, Any]:
    """Save defaults available to every PMO project; caller owns authorization."""

    from plugins.pmo.project_scope import ModelsConfig, ProjectRuntimeConfig

    try:
        clean_models = ModelsConfig.model_validate(models)
        clean_runtime = ProjectRuntimeConfig.model_validate(runtime)
    except ValueError as exc:
        raise AdministrationError(str(exc)) from exc
    current = _read_global_configuration()
    result = {
        "models": clean_models.model_dump(mode="json"),
        "runtime": clean_runtime.model_dump(mode="json"),
        # Configuration agents cannot promote themselves: only the separate
        # rank-100-only endpoint below may alter this allowlist.
        "access": current["access"],
    }
    path = _global_configuration_path()
    path.write_text(yaml.safe_dump(result, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return result


def set_global_configuration_agents(*, principals: list[str]) -> dict[str, Any]:
    """Set the machine-principal allowlist for global configuration edits.

    The dashboard route reserves this operation for a human global admin. It
    lives alongside the global defaults rather than in a project policy so no
    project-local agent can self-elevate into cross-project authority.
    """

    current = _read_global_configuration()
    current["access"] = {"global_agents": _global_agent_access(principals)}
    path = _global_configuration_path()
    path.write_text(
        yaml.safe_dump(current, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    return current


def can_manage_global_configuration(principal: Any) -> bool:
    """Allow a human global admin or an explicitly registered global agent."""

    from plugins.pmo.access_policy import has_global_admin

    if has_global_admin(principal):
        return True
    if getattr(principal, "kind", None) != "agent":
        return False
    try:
        allowed = global_configuration()["access"]["global_agents"]
    except AdministrationError:
        return False
    return str(getattr(principal, "id", "")).casefold() in {
        item.casefold() for item in allowed
    }


def effective_configuration(scope: Any) -> dict[str, dict[str, Any]]:
    """Compose root defaults with project additions, keeping item order stable."""

    global_config = global_configuration()
    project_models = scope.config.models.model_dump(mode="json")
    effective_models = {name: project_models.get(name) or value for name, value in global_config["models"].items()}
    for name, value in project_models.items():
        effective_models.setdefault(name, value)

    def merge(global_items: list[str], project_items: list[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for item in [*global_items, *project_items]:
            key = item.casefold()
            if key not in seen:
                seen.add(key)
                result.append(item)
        return result

    project_runtime = scope.config.runtime.model_dump(mode="json")
    effective_runtime = {name: merge(global_config["runtime"].get(name, []), project_runtime.get(name, [])) for name in ("skills", "plugins", "channels")}
    # The global-agent registry is authorization metadata, not project
    # configuration. Project operators see only the defaults they inherit;
    # they never discover machine principals that administer the global plane.
    visible_global = {
        "models": global_config["models"],
        "runtime": global_config["runtime"],
    }
    return {"global": visible_global, "project": {"models": project_models, "runtime": project_runtime}, "effective": {"models": effective_models, "runtime": effective_runtime}}


def permissions_for(principal: Any, scope: Any) -> dict[str, Any]:
    """Return project-scoped operator permissions for an authenticated human.

    Developers and maintainers may operate agents/configuration only in a
    project they can already read. Global administration remains tied to the
    existing rank-100 policy and is never inferred from an email label.
    """

    from plugins.pmo.access_policy import evaluate, has_global_admin
    from plugins.pmo.tab_policy import resolve_tab_role

    membership = evaluate(principal, "project.read", scope, write_audit=False)
    if not membership.allowed:
        return {
            "read": False,
            "manage_agents": False,
            "manage_config": False,
            "connect_provider": False,
            "health": False,
            "global": False,
            "role": "none",
        }
    rank = evaluate(principal, "approval.decide", scope, write_audit=False).org_rank
    role = resolve_tab_role(
        principal, project_role=membership.project_role, org_rank=rank
    )
    global_admin = bool(has_global_admin(principal))
    project_admin = membership.project_role == "project_admin"
    developer = role in {"backend-developer", "frontend-developer", "maintainer"}
    operator = global_admin or project_admin or developer
    return {
        "read": True,
        "manage_agents": operator,
        "manage_config": operator,
        "connect_provider": operator,
        "health": True,
        "global": global_admin,
        "role": role,
        "project_role": membership.project_role,
        "org_rank": rank,
    }


def health(scope: Any) -> dict[str, Any]:
    """Return bounded health facts without inspecting another project."""

    from hermes_cli import profiles as profiles_mod
    from plugins.pmo import provider_auth

    profiles = provider_auth.project_profiles(scope)
    return {
        "project_config": (scope.primary_path / ".datansh" / "project.yaml").is_file(),
        "profiles": [
            {
                "handle": handle,
                "profile": profile,
                "available": bool(profiles_mod.profile_exists(profile)),
            }
            for handle, profile in profiles
        ],
        "providers": provider_auth.status(scope),
    }


def _log_path(scope: Any | None) -> Path:
    if scope is None:
        root = get_hermes_home() / "pmo-admin-workspace"
        root.mkdir(parents=True, exist_ok=True)
        return root / "global.jsonl"
    return state_directory(scope.primary_path) / "admin-workspace.jsonl"


def messages(scope: Any | None, *, limit: int = 200) -> list[dict[str, Any]]:
    """Read a bounded, redacted local administration transcript."""

    path = _log_path(scope)
    if path.is_symlink() or not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            raw = json.loads(line)
            if isinstance(raw, dict) and isinstance(raw.get("body"), str):
                raw["body"] = redact_sensitive_text(raw["body"], force=True)
                rows.append(raw)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return []
    return rows[-max(1, min(int(limit), 500)) :]


def append_message(
    scope: Any | None,
    *,
    author: str,
    body: str,
    kind: str = "human",
    actions: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Append one redacted administration message to its scoped transcript."""

    clean_author = str(author or "").strip()
    clean_body = str(body or "").strip()
    if not clean_author or not clean_body:
        raise AdministrationError("author and message body are required")
    if len(clean_body) > 65_000:
        raise AdministrationError("message body exceeds 65,000 characters")
    row = {
        "id": "adm_" + secrets.token_urlsafe(12),
        "author": clean_author[:200],
        "body": redact_sensitive_text(clean_body, force=True),
        "kind": kind if kind in {"human", "assistant", "system"} else "system",
        "created_at": int(time.time()),
        "actions": actions or [],
    }
    path = _log_path(scope)
    if path.exists() and path.is_symlink():
        raise AdministrationError("refusing to write a symlinked administration transcript")
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return row


def suggestion(body: str, permissions: dict[str, Any], *, project_name: str | None) -> dict[str, Any]:
    """Create a safe, explicit next-step response for the operations chat."""

    text = str(body or "").casefold()
    project_label = project_name or "this workspace"
    actions: list[dict[str, str]] = []
    asked_codex = "codex" in text or "oauth" in text or "openai" in text
    asked_agents = "agent" in text or "developer" in text or "qa" in text
    asked_health = "health" in text or "status" in text or "gateway" in text
    asked_global = "onboard" in text or "access" in text or "iam" in text
    notes: list[str] = []
    if asked_agents:
        if permissions.get("manage_agents"):
            actions.append({"kind": "create_agent", "label": "Create project agent"})
            notes.append(f"create an isolated agent profile for {project_label}")
        else:
            notes.append("inspect agents only; agent changes require project operator access")
    if asked_codex:
        if permissions.get("connect_provider"):
            actions.append({"kind": "connect_codex", "label": "Connect OpenAI Codex"})
            notes.append("start a profile-scoped OpenAI Codex sign-in")
        else:
            notes.append("inspect provider health only; sign-in requires project operator access")
    if asked_health:
        actions.append({"kind": "refresh_health", "label": "Refresh project health"})
        notes.append("refresh bounded project health")
    if permissions.get("global") and asked_global:
        actions.append({"kind": "open_onboarding", "label": "Open project onboarding"})
        actions.append({"kind": "open_access", "label": "Open access management"})
        notes.append("open explicit global onboarding or IAM controls")
    if notes:
        reply = "I can " + "; ".join(notes) + ". Review each explicit action before it changes anything."
    else:
        reply = "I can help with project agents, model routing, OpenAI Codex sign-in, health, or—when you are a global administrator—onboarding and IAM. Choose a project first for any scoped change."
    return {"body": reply, "actions": actions}


__all__ = [
    "AdministrationError",
    "append_message",
    "effective_configuration",
    "global_configuration",
    "health",
    "messages",
    "permissions_for",
    "suggestion",
    "set_global_configuration",
]
