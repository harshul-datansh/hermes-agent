"""Executable contract for PM-OS' additive capability boundary."""

from __future__ import annotations

import importlib.util
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
CHECKER_PATH = REPO_ROOT / "scripts" / "check_pmo_capability_contract.py"


def _load_checker():
    spec = importlib.util.spec_from_file_location("check_pmo_capability_contract", CHECKER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_current_tree_satisfies_pmo_capability_contract():
    checker = _load_checker()
    assert checker.validate(REPO_ROOT) == []


def test_distribution_owns_only_additive_role_payload():
    checker = _load_checker()
    assert checker.validate_distribution(REPO_ROOT) == []


def test_pmo_additions_exclude_replacement_runtime_modules():
    checker = _load_checker()
    additions = checker.pmo_addition_paths(REPO_ROOT)
    assert {path.name for path in additions} >= {
        "approval.py",
        "bootstrap.py",
        "comment.py",
        "report.py",
        "ticket.py",
    }
    assert all(checker.validate_pmo_source(path) == [] for path in additions)


def test_verifier_rejects_forbidden_modules_tool_registration_and_runtime(tmp_path):
    checker = _load_checker()
    violation = tmp_path / "replacement.py"
    violation.write_text(
        "import pmo_db\n"
        "from tools.registry import register\n"
        "class MessageRuntime:\n"
        "    pass\n"
        "register({})\n"
        "SCHEMA = 'CREATE TABLE messages (id TEXT)'\n",
        encoding="utf-8",
    )

    errors = "\n".join(checker.validate_pmo_source(violation))
    assert "forbidden replacement module pmo_db" in errors
    assert "tool-registration surface tools.registry" in errors
    assert "parallel runtime surface MessageRuntime" in errors
    assert "registers a model tool" in errors
    assert "defines a database schema" in errors


def test_verifier_rejects_direct_database_state_outside_read_only_report(tmp_path):
    checker = _load_checker()
    violation = tmp_path / "approval.py"
    violation.write_text("import sqlite3\n", encoding="utf-8")

    errors = "\n".join(checker.validate_pmo_source(violation))
    assert "imports sqlite3 instead of using an existing Hermes state API" in errors


def test_core_and_upstream_kanban_match_recorded_baseline():
    checker = _load_checker()
    assert checker.validate_core_boundary(REPO_ROOT) == []

    sync = checker._load_sync_checker(REPO_ROOT)
    source_commit = sync.read_source_commit(REPO_ROOT)
    assert sync.compare_tree(REPO_ROOT, source_commit, "plugins/kanban") == []


def test_customization_budget_excludes_copied_baseline_and_is_under_ten_percent():
    checker = _load_checker()

    budget = checker.customization_budget(REPO_ROOT)
    paths = set(budget["paths"])

    assert budget["numerator"] == len(paths)
    assert budget["denominator"] > budget["numerator"]
    assert budget["ratio"] <= 0.10
    assert "plugins/pmo/systemd/hermes-kanban-dispatcher.service" not in paths
    assert not any(path.startswith("plugins/kanban/") for path in paths)
    assert "plugins/pmo/workflow.py" in paths
    assert "plugins/platforms/pmo/adapter.py" in paths
    assert "distributions/datansh-pm-os/SOUL.md" in paths


def test_customization_budget_accepts_minimal_and_rejects_only_over_ten_percent():
    checker = _load_checker()

    assert checker.validate_customization_budget(numerator=4, denominator=100) == []
    assert checker.validate_customization_budget(numerator=10, denominator=100) == []
    errors = checker.validate_customization_budget(numerator=11, denominator=100)

    assert len(errors) == 1
    assert "11/100 (11.00%) > 10%" in errors[0]
