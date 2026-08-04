"""Resume guarded client-review intake after a driver resolves fork drift."""
from __future__ import annotations

import json

from hermes_cli import client_review


def main() -> int:
    try:
        result = client_review.resume_completed_driver_conflicts()
    except Exception as exc:
        print(json.dumps({"ok": False, "error": client_review._redact(str(exc))}))
        return 2
    if result.get("resumed"):
        print(json.dumps({"ok": result.get("status") == "reviewed", **result}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
