"""Run the guarded client-review intake from Hermes cron without an LLM."""
from __future__ import annotations

import json

from hermes_cli import client_review


def main() -> int:
    try:
        result = client_review.run_once()
    except Exception as exc:
        print(json.dumps({"ok": False, "error": client_review._redact(str(exc))}))
        return 2
    # Routine no-change intake stays silent. A changed or partial intake is
    # retained by cron's local run history without spending model tokens.
    if result.get("status") != "reviewed" or result.get("changed_files", 0):
        print(json.dumps({
            "ok": result.get("status") == "reviewed",
            "run_id": result.get("run_id"),
            "status": result.get("status"),
            "changed_files": result.get("changed_files", 0),
            "changed_lines": result.get("changed_lines", 0),
        }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
