"""Ephemeral, GitHub-App-backed onboarding for a new PM-OS project."""

from __future__ import annotations

import re
import secrets
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from hermes_cli import projects_db
from plugins.pmo import github_app, repositories
from plugins.pmo.project_scope import (
    GitHubAppRepositoryBinding,
    assert_project_folder_disjoint,
    resolve,
)


class ProjectOnboardingError(ValueError):
    """An onboarding draft or repository selection is invalid."""


@dataclass(frozen=True)
class OnboardingRepository:
    name: str
    local_path: str
    default_branch: str
    access: str
    primary: bool
    github_app: GitHubAppRepositoryBinding


@dataclass
class OnboardingDraft:
    onboarding_id: str
    principal: str
    slug: str
    name: str
    workspace_path: str
    board_slug: str
    pm_profile: str
    admin: str | None
    admin_rank: int | None
    created_at: int
    expires_at: int
    repositories: list[OnboardingRepository] = field(default_factory=list)
    completing: bool = False


_DRAFT_TTL_SECONDS = 3600
_LOCK = threading.RLock()
_DRAFTS: dict[str, OnboardingDraft] = {}


def _prune(now: int) -> None:
    for key in [key for key, draft in _DRAFTS.items() if draft.expires_at <= now]:
        _DRAFTS.pop(key, None)


def _view(draft: OnboardingDraft) -> dict[str, Any]:
    return {
        "onboarding_id": draft.onboarding_id,
        "slug": draft.slug,
        "name": draft.name,
        "workspace_path": draft.workspace_path,
        "board_slug": draft.board_slug,
        "pm_profile": draft.pm_profile,
        "expires_at": draft.expires_at,
        "repositories": [
            asdict(item)
            | {"github_app": item.github_app.model_dump(mode="json")}
            for item in draft.repositories
        ],
    }


def create_draft(
    *,
    principal: str,
    slug: str,
    name: str,
    workspace_path: str | Path,
    board_slug: str | None = None,
    admin: str | None = None,
    admin_rank: int | None = None,
    now: int | None = None,
) -> dict[str, Any]:
    normalized_slug = projects_db.normalize_slug(slug)
    normalized_board = projects_db.normalize_slug(board_slug or normalized_slug)
    display_name = str(name or "").strip()
    if not normalized_slug or not normalized_board or not display_name:
        raise ProjectOnboardingError("project slug, name, and board are required")
    candidate = Path(workspace_path).expanduser().resolve(strict=False)
    if candidate.exists() and not candidate.is_dir():
        raise ProjectOnboardingError("workspace_path must be a directory or new path")
    parent = candidate.parent.resolve(strict=True)
    if not parent.is_dir():
        raise ProjectOnboardingError("workspace_path parent must exist")
    with projects_db.connect_closing() as conn:
        if projects_db.get_project(conn, normalized_slug) is not None:
            raise ProjectOnboardingError(f"project '{normalized_slug}' already exists")
    assert_project_folder_disjoint(candidate, project_slug=normalized_slug)
    timestamp = int(now or time.time())
    draft = OnboardingDraft(
        onboarding_id="onb_" + secrets.token_urlsafe(18),
        principal=principal,
        slug=normalized_slug,
        name=display_name,
        workspace_path=str(candidate),
        board_slug=normalized_board,
        pm_profile=f"pm-{normalized_slug}",
        admin=admin or (principal if principal.startswith("human:") else None),
        admin_rank=admin_rank,
        created_at=timestamp,
        expires_at=timestamp + _DRAFT_TTL_SECONDS,
    )
    with _LOCK:
        _prune(timestamp)
        _DRAFTS[draft.onboarding_id] = draft
    return _view(draft)


def get_draft(
    onboarding_id: str, *, principal: str, now: int | None = None
) -> OnboardingDraft:
    timestamp = int(now or time.time())
    with _LOCK:
        _prune(timestamp)
        draft = _DRAFTS.get(str(onboarding_id))
    if draft is None or not secrets.compare_digest(draft.principal, principal):
        raise ProjectOnboardingError("onboarding session is invalid or expired")
    return draft


def draft_view(onboarding_id: str, *, principal: str) -> dict[str, Any]:
    return _view(get_draft(onboarding_id, principal=principal))


def consent_subject(onboarding_id: str, *, principal: str):
    draft = get_draft(onboarding_id, principal=principal)
    return type(
        "OnboardingConsentSubject",
        (),
        {
            "project_id": f"onboarding:{draft.onboarding_id}",
            "board_slug": draft.board_slug,
        },
    )()


def _safe_repo_name(value: str) -> str:
    clean = re.sub(r"[^a-z0-9_-]+", "-", str(value).casefold()).strip("-_")
    if not clean or len(clean) > 64:
        raise ProjectOnboardingError("repository name is invalid")
    return clean


def add_repository(
    onboarding_id: str,
    *,
    principal: str,
    installation_id: int,
    repository_id: int,
    full_name: str,
    name: str | None = None,
    local_path: str | Path | None = None,
    default_branch: str | None = None,
    access: str = "write",
    primary: bool = False,
    provider: github_app.GitHubAppProvider | None = None,
) -> dict[str, Any]:
    draft = get_draft(onboarding_id, principal=principal)
    subject_id = f"onboarding:{draft.onboarding_id}"
    github_app.require_grant(
        principal=principal,
        project_id=subject_id,
        installation_id=installation_id,
    )
    selected = provider or github_app.GitHubApiProvider()
    installation, remote = selected.verify_repository(
        installation_id, repository_id, full_name
    )
    if access not in {"read", "write"}:
        raise ProjectOnboardingError("repository access must be read or write")
    if primary and access != "write":
        raise ProjectOnboardingError("the primary project repository requires write access")
    if access == "write" and installation.contents_permission != "write":
        raise ProjectOnboardingError(
            "GitHub App installation grants read-only Contents permission"
        )
    repo_name = _safe_repo_name(name or remote.full_name.rsplit("/", 1)[-1])
    path = Path(local_path).expanduser().resolve(strict=False) if local_path else (
        Path(draft.workspace_path)
        if primary
        else Path(draft.workspace_path).parent / repo_name
    )
    if primary and path != Path(draft.workspace_path):
        raise ProjectOnboardingError("primary repository must use the draft workspace_path")
    assert_project_folder_disjoint(path, project_slug=draft.slug)
    binding = GitHubAppRepositoryBinding(
        installation_id=installation.installation_id,
        installation_account=installation.installation_account,
        repository_id=remote.repository_id,
        full_name=remote.full_name,
    )
    with _LOCK:
        current = _DRAFTS.get(draft.onboarding_id)
        if current is not draft:
            raise ProjectOnboardingError("onboarding session is invalid or expired")
        if draft.completing:
            raise ProjectOnboardingError("onboarding completion is already in progress")
        for existing in draft.repositories:
            existing_path = Path(existing.local_path)
            if existing.name == repo_name:
                raise ProjectOnboardingError(f"repository name '{repo_name}' is already selected")
            if path == existing_path or path.is_relative_to(existing_path) or existing_path.is_relative_to(path):
                raise ProjectOnboardingError("selected repository paths overlap")
        if primary and any(item.primary for item in draft.repositories):
            raise ProjectOnboardingError("onboarding already has a primary repository")
        draft.repositories.append(
            OnboardingRepository(
                name=repo_name,
                local_path=str(path),
                default_branch=default_branch or remote.default_branch,
                access=access,
                primary=primary,
                github_app=binding,
            )
        )
    return _view(draft)


def remove_repository(
    onboarding_id: str, *, principal: str, name: str
) -> dict[str, Any]:
    draft = get_draft(onboarding_id, principal=principal)
    with _LOCK:
        if _DRAFTS.get(draft.onboarding_id) is not draft:
            raise ProjectOnboardingError("onboarding session is invalid or expired")
        if draft.completing:
            raise ProjectOnboardingError("onboarding completion is already in progress")
        before = len(draft.repositories)
        draft.repositories[:] = [item for item in draft.repositories if item.name != name]
        if len(draft.repositories) == before:
            raise ProjectOnboardingError("repository selection was not found")
    return _view(draft)


def complete_draft(
    onboarding_id: str,
    *,
    principal: str,
    provider: github_app.GitHubAppProvider | None = None,
) -> dict[str, Any]:
    draft = get_draft(onboarding_id, principal=principal)
    with _LOCK:
        if _DRAFTS.get(draft.onboarding_id) is not draft:
            raise ProjectOnboardingError("onboarding session is invalid or expired")
        if draft.completing:
            raise ProjectOnboardingError("onboarding completion is already in progress")
        draft.completing = True
        repository_selection = tuple(draft.repositories)
    primary = [item for item in repository_selection if item.primary]
    if len(primary) != 1:
        with _LOCK:
            draft.completing = False
        raise ProjectOnboardingError("select exactly one primary repository")
    selected_provider = provider or github_app.GitHubApiProvider()
    try:
        # Consent and repository access are re-checked immediately before any
        # filesystem or project mutation. A draft alone is never authority.
        subject_id = f"onboarding:{draft.onboarding_id}"
        for item in repository_selection:
            github_app.require_grant(
                principal=principal,
                project_id=subject_id,
                installation_id=item.github_app.installation_id,
            )
            installation, remote = selected_provider.verify_repository(
                item.github_app.installation_id,
                item.github_app.repository_id,
                item.github_app.full_name,
            )
            if installation.installation_account != item.github_app.installation_account:
                raise ProjectOnboardingError("GitHub App installation account changed")
            if remote.full_name.casefold() != item.github_app.full_name.casefold():
                raise ProjectOnboardingError("GitHub repository selection changed")
            if item.access == "write" and installation.contents_permission != "write":
                raise ProjectOnboardingError("GitHub App Contents permission is now read-only")

        primary_repo = primary[0]
        try:
            scope = resolve(draft.slug, board_slug=draft.board_slug)
        except (FileNotFoundError, KeyError, RuntimeError, ValueError):
            primary_url = github_app.canonical_clone_url(primary_repo.github_app)
            result = repositories.onboard_project(
                slug=draft.slug,
                name=draft.name,
                path=primary_repo.local_path,
                url=primary_url,
                default_branch=primary_repo.default_branch,
                board_slug=draft.board_slug,
                admin=draft.admin,
                admin_rank=draft.admin_rank,
                github_app=primary_repo.github_app,
                github_provider=selected_provider,
            )
            scope = resolve(result.project_id, board_slug=result.board_slug)
        else:
            # A prior completion attempt may have created the project before a
            # secondary clone failed. Only that exact partial project is safe
            # to resume; an unrelated project with this slug remains rejected.
            if (
                scope.primary_path != Path(draft.workspace_path)
                or scope.board_slug != draft.board_slug
                or scope.config.orchestrator.profile != draft.pm_profile
            ):
                raise ProjectOnboardingError(
                    "project slug was claimed by a different project during onboarding"
                )

        for item in repository_selection:
            if item.primary or any(repo.name == item.name for repo in scope.config.repositories):
                continue
            path = Path(item.local_path)
            kwargs = {
                "scope": scope,
                "name": item.name,
                "path": path,
                "url": github_app.canonical_clone_url(item.github_app),
                "default_branch": item.default_branch,
                "access": item.access,
                "github_app": item.github_app,
                "github_provider": selected_provider,
            }
            if path.exists():
                repositories.connect_repository(**kwargs)
            else:
                repositories.clone_repository(**kwargs)
            scope = resolve(scope.project_id, board_slug=scope.board_slug)
        statuses = repositories.list_repository_status(scope)
    except Exception:
        with _LOCK:
            if _DRAFTS.get(draft.onboarding_id) is draft:
                draft.completing = False
        raise
    with _LOCK:
        _DRAFTS.pop(draft.onboarding_id, None)
    return {
        "project_id": scope.project_id,
        "project_slug": scope.slug,
        "board_slug": scope.board_slug,
        "pm_profile": scope.config.orchestrator.profile,
        "repositories": [asdict(item) for item in statuses],
    }


__all__ = [
    "ProjectOnboardingError",
    "add_repository",
    "complete_draft",
    "consent_subject",
    "create_draft",
    "draft_view",
    "get_draft",
    "remove_repository",
]
