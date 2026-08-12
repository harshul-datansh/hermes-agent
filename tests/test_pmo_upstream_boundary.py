"""Merge-safety checks for the copied PMO Kanban plugin."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
CHECKER_PATH = REPO_ROOT / "scripts" / "check_pmo_kanban_sync.py"


def _canonical_bytes(path: Path) -> bytes:
    """Match Git's canonical text representation on CRLF worktrees."""

    return path.read_bytes().replace(b"\r\n", b"\n")


def _load_checker():
    spec = importlib.util.spec_from_file_location("check_pmo_kanban_sync", CHECKER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_recorded_source_commit_exists() -> None:
    checker = _load_checker()
    source_commit = checker.read_source_commit(REPO_ROOT)

    assert len(source_commit) == 40
    assert checker.source_files(REPO_ROOT, source_commit)


def test_no_modifications_to_plugins_kanban() -> None:
    checker = _load_checker()
    source_commit = checker.read_source_commit(REPO_ROOT)

    assert checker.compare_tree(REPO_ROOT, source_commit, "plugins/kanban") == []


def test_pmo_copy_has_only_the_independent_plugin_identity_changes() -> None:
    checker = _load_checker()
    source_commit = checker.read_source_commit(REPO_ROOT)

    differences = checker.compare_tree(
        REPO_ROOT,
        source_commit,
        "plugins/pmo",
        ignored_files=checker.PMO_METADATA_FILES,
    )

    assert set(differences) == checker.KNOWN_PMO_DIVERGENCES


def test_pmo_backend_change_is_mount_and_access_guard_only() -> None:
    checker = _load_checker()
    source_commit = checker.read_source_commit(REPO_ROOT)

    assert checker.pmo_backend_matches_allowed_transform(REPO_ROOT, source_commit)


def test_pmo_dashboard_extensions_retain_the_copied_capability_baseline() -> None:
    checker = _load_checker()
    source_commit = checker.read_source_commit(REPO_ROOT)

    assert checker.pmo_client_matches_allowed_transform(REPO_ROOT, source_commit)
    assert checker.pmo_style_matches_allowed_transform(REPO_ROOT, source_commit)


def test_pmo_board_selection_has_an_independent_browser_namespace() -> None:
    kanban_client = _canonical_bytes(
        REPO_ROOT / "plugins/kanban/dashboard/dist/index.js"
    )
    pmo_client = _canonical_bytes(REPO_ROOT / "plugins/pmo/dashboard/dist/index.js")

    assert b'const LS_BOARD_KEY = "hermes.kanban.selectedBoard";' in kanban_client
    assert b'const LS_BOARD_KEY = "hermes.pmo.selectedBoard";' in pmo_client
    assert b'const LS_BOARD_KEY = "hermes.kanban.selectedBoard";' not in pmo_client

    checker = _load_checker()
    source_commit = checker.read_source_commit(REPO_ROOT)
    assert checker.pmo_client_matches_allowed_transform(REPO_ROOT, source_commit)


def test_pmo_manifest_registers_an_independent_dashboard_plugin() -> None:
    manifest = json.loads(
        (REPO_ROOT / "plugins/pmo/dashboard/manifest.json").read_text(encoding="utf-8")
    )

    assert manifest["name"] == "pmo"
    assert manifest["label"] == "Datansh PM-OS"
    assert manifest["tab"] == {"path": "/pmo", "position": "before:kanban"}
    assert manifest["api"] == "plugin_api.py"
