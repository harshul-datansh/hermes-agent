"""Create or reuse a Hermes project and its PM-OS Kanban board.

This is intentionally a small edge utility, not a second PM-OS runtime.  It
binds the existing ``projects_db`` and ``kanban_db`` records so the normal
Hermes dispatcher, sessions, memory, skills, and approvals remain in charge.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from hermes_cli import kanban_db, projects_db


@dataclass(frozen=True)
class BootstrapResult:
    """The existing Hermes records selected for a PM-OS project."""

    project_id: str
    project_slug: str
    board_slug: str
    workspace: Path
    config_path: Path
    access_path: Path
    created_project: bool
    created_board: bool
    created_config: bool
    created_access: bool
    warnings: tuple[str, ...] = ()
    created_profiles: tuple[str, ...] = ()


def _config_writer():
    """Import the adjacent plugin module in package and script execution modes."""

    try:
        from plugins.pmo.project_scope import write_project_config_template
    except ModuleNotFoundError as exc:  # ``python plugins/pmo/bootstrap.py``
        if exc.name not in {"plugins", "plugins.pmo", "plugins.pmo.project_scope"}:
            raise
        from project_scope import write_project_config_template
    return write_project_config_template


def _access_writer():
    """Resolve the finished project binding and create its access projection."""

    try:
        from plugins.pmo.access_policy import write_access_policy_template
        from plugins.pmo.project_scope import resolve
    except ModuleNotFoundError as exc:  # ``python plugins/pmo/bootstrap.py``
        if exc.name not in {
            "plugins",
            "plugins.pmo",
            "plugins.pmo.access_policy",
            "plugins.pmo.project_scope",
        }:
            raise
        from access_policy import write_access_policy_template
        from project_scope import resolve
    return resolve, write_access_policy_template


def _folder_scope_checker():
    """Import the project-root overlap preflight in both execution modes."""

    try:
        from plugins.pmo.project_scope import assert_project_folder_disjoint
    except ModuleNotFoundError as exc:  # ``python plugins/pmo/bootstrap.py``
        if exc.name not in {"plugins", "plugins.pmo", "plugins.pmo.project_scope"}:
            raise
        from project_scope import assert_project_folder_disjoint
    return assert_project_folder_disjoint


def _conversation_provisioner():
    """Load the native PMO conversation bootstrap without owning a new store."""

    try:
        from plugins.platforms.pmo.adapter import ensure_project_conversations
    except ModuleNotFoundError as exc:  # ``python plugins/pmo/bootstrap.py``
        if exc.name not in {
            "plugins",
            "plugins.platforms",
            "plugins.platforms.pmo",
            "plugins.platforms.pmo.adapter",
        }:
            raise
        from platforms.pmo.adapter import ensure_project_conversations
    return ensure_project_conversations


def _ensure_project_profiles_and_routes(scope, conversations) -> tuple[str, ...]:
    """Provision dedicated runtimes and merge their native PMO routes.

    Project config remains the authority for ownership. ``resolve`` has
    already rejected a PM profile used by another active project before this
    helper mutates the user-level gateway config.
    """

    from hermes_cli import profiles
    from hermes_cli.config import load_config, save_config

    created: list[str] = []
    pm_profile = conversations.pm_profile
    client_profile = conversations.client_profile
    if not profiles.profile_exists(pm_profile):
        profiles.create_profile(
            pm_profile,
            clone_from="default",
            clone_config=True,
            description=f"Project manager for {scope.name}",
        )
        created.append(pm_profile)
    if not profiles.profile_exists(client_profile):
        profiles.create_profile(
            client_profile,
            clone_from=pm_profile,
            clone_config=True,
            description=f"Restricted client intake for {scope.name}",
        )
        created.append(client_profile)

    # A PM profile is orchestration-only: reconcile it at bootstrap time so a
    # newly created project and an idempotent re-run both get the managed PM
    # charter and its leaf collaboration toolset.  Do this after cloning the
    # client profile so the client route does not inherit PM-only controls.
    from plugins.pmo import agents as pmo_agents

    pmo_agents.reconcile_profiles(scope)

    config = load_config() or {}
    if not isinstance(config, dict):
        raise ValueError("Hermes config root must be a mapping")
    gateway = config.setdefault("gateway", {})
    if not isinstance(gateway, dict):
        gateway = {}
        config["gateway"] = gateway
    platforms_config = config.setdefault("platforms", {})
    if not isinstance(platforms_config, dict):
        platforms_config = {}
        config["platforms"] = platforms_config
    pmo_config = platforms_config.setdefault("pmo", {})
    if not isinstance(pmo_config, dict):
        pmo_config = {}
        platforms_config["pmo"] = pmo_config
    pmo_config.setdefault("enabled", True)
    pmo_config.setdefault("home_channel", {
        "platform": "pmo",
        "chat_id": conversations.board_slug,
        "thread_id": conversations.founders_office_thread_id,
        "name": "Founder's Office",
    })

    # Respect GatewayConfig's top-level-precedence rule. If an installation
    # already owns top-level settings, extend those; otherwise use the nested
    # gateway form produced by `hermes config set gateway.*`.
    if "multiplex_profiles" in config:
        config["multiplex_profiles"] = True
    else:
        gateway["multiplex_profiles"] = True
    routes_owner = config if isinstance(config.get("profile_routes"), list) else gateway
    existing = routes_owner.get("profile_routes")
    routes = list(existing) if isinstance(existing, list) else []
    additions = conversations.config_routes()
    from plugins.platforms.pmo.adapter import list_project_conversations

    routed_threads = {item["thread_id"] for item in additions}
    for conversation in list_project_conversations(scope):
        if conversation.thread_id in routed_threads:
            continue
        additions.append(
            {
                "name": f"pmo-{scope.board_slug}-founders-{conversation.thread_id[:12]}",
                "platform": "pmo",
                "chat_id": scope.board_slug,
                "thread_id": conversation.thread_id,
                "profile": pm_profile,
                "enabled": True,
            }
        )
    routes = [
        item
        for item in routes
        if not (
            isinstance(item, dict)
            and item.get("platform") == "pmo"
            and item.get("chat_id") == scope.board_slug
        )
    ]
    routes.extend(additions)
    routes_owner["profile_routes"] = routes
    save_config(config)
    return tuple(created)


def grant_project_admin(scope, principal: str, *, rank: int | None = None) -> str:
    """Add a human ``project_admin`` to a project's own access policy.

    Bootstrap otherwise produces a project no human can enter. The template
    policy names only ``dashboard:local-operator`` — the loopback operator
    identity — so once the dashboard requires real logins there is no member,
    no way to reach the Access screen (it needs ``member.write`` *on that
    project*), and no CLI to grant one. The project exists and is unreachable.

    Idempotent: re-granting updates the role rather than duplicating the
    member, so re-running bootstrap is safe.
    """
    from plugins.pmo.access_policy import (
        AccessPolicy,
        MemberRule,
        OrgRankRule,
        load_access_policy,
        write_access_policy,
    )
    from plugins.pmo.credentials import normalize_human_principal

    canonical = normalize_human_principal(principal)
    if not canonical.startswith("human:") or "@" not in canonical[6:]:
        raise ValueError(
            f"project admin must be a human email principal, got {principal!r}"
        )

    policy = load_access_policy(scope)
    members = [m for m in policy.members if m.principal != canonical]
    members.append(MemberRule(principal=canonical, role="project_admin"))
    ranks = list(policy.org_ranks)
    if rank is not None:
        ranks = [r for r in ranks if r.principal != canonical]
        ranks.append(OrgRankRule(principal=canonical, rank=rank))
    write_access_policy(
        scope,
        AccessPolicy(
            version=policy.version,
            members=tuple(members),
            org_ranks=tuple(ranks),
        ),
    )
    return canonical


def bootstrap_project(
    *,
    slug: str,
    name: str,
    workspace: str | Path,
    board_slug: str | None = None,
) -> BootstrapResult:
    """Idempotently bind a project slug, workspace, and explicit board.

    An existing project may be reused only when it has the same primary
    workspace and board slug. When omitted, ``board_slug`` defaults to the
    normalized project slug. Failing rather than silently retargeting either
    record prevents a command for one project from changing another project's
    dispatcher/worktree binding.
    """

    normalized_slug = projects_db.normalize_slug(slug)
    normalized_board = projects_db.normalize_slug(board_slug or normalized_slug)
    display_name = str(name).strip()
    if not display_name:
        raise ValueError("project name must not be empty")

    resolved_workspace = Path(workspace).expanduser().resolve()
    if not resolved_workspace.is_dir():
        raise ValueError(f"workspace must be an existing directory: {resolved_workspace}")

    with projects_db.connect_closing() as conn:
        project = projects_db.get_project(conn, normalized_slug)
        created_project = project is None
        _folder_scope_checker()(
            resolved_workspace,
            project_slug=normalized_slug,
            exclude_project_id=project.id if project is not None else None,
        )
        if project is None:
            project_id = projects_db.create_project(
                conn,
                name=display_name,
                slug=normalized_slug,
                primary_path=str(resolved_workspace),
                board_slug=normalized_board,
            )
        else:
            project_id = project.id
            existing_workspace = Path(project.primary_path).expanduser().resolve() if project.primary_path else None
            if existing_workspace != resolved_workspace:
                raise ValueError(
                    f"project '{normalized_slug}' already uses workspace "
                    f"{existing_workspace}; refusing to retarget it"
                )
            if project.board_slug not in (None, normalized_board):
                raise ValueError(
                    f"project '{normalized_slug}' already uses board "
                    f"'{project.board_slug}'; refusing to retarget it"
                )
            if project.board_slug is None:
                projects_db.update_project(conn, project_id, board_slug=normalized_board)

    board_metadata = kanban_db.read_board_metadata(normalized_board)
    existing_board_project_id = board_metadata.get("project_id")
    if existing_board_project_id and existing_board_project_id != project_id:
        raise ValueError(
            f"board '{normalized_board}' is already bound to project "
            f"'{existing_board_project_id}'; refusing to rebind it"
        )

    board_db_path = kanban_db.kanban_db_path(board=normalized_board)
    created_board = not board_db_path.exists()
    kanban_db.create_board(
        normalized_board,
        name=display_name,
        default_workdir=str(resolved_workspace),
        project_id=project_id,
    )
    config_path, created_config = _config_writer()(
        workspace=resolved_workspace,
        slug=normalized_slug,
        name=display_name,
    )
    resolve_scope, write_access = _access_writer()
    scope = resolve_scope(project_id, board_slug=normalized_board)
    access_path, created_access = write_access(scope)
    # Every project needs a durable Founder\'s Office thread from day one.
    # These are native blocked Kanban cards, so this only adds the PMO shell
    # around Hermes\' existing task comments and gateway routing. The helper
    # is idempotent and therefore also repairs projects bootstrapped by older
    # releases without duplicating any conversation.
    conversations = _conversation_provisioner()(scope)
    created_profiles = _ensure_project_profiles_and_routes(scope, conversations)
    try:
        from plugins.pmo import cost
    except ModuleNotFoundError as exc:  # ``python plugins/pmo/bootstrap.py``
        if exc.name not in {"plugins", "plugins.pmo", "plugins.pmo.cost"}:
            raise
        import cost
    warnings = cost.expensive_model_warnings(scope.config)
    return BootstrapResult(
        project_id=project_id,
        project_slug=normalized_slug,
        board_slug=normalized_board,
        workspace=resolved_workspace,
        config_path=config_path,
        access_path=access_path,
        created_project=created_project,
        created_board=created_board,
        created_config=created_config,
        created_access=created_access,
        warnings=warnings,
        created_profiles=created_profiles,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slug", required=True, help="Project and board slug")
    parser.add_argument("--name", required=True, help="Human-readable project name")
    parser.add_argument("--workspace", required=True, help="Existing project directory")
    parser.add_argument(
        "--board",
        help="Explicit board slug (defaults to the normalized project slug)",
    )
    parser.add_argument(
        "--admin",
        help=(
            "Email of the first human project_admin. Without this the project "
            "has no human member and cannot be opened from the dashboard."
        ),
    )
    parser.add_argument(
        "--admin-rank",
        type=int,
        help="Optional org rank for --admin (100 grants approval authority)",
    )
    parser.add_argument(
        "--agents",
        metavar="ROLES",
        help=(
            "Comma-separated agent roles to provision as <role>-<slug> profiles, "
            "e.g. --agents dev,qa. Omit to bootstrap with an orchestrator only "
            "and run `hermes pmo agents provision` later."
        ),
    )
    args = parser.parse_args(argv)

    result = bootstrap_project(
        slug=args.slug,
        name=args.name,
        workspace=args.workspace,
        board_slug=args.board,
    )
    granted = None
    if args.admin:
        from plugins.pmo.project_scope import resolve as _resolve

        granted = grant_project_admin(
            _resolve(result.project_slug, board_slug=result.board_slug),
            args.admin,
            rank=args.admin_rank,
        )
    provisioned = []
    if args.agents:
        from plugins.pmo import agents as agents_mod

        scope = agents_mod.scope_for(result.project_slug)
        provisioned = agents_mod.provision(
            scope,
            roles=[r for r in str(args.agents).split(",") if r.strip()],
        )
        agents_mod.write_roster(scope, provisioned)
    action = "created" if result.created_project else "reused"
    board_action = "created" if result.created_board else "reused"
    config_action = "created" if result.created_config else "reused"
    access_action = "created" if result.created_access else "reused"
    print(
        f"{action} project '{result.project_slug}' ({result.project_id}); "
        f"{board_action} PM-OS board '{result.board_slug}'; "
        f"{config_action} {result.config_path}; "
        f"{access_action} {result.access_path} at {result.workspace}"
    )
    for warning in result.warnings:
        print(warning)
    if provisioned:
        print(f"provisioned {len(provisioned)} agent profile(s):")
        for agent in provisioned:
            mark = "created" if agent.profile_created else "reused"
            print(f"  @{agent.handle:<10} {agent.profile:<26} ({mark})")
        print(
            "  next: give this project its own provider account with — "
            f"hermes pmo auth login --project {result.project_slug} "
            "--provider openai-codex"
        )
    if granted:
        rank_note = f" with org rank {args.admin_rank}" if args.admin_rank is not None else ""
        print(f"granted project_admin to {granted}{rank_note}")
        print(
            "  next: enrol a password so this human can sign in — "
            f"hermes pmo setup password --project {result.project_slug} "
            f"--principal {granted}"
        )
    else:
        print(
            "WARNING: this project has no human member, so it cannot be opened "
            "from the dashboard. Re-run with --admin <email>, or add a member to "
            f"{result.access_path}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
