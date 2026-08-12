"""Behavior and isolation tests for PM-OS project decisions and knowledge."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "plugins" / "pmo" / "project_context.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("pmo_project_context", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def project(tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    return workspace


def _decision(module, workspace, *, title="Use PostgreSQL", supersedes=None):
    return module.record_decision(
        workspace,
        title=title,
        context="The service needs transactional writes.",
        decision=f"{title} for the primary store.",
        rationale="It matches the consistency and operational requirements.",
        alternatives="SQLite was considered for local-only deployments.",
        decided_by="@pm",
        task_id="task-1",
        thread_id="thread-1",
        approval_id="approval-1",
        supersedes=supersedes,
    )


def test_decision_log_is_append_only_and_supersession_is_derived(project):
    module = _load_module()
    first = _decision(module, project)
    log = project / ".datansh" / "decisions.jsonl"
    original = log.read_bytes()

    replacement = _decision(
        module,
        project,
        title="Use managed PostgreSQL",
        supersedes=first.id,
    )

    updated = log.read_bytes()
    assert updated.startswith(original)
    assert len(updated) > len(original)
    events = [json.loads(line) for line in updated.decode().splitlines()]
    assert len(events) == 2
    assert "status" not in events[0]
    assert "superseded_by" not in events[0]
    assert events[1]["supersedes"] == first.id

    decisions = {item.id: item for item in module.list_decisions(project, limit=10)}
    assert decisions[first.id].status == "superseded"
    assert decisions[first.id].superseded_by == replacement.id
    assert decisions[replacement.id].status == "active"


def test_project_context_redacts_secrets_before_append(project):
    module = _load_module()
    secret = "sk-proj-abcdefghijklmnopqrstuvwxyz0123456789"

    decision = module.record_decision(
        project,
        title="Rotate API token",
        context=f"A credential was exposed: {secret}",
        decision="Rotate it now.",
        rationale="Durable project records must not retain credentials.",
        decided_by="@pm",
    )

    persisted = (project / ".datansh" / "decisions.jsonl").read_text(encoding="utf-8")
    assert secret not in decision.context
    assert secret not in persisted


def test_decision_cannot_be_superseded_twice(project):
    module = _load_module()
    first = _decision(module, project)
    _decision(module, project, title="First replacement", supersedes=first.id)

    with pytest.raises(ValueError, match="already superseded"):
        _decision(module, project, title="Second replacement", supersedes=first.id)


def test_decision_listing_is_recent_and_bounded(project):
    module = _load_module()
    decisions = [_decision(module, project, title=f"Decision {index}") for index in range(7)]

    recent = module.list_decisions(project, limit=3)
    assert [item.id for item in recent] == [item.id for item in reversed(decisions[-3:])]
    with pytest.raises(ValueError, match="between 1 and"):
        module.list_decisions(project, limit=module.MAX_LIST_LIMIT + 1)


def test_knowledge_length_dedupe_and_usage_events_are_append_only(project):
    module = _load_module()
    with pytest.raises(ValueError, match="at most 280"):
        module.add_knowledge(
            project,
            kind="gotcha",
            body="x" * 281,
            created_by="@worker",
        )

    first = module.add_knowledge(
        project,
        kind="env",
        body="The test suite requires DATABASE_URL to be set.",
        created_by="@worker",
        source="task-1",
    )
    log = project / ".datansh" / "knowledge.jsonl"
    original = log.read_bytes()
    duplicate = module.add_knowledge(
        project,
        kind="env",
        body="The test suite requires database_url to be set!",
        created_by="@reviewer",
        source="task-2",
    )

    assert duplicate.deduplicated is True
    assert duplicate.knowledge.id == first.knowledge.id
    assert duplicate.knowledge.uses == 1
    assert log.read_bytes().startswith(original)
    events = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert [event["record_type"] for event in events] == ["knowledge", "knowledge_use"]


def test_knowledge_unique_count_is_bounded(project, monkeypatch):
    module = _load_module()
    monkeypatch.setattr(module, "MAX_KNOWLEDGE_ENTRIES", 2)
    for index in range(2):
        module.add_knowledge(
            project,
            kind="risk",
            body=f"Independent project risk number {index}.",
            created_by="@pm",
        )

    with pytest.raises(ValueError, match="capped at 2"):
        module.add_knowledge(
            project,
            kind="risk",
            body="A third independent project risk.",
            created_by="@pm",
        )


def test_near_duplicate_detection_preserves_distinct_numbers(project):
    module = _load_module()
    first = module.add_knowledge(
        project,
        kind="env",
        body="Production runs PostgreSQL version 14.",
        created_by="@worker",
    )
    second = module.add_knowledge(
        project,
        kind="env",
        body="Production runs PostgreSQL version 15.",
        created_by="@worker",
    )

    assert first.deduplicated is False
    assert second.deduplicated is False
    assert first.knowledge.id != second.knowledge.id


def test_knowledge_never_crosses_project_workspaces(tmp_path):
    module = _load_module()
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    module.add_knowledge(
        first,
        kind="convention",
        body="Use conventional commits in this project.",
        created_by="@pm",
    )

    assert len(module.list_knowledge(first)) == 1
    assert module.list_knowledge(second) == []
    assert "conventional commits" not in module.render_context(second)


def test_context_projection_caps_items_labels_confidence_and_bounds_chars(project):
    module = _load_module()
    for index in range(8):
        _decision(module, project, title=f"Architecture decision {index}")
    for index in range(25):
        module.add_knowledge(
            project,
            kind="gotcha",
            body=f"Project-specific operational fact number {index}.",
            created_by="@worker",
            confidence="confirmed" if index == 24 else "observed",
        )

    rendered = module.render_context(
        project,
        decision_limit=5,
        knowledge_limit=20,
        max_chars=4_000,
    )
    assert len(rendered) <= 4_000
    assert rendered.count("- [d_") == 5
    assert rendered.count("(id=k_") == 20
    assert "[confirmed]" in rendered
    assert "complements Hermes agent memory and skills" in rendered
    assert (project / ".datansh" / "context.md").read_text(encoding="utf-8")


def test_context_orders_knowledge_by_derived_usage(project):
    module = _load_module()
    low = module.add_knowledge(
        project,
        kind="risk",
        body="The low-use risk.",
        created_by="@worker",
    )
    high = module.add_knowledge(
        project,
        kind="risk",
        body="The frequently observed high-use risk.",
        created_by="@worker",
    )
    for _ in range(3):
        module.add_knowledge(
            project,
            kind="risk",
            body="The frequently observed high-use risk!",
            created_by="@worker",
        )

    ordered = module.list_knowledge(project, limit=2)
    assert [item.id for item in ordered] == [high.knowledge.id, low.knowledge.id]
    assert ordered[0].uses == 3


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlinks are unavailable")
def test_symlinked_state_directory_is_rejected(tmp_path):
    module = _load_module()
    workspace = tmp_path / "project"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    try:
        os.symlink(outside, workspace / ".datansh", target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink creation is not permitted: {exc}")

    with pytest.raises(ValueError, match="symlinked project state directory"):
        module.render_context(workspace)
    assert not (outside / "context.md").exists()


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlinks are unavailable")
def test_symlinked_log_file_is_rejected(tmp_path):
    module = _load_module()
    workspace = tmp_path / "project"
    outside = tmp_path / "outside.jsonl"
    workspace.mkdir()
    (workspace / ".datansh").mkdir()
    outside.write_text("", encoding="utf-8")
    try:
        os.symlink(outside, workspace / ".datansh" / "decisions.jsonl")
    except OSError as exc:
        pytest.skip(f"symlink creation is not permitted: {exc}")

    with pytest.raises(ValueError, match="symlinked project state file"):
        _decision(module, workspace)
    assert outside.read_text(encoding="utf-8") == ""


def test_workspace_is_always_explicit_and_existing(tmp_path):
    module = _load_module()
    with pytest.raises(ValueError, match="existing directory"):
        module.render_context(tmp_path / "missing")
