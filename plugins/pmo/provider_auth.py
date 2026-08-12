"""Per-project provider credentials for Datansh PM-OS.

One Hermes install, several projects, each with its own OpenAI-Codex (or
Anthropic, or Gemini) account.

This needs **no new credential store**. Hermes already writes ``auth.json``
to ``get_hermes_home()``, and a profile *is* a HERMES_HOME. So running the
normal login flow with HERMES_HOME pointed at ``pm-hedgi`` puts those
credentials in that project's profile and nowhere else. Reads fall back to
the global root (``_global_auth_file_path``), so a project without its own
credentials keeps working on the shared account.

That fallback is the useful part of the design: a project opts *in* to a
dedicated account, and until it does it inherits the global one.

Note on ports: the Codex login is a **device-code** flow — the operator opens
a URL and types a code. There is no localhost callback, so no per-project
port needs assigning. Port allocation only applies to redirect-based PKCE
flows (Spotify is the only one here). Project isolation comes from the
profile's HERMES_HOME, which is stronger anyway: it separates the stored
credential, not just the moment of login.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
from pathlib import Path
from typing import Iterator, Sequence

from plugins.pmo.project_scope import ProjectScope


# Providers worth offering per project. The login itself is whatever
# ``hermes auth login`` already does; this module only decides *where* the
# resulting credential lands.
KNOWN_PROVIDERS: tuple[str, ...] = (
    "openai-codex",
    "anthropic",
    "openai",
    "gemini",
    "nous",
)


class ProviderAuthError(ValueError):
    """Refused before any credential state was touched."""


@contextlib.contextmanager
def profile_home(profile: str) -> Iterator[Path]:
    """Point HERMES_HOME at a profile for the duration of the block.

    ``hermes_constants`` caches the resolved home, so the override helpers
    are used rather than mutating ``os.environ`` alone — otherwise an
    already-imported module keeps writing to the caller's home.
    """
    from hermes_cli import profiles as profiles_mod
    from hermes_constants import (
        reset_hermes_home_override,
        set_hermes_home_override,
    )

    if not profiles_mod.profile_exists(profile):
        raise ProviderAuthError(
            f"profile {profile!r} does not exist; run "
            "`hermes pmo agents provision --project <slug>` first"
        )
    target = profiles_mod.get_profile_dir(profile)
    previous_env = os.environ.get("HERMES_HOME")
    # Both layers are needed: the contextvar scopes in-process resolution,
    # while HERMES_HOME covers anything that re-reads the environment or
    # shells out to a subprocess during login.
    os.environ["HERMES_HOME"] = str(target)
    token = set_hermes_home_override(target)
    try:
        yield target
    finally:
        reset_hermes_home_override(token)
        if previous_env is None:
            os.environ.pop("HERMES_HOME", None)
        else:
            os.environ["HERMES_HOME"] = previous_env


def project_profiles(scope: ProjectScope) -> list[tuple[str, str]]:
    """``(handle, profile)`` for every agent profile in a project."""
    rows = [("pm", scope.config.orchestrator.profile)]
    for agent in scope.config.agents:
        rows.append((agent.handle, agent.profile))
    return rows


def _auth_snapshot(profile: str) -> dict:
    """Providers configured in this profile's own auth.json.

    Read directly rather than through ``hermes_cli.auth`` so the global
    read-through fallback does not make an inherited credential look local.
    Telling "has its own account" from "is borrowing the global one" is the
    entire point of this command.
    """
    from hermes_cli import profiles as profiles_mod

    path = profiles_mod.get_profile_dir(profile) / "auth.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    providers = data.get("providers")
    return providers if isinstance(providers, dict) else {}


def _global_auth_snapshot() -> dict:
    from hermes_constants import get_default_hermes_root

    path = get_default_hermes_root() / "auth.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    providers = data.get("providers")
    return providers if isinstance(providers, dict) else {}


def status(scope: ProjectScope) -> list[dict]:
    """Per-profile credential state for a project."""
    global_providers = sorted(_global_auth_snapshot())
    rows = []
    for handle, profile in project_profiles(scope):
        own = sorted(_auth_snapshot(profile))
        inherited = [p for p in global_providers if p not in own]
        rows.append(
            {
                "handle": handle,
                "profile": profile,
                "own_providers": own,
                "inherited_providers": inherited,
                "effective_providers": own + inherited,
                "openai_codex_source": (
                    "project"
                    if "openai-codex" in own
                    else "global"
                    if "openai-codex" in inherited
                    else "missing"
                ),
            }
        )
    return rows


def disconnect(
    scope: ProjectScope,
    *,
    provider: str,
    handle: str = "pm",
) -> bool:
    """Remove only a profile's own provider override.

    The global store is never touched. After removal, normal auth resolution
    immediately falls back to the global provider when one is configured.
    """
    clean = str(provider or "").strip().lower()
    if clean not in KNOWN_PROVIDERS:
        raise ProviderAuthError(
            f"unknown provider {provider!r}; known: {', '.join(KNOWN_PROVIDERS)}"
        )
    profiles = dict(project_profiles(scope))
    clean_handle = str(handle or "").strip().lstrip("@")
    profile = profiles.get(clean_handle)
    if not profile:
        raise ProviderAuthError(
            f"unknown handle {handle!r} in project {scope.slug}; "
            f"known: {', '.join(sorted(profiles))}"
        )
    with profile_home(profile):
        from hermes_cli.auth import clear_provider_auth

        return clear_provider_auth(clean)


def login(scope: ProjectScope, *, provider: str, handle: str = "pm") -> int:
    """Run the normal Hermes login inside one project profile."""
    clean = str(provider or "").strip().lower()
    if not clean:
        raise ProviderAuthError("a provider is required")

    profiles = dict(project_profiles(scope))
    if handle not in profiles:
        raise ProviderAuthError(
            f"unknown handle {handle!r} in project {scope.slug}; "
            f"known: {', '.join(sorted(profiles))}"
        )
    profile = profiles[handle]

    with profile_home(profile) as home:
        print(f"Signing in to {clean} for project {scope.slug}")
        print(f"  profile:     {profile}  (@{handle})")
        print(f"  credentials: {home / 'auth.json'}")
        print()
        from hermes_cli import auth as auth_mod

        login_fn = getattr(auth_mod, "login", None)
        if not callable(login_fn):
            raise ProviderAuthError(
                "this Hermes build exposes no auth.login(); run "
                f"`HERMES_HOME={home} hermes auth login {clean}` manually"
            )
        result = login_fn(clean)
        return 0 if result is None else int(bool(result) is False)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="hermes pmo auth",
        description=(
            "Give a project its own provider account. Credentials are written "
            "into the project's agent profile, so several projects can each "
            "hold a different OpenAI-Codex login on one machine. A project "
            "without its own credential inherits the global one."
        ),
    )
    sub = parser.add_subparsers(dest="action", required=True)

    p_status = sub.add_parser("status", help="Show per-project credential state")
    p_status.add_argument("--project", required=True)

    p_login = sub.add_parser("login", help="Sign in inside a project's profile")
    p_login.add_argument("--project", required=True)
    p_login.add_argument(
        "--provider",
        required=True,
        help="Provider id, e.g. " + ", ".join(KNOWN_PROVIDERS),
    )
    p_login.add_argument(
        "--handle",
        default="pm",
        help="Which agent profile receives the credential (default: pm)",
    )
    p_login.add_argument(
        "--all-agents",
        action="store_true",
        help="Sign in once per agent profile in the project",
    )

    p_disconnect = sub.add_parser(
        "disconnect",
        help="Remove a project credential so the profile uses the global account",
    )
    p_disconnect.add_argument("--project", required=True)
    p_disconnect.add_argument("--provider", required=True, choices=KNOWN_PROVIDERS)
    p_disconnect.add_argument("--handle", default="pm")

    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        from plugins.pmo.agents import scope_for

        scope = scope_for(args.project)
    except Exception as exc:  # noqa: BLE001 - surface the reason, not a traceback
        print(f"pmo auth: {exc}")
        return 2

    if args.action == "status":
        rows = status(scope)
        print(f"project {scope.slug}: {len(rows)} agent profile(s)")
        print("  {:<12} {:<24} {:<28} {}".format(
            "handle", "profile", "own credentials", "inherited (global)"
        ))
        for row in rows:
            own = ", ".join(row["own_providers"]) or "-"
            inherited = ", ".join(row["inherited_providers"]) or "-"
            print("  @{:<11} {:<24} {:<28} {}".format(
                row["handle"], row["profile"], own[:28], inherited[:40]
            ))
        print()
        print(
            "A profile with no own credentials uses the global account. "
            "Give one its own with:\n"
            f"  hermes pmo auth login --project {scope.slug} "
            "--provider openai-codex"
        )
        return 0

    if args.action == "disconnect":
        try:
            removed = disconnect(
                scope,
                provider=args.provider,
                handle=args.handle,
            )
        except ProviderAuthError as exc:
            print(f"pmo auth: {exc}")
            return 2
        if removed:
            print(
                f"Removed project credential {args.provider} from @{args.handle}; "
                "the global credential is now the fallback."
            )
        else:
            print(f"@{args.handle} had no project credential for {args.provider}.")
        return 0

    targets = (
        [h for h, _ in project_profiles(scope)] if args.all_agents else [args.handle]
    )
    for handle in targets:
        try:
            code = login(scope, provider=args.provider, handle=handle)
        except ProviderAuthError as exc:
            print(f"pmo auth: {exc}")
            return 2
        if code != 0:
            return code
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
