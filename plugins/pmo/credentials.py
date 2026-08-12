"""Project-owned password enrollment for Datansh PM-OS.

Authentication identifies a human once; ``access.yaml`` decides which
projects that identity may enter.  Password hashes are kept beside each
project rather than in a user-level PMO database.  The dashboard provider
scans those project-owned records at login, so one human can be a member of
many projects while retaining one stable identity.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Iterable

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from hermes_cli import projects_db
from plugins.pmo.project_context import state_directory


log = logging.getLogger(__name__)
CREDENTIALS_FILENAME = "credentials.yaml"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CredentialRecord(_StrictModel):
    principal: str = Field(min_length=8, max_length=200)
    password_hash: str = Field(min_length=20, max_length=500)

    @field_validator("principal")
    @classmethod
    def human_principal(cls, value: str) -> str:
        value = value.strip()
        if not value.startswith("human:") or "@" not in value[6:]:
            raise ValueError("project credentials must use a human:<email> principal")
        return value


class ProjectCredentials(_StrictModel):
    version: int = Field(default=1, ge=1, le=1)
    credentials: tuple[CredentialRecord, ...] = ()

    @field_validator("credentials")
    @classmethod
    def unique_principals(cls, value: tuple[CredentialRecord, ...]) -> tuple[CredentialRecord, ...]:
        principals = [item.principal for item in value]
        if len(principals) != len(set(principals)):
            raise ValueError("duplicate project credential principal")
        return value


def normalize_human_principal(value: str) -> str:
    """Accept the friendly email form used by the UI and canonicalize it."""

    clean = str(value or "").strip()
    if clean.startswith("human:"):
        return clean
    if "@" in clean:
        return f"human:{clean}"
    return clean


def credentials_path(scope) -> Path:
    return state_directory(scope.primary_path) / CREDENTIALS_FILENAME


def load_project_credentials(scope) -> ProjectCredentials:
    path = credentials_path(scope)
    if not path.is_file() or path.is_symlink():
        return ProjectCredentials()
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return ProjectCredentials.model_validate(raw)
    except (OSError, UnicodeError, yaml.YAMLError, ValidationError) as exc:
        raise ValueError(f"invalid project credentials file: {path}") from exc


def _write(path: Path, credentials: ProjectCredentials) -> Path:
    if path.is_symlink():
        raise ValueError(f"refusing symlinked project credentials: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent), text=True
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(yaml.safe_dump(credentials.model_dump(mode="json"), sort_keys=False))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
    return path


def set_project_password(scope, principal: str, password: str) -> Path:
    """Hash and save a project login password; plaintext never reaches disk."""

    canonical = normalize_human_principal(principal)
    if not canonical.startswith("human:"):
        raise ValueError("project credentials require a human email principal")
    if len(password) < 8:
        raise ValueError("password must be at least 8 characters")
    from plugins.dashboard_auth.basic import hash_password

    current = load_project_credentials(scope)
    record = CredentialRecord(principal=canonical, password_hash=hash_password(password))
    updated = [item for item in current.credentials if item.principal != canonical]
    updated.append(record)
    return _write(
        credentials_path(scope),
        ProjectCredentials(version=1, credentials=tuple(updated)),
    )


def remove_project_password(scope, principal: str) -> bool:
    canonical = normalize_human_principal(principal)
    current = load_project_credentials(scope)
    updated = tuple(item for item in current.credentials if item.principal != canonical)
    if len(updated) == len(current.credentials):
        return False
    _write(credentials_path(scope), ProjectCredentials(version=1, credentials=updated))
    return True


def project_credential_status(scope) -> dict[str, bool]:
    return {item.principal: True for item in load_project_credentials(scope).credentials}


def iter_project_credentials() -> Iterable[CredentialRecord]:
    """Yield credentials from all registered, non-archived projects."""

    try:
        with projects_db.connect_closing() as conn:
            projects = projects_db.list_projects(conn, include_archived=False)
    except Exception as exc:  # noqa: BLE001 - auth must fail closed per record
        log.warning("could not enumerate projects for Datansh login: %s", exc)
        return

    for project in projects:
        if not project.primary_path:
            continue
        path = Path(project.primary_path).expanduser() / ".datansh" / CREDENTIALS_FILENAME
        if path.is_symlink() or not path.is_file():
            continue
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            parsed = ProjectCredentials.model_validate(raw)
        except (OSError, UnicodeError, yaml.YAMLError, ValidationError) as exc:
            log.warning("skipping invalid project credentials %s: %s", path, exc)
            continue
        yield from parsed.credentials


def verify_project_password(username: str, password: str) -> str | None:
    """Return the canonical human principal for a valid project password."""

    from plugins.dashboard_auth.basic import _DUMMY_HASH, _verify_password

    canonical = normalize_human_principal(username)
    matched = False
    for record in iter_project_credentials():
        if record.principal == canonical:
            # A human may have a credential record in more than one project.
            # Try every matching hash so project-local enrollment can use
            # different passwords without making the result depend on the
            # order projects happen to be returned from projects.db.
            matched = _verify_password(password, record.password_hash) or matched
    if not matched:
        # Keep the unknown-user path comparable in cost to a real scrypt check.
        _verify_password(password, _DUMMY_HASH)
    return canonical if matched else None
