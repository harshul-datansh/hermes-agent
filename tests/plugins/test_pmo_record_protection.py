"""PM-OS records must survive the triage specifier.

Found live: a CFO->CEO approval was raised, escalated to rank 100, and then
became **undecidable**. The triage specifier had rewritten its title and body
via the auxiliary LLM, erasing ``[pmo:approval:v1]`` along with the stored
required rank, target task and raiser. ``pmo approval decide`` then reported
"task is not a PM-OS approval", and the record had been promoted to ``ready``
— into the dispatcher's claim set.

The collision is structural, not incidental: PM-OS parks records in ``triage``
because the dispatcher ignores that column, while the specifier treats every
``triage`` task as a rough idea to flesh out. Its only skip condition is
``status != "triage"``, so PM-OS records are guaranteed targets.

This is requirement #8 ("most important" per the CEO) failing in any project
where an agent is actually running.
"""

from __future__ import annotations

import pytest

from plugins.pmo import workflow


class _Task:
    def __init__(self, body="", assignee="", title=""):
        self.body = body
        self.assignee = assignee
        self.title = title


class TestProtectedRecordDetection:
    def test_approval_is_protected(self):
        task = _Task(body="Title: X\n" + workflow.APPROVAL_MARKER)
        assert workflow.protected_record_kind(task) == "approval"

    def test_human_task_is_protected_via_marker(self):
        task = _Task(body="do it\n" + workflow.HUMAN_TASK_MARKER)
        assert workflow.protected_record_kind(task) == "human-task"

    def test_human_task_is_protected_via_assignee_after_body_rewrite(self):
        """The durable path: prose gone, assignee column still says human."""
        task = _Task(body="LLM-rewritten prose", assignee="human:ceo@datansh.local")
        assert workflow.protected_record_kind(task) == "human-task"

    def test_ordinary_ticket_is_not_protected(self):
        task = _Task(body="Goal: ship it", assignee="dev-hedgi-app-1")
        assert workflow.protected_record_kind(task) is None

    def test_empty_task_is_not_protected(self):
        assert workflow.protected_record_kind(_Task()) is None


class TestSpecifyEndpointRefusesRecords:
    """The specify endpoint is the actual damage path, so guard it there."""

    def _endpoint_source(self):
        from pathlib import Path

        path = (
            Path(__file__).resolve().parents[2]
            / "plugins" / "pmo" / "dashboard" / "plugin_api.py"
        )
        source = path.read_text(encoding="utf-8")
        start = source.index('@router.post("/tasks/{task_id}/specify")')
        end = source.index("@router.", start + 10)
        return source[start:end]

    def test_endpoint_consults_protected_record_kind(self):
        body = self._endpoint_source()
        assert "protected_record_kind" in body, (
            "the specify endpoint must refuse PM-OS records; without this an "
            "approval is silently rewritten into an undecidable ticket"
        )

    def test_guard_runs_before_the_specifier_is_invoked(self):
        """Order matters: refusing after the LLM call has already destroyed it."""
        body = self._endpoint_source()
        assert body.index("protected_record_kind") < body.index(
            "kanban_specify"
        ), "the protected-record check must precede the specifier import/call"
