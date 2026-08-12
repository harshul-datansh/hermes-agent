"""Audit the PM-OS fork against the configured Hermes upstream remote.

The default mode is read-only and uses the local ``upstream/main`` ref. Pass
``--fetch`` during the scheduled weekly review to refresh that ref first.
Being behind upstream is reported, not treated as a contract failure: porting a
change into the copied plugin is always an explicit review decision.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from check_pmo_capability_contract import validate as validate_capabilities
from check_pmo_kanban_sync import read_source_commit


EXPECTED_UPSTREAM = "https://github.com/NousResearch/hermes-agent.git"
REVIEW_SURFACES = (
    "plugins/kanban",
    "hermes_cli/kanban_db.py",
    "hermes_cli/projects_db.py",
    "hermes_cli/dashboard_auth",
    "web/src/plugins/types.ts",
)


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def review(repo: Path, *, fetch: bool = False) -> tuple[dict[str, object], list[str]]:
    errors: list[str] = []
    if fetch:
        _git(repo, "fetch", "--prune", "upstream", "main")

    remote = _git(repo, "remote", "get-url", "upstream", check=False)
    upstream_url = remote.stdout.strip()
    if remote.returncode or upstream_url.rstrip("/") != EXPECTED_UPSTREAM.rstrip("/"):
        errors.append(
            f"upstream remote must be {EXPECTED_UPSTREAM!r}; found {upstream_url!r}"
        )

    source = read_source_commit(repo)
    ref = _git(repo, "rev-parse", "--verify", "upstream/main", check=False)
    upstream_head = ref.stdout.strip()
    behind: int | None = None
    changed: list[str] = []
    if ref.returncode:
        errors.append("local upstream/main ref is missing; run with --fetch")
    else:
        ancestry = _git(repo, "merge-base", "--is-ancestor", source, upstream_head, check=False)
        if ancestry.returncode != 0:
            errors.append("recorded PMO source is not an ancestor of upstream/main")
        else:
            behind = int(_git(repo, "rev-list", "--count", f"{source}..{upstream_head}").stdout)
            names = _git(
                repo,
                "diff",
                "--name-only",
                f"{source}..{upstream_head}",
                "--",
                *REVIEW_SURFACES,
            ).stdout
            changed = [line for line in names.splitlines() if line]

    errors.extend(validate_capabilities(repo))
    payload: dict[str, object] = {
        "recorded_source": source,
        "upstream_head": upstream_head or None,
        "commits_behind": behind,
        "review_surfaces_changed": changed,
        "contract_clean": not errors,
    }
    return payload, errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--fetch", action="store_true", help="Refresh upstream/main first")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        payload, errors = review(args.repo_root.resolve(), fetch=args.fetch)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"PM-OS upstream review failed: {exc}")
        return 2
    payload["errors"] = errors
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"Recorded source: {payload['recorded_source']}")
        print(f"Upstream head: {payload['upstream_head']}")
        print(f"Commits behind: {payload['commits_behind']}")
        for path in payload["review_surfaces_changed"]:
            print(f"  review: {path}")
        for error in errors:
            print(f"  error: {error}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
