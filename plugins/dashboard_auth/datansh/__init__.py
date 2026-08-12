"""Multi-user Datansh dashboard authentication.

The password provider is global to the dashboard process only in the narrow
identity sense: it recognizes a human once.  Project membership and every
capability decision remain in each project's ``.datansh/access.yaml``.  A
single human can therefore belong to multiple projects without sharing role
assignments between them.
"""

from __future__ import annotations

import logging
import os

from hermes_cli.dashboard_auth import InvalidCredentialsError
from plugins.dashboard_auth.basic import (
    BasicAuthProvider,
    _DUMMY_HASH,
    _DEFAULT_TTL_SECONDS,
    _resolve_secret,
)
from plugins.pmo.credentials import verify_project_password


log = logging.getLogger(__name__)
LAST_SKIP_REASON = ""


class DatanshAuthProvider(BasicAuthProvider):
    name = "datansh"
    display_name = "Datansh Project Accounts"
    supports_password = True
    supports_session = True

    def complete_password_login(self, *, username: str, password: str):
        principal = verify_project_password(username, password)
        if principal is None:
            raise InvalidCredentialsError("invalid username or password")
        return self._mint_session(principal.removeprefix("human:"))


def _section() -> dict:
    try:
        from hermes_cli.config import cfg_get, load_config

        cfg = load_config()
        value = cfg_get(cfg, "dashboard", "datansh_auth", default=None)
        return value if isinstance(value, dict) else {}
    except Exception as exc:  # noqa: BLE001 - auth discovery must not break host
        log.debug("datansh auth config unavailable: %s", exc)
        return {}


def register(ctx) -> None:
    global LAST_SKIP_REASON
    LAST_SKIP_REASON = ""
    cfg = _section()
    enabled = bool(cfg.get("enabled", False))
    if not enabled and os.environ.get("HERMES_DASHBOARD_DATANSH_AUTH", "").strip() != "1":
        LAST_SKIP_REASON = "dashboard.datansh_auth.enabled is false"
        return
    secret = _resolve_secret(cfg)
    try:
        ttl = int(cfg.get("session_ttl_seconds") or _DEFAULT_TTL_SECONDS)
    except (TypeError, ValueError):
        ttl = _DEFAULT_TTL_SECONDS
    provider = DatanshAuthProvider(
        username="__datansh_project_identity__",
        password_hash=_DUMMY_HASH,
        secret=secret,
        ttl_seconds=ttl,
    )
    ctx.register_dashboard_auth_provider(provider)
    log.info("datansh auth: registered project-owned multi-user provider")
