"""Project-specific agent provisioning for Datansh PM-OS.

A project's agents are ordinary Hermes profiles named ``<role>-<slug>``:
``pm-hedgi``, ``dev-hedgi``, ``qa-hedgi``. Nothing here invents a second
agent runtime — it creates the profiles the existing Kanban dispatcher
already spawns with ``hermes -p <assignee>`` and records them in the
project's own ``.datansh/project.yaml`` roster.

Why per-project profiles rather than one shared fleet: a Hermes profile is a
complete filesystem namespace (config, credentials, memory, sessions,
skills). Per-project profiles are therefore what make per-project *model
routing* and per-project *provider credentials* possible at all — see
``plugins/pmo/provider_auth.py``. It is also what keeps one client's memory
and API keys out of another's.
"""

from __future__ import annotations

import argparse
import re
import time
from dataclasses import dataclass
from typing import Sequence

import yaml

from plugins.pmo.project_scope import ProjectScope, resolve


# Role templates. ``handle`` is what people type after ``@``; ``profile`` is
# derived as ``<role>-<slug>`` and is what lands in ``tasks.assignee``.
@dataclass(frozen=True)
class RoleTemplate:
    role: str
    description: str
    toolset: str | None


ROLE_TEMPLATES: dict[str, RoleTemplate] = {
    "pm": RoleTemplate("pm", "Project manager / orchestrator", None),
    "dev": RoleTemplate("dev", "Engineer", "coding"),
    "qa": RoleTemplate("qa", "Reviewer", "review"),
    "ops": RoleTemplate("ops", "Operations", "coding"),
    "research": RoleTemplate("research", "Researcher", "read-web"),
    "design": RoleTemplate("design", "Designer", "read-web"),
    "data": RoleTemplate("data", "Data analyst", "coding"),
}

# Provisioned by default when ``--roles`` is not given. ``pm`` is excluded
# because bootstrap already owns the orchestrator profile.
DEFAULT_ROLES: tuple[str, ...] = ("dev", "qa")

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


class AgentProvisionError(ValueError):
    """Provisioning refused; the project roster is left untouched."""


def profile_for(role: str, slug: str) -> str:
    """``dev`` + ``hedgi`` -> ``dev-hedgi``.

    Deterministic so a profile lost from disk is re-creatable from the
    project config alone, and so a developer reading ``hermes profile list``
    can tell which project a profile belongs to.
    """
    clean_role = str(role or "").strip().lower()
    clean_slug = str(slug or "").strip().lower()
    if clean_role not in ROLE_TEMPLATES:
        raise AgentProvisionError(
            f"unknown role {role!r}; known roles: {', '.join(sorted(ROLE_TEMPLATES))}"
        )
    if not _SLUG_RE.match(clean_slug):
        raise AgentProvisionError(f"project slug is not profile-safe: {slug!r}")
    return f"{clean_role}-{clean_slug}"


def handle_for(role: str, index: int = 1, total: int = 1) -> str:
    """Mention handle for a role.

    A single agent of a role keeps the bare handle (``@dev``); several get a
    numbered suffix (``@dev-1``). Handles stay short because they are typed
    constantly — the *profile* carries the project name, the handle does not
    need to, since handles are already project-scoped.
    """
    base = str(role or "").strip().lower()
    return base if total <= 1 else f"{base}-{index}"


@dataclass(frozen=True)
class ProvisionedAgent:
    handle: str
    profile: str
    role: str
    toolset: str | None
    model: str | None
    profile_created: bool


def _create_profile(name: str, description: str) -> bool:
    """Create a Hermes profile if absent. Returns True when created.

    ``no_alias=True`` matters: alias wrapper scripts are written to
    ``~/.local/bin``, which is outside any profile's HERMES_HOME and would
    leak project profiles into the operator's shell.
    """
    from hermes_cli import profiles as profiles_mod

    if profiles_mod.profile_exists(name):
        return False
    profiles_mod.create_profile(
        name,
        no_alias=True,
        no_skills=False,
        description=description,
    )
    return True


# Toolset every PM-OS agent profile gets in addition to the CLI default, so
# ``pmo_ask`` / ``pmo_transfer`` / ``pmo_escalate`` are actually reachable.
# Registering the tools is not enough: a dispatched worker resolves its tools
# from its own profile's ``platform_toolsets.cli``, and a live worker proved
# the gap — it completed a ticket, wrote a good comment, and could mention
# nobody because the tools were not in its set.
PMO_COLLAB_TOOLSET = "pmo-collab"
_CLI_DEFAULT_TOOLSET = "hermes-cli"


def enable_collab_toolset(profile: str, *, project_manager: bool = False) -> bool:
    """Configure one PM-OS profile's collaboration capability.

    Workers retain their normal Hermes tools and gain project-local
    collaboration on both the CLI ticket dispatcher and the PMO conversation
    dispatcher. The project manager receives only the ``pmo-collab`` leaf
    set, which provides planning, consultation, delegation and read-only repo
    inspection without generic implementation tools.
    """
    import yaml as _yaml
    from hermes_cli import profiles as profiles_mod

    if not profiles_mod.profile_exists(profile):
        return False
    config_path = profiles_mod.get_profile_dir(profile) / "config.yaml"
    if config_path.is_file():
        data = _yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    else:
        data = {}
    platform_toolsets = data.setdefault("platform_toolsets", {})
    if not isinstance(platform_toolsets, dict):
        platform_toolsets = {}
        data["platform_toolsets"] = platform_toolsets
    current_cli = platform_toolsets.get("cli")
    current_cli = [str(item) for item in current_cli] if isinstance(current_cli, list) else []
    desired_cli = [PMO_COLLAB_TOOLSET, "no_mcp"] if project_manager else list(current_cli or [_CLI_DEFAULT_TOOLSET])
    if not project_manager and PMO_COLLAB_TOOLSET not in desired_cli:
        desired_cli.append(PMO_COLLAB_TOOLSET)

    # Founder’s Office runs through the PMO platform, not the CLI platform.
    # Leaving ``pmo`` unspecified means a consulted specialist loses the
    # collaboration capability exactly when it needs to reply in a Founder’s
    # Office sub-chat. PMs stay leaf-configured on both execution paths;
    # workers retain normal PMO tools plus the collaboration capability.
    if project_manager:
        current_pmo = platform_toolsets.get("pmo")
        current_pmo = [str(item) for item in current_pmo] if isinstance(current_pmo, list) else []
        agent_config = data.setdefault("agent", {})
        if not isinstance(agent_config, dict):
            agent_config = {}
            data["agent"] = agent_config
        allowlist = agent_config.setdefault("toolset_allowlist", {})
        if not isinstance(allowlist, dict):
            allowlist = {}
            agent_config["toolset_allowlist"] = allowlist
        leaf_allowlist = [PMO_COLLAB_TOOLSET]
        current_cli_allowlist = allowlist.get("cli")
        current_pmo_allowlist = allowlist.get("pmo")
        if (
            current_cli == desired_cli
            and current_pmo == desired_cli
            and current_cli_allowlist == leaf_allowlist
            and current_pmo_allowlist == leaf_allowlist
        ):
            return False
        platform_toolsets["cli"] = list(desired_cli)
        platform_toolsets["pmo"] = list(desired_cli)
        # Toolsets can be recovered from a platform composite or an enabled
        # plugin after platform_toolsets is read. This explicit per-profile
        # allowlist is enforced by the canonical resolver as its final step,
        # so a PM schema stays limited to this leaf set even after plugins are
        # added to Hermes later.
        allowlist["cli"] = list(leaf_allowlist)
        allowlist["pmo"] = list(leaf_allowlist)
    else:
        current_pmo = platform_toolsets.get("pmo")
        current_pmo = [str(item) for item in current_pmo] if isinstance(current_pmo, list) else []
        desired_pmo = list(current_pmo or ["hermes-pmo"])
        if PMO_COLLAB_TOOLSET not in desired_pmo:
            desired_pmo.append(PMO_COLLAB_TOOLSET)
        if current_cli == desired_cli and current_pmo == desired_pmo:
            return False
        platform_toolsets["cli"] = desired_cli
        platform_toolsets["pmo"] = desired_pmo
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        _yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    return True


def reconcile_profiles(
    scope: ProjectScope, *, force_soul: bool = False
) -> list[tuple[str, str, str]]:
    """Repair PM-OS charters and toolsets for declared project profiles.

    Projects created before the orchestration hardening can have a stock PM
    profile with the broad default toolset.  This migration never adds staff
    or edits the roster.  It only applies the declared role policy to the PM
    and existing workers; operator-authored SOUL files remain untouched unless
    explicitly forced.
    """

    from plugins.pmo import agent_soul

    roster: list[tuple[str, str, str]] = [
        ("pm", "pm", scope.config.orchestrator.profile)
    ]
    for item in scope.config.agents:
        handle = str(item.handle).strip()
        role = handle.split("-", 1)[0].strip().lower()
        if role in ROLE_TEMPLATES:
            roster.append((role, handle, item.profile))

    handles = [handle for _role, handle, _profile in roster]
    outcomes: list[tuple[str, str, str]] = []
    for role, handle, profile in roster:
        _sync_profile_project(scope, profile)
        enable_collab_toolset(profile, project_manager=(role == "pm"))
        outcome = agent_soul.write_soul(
            profile,
            agent_soul.render(
                role=role,
                handle=handle,
                project_name=scope.config.project.name,
                teammates=[item for item in handles if item != handle],
            ),
            force=force_soul,
        )
        outcomes.append((handle, profile, outcome))
    return outcomes


def _sync_profile_project(scope: ProjectScope, profile: str) -> None:
    """Give one project-owned profile exactly one resolvable project record.

    Hermes resolves a task's first-class project link through the active
    profile's ``projects.db``. PM-OS profiles are deliberately project-local,
    but older provisioning created an empty database for them. In that state
    ``kanban_db.create_task`` silently dropped the otherwise valid project id,
    breaking Founder consultation sub-chats and worker tickets. This bounded
    projection keeps normal Hermes task creation intact while preventing a
    profile from learning another project's folders.
    """

    from hermes_cli import profiles as profiles_mod, projects_db

    if not profiles_mod.profile_exists(profile):
        return
    db_path = profiles_mod.get_profile_dir(profile) / "projects.db"
    now = int(time.time())
    with projects_db.connect_closing(db_path=db_path) as conn:
        with conn:
            conn.execute("DELETE FROM project_folders")
            conn.execute("DELETE FROM projects")
            conn.execute("DELETE FROM discovered_repos")
            conn.execute("DELETE FROM project_meta")
            conn.execute(
                "INSERT INTO projects "
                "(id, slug, name, description, icon, color, board_slug, "
                "primary_path, created_at, archived) "
                "VALUES (?, ?, ?, ?, NULL, NULL, ?, ?, ?, 0)",
                (
                    scope.project_id,
                    scope.slug,
                    scope.name,
                    "Project-local PM-OS profile scope",
                    scope.board_slug,
                    str(scope.primary_path),
                    now,
                ),
            )
            for index, folder in enumerate(scope.folders):
                conn.execute(
                    "INSERT INTO project_folders "
                    "(project_id, path, label, is_primary, added_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        scope.project_id,
                        str(folder),
                        "Primary" if index == 0 else f"Folder {index + 1}",
                        1 if index == 0 else 0,
                        now,
                    ),
                )


def provision(
    scope: ProjectScope,
    *,
    roles: Sequence[str] = DEFAULT_ROLES,
    counts: dict[str, int] | None = None,
    models: dict[str, str] | None = None,
    include_orchestrator: bool = True,
) -> list[ProvisionedAgent]:
    """Create ``<role>-<slug>`` profiles and write them into the roster.

    Idempotent: an existing profile is reused, and an existing roster entry
    with the same handle is replaced rather than duplicated, so re-running
    after adding a role is safe.
    """
    slug = scope.slug
    counts = dict(counts or {})
    models = dict(models or {})
    from plugins.pmo import admin_workspace

    effective_models = admin_workspace.effective_configuration(scope)["effective"]["models"]

    requested = [str(r).strip().lower() for r in roles if str(r).strip()]
    unknown = [r for r in requested if r not in ROLE_TEMPLATES]
    if unknown:
        raise AgentProvisionError(
            f"unknown role(s): {', '.join(sorted(set(unknown)))}; "
            f"known roles: {', '.join(sorted(ROLE_TEMPLATES))}"
        )

    provisioned: list[ProvisionedAgent] = []

    # Handles are needed for the charters' teammate snapshot, and the PM's
    # charter must know who it can delegate to, so plan the roster before
    # creating anything.
    planned: list[tuple[str, str]] = []  # (role, handle)
    if include_orchestrator:
        planned.append(("pm", "pm"))
    for role in requested:
        if role == "pm":
            continue
        total = max(1, int(counts.get(role, 1)))
        for index in range(1, total + 1):
            planned.append((role, handle_for(role, index, total)))
    all_handles = [handle for _role, handle in planned]

    def _charter(role: str, handle: str, profile: str) -> None:
        from plugins.pmo import agent_soul

        _sync_profile_project(scope, profile)
        agent_soul.write_soul(
            profile,
            agent_soul.render(
                role=role,
                handle=handle,
                project_name=scope.config.project.name,
                teammates=[h for h in all_handles if h != handle],
            ),
        )

    if include_orchestrator:
        orchestrator_profile = scope.config.orchestrator.profile
        created = _create_profile(
            orchestrator_profile, f"PM-OS orchestrator for project {slug}"
        )
        enable_collab_toolset(orchestrator_profile, project_manager=True)
        _charter("pm", "pm", orchestrator_profile)
        provisioned.append(
            ProvisionedAgent(
                handle="pm",
                profile=orchestrator_profile,
                role=ROLE_TEMPLATES["pm"].description,
                toolset=None,
                model=scope.config.models.pm or effective_models.get("pm"),
                profile_created=created,
            )
        )

    for role in requested:
        if role == "pm":
            # The orchestrator is owned by bootstrap and handled above; a
            # second @pm would make mention routing ambiguous.
            continue
        template = ROLE_TEMPLATES[role]
        total = max(1, int(counts.get(role, 1)))
        for index in range(1, total + 1):
            profile = profile_for(role, slug)
            if total > 1:
                profile = f"{profile}-{index}"
            created = _create_profile(
                profile, f"PM-OS {template.description} for project {slug}"
            )
            enable_collab_toolset(profile)
            _charter(role, handle_for(role, index, total), profile)
            provisioned.append(
                ProvisionedAgent(
                    handle=handle_for(role, index, total),
                    profile=profile,
                    role=template.description,
                    toolset=template.toolset,
                    model=models.get(role),
                    profile_created=created,
                )
            )
    return provisioned


def write_roster(scope: ProjectScope, agents: Sequence[ProvisionedAgent]) -> list[dict]:
    """Merge provisioned agents into ``.datansh/project.yaml``.

    Only the ``agents`` list is touched; every other key the operator has set
    is preserved, because this file is version-controlled with the project
    and is frequently hand-edited.
    """
    path = scope.primary_path / ".datansh" / "project.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    existing = list(data.get("agents") or [])

    # The orchestrator lives under ``orchestrator:``; it must not also appear
    # in ``agents:`` or the roster would expose two @pm entries.
    incoming = [a for a in agents if a.handle != "pm"]
    by_handle = {str(item.get("handle")): dict(item) for item in existing}
    for agent in incoming:
        entry = by_handle.get(agent.handle, {})
        entry.update(
            {
                "handle": agent.handle,
                "profile": agent.profile,
                "role": agent.role,
            }
        )
        if agent.toolset:
            entry["toolset"] = agent.toolset
        if agent.model:
            entry["model"] = agent.model
        by_handle[agent.handle] = entry

    merged = sorted(by_handle.values(), key=lambda item: str(item.get("handle")))
    data["agents"] = merged
    if not data.get("board", {}).get("default_assignee"):
        # A board with no default assignee fails finalize on every unassigned
        # ticket; seed it with the first engineer we just created.
        first_dev = next(
            (a for a in incoming if a.handle.startswith("dev")), None
        )
        if first_dev is not None:
            board = dict(data.get("board") or {})
            board["default_assignee"] = first_dev.profile
            data["board"] = board
    path.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    return merged


def set_agent_model(scope: ProjectScope, *, handle: str, model: str | None) -> list[dict]:
    """Set or clear one project's agent model pin without touching other config.

    The PM is stored beneath ``models.pm`` while worker agents live in the
    roster.  Keeping this small operation here makes dashboard administration
    use the same project-owned YAML as the CLI rather than inventing a second
    model-routing store.
    """

    clean_handle = str(handle or "").strip().lstrip("@")
    clean_model = str(model or "").strip() or None
    path = scope.primary_path / ".datansh" / "project.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if clean_handle == "pm":
        models = dict(data.get("models") or {})
        if clean_model is None:
            models.pop("pm", None)
        else:
            models["pm"] = clean_model
        data["models"] = models
    else:
        rows = list(data.get("agents") or [])
        found = False
        for row in rows:
            if str((row or {}).get("handle") or "").strip().lstrip("@") != clean_handle:
                continue
            found = True
            if clean_model is None:
                row.pop("model", None)
            else:
                row["model"] = clean_model
            break
        if not found:
            raise AgentProvisionError(f"unknown project agent handle: {handle!r}")
        data["agents"] = rows
    path.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    return list(data.get("agents") or [])


def set_project_runtime(
    scope: ProjectScope,
    *,
    skills: list[str],
    plugins: list[str],
    channels: list[str],
) -> dict[str, list[str]]:
    """Persist project-owned runtime defaults without changing global settings.

    Skills are used as defaults for subsequently created project tasks. Plugin
    and channel names are an explicit project allowlist/declaration: saving
    them never installs a plugin or enables a gateway channel globally.
    """

    from plugins.pmo.project_scope import ProjectRuntimeConfig

    runtime = ProjectRuntimeConfig.model_validate(
        {"skills": skills, "plugins": plugins, "channels": channels}
    )
    path = scope.primary_path / ".datansh" / "project.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    data["runtime"] = runtime.model_dump(mode="json")
    path.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    return data["runtime"]


def scope_for(project: str) -> ProjectScope:
    """Resolve a project by slug, using its own recorded board binding.

    ``resolve()`` requires an explicit ``board_slug`` on purpose — it must
    never consult the process-global active board. Reading the binding the
    project itself records keeps that guarantee while letting the CLI take a
    single ``--project`` argument.
    """
    from hermes_cli import projects_db

    with projects_db.connect_closing() as conn:
        record = projects_db.get_project(conn, project)
    if record is None:
        raise AgentProvisionError(f"unknown project: {project}")
    return resolve(record.slug, board_slug=record.board_slug)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="hermes pmo agents",
        description=(
            "Provision project-specific agent profiles named <role>-<slug> "
            "(pm-hedgi, dev-hedgi, qa-hedgi) and record them in the project's "
            "own .datansh/project.yaml roster."
        ),
    )
    sub = parser.add_subparsers(dest="action", required=True)

    p_list = sub.add_parser("list", help="Show the project roster and profile state")
    p_list.add_argument("--project", required=True)

    p_rm = sub.add_parser(
        "remove", help="Drop agents from the roster (profiles are kept)"
    )
    p_rm.add_argument("--project", required=True)
    p_rm.add_argument(
        "--handle",
        action="append",
        required=True,
        help="Handle to remove; repeatable",
    )

    p_soul = sub.add_parser(
        "charter",
        help="Write the PM-OS role charter into each agent profile's SOUL.md",
    )
    p_soul.add_argument("--project", required=True)
    p_soul.add_argument(
        "--force",
        action="store_true",
        help="Overwrite a hand-edited SOUL.md as well (default: leave it alone)",
    )

    p_reconcile = sub.add_parser(
        "reconcile",
        help="Repair PM/worker charter and toolset policy for an existing project",
    )
    p_reconcile.add_argument("--project", required=True)
    p_reconcile.add_argument(
        "--force-soul",
        action="store_true",
        help="Overwrite a hand-edited SOUL.md as well (default: leave it alone)",
    )

    p_prov = sub.add_parser("provision", help="Create agent profiles for a project")
    p_prov.add_argument("--project", required=True)
    p_prov.add_argument(
        "--roles",
        default=",".join(DEFAULT_ROLES),
        help=(
            "Comma-separated roles to provision. Known: "
            + ", ".join(sorted(ROLE_TEMPLATES))
        ),
    )
    p_prov.add_argument(
        "--count",
        action="append",
        default=[],
        metavar="ROLE=N",
        help="Provision N agents of a role, e.g. --count dev=3",
    )
    p_prov.add_argument(
        "--model",
        action="append",
        default=[],
        metavar="ROLE=ROUTE",
        help=(
            "Pin a role to a model/provider route, e.g. "
            "--model dev=openai-codex-hedgi"
        ),
    )
    p_prov.add_argument(
        "--no-orchestrator",
        action="store_true",
        help="Skip the pm-<slug> orchestrator profile",
    )

    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        scope = scope_for(args.project)
    except AgentProvisionError as exc:
        print(f"pmo agents: {exc}")
        return 2

    if args.action == "list":
        from hermes_cli import profiles as profiles_mod

        orchestrator = scope.config.orchestrator.profile
        rows = [("pm", orchestrator, "orchestrator", scope.config.models.pm)]
        for agent in scope.config.agents:
            rows.append((agent.handle, agent.profile, agent.role, agent.model))
        print(f"project {scope.slug}: {len(rows)} agent(s)")
        print(
            "  {:<12} {:<24} {:<26} {:<22} {}".format(
                "handle", "profile", "role", "model", "profile on disk"
            )
        )
        for handle, profile, role, model in rows:
            exists = "yes" if profiles_mod.profile_exists(profile) else "MISSING"
            print(
                "  @{:<11} {:<24} {:<26} {:<22} {}".format(
                    handle, profile, str(role)[:26], str(model or "-")[:22], exists
                )
            )
        return 0

    if args.action == "charter":
        from plugins.pmo import agent_soul

        roster = [("pm", "pm", scope.config.orchestrator.profile)]
        for agent in scope.config.agents:
            role = str(agent.handle).split("-")[0]
            roster.append((role, agent.handle, agent.profile))
        handles = [handle for _r, handle, _p in roster]

        print(f"project {scope.slug}: {len(roster)} charter(s)")
        for role, handle, profile in roster:
            try:
                text = agent_soul.render(
                    role=role,
                    handle=handle,
                    project_name=scope.config.project.name,
                    teammates=[h for h in handles if h != handle],
                )
            except KeyError:
                print(f"  @{handle:<11} {profile:<24} no charter for role {role!r}")
                continue
            outcome = agent_soul.write_soul(profile, text, force=args.force)
            note = (
                " (hand-edited; re-run with --force to replace)"
                if outcome == "skipped"
                else ""
            )
            print(f"  @{handle:<11} {profile:<24} {outcome}{note}")
        return 0

    if args.action == "reconcile":
        outcomes = reconcile_profiles(scope, force_soul=args.force_soul)
        print(f"project {scope.slug}: reconciled {len(outcomes)} profile(s)")
        for handle, profile, outcome in outcomes:
            print(f"  @{handle:<11} {profile:<24} {outcome}")
        return 0

    if args.action == "remove":
        path = scope.primary_path / ".datansh" / "project.yaml"
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        before = list(data.get("agents") or [])
        drop = {str(h).strip().lstrip("@") for h in args.handle}
        unknown = drop - {str(a.get("handle")) for a in before}
        if unknown:
            print(
                "pmo agents: unknown handle(s): "
                + ", ".join(sorted(unknown))
                + "; roster has: "
                + ", ".join(sorted(str(a.get("handle")) for a in before))
            )
            return 2
        data["agents"] = [a for a in before if str(a.get("handle")) not in drop]
        path.write_text(
            yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8"
        )
        print(
            f"project {scope.slug}: removed {len(before) - len(data['agents'])} "
            f"agent(s) from the roster"
        )
        # Profiles are deliberately left on disk: they hold session history,
        # memory and credentials. Removing them is a separate, explicit act.
        print("  profiles kept on disk; delete with `hermes profile delete <name>`")
        return 0

    counts: dict[str, int] = {}
    for item in args.count:
        role, _, raw = str(item).partition("=")
        try:
            counts[role.strip().lower()] = int(raw)
        except ValueError:
            print(f"--count expects ROLE=N, got: {item}")
            return 2

    models: dict[str, str] = {}
    for item in args.model:
        role, _, route = str(item).partition("=")
        if not route.strip():
            print(f"--model expects ROLE=ROUTE, got: {item}")
            return 2
        models[role.strip().lower()] = route.strip()

    roles = [r for r in str(args.roles).split(",") if r.strip()]
    try:
        agents = provision(
            scope,
            roles=roles,
            counts=counts,
            models=models,
            include_orchestrator=not args.no_orchestrator,
        )
    except AgentProvisionError as exc:
        print(f"pmo agents: {exc}")
        return 2

    write_roster(scope, agents)
    created = [a for a in agents if a.profile_created]
    print(f"project {scope.slug}: {len(agents)} agent(s) in roster")
    for agent in agents:
        mark = "created" if agent.profile_created else "reused"
        model = f"  model={agent.model}" if agent.model else ""
        print(f"  @{agent.handle:<10} {agent.profile:<24} ({mark}){model}")
    if created:
        print()
        print(
            "Next: give a project its own provider credentials with\n"
            f"  hermes pmo auth login --project {scope.slug} --provider openai-codex"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
