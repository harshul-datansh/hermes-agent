"""Human tasks must stay human after an agent rewrites the body.

Requirement #5 delegates work to *people*, and the dashboard promises human
tasks "are never dispatched to an agent". That promise originally rested on a
text marker inside the task body — but the ``specified`` step lets an agent
rewrite the body wholesale, which dropped the marker. The task was then
reclassified as an agent task, promoted to ``ready``, and entered the
dispatcher's claim set with an assignee matching no profile, where it retries
as nonspawnable forever instead of reaching a person.

These tests pin the durable signal: the ``assignee`` prefix, which lives in a
column no prose rewrite can touch.
"""

from __future__ import annotations

import pytest

from plugins.pmo import workflow


class _Task:
    """Minimal stand-in with the two fields the check reads."""

    def __init__(self, assignee="", body=""):
        self.assignee = assignee
        self.body = body


class TestHumanTaskDetection:
    def test_marker_alone_still_identifies_a_human_task(self):
        """Back-compat: tasks created before this fix carry only the marker."""
        task = _Task(assignee="", body="do the thing\n" + workflow.HUMAN_TASK_MARKER)
        assert workflow.is_human_task(task) is True

    def test_human_assignee_survives_the_body_being_rewritten(self):
        """The regression: a specifier agent replaces the body, marker gone."""
        task = _Task(
            assignee="human:ceo@datansh.local",
            body="Coordinate completion of the vendor MSA signature process.",
        )
        assert workflow.is_human_task(task) is True

    def test_dashboard_principal_is_also_human(self):
        task = _Task(assignee="dashboard:local-operator", body="rewritten")
        assert workflow.is_human_task(task) is True

    @pytest.mark.parametrize("assignee", ["HUMAN:CEO@X", "Human:ceo@x"])
    def test_prefix_match_is_case_insensitive(self, assignee):
        assert workflow.is_human_task(_Task(assignee=assignee, body="x")) is True

    def test_agent_profile_is_not_a_human_task(self):
        task = _Task(assignee="dev-hedgi-app-1", body="ordinary ticket body")
        assert workflow.is_human_task(task) is False

    def test_empty_task_is_not_a_human_task(self):
        assert workflow.is_human_task(_Task()) is False

    def test_a_profile_merely_containing_human_is_not_a_human_task(self):
        """Guards against substring confusion: prefix, not ``in``."""
        task = _Task(assignee="qa-human-factors", body="ordinary ticket body")
        assert workflow.is_human_task(task) is False
