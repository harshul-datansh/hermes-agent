#!/usr/bin/env python3
"""Recompute the PM-OS dashboard bundle's Subresource Integrity hash.

``plugins/pmo/dashboard/manifest.json`` pins a ``sha384`` digest of the
bundle it ships, and the dashboard refuses to load a script whose hash does
not match. That is the point of the check, but it means the digest is stale
the instant anyone edits ``dist/index.js`` — and the only thing that notices
is ``test_pmo_security_hardening.py::test_manifest_sri_matches_bundle``,
late, as a confusing failure in an unrelated area.

Run this after touching the bundle, or wire it into a pre-commit hook:

    python scripts/sync_pmo_bundle_sri.py

``--check`` recomputes without writing and exits non-zero on drift, which is
the form to use in CI.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sys
from pathlib import Path

DASHBOARD = Path(__file__).resolve().parents[1] / "plugins" / "pmo" / "dashboard"


def compute(manifest: dict, root: Path) -> str:
    digest = hashlib.sha384((root / manifest["entry"]).read_bytes()).digest()
    return "sha384-" + base64.b64encode(digest).decode()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="report drift and exit 1 instead of rewriting the manifest",
    )
    args = parser.parse_args()

    path = DASHBOARD / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    current = str(manifest.get("integrity") or "")
    expected = compute(manifest, DASHBOARD)

    if current == expected:
        print(f"SRI up to date: {expected}")
        return 0

    if args.check:
        print(f"SRI drift in {path.name}", file=sys.stderr)
        print(f"  manifest: {current or '(missing)'}", file=sys.stderr)
        print(f"  bundle:   {expected}", file=sys.stderr)
        print("Run: python scripts/sync_pmo_bundle_sri.py", file=sys.stderr)
        return 1

    manifest["integrity"] = expected
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"SRI resynced\n  was: {current or '(missing)'}\n  now: {expected}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
