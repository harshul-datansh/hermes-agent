"""Project scope and configuration for the Datansh PM-OS plugin.

This module joins Hermes' existing first-class project records to an explicit
Kanban board.  It does not attempt to sandbox the Hermes agent or replace its
file tools; callers use :func:`assert_path` at plugin-owned file boundaries.
For untrusted shell execution, Hermes' container/remote terminal backends are
still the security boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from fnmatch import fnmatchcase
from pathlib import Path
from pathlib import PurePosixPath
import re
from typing import Any, Literal, Mapping

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from hermes_cli import kanban_db, projects_db
from hermes_cli.profiles import normalize_profile_name, validate_profile_name


class ProjectScopeError(ValueError):
    """Base error for an invalid or missing PM-OS project binding."""


class UnknownProject(ProjectScopeError):
    """Raised when a Hermes project id or slug cannot be resolved."""


class BoardBindingError(ProjectScopeError):
    """Raised when an explicit board is not bound to the project."""


class ScopeViolation(ProjectScopeError):
    """Raised when a plugin-owned path escapes the project folders."""


class ProjectConfigError(ProjectScopeError):
    """Raised when project.yaml is missing, inaccessible, or invalid."""


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ProjectIdentity(_StrictModel):
    name: str = Field(min_length=1)
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    client: str | None = None


class OrchestratorConfig(_StrictModel):
    profile: str = Field(min_length=1)
    model: str | None = None
    auto_decompose: bool = True
    auto_promote_children: bool = True


class AgentConfig(_StrictModel):
    handle: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    profile: str = Field(min_length=1)
    role: str = Field(min_length=1)
    toolset: str | None = None
    # Optional per-agent model route. Falls back to ``models.worker`` and then
    # to the profile's own configuration, so one agent can be pinned to a
    # project-specific provider (``openai-codex-hedgi``) without changing the
    # rest of the roster.
    model: str | None = None

    @field_validator("model", mode="before")
    @classmethod
    def _clean_model(cls, value: Any) -> str | None:
        if value is None:
            return None
        route = str(value).strip()
        if not route:
            return None
        lowered = route.casefold()
        # Same rule as ModelsConfig: an unattended project route must never
        # implicitly select the virtual MoA provider.
        if lowered == "moa" or lowered.startswith(("moa:", "moa/", "moa://")):
            raise ValueError("PM-OS project model routes cannot select MoA")
        return route


class BoardConfig(_StrictModel):
    columns: tuple[str, ...] = (
        "todo",
        "in_progress",
        "blocked",
        "review",
        "done",
    )
    wip_limits: Mapping[str, int] = Field(default_factory=dict)
    default_assignee: str | None = None
    labels: tuple[str, ...] = ()
    base_branch: str = Field(default="main", min_length=1)
    integration_branch: str | None = None
    auto_merge: bool = False
    push_remote: bool = True
    max_concurrent_workers: int = Field(default=3, ge=1)
    worktree_stale_days: int = Field(default=7, ge=1)
    restart_drain_limit: int = Field(default=25, ge=1, le=500)

    @field_validator("base_branch", "integration_branch", mode="before")
    @classmethod
    def _clean_branch(cls, value: Any) -> str | None:
        if value is None:
            return None
        clean = str(value).strip()
        if not clean:
            return None
        if clean.startswith("-") or any(char.isspace() for char in clean):
            raise ValueError("branch names must be non-empty git refs without whitespace")
        return clean

    @model_validator(mode="after")
    def _human_owns_main(self) -> "BoardConfig":
        if self.auto_merge and not self.integration_branch:
            raise ValueError("auto_merge requires an integration_branch; main is human-owned")
        if self.integration_branch == self.base_branch:
            raise ValueError("integration_branch must differ from base_branch")
        return self


class ApprovalRule(_StrictModel):
    when: Mapping[str, str]
    required_rank: int = Field(ge=0, le=100)


class ApprovalsConfig(_StrictModel):
    rules: tuple[ApprovalRule, ...] = ()


class EscalationRankTarget(_StrictModel):
    rank: int = Field(ge=0, le=100)
    approver_profile: str = Field(min_length=1)


class EscalationConfig(_StrictModel):
    pm_retry_limit: int = Field(default=2, ge=0)
    blocked_timeout_minutes: int = Field(default=30, ge=1)
    respond_within_minutes: int = Field(default=120, ge=1)
    reminder_after_minutes: int = Field(default=60, ge=1)
    escalate_rank_after_minutes: int = Field(default=180, ge=1)
    rank_targets: tuple[EscalationRankTarget, ...] = ()

    @model_validator(mode="after")
    def _ordered_sla(self) -> "EscalationConfig":
        if self.reminder_after_minutes > self.respond_within_minutes:
            raise ValueError("reminder_after_minutes cannot exceed respond_within_minutes")
        if self.respond_within_minutes > self.escalate_rank_after_minutes:
            raise ValueError(
                "respond_within_minutes cannot exceed escalate_rank_after_minutes"
            )
        ranks = tuple(target.rank for target in self.rank_targets)
        if tuple(sorted(set(ranks))) != ranks:
            raise ValueError("escalation rank_targets must be unique and increasing")
        return self


class ModelsConfig(_StrictModel):
    """Project roles mapped onto Hermes' existing per-task model override.

    PM-OS deliberately refuses the virtual ``moa`` provider for unattended
    project roles.  This does not remove the MoA tool/provider from Hermes;
    it only prevents a project automation route from selecting it implicitly.
    """

    pm: str | None = None
    worker: str | None = None
    reviewer: str | None = None
    summariser: str | None = None
    escalation_override: str | None = None

    @field_validator("*", mode="before")
    @classmethod
    def _clean_route(cls, value: Any) -> str | None:
        if value is None:
            return None
        route = str(value).strip()
        if not route:
            return None
        lowered = route.casefold()
        if lowered == "moa" or lowered.startswith(("moa:", "moa/", "moa://")):
            raise ValueError("PM-OS project model routes cannot select MoA")
        return route


class ProjectRuntimeConfig(_StrictModel):
    """Project-owned defaults for workers, integrations, and notifications."""

    skills: tuple[str, ...] = ()
    plugins: tuple[str, ...] = ()
    channels: tuple[str, ...] = ("pmo",)

    @field_validator("skills", "plugins", "channels", mode="before")
    @classmethod
    def _clean_names(cls, value: Any) -> tuple[str, ...]:
        if value is None:
            return ()
        if not isinstance(value, (list, tuple)):
            raise ValueError("project runtime values must be lists")
        names = tuple(str(item).strip() for item in value if str(item).strip())
        if any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", name) for name in names):
            raise ValueError("project runtime names must be safe identifiers")
        if len(names) != len(set(item.casefold() for item in names)):
            raise ValueError("project runtime names must be unique")
        return names


class BudgetConfig(_StrictModel):
    """Optional project spend limits; money is always parsed as Decimal."""

    currency: Literal["USD"] = "USD"
    monthly_cap: Decimal | None = Field(default=None, gt=0)
    per_ticket_soft_cap: Decimal | None = Field(default=None, gt=0)
    per_ticket_hard_cap: Decimal | None = Field(default=None, gt=0)
    per_run_max_turns: int = Field(default=40, ge=1)
    on_soft_cap: Literal["warn", "block"] = "warn"
    on_hard_cap: Literal["block", "escalate"] = "block"
    alert_at_percent: tuple[int, ...] = (50, 80, 95)

    @field_validator("alert_at_percent")
    @classmethod
    def _valid_alerts(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        if any(percent < 1 or percent > 100 for percent in value):
            raise ValueError("budget alert percentages must be between 1 and 100")
        if tuple(sorted(set(value))) != value:
            raise ValueError("budget alert percentages must be unique and increasing")
        return value

    @model_validator(mode="after")
    def _ordered_ticket_caps(self) -> "BudgetConfig":
        if (
            self.per_ticket_soft_cap is not None
            and self.per_ticket_hard_cap is not None
            and self.per_ticket_soft_cap > self.per_ticket_hard_cap
        ):
            raise ValueError("per-ticket soft cap cannot exceed the hard cap")
        return self


class ScopeConfig(_StrictModel):
    folders: tuple[str, ...] = (".",)
    deny: tuple[str, ...] = ()
    terminal_isolation: Literal["docker", "host_guard"] = "docker"
    terminal_image: str = "datansh-pm-os:sandbox"

    @field_validator("terminal_image")
    @classmethod
    def _nonempty_terminal_image(cls, value: str) -> str:
        image = str(value).strip()
        if not image:
            raise ValueError("scope.terminal_image cannot be empty")
        return image


class GitHubAppRepositoryBinding(_StrictModel):
    """Non-secret GitHub App installation and repository identity."""

    provider: Literal["github_app"] = "github_app"
    installation_id: int = Field(gt=0)
    installation_account: str = Field(
        min_length=1, max_length=255, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$"
    )
    repository_id: int = Field(gt=0)
    full_name: str = Field(
        min_length=3,
        max_length=255,
        pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$",
    )


class RepositoryConfig(_StrictModel):
    """One Git repository explicitly attached to this PM-OS project.

    Paths are interpreted relative to the primary project folder.  A sibling
    repository may therefore use ``../name``. User credentials are absent.
    The optional GitHub App binding contains identifiers only; private keys
    and short-lived installation tokens remain server-side.
    """

    name: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    path: str = Field(min_length=1, max_length=4096)
    url: str | None = Field(default=None, max_length=4096)
    default_branch: str = Field(default="main", min_length=1, max_length=255)
    access: Literal["read", "write"] = "write"
    primary: bool = False
    github_app: GitHubAppRepositoryBinding | None = None

    @field_validator("path", "url", "default_branch", mode="before")
    @classmethod
    def _clean_repository_text(cls, value: Any) -> str | None:
        if value is None:
            return None
        clean = str(value).strip()
        if not clean:
            return None
        if "\x00" in clean or "\r" in clean or "\n" in clean:
            raise ValueError("repository values cannot contain control characters")
        return clean

    @field_validator("default_branch")
    @classmethod
    def _valid_default_branch(cls, value: str) -> str:
        if value.startswith("-") or any(char.isspace() for char in value):
            raise ValueError("repository default_branch must be a safe Git ref")
        return value


class ProjectConfig(_StrictModel):
    """Validated schema for ``.datansh/project.yaml``.

    ``scope.deny`` is enforced by :func:`path_allowed` on resolved paths but is
    not a shell sandbox.  Plugin-owned file operations should use :func:`assert_path`;
    secrets and shell commands remain governed by Hermes approvals and the
    selected terminal backend.
    """

    version: Literal[1]
    project: ProjectIdentity
    orchestrator: OrchestratorConfig
    agents: tuple[AgentConfig, ...] = ()
    board: BoardConfig = Field(default_factory=BoardConfig)
    approvals: ApprovalsConfig = Field(default_factory=ApprovalsConfig)
    escalation: EscalationConfig = Field(default_factory=EscalationConfig)
    models: ModelsConfig = Field(default_factory=ModelsConfig)
    runtime: ProjectRuntimeConfig = Field(default_factory=ProjectRuntimeConfig)
    budget: BudgetConfig = Field(default_factory=BudgetConfig)
    scope: ScopeConfig = Field(default_factory=ScopeConfig)

    @model_validator(mode="after")
    def _unique_project_agent_identities(self) -> "ProjectConfig":
        """Keep every durable agent identity unambiguous in one project."""

        handles: set[str] = set()
        profiles = {normalize_profile_name(self.orchestrator.profile)}
        for agent in self.agents:
            handle = agent.handle.casefold()
            if handle in {"pm", "founders-office"}:
                raise ValueError(f"agent handle '{agent.handle}' is reserved")
            if handle in handles:
                raise ValueError(f"agent handle '{agent.handle}' is duplicated")
            handles.add(handle)
            profile = normalize_profile_name(agent.profile)
            if profile in profiles:
                raise ValueError(
                    f"Hermes profile '{agent.profile}' is assigned to more than one project role"
                )
            profiles.add(profile)
        return self
    repositories: tuple[RepositoryConfig, ...] = ()

    @field_validator("repositories")
    @classmethod
    def _valid_repositories(
        cls, value: tuple[RepositoryConfig, ...]
    ) -> tuple[RepositoryConfig, ...]:
        if not value:
            # Backward compatibility for projects created before repository
            # metadata existed. Repository management materializes the list on
            # its first write; new templates always include the primary repo.
            return value
        names = [item.name for item in value]
        if len(names) != len(set(names)):
            raise ValueError("repository names must be unique")
        paths = [item.path.casefold() for item in value]
        if len(paths) != len(set(paths)):
            raise ValueError("repository paths must be unique")
        primary = [item for item in value if item.primary]
        if len(primary) != 1:
            raise ValueError("repositories must contain exactly one primary repository")
        if primary[0].path not in {".", "./"}:
            raise ValueError("the primary repository path must be '.'")
        return value


@dataclass(frozen=True)
class ProjectScope:
    project_id: str
    slug: str
    name: str
    board_slug: str
    primary_path: Path
    folders: tuple[Path, ...]
    workspace: Path
    config: ProjectConfig


_CONFIG_CACHE: dict[tuple[Path, int, int], ProjectConfig] = {}


def _candidate_path(scope: ProjectScope, path: str | Path) -> Path:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = scope.primary_path / candidate
    return candidate


def _nearest_existing(path: Path) -> Path | None:
    probe = path
    # A broken symlink is still a filesystem object and must be resolved
    # strictly (and rejected), not treated as an ordinary missing leaf.
    while not probe.exists() and not probe.is_symlink():
        parent = probe.parent
        if parent == probe:
            return None
        probe = parent
    return probe


def _deny_rule(relative: Path, patterns: tuple[str, ...]) -> str | None:
    value = relative.as_posix()
    pure = PurePosixPath(value)
    for raw_pattern in patterns:
        pattern = raw_pattern.replace("\\", "/").strip()
        candidates = (pattern, pattern[3:]) if pattern.startswith("**/") else (pattern,)
        for candidate in candidates:
            if pure.match(candidate) or fnmatchcase(value, candidate):
                return raw_pattern
            # Treat ``dir/**`` as including the directory itself. This keeps
            # the rule effective before a denied new descendant is created.
            if candidate.endswith("/**"):
                directory = candidate[:-3].rstrip("/")
                if value == directory or value.startswith(directory + "/"):
                    return raw_pattern
    return None


def _deny_match(relative: Path, patterns: tuple[str, ...]) -> bool:
    return _deny_rule(relative, patterns) is not None


def _contained_root(scope: ProjectScope, resolved: Path) -> Path | None:
    for root in scope.folders:
        if resolved == root or resolved.is_relative_to(root):
            return root
    return None


def path_allowed(scope: ProjectScope, path: str | Path) -> bool:
    """Return whether ``path`` resolves inside a registered project folder.

    Existing symlinks are resolved before comparison.  For a new file, the
    nearest existing ancestor is resolved so a symlinked parent cannot be used
    to write outside the project.  Path objects (not string prefixes) provide
    correct sibling and case behavior on the host platform.
    """

    try:
        candidate = _candidate_path(scope, path)
        probe = _nearest_existing(candidate)
        if probe is None:
            return False
        probe.resolve(strict=True)
        resolved = candidate.resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        return False
    root = _contained_root(scope, resolved)
    if root is None:
        return False
    relative = resolved.relative_to(root)
    return not _deny_match(relative, scope.config.scope.deny)


def assert_path(scope: ProjectScope, path: str | Path) -> Path:
    """Return a normalized project path or raise :class:`ScopeViolation`."""

    candidate = _candidate_path(scope, path)
    if not path_allowed(scope, candidate):
        try:
            resolved = candidate.resolve(strict=False)
            root = _contained_root(scope, resolved)
            rule = (
                _deny_rule(resolved.relative_to(root), scope.config.scope.deny)
                if root is not None
                else None
            )
        except (OSError, RuntimeError, ValueError):
            rule = None
        if rule is not None:
            raise ScopeViolation(
                f"path is denied by project scope rule {rule!r}: {candidate}"
            )
        raise ScopeViolation(f"path is outside project '{scope.slug}': {candidate}")
    # strict=False resolves existing symlink components while preserving a new
    # leaf name.  path_allowed already validated the nearest existing parent.
    try:
        return candidate.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise ScopeViolation(f"cannot resolve project path: {candidate}") from exc


def project_config_path(scope: ProjectScope) -> Path:
    return assert_path(scope, scope.primary_path / ".datansh" / "project.yaml")


def _read_config(path: Path) -> ProjectConfig:
    try:
        stat = path.stat()
    except OSError as exc:
        raise ProjectConfigError(f"cannot read project config: {path}") from exc
    key = (path, stat.st_mtime_ns, stat.st_size)
    cached = _CONFIG_CACHE.get(key)
    if cached is not None:
        return cached.model_copy(deep=True)
    try:
        raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ProjectConfigError(f"invalid project config {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ProjectConfigError(f"invalid project config {path}: root must be a mapping")
    try:
        parsed = ProjectConfig.model_validate(raw)
    except ValidationError as exc:
        raise ProjectConfigError(f"invalid project config {path}: {exc}") from exc
    for stale in [cache_key for cache_key in _CONFIG_CACHE if cache_key[0] == path]:
        _CONFIG_CACHE.pop(stale, None)
    _CONFIG_CACHE[key] = parsed
    return parsed.model_copy(deep=True)


def load_project_config(scope: ProjectScope) -> ProjectConfig:
    """Load the sole PM-OS project config, failing loudly on any mismatch.

    PM-OS deliberately does not merge a user-level config layer here.  Hermes'
    normal profile config remains available to the agent independently; this
    file contains only project metadata owned by this plugin.
    """

    path = project_config_path(scope)
    if not path.is_file():
        raise ProjectConfigError(f"project config does not exist: {path}")
    parsed = _read_config(path)
    if parsed.project.slug != scope.slug:
        raise ProjectConfigError(
            f"project config slug '{parsed.project.slug}' does not match '{scope.slug}'"
        )
    return parsed


def task_agent_profiles(scope: ProjectScope) -> frozenset[str]:
    """Return the only Hermes profiles allowed to execute project tasks."""

    return frozenset(
        {
            normalize_profile_name(scope.config.orchestrator.profile),
            *(normalize_profile_name(agent.profile) for agent in scope.config.agents),
        }
    )


def project_runtime_profiles(config: ProjectConfig) -> frozenset[str]:
    """Return all project-owned runtimes, including restricted client intake."""

    pm_profile = normalize_profile_name(config.orchestrator.profile)
    client_profile = normalize_profile_name(f"{pm_profile}-client")
    validate_profile_name(client_profile)
    return frozenset(
        {
            pm_profile,
            client_profile,
            *(normalize_profile_name(agent.profile) for agent in config.agents),
        }
    )


def ensure_unique_orchestrator_profile(scope: ProjectScope) -> None:
    """Reject a project-manager profile that is already owned by another project.

    PM-OS deliberately keeps the Hermes profile runtime intact, but its PM
    identity is project-owned.  Looking at the other project records here
    prevents a second project from silently routing Founder’s Office traffic
    into the first project's PM session.
    """

    candidates = project_runtime_profiles(scope.config)
    with projects_db.connect_closing() as conn:
        projects = projects_db.list_projects(conn, include_archived=False)
    for project in projects:
        if project.id == scope.project_id or not project.primary_path:
            continue
        config_path = Path(project.primary_path).expanduser() / ".datansh" / "project.yaml"
        if not config_path.is_file() or config_path.is_symlink():
            continue
        try:
            other = _read_config(config_path.resolve(strict=True))
        except (OSError, RuntimeError, ProjectConfigError):
            # The other project's own resolver/doctor reports malformed config;
            # it must not make an otherwise valid project unreadable here.
            continue
        overlap = sorted(candidates & project_runtime_profiles(other))
        if overlap:
            raise ProjectConfigError(
                f"Hermes profile(s) {', '.join(overlap)} already owned by "
                f"project '{project.slug}'"
            )


def dispatch_candidate_allowed(task: Any, assignee: str, board_slug: str | None) -> bool:
    """Validate a task before Kanban claims it on a PM-OS-managed board."""

    try:
        board = projects_db.normalize_slug(board_slug or kanban_db.get_current_board())
        metadata = kanban_db.read_board_metadata(board)
        project_id = str(metadata.get("project_id") or "").strip()
        if not project_id:
            return True
        with projects_db.connect_closing() as conn:
            project = projects_db.get_project(conn, project_id)
        if project is None or not project.primary_path:
            return False
        config_path = Path(project.primary_path).expanduser() / ".datansh" / "project.yaml"
        if not config_path.is_file():
            # A first-class Hermes project is not automatically PM-OS managed.
            return True
        scope = resolve(project.id, board_slug=board)
        return (
            str(getattr(task, "project_id", "") or "") == scope.project_id
            and normalize_profile_name(assignee) in task_agent_profiles(scope)
        )
    except Exception:
        # Once the PM-OS marker exists, an unprovable binding must never
        # degrade into spawning a potentially foreign runtime.
        return False


def ensure_disjoint_project_folders(scope: ProjectScope) -> None:
    """Reject roots that overlap another active project's filesystem tree.

    Docker bind mounts isolate distinct roots, but mounting a parent directory
    necessarily exposes every nested project below it. Fail closed during
    project resolution so the dashboard, PM profile, and terminal all share one
    honest boundary instead of promising isolation that the mount graph cannot
    provide.
    """

    with projects_db.connect_closing() as conn:
        projects = projects_db.list_projects(conn, include_archived=False)
    for project in projects:
        if project.id == scope.project_id:
            continue
        try:
            other_folders = _resolved_project_folders(project)
        except ProjectScopeError:
            # The other project's own resolver/doctor reports unavailable
            # folders. Only roots that can actually be resolved can overlap.
            continue
        for own_root in scope.folders:
            for other_root in other_folders:
                if (
                    own_root == other_root
                    or own_root.is_relative_to(other_root)
                    or other_root.is_relative_to(own_root)
                ):
                    raise ProjectConfigError(
                        f"project folder overlap between '{scope.slug}' and "
                        f"'{project.slug}': {own_root} <> {other_root}"
                    )


def assert_project_folder_disjoint(
    folder: str | Path,
    *,
    project_slug: str,
    exclude_project_id: str | None = None,
) -> Path:
    """Preflight one project root before bootstrap persists any records."""

    raw_candidate = Path(folder).expanduser()
    # Clone destinations do not exist yet.  Resolve their existing parent
    # chain without requiring the leaf while preserving the same canonical
    # overlap comparison used for already-connected folders.
    try:
        candidate = raw_candidate.resolve(strict=False)
        parent = _nearest_existing(candidate)
        if parent is None:
            raise ProjectConfigError(f"project folder has no accessible parent: {folder}")
        parent.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ProjectConfigError(f"cannot resolve project folder: {folder}") from exc
    with projects_db.connect_closing() as conn:
        projects = projects_db.list_projects(conn, include_archived=False)
    for project in projects:
        if exclude_project_id and project.id == exclude_project_id:
            continue
        try:
            other_folders = _resolved_project_folders(project)
        except ProjectScopeError:
            continue
        for other_root in other_folders:
            if (
                candidate == other_root
                or candidate.is_relative_to(other_root)
                or other_root.is_relative_to(candidate)
            ):
                raise ProjectConfigError(
                    f"project folder overlap between '{project_slug}' and "
                    f"'{project.slug}': {candidate} <> {other_root}"
                )
    return candidate


def read_project_config_file(path: str | Path) -> ProjectConfig:
    """Validate one explicit project config path."""

    resolved = Path(path).expanduser().resolve(strict=True)
    if not resolved.is_file():
        raise ProjectConfigError(f"project config does not exist: {resolved}")
    return _read_config(resolved)


def _resolved_project_folders(project: projects_db.Project) -> tuple[Path, ...]:
    paths = [folder.path for folder in project.folders]
    if project.primary_path and project.primary_path not in paths:
        paths.insert(0, project.primary_path)
    resolved: list[Path] = []
    for value in paths:
        try:
            folder = Path(value).expanduser().resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise ProjectScopeError(
                f"project '{project.slug}' has an unavailable folder: {value}"
            ) from exc
        if not folder.is_dir():
            raise ProjectScopeError(
                f"project '{project.slug}' folder is not a directory: {folder}"
            )
        if folder not in resolved:
            resolved.append(folder)
    if not resolved:
        raise ProjectScopeError(f"project '{project.slug}' has no workspace folders")
    return tuple(resolved)


def _root_projects_db() -> Path | None:
    """The install-root ``projects.db``, when the active home is a profile.

    ``projects.db`` is per-profile by design, mirroring sessions and config.
    That breaks down for a *dispatched worker*: it runs with ``HERMES_HOME``
    pointed at its own profile, where no project was ever registered, so the
    project id carried on the board it was handed resolves to nothing.

    Three live agents hit exactly this. Each made the right judgment call and
    reached for the right collaboration tool, and each got back
    ``unknown project: p_...`` — the reasoning was sound and the plumbing was
    not.

    Same shape as the auth store's global fallback: the profile's own DB
    first, the root as a read-only backstop. Nothing here ever writes to the
    root. Cross-project reads are not opened up by this, because every caller
    derives the project id from the board it was dispatched for, and
    ``resolve`` re-checks the board/project binding in both directions below.
    """
    from hermes_constants import get_default_hermes_root, get_hermes_home

    try:
        root = get_default_hermes_root()
        if root.resolve() == get_hermes_home().resolve():
            return None  # Already at the root; there is no second place to look.
        candidate = root / "projects.db"
        return candidate if candidate.is_file() else None
    except Exception:  # noqa: BLE001 - a missing fallback is not an error
        return None


def _get_project(project_ref: str):
    """Project lookup with the profile-then-root fallback."""
    with projects_db.connect_closing() as conn:
        project = projects_db.get_project(conn, project_ref)
    if project is not None:
        return project
    fallback = _root_projects_db()
    if fallback is None:
        return None
    with projects_db.connect_closing(fallback) as conn:
        return projects_db.get_project(conn, project_ref)


def registered_projects():
    """List the root PMO project registry from an isolated profile runtime.

    Project agents normally see only their own scope. Cross-project PM routing
    needs a bounded directory of peer PM identities, not access to peer files or
    agents, so this returns project metadata while every subsequent scope
    resolution still validates the target board/project binding.
    """

    with projects_db.connect_closing() as conn:
        projects = projects_db.list_projects(conn, include_archived=False)
    fallback = _root_projects_db()
    if fallback is None:
        return projects
    with projects_db.connect_closing(fallback) as conn:
        root_projects = projects_db.list_projects(conn, include_archived=False)
    # A dedicated PM profile commonly has a one-project local registry. Merge
    # it with the root directory so peer PM identities remain discoverable,
    # while scope resolution below continues to enforce every board binding.
    merged = {project.id: project for project in projects}
    merged.update({project.id: project for project in root_projects})
    return sorted(merged.values(), key=lambda project: (project.name.casefold(), project.id))


def resolve(
    project_ref: str,
    *,
    board_slug: str,
    workspace: str | Path | None = None,
) -> ProjectScope:
    """Resolve an explicit Hermes project/board/workspace binding.

    ``board_slug`` has no default by design: this function never consults the
    process-global active board.  The board metadata must name the same Hermes
    project, preventing an accidental query against another project's tickets.
    """

    project = _get_project(project_ref)
    if project is None:
        raise UnknownProject(f"unknown project: {project_ref}")
    explicit_board = projects_db.normalize_slug(board_slug)
    if project.board_slug != explicit_board:
        raise BoardBindingError(
            f"project '{project.slug}' is bound to board {project.board_slug!r}, "
            f"not '{explicit_board}'"
        )
    if not kanban_db.board_exists(explicit_board):
        raise BoardBindingError(f"board does not exist: {explicit_board}")
    metadata = kanban_db.read_board_metadata(explicit_board)
    if metadata.get("project_id") != project.id:
        raise BoardBindingError(
            f"board '{explicit_board}' is not bound to project '{project.id}'"
        )
    if not project.primary_path:
        raise ProjectScopeError(f"project '{project.slug}' has no primary workspace")
    folders = _resolved_project_folders(project)
    primary = Path(project.primary_path).expanduser().resolve(strict=True)

    # Construct a narrow temporary scope so workspace and config paths are
    # checked by the same containment primitive as all other plugin paths.
    placeholder = ProjectConfig(
        version=1,
        project=ProjectIdentity(name=project.name, slug=project.slug),
        orchestrator=OrchestratorConfig(profile=f"pm-{project.slug}"),
    )
    preliminary = ProjectScope(
        project_id=project.id,
        slug=project.slug,
        name=project.name,
        board_slug=explicit_board,
        primary_path=primary,
        folders=folders,
        workspace=primary,
        config=placeholder,
    )
    selected_workspace = assert_path(preliminary, workspace or primary)
    if not selected_workspace.is_dir():
        raise ProjectScopeError(f"workspace is not an existing directory: {selected_workspace}")
    config = load_project_config(preliminary)
    for declared_folder in config.scope.folders:
        configured_folder = assert_path(preliminary, declared_folder)
        if not configured_folder.is_dir():
            raise ProjectConfigError(
                f"configured scope folder is not an existing directory: {declared_folder}"
            )
    if config.repositories:
        declared_roots: list[Path] = []
        for repository in config.repositories:
            candidate = Path(repository.path).expanduser()
            if not candidate.is_absolute():
                candidate = primary / candidate
            try:
                repository_root = candidate.resolve(strict=True)
            except (OSError, RuntimeError) as exc:
                raise ProjectConfigError(
                    f"configured repository is unavailable: {repository.name}"
                ) from exc
            if not repository_root.is_dir() or repository_root not in folders:
                raise ProjectConfigError(
                    f"repository '{repository.name}' is not a registered project folder: "
                    f"{repository_root}"
                )
            if repository.primary and repository_root != primary:
                raise ProjectConfigError(
                    f"repository '{repository.name}' does not match the primary project folder"
                )
            declared_roots.append(repository_root)
        if set(declared_roots) != set(folders):
            missing = sorted(str(path) for path in set(folders) - set(declared_roots))
            raise ProjectConfigError(
                "every registered project folder must have repository metadata; missing: "
                + ", ".join(missing)
            )
    resolved_scope = ProjectScope(
        project_id=project.id,
        slug=project.slug,
        name=project.name,
        board_slug=explicit_board,
        primary_path=primary,
        folders=folders,
        workspace=selected_workspace,
        config=config,
    )
    ensure_disjoint_project_folders(resolved_scope)
    ensure_unique_orchestrator_profile(resolved_scope)
    return resolved_scope


def _template_data(*, slug: str, name: str) -> dict[str, Any]:
    return {
        "version": 1,
        "project": {"name": name, "slug": slug, "client": None},
        "orchestrator": {
            "profile": f"pm-{slug}",
            "model": None,
            "auto_decompose": True,
            "auto_promote_children": True,
        },
        "agents": [],
        "board": {
            "columns": ["todo", "in_progress", "blocked", "review", "done"],
            "wip_limits": {},
            "default_assignee": None,
            "labels": [],
            "base_branch": "main",
            "integration_branch": None,
            "auto_merge": False,
            "push_remote": True,
            "max_concurrent_workers": 3,
            "worktree_stale_days": 7,
            "restart_drain_limit": 25,
        },
        "approvals": {"rules": []},
        "escalation": {
            "pm_retry_limit": 2,
            "blocked_timeout_minutes": 30,
            "respond_within_minutes": 120,
            "reminder_after_minutes": 60,
            "escalate_rank_after_minutes": 180,
            "rank_targets": [],
        },
        "models": {
            "pm": None,
            "worker": None,
            "reviewer": None,
            "summariser": None,
            "escalation_override": None,
        },
        "runtime": {
            "skills": [],
            "plugins": [],
            "channels": ["pmo"],
        },
        "budget": {
            "currency": "USD",
            "monthly_cap": None,
            "per_ticket_soft_cap": None,
            "per_ticket_hard_cap": None,
            "per_run_max_turns": 40,
            "on_soft_cap": "warn",
            "on_hard_cap": "block",
            "alert_at_percent": [50, 80, 95],
        },
        "scope": {
            "folders": ["."],
            "terminal_isolation": "docker",
            "terminal_image": "datansh-pm-os:sandbox",
            "deny": [
                ".env",
                ".env.*",
                "**/secrets/**",
                "**/*.pem",
                "**/*.key",
                "**/id_rsa*",
                "**/.aws/**",
                "**/.ssh/**",
                "**/.hermes/**",
            ],
        },
        "repositories": [
            {
                "name": slug,
                "path": ".",
                "url": None,
                "default_branch": "main",
                "access": "write",
                "primary": True,
                "github_app": None,
            }
        ],
    }


def write_project_config_template(
    *, workspace: str | Path, slug: str, name: str
) -> tuple[Path, bool]:
    """Create a validated project.yaml once; never overwrite an existing file."""

    root = Path(workspace).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ProjectConfigError(f"workspace is not a directory: {root}")
    placeholder = ProjectConfig(
        version=1,
        project=ProjectIdentity(name=name, slug=slug),
        orchestrator=OrchestratorConfig(profile=f"pm-{slug}"),
    )
    temporary = ProjectScope(
        project_id="bootstrap",
        slug=slug,
        name=name,
        board_slug=slug,
        primary_path=root,
        folders=(root,),
        workspace=root,
        config=placeholder,
    )
    config_dir = assert_path(temporary, root / ".datansh")
    config_dir.mkdir(parents=True, exist_ok=True)
    path = assert_path(temporary, config_dir / "project.yaml")
    if path.exists():
        parsed = _read_config(path)
        if parsed.project.slug != slug:
            raise ProjectConfigError(
                f"existing project config slug '{parsed.project.slug}' does not match '{slug}'"
            )
        return path, False
    text = yaml.safe_dump(
        _template_data(slug=slug, name=name),
        sort_keys=False,
        allow_unicode=True,
    )
    try:
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
    except FileExistsError:
        parsed = _read_config(path)
        if parsed.project.slug != slug:
            raise ProjectConfigError(
                f"existing project config slug '{parsed.project.slug}' does not match '{slug}'"
            )
        return path, False
    _read_config(path)
    return path, True
