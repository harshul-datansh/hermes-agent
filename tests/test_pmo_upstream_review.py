"""Tests for the weekly PM-OS upstream review command."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "pmo_upstream_review.py"


def _load_review():
    scripts = str(SCRIPT.parent)
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    spec = importlib.util.spec_from_file_location("pmo_upstream_review", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_review_confirms_remote_and_contract_without_mutating_repo():
    module = _load_review()

    payload, errors = module.review(REPO_ROOT, fetch=False)

    assert errors == []
    assert payload["recorded_source"]
    assert payload["upstream_head"]
    assert isinstance(payload["commits_behind"], int)
    assert payload["contract_clean"] is True
