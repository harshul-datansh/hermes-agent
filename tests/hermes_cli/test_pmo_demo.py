from __future__ import annotations

import builtins
import time
from pathlib import Path

import pytest

from hermes_cli import kanban_db
from plugins.pmo import approval, demo


def _snapshot(result):
    with demo.isolated_hermes_state(result.hermes_home):
        with kanban_db.connect_closing(board=result.board_slug) as conn:
            return sorted(
                (task.title, task.status, task.assignee)
                for task in kanban_db.list_tasks(conn, include_archived=True)
            )


def test_demo_uses_isolated_hermes_home(tmp_path, monkeypatch):
    real_home = tmp_path / "real-hermes"
    real_home.mkdir()
    sentinel = real_home / "keep.txt"
    sentinel.write_text("untouched", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(real_home))

    result = demo.create_demo(tmp_path / "isolated-demo")

    assert result.hermes_home != real_home
    assert sentinel.read_text(encoding="utf-8") == "untouched"
    assert sorted(path.name for path in real_home.iterdir()) == ["keep.txt"]


def test_demo_makes_no_model_calls(tmp_path, monkeypatch):
    original_import = builtins.__import__
    forbidden = []

    def guarded_import(name, *args, **kwargs):
        if name == "run_agent" or name.startswith("litellm"):
            forbidden.append(name)
            raise AssertionError(f"demo attempted to import model runtime: {name}")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    result = demo.create_demo(tmp_path / "no-model-demo")
    assert result.status_counts["done"] == 11
    assert forbidden == []


def test_demo_is_deterministic(tmp_path):
    first = demo.create_demo(tmp_path / "first")
    second = demo.create_demo(tmp_path / "second")

    assert first.status_counts == second.status_counts
    assert _snapshot(first) == _snapshot(second)


def test_demo_board_has_every_representative_native_status(tmp_path):
    result = demo.create_demo(tmp_path / "statuses")
    assert {"triage", "todo", "running", "blocked", "review", "done", "archived"} <= set(
        result.status_counts
    )


def test_demo_has_escalated_approval(tmp_path):
    result = demo.create_demo(tmp_path / "approval")
    with demo.isolated_hermes_state(result.hermes_home):
        view = approval.get_approval(
            board_slug=result.board_slug, approval_id=result.approval_id
        )
    assert view.status == "pending"
    assert view.required_rank == 100
    assert view.approver_profile == "human:ceo@datansh.local"


def test_demo_reset_is_clean_and_exact_target_only(tmp_path):
    result = demo.create_demo(tmp_path / "resettable")
    assert demo.reset_demo(result.root) is True
    assert not result.root.exists()
    assert demo.reset_demo(result.root) is False

    unsafe = tmp_path / "not-a-demo"
    unsafe.mkdir()
    (unsafe / "keep.txt").write_text("keep", encoding="utf-8")
    with pytest.raises(ValueError, match="verified demo marker"):
        demo.reset_demo(unsafe)
    assert (unsafe / "keep.txt").is_file()


def test_demo_completes_under_60s(tmp_path):
    started = time.monotonic()
    demo.create_demo(tmp_path / "fast")
    assert time.monotonic() - started < 60


def test_demo_cli_registration():
    from plugins.platforms.pmo.cli import COMMAND_MODULES

    assert COMMAND_MODULES["demo"] == "plugins.pmo.demo"
    assert COMMAND_MODULES["setup"] == "plugins.pmo.setup"


def test_demo_prints_authenticated_dashboard_command(tmp_path, capsys):
    result = demo.create_demo(tmp_path / "print-ready")

    demo._print_ready(result)

    output = capsys.readouterr().out
    assert "hermes dashboard --host 0.0.0.0 --port 8787 --no-open" in output
