"""GitHub App installation consent and ephemeral repository authentication.

Project files contain installation and repository identifiers only. The App
private key is loaded from a server-side environment secret, while installation
tokens live only in local variables for the duration of one API or Git call.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Mapping, Protocol
from urllib.parse import quote, urlencode

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from hermes_cli.config import load_config
from plugins.pmo.project_scope import GitHubAppRepositoryBinding, ProjectScope


class GitHubAppError(ValueError):
    """GitHub App configuration, consent, or repository verification failed."""


@dataclass(frozen=True)
class GitHubAppSettings:
    app_slug: str
    app_id: int
    client_id: str | None
    callback_url: str | None
    api_url: str
    private_key: str = field(repr=False)
    client_secret: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class InstallationInfo:
    installation_id: int
    installation_account: str
    repository_selection: str
    contents_permission: str


@dataclass(frozen=True)
class GitHubRepositoryInfo:
    repository_id: int
    full_name: str
    private: bool
    default_branch: str


@dataclass(frozen=True)
class GitHubInstallationCandidate:
    """One installation visible to the configured GitHub App."""

    installation: InstallationInfo
    updated_at: int | None = None


class GitHubAppProvider(Protocol):
    """Small provider boundary used by routes and repository operations."""

    settings: GitHubAppSettings

    def get_installation(self, installation_id: int) -> InstallationInfo: ...

    def list_installations(self) -> tuple[GitHubInstallationCandidate, ...]: ...

    def list_repositories(
        self, installation_id: int
    ) -> tuple[GitHubRepositoryInfo, ...]: ...

    def verify_repository(
        self, installation_id: int, repository_id: int, full_name: str
    ) -> tuple[InstallationInfo, GitHubRepositoryInfo]: ...

    def git_environment(
        self, binding: GitHubAppRepositoryBinding, *, access: str
    ) -> Mapping[str, str]: ...


def _nested_mapping(data: Mapping[str, Any], *keys: str) -> Mapping[str, Any]:
    current: Any = data
    for key in keys:
        if not isinstance(current, Mapping):
            return {}
        current = current.get(key)
    return current if isinstance(current, Mapping) else {}


def load_settings() -> GitHubAppSettings:
    """Load public App metadata from config and secrets from named env vars."""

    raw = load_config() or {}
    section = _nested_mapping(raw, "pmo", "github_app")
    app_slug = str(section.get("app_slug") or "").strip()
    client_id = str(section.get("client_id") or "").strip() or None
    callback_url = str(section.get("callback_url") or "").strip() or None
    api_url = str(section.get("api_url") or "https://api.github.com").strip().rstrip("/")
    try:
        app_id = int(section.get("app_id") or 0)
    except (TypeError, ValueError) as exc:
        raise GitHubAppError("pmo.github_app.app_id must be a positive integer") from exc
    private_key_env = str(
        section.get("private_key_env") or "DATANSH_GITHUB_APP_PRIVATE_KEY"
    ).strip()
    client_secret_env = str(
        section.get("client_secret_env") or "DATANSH_GITHUB_APP_CLIENT_SECRET"
    ).strip()
    if not private_key_env or not client_secret_env:
        raise GitHubAppError("GitHub App secret environment references cannot be empty")
    private_key = os.environ.get(private_key_env, "").replace("\\n", "\n").strip()
    client_secret = os.environ.get(client_secret_env, "").strip() or None
    if not app_slug or app_id <= 0 or not private_key:
        raise GitHubAppError(
            "GitHub App is not configured; set pmo.github_app app_slug/app_id and its server-side private-key secret"
        )
    if not api_url.startswith("https://"):
        raise GitHubAppError("pmo.github_app.api_url must use HTTPS")
    return GitHubAppSettings(
        app_slug=app_slug,
        app_id=app_id,
        client_id=client_id,
        callback_url=callback_url,
        api_url=api_url,
        private_key=private_key,
        client_secret=client_secret,
    )


def public_status() -> dict[str, Any]:
    """Return configuration status without secret values or env-var names."""

    try:
        settings = load_settings()
    except GitHubAppError:
        return {
            "provider": "github_app",
            "configured": False,
            "required_permission": "contents",
        }
    return {
        "provider": "github_app",
        "configured": True,
        "app_slug": settings.app_slug,
        "app_id": settings.app_id,
        "client_id": settings.client_id,
        "callback_url": settings.callback_url,
        "required_permission": "contents",
    }


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


class GitHubApiProvider:
    """GitHub REST implementation authenticated as the configured App."""

    def __init__(
        self,
        settings: GitHubAppSettings | None = None,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        self.settings = settings or load_settings()
        self._client = client

    def _app_jwt(self) -> str:
        now = int(time.time())
        # GitHub accepts either identifier for ``iss``, but recommends the
        # client ID for newly registered Apps. Prefer it when available while
        # keeping established App-ID-only installations compatible.
        issuer = self.settings.client_id or str(self.settings.app_id)
        header = _b64url(json.dumps({"alg": "RS256", "typ": "JWT"}, separators=(",", ":")).encode())
        payload = _b64url(
            json.dumps(
                {"iat": now - 60, "exp": now + 540, "iss": issuer},
                separators=(",", ":"),
            ).encode()
        )
        signing_input = f"{header}.{payload}".encode("ascii")
        try:
            key = serialization.load_pem_private_key(
                self.settings.private_key.encode("utf-8"), password=None
            )
            signature = key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
        except (TypeError, ValueError) as exc:
            raise GitHubAppError("configured GitHub App private key is invalid") from exc
        return f"{header}.{payload}.{_b64url(signature)}"

    def _request(
        self,
        method: str,
        path: str,
        *,
        bearer: str,
        payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {bearer}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "datansh-pm-os",
        }
        client = self._client or httpx.Client(timeout=20.0, follow_redirects=False)
        owns_client = self._client is None
        try:
            response = client.request(
                method,
                f"{self.settings.api_url}{path}",
                headers=headers,
                json=dict(payload) if payload is not None else None,
            )
        except httpx.HTTPError as exc:
            raise GitHubAppError("GitHub App API request failed") from exc
        finally:
            if owns_client:
                client.close()
        if response.status_code < 200 or response.status_code >= 300:
            # Response bodies can contain provider diagnostics and request
            # material; never include them in logs or user-facing exceptions.
            raise GitHubAppError(
                f"GitHub App API {method.upper()} {path.split('?')[0]} returned {response.status_code}"
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise GitHubAppError("GitHub App API returned invalid JSON") from exc
        if not isinstance(body, dict):
            raise GitHubAppError("GitHub App API returned an invalid response")
        return body

    def get_installation(self, installation_id: int) -> InstallationInfo:
        if int(installation_id) <= 0:
            raise GitHubAppError("installation_id must be positive")
        body = self._request(
            "GET",
            f"/app/installations/{int(installation_id)}",
            bearer=self._app_jwt(),
        )
        if int(body.get("app_id") or 0) != self.settings.app_id:
            raise GitHubAppError("installation does not belong to the configured GitHub App")
        account = body.get("account")
        login = str(account.get("login") if isinstance(account, Mapping) else "").strip()
        permissions = body.get("permissions")
        contents = str(
            permissions.get("contents") if isinstance(permissions, Mapping) else ""
        ).strip().casefold()
        if not login or contents not in {"read", "write"}:
            raise GitHubAppError("installation lacks the required Contents permission")
        return InstallationInfo(
            installation_id=int(installation_id),
            installation_account=login,
            repository_selection=str(body.get("repository_selection") or "selected"),
            contents_permission=contents,
        )

    def list_installations(self) -> tuple[GitHubInstallationCandidate, ...]:
        """List App installations without ever returning installation tokens."""

        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self._app_jwt()}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "datansh-pm-os",
        }
        client = self._client or httpx.Client(timeout=20.0, follow_redirects=False)
        owns_client = self._client is None
        try:
            response = client.get(
                f"{self.settings.api_url}/app/installations?per_page=100",
                headers=headers,
            )
        except httpx.HTTPError as exc:
            raise GitHubAppError("GitHub App installation check failed") from exc
        finally:
            if owns_client:
                client.close()
        if response.status_code < 200 or response.status_code >= 300:
            raise GitHubAppError(
                f"GitHub App API GET /app/installations returned {response.status_code}"
            )
        try:
            rows = response.json()
        except ValueError as exc:
            raise GitHubAppError("GitHub App installation check returned invalid JSON") from exc
        if not isinstance(rows, list):
            raise GitHubAppError("GitHub App installation check returned an invalid response")

        candidates: list[GitHubInstallationCandidate] = []
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            account = row.get("account")
            permissions = row.get("permissions")
            installation_id = int(row.get("id") or 0)
            account_name = str(account.get("login") if isinstance(account, Mapping) else "").strip()
            contents = str(
                permissions.get("contents") if isinstance(permissions, Mapping) else ""
            ).strip().casefold()
            if installation_id <= 0 or not account_name or contents not in {"read", "write"}:
                continue
            raw_updated = str(row.get("updated_at") or row.get("created_at") or "").strip()
            try:
                updated_at = int(datetime.fromisoformat(raw_updated.replace("Z", "+00:00")).timestamp())
            except ValueError:
                updated_at = None
            candidates.append(
                GitHubInstallationCandidate(
                    installation=InstallationInfo(
                        installation_id=installation_id,
                        installation_account=account_name,
                        repository_selection=str(row.get("repository_selection") or "selected"),
                        contents_permission=contents,
                    ),
                    updated_at=updated_at,
                )
            )
        return tuple(candidates)

    def _installation_token(
        self,
        installation_id: int,
        *,
        repository_id: int | None,
        access: str,
    ) -> str:
        permission = "write" if access == "write" else "read"
        payload: dict[str, Any] = {"permissions": {"contents": permission}}
        if repository_id is not None:
            payload["repository_ids"] = [int(repository_id)]
        body = self._request(
            "POST",
            f"/app/installations/{int(installation_id)}/access_tokens",
            bearer=self._app_jwt(),
            payload=payload,
        )
        token = str(body.get("token") or "")
        if not token:
            raise GitHubAppError("GitHub did not issue an installation token")
        return token

    def list_repositories(
        self, installation_id: int
    ) -> tuple[GitHubRepositoryInfo, ...]:
        installation = self.get_installation(installation_id)
        token = self._installation_token(
            installation.installation_id,
            repository_id=None,
            access="read",
        )
        rows: list[GitHubRepositoryInfo] = []
        for page in range(1, 11):
            body = self._request(
                "GET",
                f"/installation/repositories?per_page=100&page={page}",
                bearer=token,
            )
            raw_repositories = body.get("repositories")
            if not isinstance(raw_repositories, list):
                raise GitHubAppError("GitHub returned an invalid repository list")
            for item in raw_repositories:
                if not isinstance(item, Mapping):
                    continue
                rows.append(
                    GitHubRepositoryInfo(
                        repository_id=int(item.get("id") or 0),
                        full_name=str(item.get("full_name") or ""),
                        private=bool(item.get("private")),
                        default_branch=str(item.get("default_branch") or "main"),
                    )
                )
            if len(raw_repositories) < 100:
                break
        return tuple(row for row in rows if row.repository_id > 0 and "/" in row.full_name)

    def verify_repository(
        self, installation_id: int, repository_id: int, full_name: str
    ) -> tuple[InstallationInfo, GitHubRepositoryInfo]:
        installation = self.get_installation(installation_id)
        expected = str(full_name or "").strip().casefold()
        for repository in self.list_repositories(installation_id):
            if repository.repository_id == int(repository_id) and repository.full_name.casefold() == expected:
                return installation, repository
        raise GitHubAppError("repository is not accessible to this GitHub App installation")

    def git_environment(
        self, binding: GitHubAppRepositoryBinding, *, access: str
    ) -> Mapping[str, str]:
        installation, repository = self.verify_repository(
            binding.installation_id,
            binding.repository_id,
            binding.full_name,
        )
        if access == "write" and installation.contents_permission != "write":
            raise GitHubAppError(
                "GitHub App installation grants read-only Contents permission"
            )
        token = self._installation_token(
            binding.installation_id,
            repository_id=repository.repository_id,
            access=access,
        )
        # Empty helper resets the inherited helper list. The second helper is
        # constant and expands only the process-local environment variable;
        # the token never appears in argv, config, output, or a temp file.
        helper = (
            "!f() { printf '%s\\n' 'username=x-access-token' "
            "\"password=$DATANSH_PMO_GITHUB_APP_TOKEN\"; }; f"
        )
        return {
            "DATANSH_PMO_GITHUB_APP_TOKEN": token,
            "GIT_CONFIG_COUNT": "2",
            "GIT_CONFIG_KEY_0": "credential.helper",
            "GIT_CONFIG_VALUE_0": "",
            "GIT_CONFIG_KEY_1": "credential.helper",
            "GIT_CONFIG_VALUE_1": helper,
        }


@dataclass(frozen=True)
class _PendingInstall:
    principal: str
    project_id: str
    board_slug: str
    created_at: int
    expires_at: int


@dataclass(frozen=True)
class _InstallationGrant:
    principal: str
    project_id: str
    installation: InstallationInfo
    expires_at: int


_STATE_TTL_SECONDS = 600
_GRANT_TTL_SECONDS = 3600
_LOCK = threading.RLock()
_PENDING: dict[str, _PendingInstall] = {}
_GRANTS: dict[tuple[str, str, int], _InstallationGrant] = {}


def _prune(now: int) -> None:
    for state in [key for key, item in _PENDING.items() if item.expires_at <= now]:
        _PENDING.pop(state, None)
    for key in [key for key, item in _GRANTS.items() if item.expires_at <= now]:
        _GRANTS.pop(key, None)


def begin_install(
    scope: ProjectScope,
    *,
    principal: str,
    provider: GitHubAppProvider | None = None,
    now: int | None = None,
) -> dict[str, Any]:
    selected = provider or GitHubApiProvider()
    timestamp = int(now or time.time())
    state = secrets.token_urlsafe(32)
    expires_at = timestamp + _STATE_TTL_SECONDS
    with _LOCK:
        _prune(timestamp)
        _PENDING[state] = _PendingInstall(
            principal=principal,
            project_id=scope.project_id,
            board_slug=scope.board_slug,
            created_at=timestamp,
            expires_at=expires_at,
        )
    consent_url = (
        f"https://github.com/apps/{quote(selected.settings.app_slug, safe='')}/installations/new?"
        + urlencode({"state": state})
    )
    return {
        "provider": "github_app",
        "consent_url": consent_url,
        "state": state,
        "expires_at": expires_at,
    }


def finalize_install(
    *,
    state: str,
    installation_id: int,
    principal: str,
    provider: GitHubAppProvider | None = None,
    expected_project_id: str | None = None,
    now: int | None = None,
) -> tuple[_PendingInstall, InstallationInfo]:
    timestamp = int(now or time.time())
    with _LOCK:
        _prune(timestamp)
        pending = _PENDING.get(str(state))
        if pending is not None and secrets.compare_digest(pending.principal, principal):
            if expected_project_id is None or pending.project_id == expected_project_id:
                _PENDING.pop(str(state), None)
            else:
                pending = None
        else:
            pending = None
    if pending is None:
        raise GitHubAppError("installation state is invalid, expired, or already used")
    selected = provider or GitHubApiProvider()
    installation = selected.get_installation(int(installation_id))
    grant = _InstallationGrant(
        principal=principal,
        project_id=pending.project_id,
        installation=installation,
        expires_at=timestamp + _GRANT_TTL_SECONDS,
    )
    with _LOCK:
        _GRANTS[(principal, pending.project_id, installation.installation_id)] = grant
    return pending, installation


def poll_install(
    *,
    state: str,
    principal: str,
    provider: GitHubAppProvider | None = None,
    expected_project_id: str | None = None,
    now: int | None = None,
) -> dict[str, Any]:
    """Discover a newly installed/updated App and grant it to this request.

    GitHub can leave a browser on its installation settings page instead of
    following a local setup URL. The polling path stays scoped to the original
    single-use consent state and only accepts installations updated after that
    request began. It never returns an installation token or repository list.
    """

    timestamp = int(now or time.time())
    with _LOCK:
        _prune(timestamp)
        pending = _PENDING.get(str(state))
        valid = (
            pending is not None
            and secrets.compare_digest(pending.principal, principal)
            and (expected_project_id is None or pending.project_id == expected_project_id)
        )
    if not valid or pending is None:
        raise GitHubAppError("installation state is invalid, expired, or already used")

    selected = provider or GitHubApiProvider()
    # A small grace window absorbs GitHub/server timestamp rounding while
    # still preventing an old installation from satisfying a new request.
    updated_after = pending.created_at - 60
    candidates: list[dict[str, Any]] = []
    for candidate in selected.list_installations():
        if candidate.updated_at is not None and candidate.updated_at < updated_after:
            continue
        try:
            repositories = selected.list_repositories(candidate.installation.installation_id)
        except GitHubAppError:
            continue
        candidates.append(
            {
                "installation": candidate.installation,
                "repository_count": len(repositories),
                "updated_at": candidate.updated_at,
            }
        )

    ready = [item for item in candidates if item["repository_count"] > 0]
    if len(ready) == 1:
        selected_item = ready[0]
        completed, installation = finalize_install(
            state=state,
            installation_id=selected_item["installation"].installation_id,
            principal=principal,
            provider=selected,
            expected_project_id=expected_project_id,
            now=timestamp,
        )
        return {
            "status": "available",
            "project_id": completed.project_id,
            "installation": installation,
            "repository_count": selected_item["repository_count"],
            "poll_after_seconds": 0,
        }
    return {
        "status": "waiting" if not candidates else "needs_selection",
        "candidates": [
            asdict(item["installation"]) | {"repository_count": item["repository_count"]}
            for item in candidates
        ],
        "poll_after_seconds": 10,
    }


def list_grants(
    *, principal: str, project_id: str, now: int | None = None
) -> tuple[_InstallationGrant, ...]:
    timestamp = int(now or time.time())
    with _LOCK:
        _prune(timestamp)
        rows = [
            item
            for key, item in _GRANTS.items()
            if key[0] == principal and key[1] == project_id
        ]
    return tuple(sorted(rows, key=lambda item: item.installation.installation_account.casefold()))


def require_grant(
    *, principal: str, project_id: str, installation_id: int, now: int | None = None
) -> _InstallationGrant:
    timestamp = int(now or time.time())
    with _LOCK:
        _prune(timestamp)
        grant = _GRANTS.get((principal, project_id, int(installation_id)))
    if grant is None:
        raise GitHubAppError("this installation has not been consented for this project")
    return grant


def authorize_repository(
    *,
    installation_id: int,
    repository_id: int,
    full_name: str,
    provider: GitHubAppProvider | None = None,
) -> GitHubAppRepositoryBinding:
    selected = provider or GitHubApiProvider()
    installation, repository = selected.verify_repository(
        int(installation_id), int(repository_id), full_name
    )
    return GitHubAppRepositoryBinding(
        installation_id=installation.installation_id,
        installation_account=installation.installation_account,
        repository_id=repository.repository_id,
        full_name=repository.full_name,
    )


def canonical_clone_url(binding: GitHubAppRepositoryBinding) -> str:
    return f"https://github.com/{binding.full_name}.git"


__all__ = [
    "GitHubApiProvider",
    "GitHubAppError",
    "GitHubAppProvider",
    "GitHubAppSettings",
    "GitHubInstallationCandidate",
    "GitHubRepositoryInfo",
    "InstallationInfo",
    "authorize_repository",
    "begin_install",
    "canonical_clone_url",
    "finalize_install",
    "list_grants",
    "load_settings",
    "poll_install",
    "public_status",
    "require_grant",
]
