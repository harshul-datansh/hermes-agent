"""Project-owned Git repository connections for Datansh PM-OS.

This module deliberately delegates authentication to the installed ``git``
binary.  It never reads a credential file, token, SSH key, or credential-helper
response, and it never writes authentication material to project config.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit

import yaml

from hermes_cli import projects_db
from plugins.pmo.project_scope import (
    GitHubAppRepositoryBinding,
    ProjectConfigError,
    ProjectScope,
    RepositoryConfig,
    assert_project_folder_disjoint,
    project_config_path,
    resolve,
)


class RepositoryError(ValueError):
    """A repository could not be safely connected or validated."""


@dataclass(frozen=True)
class RepositoryStatus:
    name: str
    path: str
    url: str | None
    default_branch: str
    access: str
    primary: bool
    github_app: dict[str, Any] | None
    valid: bool
    current_branch: str | None = None
    head: str | None = None
    detail: str = ""


@dataclass(frozen=True)
class RepositoryOnboardingResult:
    project_id: str
    project_slug: str
    board_slug: str
    path: str
    url: str | None
    cloned: bool


_SCP_URL = re.compile(r"^[A-Za-z0-9._-]+@[A-Za-z0-9.-]+:[^\s]+$")


def _git(
    args: Sequence[str],
    *,
    cwd: Path | None = None,
    timeout: float = 60.0,
    env_overrides: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run system Git while retaining its normal credential configuration."""

    if any("\x00" in str(item) or "\n" in str(item) or "\r" in str(item) for item in args):
        raise RepositoryError("Git arguments cannot contain control characters")
    env = os.environ.copy()
    # Existing credential helpers and SSH agents remain enabled.  Disabling
    # terminal prompts keeps dashboard/API workers from hanging indefinitely
    # when those existing credentials are insufficient.
    env["GIT_TERMINAL_PROMPT"] = "0"
    if env_overrides:
        env.update({str(key): str(value) for key, value in env_overrides.items()})
    try:
        return subprocess.run(
            ["git", *map(str, args)],
            cwd=str(cwd) if cwd else None,
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise RepositoryError("system Git is not installed or is not on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise RepositoryError("Git operation timed out") from exc


def validate_remote_url(url: str) -> str:
    """Return a safe Git URL that contains no embedded secret."""

    clean = str(url or "").strip()
    if not clean or clean.startswith("-") or any(char in clean for char in "\x00\r\n"):
        raise RepositoryError("repository URL is empty or unsafe")
    if _SCP_URL.fullmatch(clean):
        return clean
    parsed = urlsplit(clean)
    if parsed.scheme not in {"https", "ssh", "git"}:
        raise RepositoryError("repository URL must use https, ssh, git, or Git's user@host:path form")
    if parsed.password is not None:
        raise RepositoryError("repository URLs must not contain credentials")
    if parsed.scheme == "https" and parsed.username is not None:
        raise RepositoryError("HTTPS repository URLs must not contain userinfo")
    if not parsed.hostname or not parsed.path.strip("/"):
        raise RepositoryError("repository URL must include a host and repository path")
    return clean


def _verified_github_binding(
    binding: GitHubAppRepositoryBinding | None,
    provider: Any | None,
    *,
    access: str,
) -> tuple[GitHubAppRepositoryBinding | None, Any | None]:
    if binding is None:
        return None, provider
    from plugins.pmo.github_app import GitHubApiProvider

    selected = provider or GitHubApiProvider()
    installation, repository = selected.verify_repository(
        binding.installation_id,
        binding.repository_id,
        binding.full_name,
    )
    if access == "write" and installation.contents_permission != "write":
        raise RepositoryError(
            "GitHub App installation grants read-only Contents permission"
        )
    verified = GitHubAppRepositoryBinding(
        installation_id=installation.installation_id,
        installation_account=installation.installation_account,
        repository_id=repository.repository_id,
        full_name=repository.full_name,
    )
    return verified, selected


def _github_origin_matches(root: Path, binding: GitHubAppRepositoryBinding) -> None:
    from plugins.pmo.github_app import canonical_clone_url

    result = _git(["-C", str(root), "config", "--get", "remote.origin.url"], timeout=15)
    if result.returncode != 0 or not result.stdout.strip():
        raise RepositoryError("GitHub App repositories require an HTTPS GitHub origin")
    actual = validate_remote_url(result.stdout.strip())
    expected = canonical_clone_url(binding)
    if actual.casefold().removesuffix(".git") != expected.casefold().removesuffix(".git"):
        raise RepositoryError(
            "repository origin does not match the GitHub App repository selection"
        )


def _safe_branch(branch: str) -> str:
    clean = str(branch or "").strip()
    if not clean or clean.startswith("-") or any(char.isspace() for char in clean):
        raise RepositoryError("default branch is not a safe Git ref")
    check = _git(["check-ref-format", "--branch", clean], timeout=10)
    if check.returncode != 0:
        raise RepositoryError("default branch is not a valid Git branch name")
    return clean


def _repo_root(path: Path) -> Path:
    candidate = path.expanduser().resolve(strict=True)
    if not candidate.is_dir():
        raise RepositoryError(f"repository path is not a directory: {candidate}")
    result = _git(["-C", str(candidate), "rev-parse", "--show-toplevel"], timeout=15)
    if result.returncode != 0:
        raise RepositoryError(f"path is not a Git repository: {candidate}")
    try:
        root = Path(result.stdout.strip()).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise RepositoryError(f"Git returned an invalid repository root for {candidate}") from exc
    if root != candidate:
        raise RepositoryError(f"connect the repository root, not a subdirectory: {root}")
    return root


def _delete_checkout(root: Path) -> None:
    """Remove one validated checkout, including Windows read-only Git objects."""

    def _retry_writable(operation: Any, path: str, exc: BaseException) -> None:
        try:
            os.chmod(path, stat.S_IWRITE)
            operation(path)
        except OSError:
            raise exc

    shutil.rmtree(root, onexc=_retry_writable)


def _relative_config_path(scope: ProjectScope, path: Path) -> str:
    try:
        relative = os.path.relpath(path, scope.primary_path)
    except ValueError:
        return str(path)
    return "." if relative == "." else Path(relative).as_posix()


def repository_path(scope: ProjectScope, repository: RepositoryConfig) -> Path:
    candidate = Path(repository.path).expanduser()
    if not candidate.is_absolute():
        candidate = scope.primary_path / candidate
    return candidate.resolve(strict=False)


def repository_for_path(
    scope: ProjectScope, path: str | Path
) -> RepositoryConfig | None:
    """Resolve a path to its most-specific configured repository."""

    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = scope.primary_path / candidate
    try:
        resolved = candidate.resolve(strict=False)
    except (OSError, RuntimeError):
        return None
    matches = [
        item
        for item in repositories_for(scope)
        if resolved == repository_path(scope, item)
        or resolved.is_relative_to(repository_path(scope, item))
    ]
    if not matches:
        return None
    return max(matches, key=lambda item: len(repository_path(scope, item).parts))


def git_environment_for_repository(
    scope: ProjectScope,
    path: str | Path,
    *,
    access: str,
    github_provider: Any | None = None,
) -> Mapping[str, str] | None:
    """Mint one repository-scoped installation token for one Git process."""

    repository = repository_for_path(scope, path)
    if repository is None or repository.github_app is None:
        return None
    from plugins.pmo.github_app import GitHubApiProvider

    provider = github_provider or GitHubApiProvider()
    return provider.git_environment(repository.github_app, access=access)


def repositories_for(scope: ProjectScope) -> tuple[RepositoryConfig, ...]:
    """Return declared repositories, synthesizing legacy folder metadata."""

    if scope.config.repositories:
        return scope.config.repositories
    used: set[str] = set()
    entries: list[RepositoryConfig] = []
    for index, folder in enumerate(scope.folders):
        base = scope.slug if index == 0 else re.sub(r"[^a-z0-9_-]+", "-", folder.name.casefold()).strip("-_")
        base = base or f"repository-{index + 1}"
        name = base[:64]
        suffix = 2
        while name in used:
            tail = f"-{suffix}"
            name = base[: 64 - len(tail)] + tail
            suffix += 1
        used.add(name)
        entries.append(
            RepositoryConfig(
                name=name,
                path=_relative_config_path(scope, folder),
                default_branch=scope.config.board.base_branch,
                access="write",
                primary=index == 0,
            )
        )
    return tuple(entries)


def _document(scope: ProjectScope) -> tuple[Path, dict[str, Any], bytes]:
    path = project_config_path(scope)
    original = path.read_bytes()
    try:
        data = yaml.safe_load(original.decode("utf-8"))
    except (UnicodeError, yaml.YAMLError) as exc:
        raise ProjectConfigError(f"invalid project config {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ProjectConfigError(f"invalid project config {path}: root must be a mapping")
    return path, data, original


def _atomic_write(path: Path, data: dict[str, Any]) -> None:
    text = yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent), text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _repository_payload(entries: Sequence[RepositoryConfig]) -> list[dict[str, Any]]:
    return [entry.model_dump(mode="json") for entry in entries]


def _assert_own_roots_disjoint(scope: ProjectScope, candidate: Path, *, replacing: Path | None = None) -> None:
    for existing in scope.folders:
        if replacing is not None and existing == replacing:
            continue
        if candidate == existing or candidate.is_relative_to(existing) or existing.is_relative_to(candidate):
            raise RepositoryError(f"repository path overlaps another repository in this project: {candidate} <> {existing}")


def _persist_connected(scope: ProjectScope, entries: Sequence[RepositoryConfig], path: Path) -> None:
    config_path, document, original = _document(scope)
    document["repositories"] = _repository_payload(entries)
    existed = path in scope.folders
    _atomic_write(config_path, document)
    try:
        with projects_db.connect_closing() as conn:
            projects_db.add_folder(conn, scope.project_id, str(path), label=path.name)
    except Exception:
        config_path.write_bytes(original)
        raise
    if not existed:
        # Resolve after both stores are updated so malformed configuration
        # never leaves a silently accepted folder registration.
        try:
            resolve(scope.project_id, board_slug=scope.board_slug)
        except Exception:
            with projects_db.connect_closing() as conn:
                projects_db.remove_folder(conn, scope.project_id, str(path))
            config_path.write_bytes(original)
            raise


def connect_repository(
    scope: ProjectScope,
    *,
    name: str,
    path: str | Path,
    url: str | None = None,
    default_branch: str = "main",
    access: str = "write",
    github_app: GitHubAppRepositoryBinding | None = None,
    github_provider: Any | None = None,
) -> RepositoryConfig:
    """Attach an existing repository without modifying its checkout."""

    normalized_name = str(name).strip().casefold()
    root = _repo_root(Path(path))
    branch = _safe_branch(default_branch)
    verified_binding, _provider = _verified_github_binding(
        github_app, github_provider, access=access
    )
    if verified_binding is not None:
        from plugins.pmo.github_app import canonical_clone_url

        _github_origin_matches(root, verified_binding)
        safe_url = canonical_clone_url(verified_binding)
        if url and validate_remote_url(url).casefold().removesuffix(".git") != safe_url.casefold().removesuffix(".git"):
            raise RepositoryError("repository URL does not match the GitHub App selection")
    else:
        safe_url = validate_remote_url(url) if url else None
    assert_project_folder_disjoint(root, project_slug=scope.slug, exclude_project_id=scope.project_id)
    entries = list(repositories_for(scope))
    by_name = next((item for item in entries if item.name == normalized_name), None)
    by_path = next((item for item in entries if repository_path(scope, item) == root), None)
    replacing = repository_path(scope, by_path) if by_path else None
    if by_name is not None and repository_path(scope, by_name) != root:
        raise RepositoryError(f"repository name '{normalized_name}' already uses another path")
    if by_path is not None and by_path.name != normalized_name:
        raise RepositoryError(f"repository path is already connected as '{by_path.name}'")
    _assert_own_roots_disjoint(scope, root, replacing=replacing)
    entry = RepositoryConfig(
        name=normalized_name,
        path=_relative_config_path(scope, root),
        url=safe_url,
        default_branch=branch,
        access=access,
        primary=bool(by_path and by_path.primary),
        github_app=verified_binding,
    )
    if entry.primary:
        raise RepositoryError("use primary repository metadata update for the project root")
    if by_name is None:
        entries.append(entry)
    else:
        entries[entries.index(by_name)] = entry
    _persist_connected(scope, entries, root)
    return entry


def clone_repository(
    scope: ProjectScope,
    *,
    name: str,
    url: str,
    path: str | Path,
    default_branch: str = "main",
    access: str = "write",
    github_app: GitHubAppRepositoryBinding | None = None,
    github_provider: Any | None = None,
) -> RepositoryConfig:
    """Clone with system Git credentials, then attach the new repository."""

    verified_binding, selected_provider = _verified_github_binding(
        github_app, github_provider, access=access
    )
    if verified_binding is not None:
        from plugins.pmo.github_app import canonical_clone_url

        safe_url = canonical_clone_url(verified_binding)
        supplied = validate_remote_url(url)
        if supplied.casefold().removesuffix(".git") != safe_url.casefold().removesuffix(".git"):
            raise RepositoryError("repository URL does not match the GitHub App selection")
    else:
        safe_url = validate_remote_url(url)
    branch = _safe_branch(default_branch)
    # Validate every metadata field before Git creates anything on disk.
    validated = RepositoryConfig(
        name=str(name).strip().casefold(),
        path="placeholder",
        url=safe_url,
        default_branch=branch,
        access=access,
        primary=False,
        github_app=verified_binding,
    )
    destination = Path(path).expanduser().resolve(strict=False)
    if destination.exists():
        raise RepositoryError(f"clone destination already exists: {destination}")
    parent = destination.parent.resolve(strict=True)
    if not parent.is_dir():
        raise RepositoryError(f"clone destination parent is not a directory: {parent}")
    assert_project_folder_disjoint(destination, project_slug=scope.slug, exclude_project_id=scope.project_id)
    _assert_own_roots_disjoint(scope, destination)
    git_env = (
        selected_provider.git_environment(verified_binding, access=access)
        if verified_binding is not None
        else None
    )
    result = _git(
        ["clone", "--branch", branch, "--", safe_url, str(destination)],
        cwd=parent,
        timeout=300,
        env_overrides=git_env,
    )
    if result.returncode != 0:
        detail = (
            "authenticated Git clone failed"
            if verified_binding is not None
            else (result.stderr or result.stdout or "Git clone failed").strip().splitlines()[-1]
        )
        raise RepositoryError(f"Git clone failed: {detail}")
    try:
        return connect_repository(
            scope,
            name=validated.name,
            path=destination,
            url=safe_url,
            default_branch=branch,
            access=access,
            github_app=verified_binding,
            github_provider=selected_provider,
        )
    except Exception as exc:
        # Never delete a checkout after Git created it: the directory may now
        # contain valuable state. Make the recoverable condition explicit.
        raise RepositoryError(f"repository cloned at {destination}, but connection failed: {exc}") from exc


def update_primary_repository(
    scope: ProjectScope,
    *,
    url: str | None,
    default_branch: str,
    access: str = "write",
    github_app: GitHubAppRepositoryBinding | None = None,
    github_provider: Any | None = None,
) -> RepositoryConfig:
    """Materialize or update metadata for the immutable primary path."""

    root = _repo_root(scope.primary_path)
    verified_binding, _provider = _verified_github_binding(
        github_app, github_provider, access=access
    )
    if verified_binding is not None:
        from plugins.pmo.github_app import canonical_clone_url

        _github_origin_matches(root, verified_binding)
        safe_url = canonical_clone_url(verified_binding)
        if url and validate_remote_url(url).casefold().removesuffix(".git") != safe_url.casefold().removesuffix(".git"):
            raise RepositoryError("repository URL does not match the GitHub App selection")
    else:
        safe_url = validate_remote_url(url) if url else None
    entry = RepositoryConfig(
        name=scope.slug,
        path=".",
        url=safe_url,
        default_branch=_safe_branch(default_branch),
        access=access,
        primary=True,
        github_app=verified_binding,
    )
    entries = [item for item in repositories_for(scope) if not item.primary]
    entries.insert(0, entry)
    config_path, document, _original = _document(scope)
    document["repositories"] = _repository_payload(entries)
    _atomic_write(config_path, document)
    resolve(scope.project_id, board_slug=scope.board_slug)
    return entry


def onboard_project(
    *,
    slug: str,
    name: str,
    path: str | Path,
    url: str | None = None,
    default_branch: str = "main",
    board_slug: str | None = None,
    admin: str | None = None,
    admin_rank: int | None = None,
    github_app: GitHubAppRepositoryBinding | None = None,
    github_provider: Any | None = None,
) -> RepositoryOnboardingResult:
    """Create a PM-OS project from an existing checkout or a new clone.

    The operation does not modify checkout contents.  When cloning succeeds
    but later PMO provisioning fails, the checkout is retained and the error
    names its path so recovery is explicit rather than destructive.
    """

    from plugins.pmo.bootstrap import bootstrap_project, grant_project_admin

    normalized_slug = projects_db.normalize_slug(slug)
    if not normalized_slug:
        raise RepositoryError("project slug must not be empty")
    if not str(name or "").strip():
        raise RepositoryError("project name must not be empty")
    if board_slug is not None:
        projects_db.normalize_slug(board_slug)
    destination = Path(path).expanduser().resolve(strict=False)
    branch = _safe_branch(default_branch)
    verified_binding, selected_provider = _verified_github_binding(
        github_app, github_provider, access="write"
    )
    if verified_binding is not None:
        from plugins.pmo.github_app import canonical_clone_url

        safe_url = canonical_clone_url(verified_binding)
        if url and validate_remote_url(url).casefold().removesuffix(".git") != safe_url.casefold().removesuffix(".git"):
            raise RepositoryError("repository URL does not match the GitHub App selection")
    else:
        safe_url = validate_remote_url(url) if url else None
    cloned = False
    if destination.exists():
        root = _repo_root(destination)
        if verified_binding is not None:
            _github_origin_matches(root, verified_binding)
    else:
        if not safe_url:
            raise RepositoryError("a repository URL is required when the project path does not exist")
        parent = destination.parent.resolve(strict=True)
        if not parent.is_dir():
            raise RepositoryError(f"project destination parent is not a directory: {parent}")
        assert_project_folder_disjoint(destination, project_slug=normalized_slug)
        git_env = (
            selected_provider.git_environment(verified_binding, access="write")
            if verified_binding is not None
            else None
        )
        result = _git(
            ["clone", "--branch", branch, "--", safe_url, str(destination)],
            cwd=parent,
            timeout=300,
            env_overrides=git_env,
        )
        if result.returncode != 0:
            detail = (
                "authenticated Git clone failed"
                if verified_binding is not None
                else (result.stderr or result.stdout or "Git clone failed").strip().splitlines()[-1]
            )
            raise RepositoryError(f"Git clone failed: {detail}")
        root = _repo_root(destination)
        cloned = True
    try:
        result = bootstrap_project(
            slug=normalized_slug,
            name=name,
            workspace=root,
            board_slug=board_slug,
        )
        scope = resolve(result.project_id, board_slug=result.board_slug)
        update_primary_repository(
            scope,
            url=safe_url,
            default_branch=branch,
            access="write",
            github_app=verified_binding,
            github_provider=selected_provider,
        )
        if admin:
            grant_project_admin(scope, admin, rank=admin_rank)
    except Exception as exc:
        suffix = f"; cloned checkout retained at {root}" if cloned else ""
        raise RepositoryError(f"project onboarding failed: {exc}{suffix}") from exc
    return RepositoryOnboardingResult(
        project_id=result.project_id,
        project_slug=result.project_slug,
        board_slug=result.board_slug,
        path=str(root),
        url=safe_url,
        cloned=cloned,
    )


def promote_repository(scope: ProjectScope, name: str) -> RepositoryConfig:
    """Make an already-connected repository the project's primary workspace.

    A PM-OS project always needs one primary checkout because its project-owned
    configuration, board worktrees, and scoped agent profiles live below that
    root.  Promotion deliberately copies ``.datansh`` to the new checkout and
    never removes it from the former primary; disconnecting the former primary
    is a separate, explicit operation.
    """

    entries = list(repositories_for(scope))
    selected = next((item for item in entries if item.name == str(name).casefold()), None)
    if selected is None:
        raise RepositoryError("repository is not connected")
    if selected.primary:
        return selected

    target = repository_path(scope, selected)
    try:
        target = _repo_root(target)
    except RepositoryError as exc:
        raise RepositoryError("the replacement repository must be an available Git checkout") from exc
    source_config_dir = project_config_path(scope).parent
    target_config_dir = target / ".datansh"
    if target_config_dir.exists():
        raise RepositoryError("the replacement repository already has PM-OS project metadata")

    # A new primary resolves repository paths relative to itself.  Re-encode
    # every retained repository against that root before the project database
    # switches primary paths, otherwise a valid ``../service`` path could point
    # somewhere different after promotion.
    rewritten: list[RepositoryConfig] = []
    for entry in entries:
        if entry == selected:
            rewritten.append(entry.model_copy(update={"path": ".", "primary": True}))
            continue
        current_path = repository_path(scope, entry)
        rewritten.append(
            entry.model_copy(
                update={
                    "path": Path(os.path.relpath(current_path, target)).as_posix(),
                    "primary": False,
                }
            )
        )

    config_path, document, _original = _document(scope)
    document["repositories"] = _repository_payload(rewritten)
    try:
        shutil.copytree(source_config_dir, target_config_dir)
        _atomic_write(target_config_dir / "project.yaml", document)
        with projects_db.connect_closing() as conn:
            if not projects_db.set_primary(conn, scope.project_id, str(target)):
                raise RepositoryError("replacement repository folder registration was missing")
        refreshed = resolve(scope.project_id, board_slug=scope.board_slug)
    except Exception as exc:
        # Do not delete a copied project directory on a failed promotion: it
        # can contain recoverable operator context.  Restore the authoritative
        # project record whenever its primary path was changed.
        with projects_db.connect_closing() as conn:
            projects_db.set_primary(conn, scope.project_id, str(scope.primary_path))
        if isinstance(exc, RepositoryError):
            raise
        raise RepositoryError(f"could not promote repository: {exc}") from exc

    promoted = next(item for item in refreshed.config.repositories if item.primary)
    if promoted.name != selected.name:
        raise RepositoryError("repository promotion did not select the requested repository")
    return promoted


def remove_repository(scope: ProjectScope, name: str) -> bool:
    """Disconnect a non-primary repository and delete its local checkout.

    To disconnect the current primary, first use :func:`promote_repository`
    on another connected checkout.  The current primary remains protected,
    while the requested disconnected checkout is removed only after its exact
    configured Git root has been resolved and validated.
    """

    entries = list(repositories_for(scope))
    selected = next((item for item in entries if item.name == str(name).casefold()), None)
    if selected is None:
        return False
    if selected.primary:
        raise RepositoryError("the primary repository cannot be disconnected")
    root = _repo_root(repository_path(scope, selected))
    if root == scope.primary_path:
        raise RepositoryError("the active project workspace cannot be deleted")
    config_path, document, original = _document(scope)
    document["repositories"] = _repository_payload([item for item in entries if item != selected])
    _atomic_write(config_path, document)
    try:
        with projects_db.connect_closing() as conn:
            removed = projects_db.remove_folder(conn, scope.project_id, str(root))
        if not removed:
            raise RepositoryError("repository folder registration was missing")
        _delete_checkout(root)
        resolve(scope.project_id, board_slug=scope.board_slug)
    except Exception:
        config_path.write_bytes(original)
        with projects_db.connect_closing() as conn:
            projects_db.add_folder(conn, scope.project_id, str(root), label=root.name)
        raise
    return True


def validate_repository(scope: ProjectScope, repository: RepositoryConfig) -> RepositoryStatus:
    path = repository_path(scope, repository)
    metadata = repository.model_dump(mode="json")
    metadata["path"] = str(path)
    try:
        root = _repo_root(path)
        branch_result = _git(["-C", str(root), "branch", "--show-current"], timeout=15)
        head_result = _git(["-C", str(root), "rev-parse", "--short=12", "HEAD"], timeout=15)
        default_result = _git(
            ["-C", str(root), "rev-parse", "--verify", f"refs/heads/{repository.default_branch}"],
            timeout=15,
        )
        if default_result.returncode != 0:
            default_result = _git(
                ["-C", str(root), "rev-parse", "--verify", f"refs/remotes/origin/{repository.default_branch}"],
                timeout=15,
            )
        branch = branch_result.stdout.strip() if branch_result.returncode == 0 else None
        head = head_result.stdout.strip() if head_result.returncode == 0 else None
        default_exists = default_result.returncode == 0
        metadata["path"] = str(root)
        return RepositoryStatus(
            **metadata,
            valid=bool(head) and default_exists,
            current_branch=branch or None,
            head=head or None,
            detail=(
                "Git repository is available"
                if head and default_exists
                else (
                    f"configured default branch '{repository.default_branch}' was not found"
                    if head
                    else "repository has no readable HEAD"
                )
            ),
        )
    except (OSError, RepositoryError) as exc:
        return RepositoryStatus(
            **metadata,
            valid=False,
            detail=str(exc),
        )


def list_repository_status(scope: ProjectScope) -> tuple[RepositoryStatus, ...]:
    return tuple(validate_repository(scope, item) for item in repositories_for(scope))


def _scope_from_args(args: argparse.Namespace) -> ProjectScope:
    return resolve(args.project, board_slug=args.board)


def _print_status(rows: Sequence[RepositoryStatus], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps({"repositories": [asdict(row) for row in rows]}, indent=2))
        return
    for row in rows:
        mark = "ok" if row.valid else "error"
        print(f"{row.name}\t{mark}\t{row.access}\t{row.default_branch}\t{row.path}\t{row.url or '-'}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="repository_action", required=True)
    for action in ("list", "validate"):
        item = sub.add_parser(action)
        item.add_argument("--project", required=True)
        item.add_argument("--board", required=True)
        item.add_argument("--json", action="store_true")
    for action in ("connect", "clone"):
        item = sub.add_parser(action)
        item.add_argument("--project", required=True)
        item.add_argument("--board", required=True)
        item.add_argument("--name", required=True)
        item.add_argument("--path", type=Path, required=True)
        item.add_argument("--url")
        item.add_argument("--branch", default="main")
        item.add_argument("--access", choices=("read", "write"), default="write")
    remove = sub.add_parser("remove")
    remove.add_argument("--project", required=True)
    remove.add_argument("--board", required=True)
    remove.add_argument("--name", required=True)
    onboard = sub.add_parser("onboard")
    onboard.add_argument("--slug", required=True)
    onboard.add_argument("--name", required=True)
    onboard.add_argument("--path", type=Path, required=True)
    onboard.add_argument("--url")
    onboard.add_argument("--branch", default="main")
    onboard.add_argument("--board")
    onboard.add_argument("--admin")
    onboard.add_argument("--admin-rank", type=int)
    args = parser.parse_args(argv)
    try:
        if args.repository_action == "onboard":
            result = onboard_project(
                slug=args.slug,
                name=args.name,
                path=args.path,
                url=args.url,
                default_branch=args.branch,
                board_slug=args.board,
                admin=args.admin,
                admin_rank=args.admin_rank,
            )
            verb = "cloned and onboarded" if result.cloned else "onboarded"
            print(f"{verb} project '{result.project_slug}' at {result.path}")
            return 0
        scope = _scope_from_args(args)
        if args.repository_action in {"list", "validate"}:
            rows = list_repository_status(scope)
            _print_status(rows, as_json=args.json)
            return 0 if args.repository_action == "list" or all(row.valid for row in rows) else 1
        if args.repository_action == "connect":
            entry = connect_repository(scope, name=args.name, path=args.path, url=args.url, default_branch=args.branch, access=args.access)
            print(f"connected repository '{entry.name}' to project '{scope.slug}'")
            return 0
        if args.repository_action == "clone":
            if args.url is None:
                raise RepositoryError("--url is required")
            entry = clone_repository(scope, name=args.name, path=args.path, url=args.url, default_branch=args.branch, access=args.access)
            print(f"cloned and connected repository '{entry.name}' to project '{scope.slug}'")
            return 0
        removed = remove_repository(scope, args.name)
        print(("disconnected" if removed else "not found") + f" repository '{args.name}'")
        return 0 if removed else 1
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"pmo repo: {exc}", file=os.sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "RepositoryError",
    "RepositoryOnboardingResult",
    "RepositoryStatus",
    "clone_repository",
    "connect_repository",
    "list_repository_status",
    "git_environment_for_repository",
    "onboard_project",
    "promote_repository",
    "remove_repository",
    "repositories_for",
    "repository_path",
    "repository_for_path",
    "update_primary_repository",
    "validate_remote_url",
]
