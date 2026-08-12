"""Guided, non-destructive setup for a first Datansh PM-OS project."""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

import yaml

from hermes_constants import get_config_path
from hermes_cli import projects_db
from plugins.pmo import bootstrap, project_scope


DEFAULT_ROSTER = "dev-1:Engineer,qa:Reviewer"


@dataclass(frozen=True)
class SetupResult:
    slug: str
    name: str
    workspace: Path
    board_slug: str
    provider_summary: str
    doctor_lines: tuple[str, ...]


def _prompt(label: str, default: str, *, input_fn: Callable[[str], str] = input) -> str:
    """Ask one skippable question; blank input always selects its default."""

    response = input_fn(f"{label} [{default}]: ").strip()
    return response or default


def _default_slug(path: Path) -> str:
    try:
        return projects_db.normalize_slug(path.name)
    except ValueError:
        return "first-project"


def detect_provider_state(config_path: Path | None = None) -> str:
    """Describe existing provider routing without reading or printing secrets."""

    path = config_path or get_config_path()
    if not path.is_file():
        return "Hermes provider state: none detected; setup left provider routing unchanged."
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeError, yaml.YAMLError):
        return "Hermes provider state: existing config retained (could not summarize it)."
    if not isinstance(data, dict):
        return "Hermes provider state: existing config retained."
    model = str(data.get("model") or data.get("default_model") or "").strip()
    provider = str(data.get("provider") or data.get("default_provider") or "").strip()
    routing = data.get("model_provider")
    if isinstance(routing, dict):
        model = model or str(routing.get("model") or "").strip()
        provider = provider or str(routing.get("provider") or "").strip()
    detail = "/".join(part for part in (provider, model) if part)
    return (
        f"Hermes provider state: reused existing routing ({detail})."
        if detail
        else "Hermes provider state: existing config retained; no secrets requested."
    )


def _parse_roster(value: str) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for entry in str(value or "").split(","):
        entry = entry.strip()
        if not entry:
            continue
        handle, separator, role = entry.partition(":")
        handle = handle.strip().casefold()
        role = role.strip() if separator else "Engineer"
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", handle):
            raise ValueError(f"invalid agent handle: {handle!r}")
        if handle in seen:
            raise ValueError(f"duplicate agent handle: {handle}")
        seen.add(handle)
        result.append({"handle": handle, "profile": handle, "role": role or "Engineer"})
    return result


def _apply_roster(config_path: Path, roster: str) -> None:
    agents = _parse_roster(roster)
    if not agents:
        return
    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    data["agents"] = agents
    config_path.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )


def setup_project(
    *,
    workspace: str | Path = ".",
    slug: str | None = None,
    name: str | None = None,
    board_slug: str | None = None,
    roster: str = DEFAULT_ROSTER,
    config_path: Path | None = None,
) -> SetupResult:
    """Bootstrap or reuse one project while preserving existing Hermes auth."""

    root = Path(workspace).expanduser().resolve(strict=True)
    selected_slug = projects_db.normalize_slug(slug or _default_slug(root))
    selected_name = str(name or root.name.replace("-", " ").title()).strip()
    result = bootstrap.bootstrap_project(
        slug=selected_slug,
        name=selected_name,
        workspace=root,
        board_slug=board_slug,
    )
    if result.created_config:
        _apply_roster(result.config_path, roster)
    scope = project_scope.resolve(selected_slug, board_slug=result.board_slug)
    provider = detect_provider_state(config_path)
    doctor = (
        "Doctor: project binding healthy.",
        f"Doctor: config valid at {result.config_path}.",
        f"Doctor: access policy valid at {result.access_path}.",
        f"Doctor: board '{scope.board_slug}' is bound to project '{scope.slug}'.",
        f"Doctor: {provider}",
    )
    return SetupResult(
        slug=scope.slug,
        name=scope.name,
        workspace=scope.primary_path,
        board_slug=scope.board_slug,
        provider_summary=provider,
        doctor_lines=doctor,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    parser.add_argument("--slug")
    parser.add_argument("--name")
    parser.add_argument("--board")
    parser.add_argument("--roster", default=DEFAULT_ROSTER)
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Prompt for values; every prompt accepts the displayed default",
    )
    args = parser.parse_args(argv)

    workspace = args.workspace
    slug = args.slug
    name = args.name
    roster = args.roster
    if args.interactive:
        workspace = Path(_prompt("Existing git project path", str(workspace)))
        resolved = workspace.expanduser().resolve()
        slug = _prompt("Project slug", slug or _default_slug(resolved))
        name = _prompt(
            "Project name", name or resolved.name.replace("-", " ").title()
        )
        roster = _prompt("Agent roster (handle:role, ...)", roster)
        print(".datansh/ is version-controlled project configuration and may be reviewed normally.")

    try:
        result = setup_project(
            workspace=workspace,
            slug=slug,
            name=name,
            board_slug=args.board,
            roster=roster,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"pmo setup: {exc}", file=sys.stderr)
        return 2
    for line in result.doctor_lines:
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
