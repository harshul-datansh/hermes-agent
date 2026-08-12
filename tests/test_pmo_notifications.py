"""Egress-only quiet-hours policy."""

from __future__ import annotations

import ast
from datetime import datetime, time, timezone
from pathlib import Path

import pytest

from plugins.pmo import notifications


QUIET = notifications.QuietHours(time(20), time(8), "Asia/Kolkata")


def test_high_urgency_ignores_quiet_hours():
    midnight_india = datetime(2026, 8, 5, 18, 30, tzinfo=timezone.utc)
    assert notifications.should_egress(
        {"urgency": "high", "egressed": False}, now=midnight_india, quiet=QUIET
    )
    assert not notifications.should_egress(
        {"urgency": "normal", "egressed": False}, now=midnight_india, quiet=QUIET
    )


def test_delivery_is_bounded_and_failure_is_not_acknowledged():
    noon_india = datetime(2026, 8, 5, 6, 30, tzinfo=timezone.utc)
    items = [
        {"id": 1, "urgency": "normal", "egressed": False},
        {"id": 2, "urgency": "high", "egressed": False},
    ]
    calls = []
    assert notifications.deliver_outbound(
        items, lambda item: calls.append(item["id"]), now=noon_india, quiet=QUIET, limit=1
    ) == [1]
    assert calls == [1]

    def fail(_item):
        raise RuntimeError("offline")

    with pytest.raises(RuntimeError, match="offline"):
        notifications.deliver_outbound(items, fail, now=noon_india, quiet=QUIET)


def test_notification_module_is_structurally_egress_only():
    path = Path(notifications.__file__)
    tree = ast.parse(path.read_text(encoding="utf-8"))
    forbidden = {"add_comment", "post_comment", "create_task", "wake_agent"}
    calls = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert not (calls & forbidden)
    assert not any(name.endswith("kanban_db") for name in imports)
