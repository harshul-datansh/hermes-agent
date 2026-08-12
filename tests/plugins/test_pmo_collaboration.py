"""Agent-to-agent collaboration on the board.

Requirement #7 makes task comments the only channel between agents, but a
channel is not collaboration: agents also need to pull in a specialist, hand
work over, and escalate to a named human. Each of the latter two is a state
change, and a comment alone would leave ``tasks.assignee`` pointing at the
agent that just handed the work away — so the dispatcher would give it
straight back.

Also pins the routing fix behind "why hasn't anything been picked up?":
upstream's decomposer resolves assignees against every installed Hermes
profile and falls back to the global ``kanban.default_assignee`` (unset in a
PM-OS install), so children landed on the literal profile ``default`` with
``project_id = NULL`` — unroutable by the dispatcher and invisible to every
project-scoped surface.
"""

from __future__ import annotations

import pytest

from hermes_cli import kanban_db
from plugins.pmo import collaboration


pytest_plugins = ("tests.pmo_fixtures",)


class _Cfg:
    def __init__(self, default_assignee=None, orchestrator="pm-x"):
        self.board = type("B", (), {"default_assignee": default_assignee})()
        self.orchestrator = type("O", (), {"profile": orchestrator})()


class _Scope:
    def __init__(self, cfg=None):
        self.config = cfg or _Cfg()
        self.project_id = "p_test"
        self.board_slug = "x"
        self.slug = "x"


def _roster(monkeypatch, entries):
    """entries: (handle, kind, profile, principal)"""
    from plugins.pmo import mentions

    rows = [
        mentions.Recipient(h, k, "role", p, principal)
        for h, k, p, principal in entries
    ]
    monkeypatch.setattr(collaboration.mentions, "roster", lambda scope: tuple(rows))


AGENTS = [
    ("pm", "agent", "pm-x", None),
    ("dev-1", "agent", "dev-x-1", None),
    ("qa", "agent", "qa-x", None),
    ("ops", "agent", "ops-x", None),
    ("ceo", "human", None, "human:ceo@datansh.local"),
    ("founders-office", "human", None, None),
]


class TestRoleRouting:
    def test_testing_work_routes_to_qa(self, monkeypatch):
        _roster(monkeypatch, AGENTS)
        assert collaboration.route_assignee(
            _Scope(), "Run and repair payment integration tests"
        ) == "qa-x"

    def test_deploy_work_routes_to_ops(self, monkeypatch):
        _roster(monkeypatch, AGENTS)
        assert collaboration.route_assignee(
            _Scope(), "Deploy the billing service to staging"
        ) == "ops-x"

    def test_generic_work_falls_back_to_an_engineer(self, monkeypatch):
        _roster(monkeypatch, AGENTS)
        assert collaboration.route_assignee(
            _Scope(), "Renew observability vendor contract"
        ) == "dev-x-1"

    def test_only_the_title_is_matched(self, monkeypatch):
        """Bodies mention tests and dashboards in acceptance criteria.

        Scanning them sent "Renew observability vendor contract" to @design
        because the evidence field said "dashboard link".
        """
        _roster(monkeypatch, AGENTS)
        assert collaboration.route_assignee(
            _Scope(),
            "Renew observability vendor contract",
            body="Acceptance: all tests pass. Evidence: deploy dashboard link.",
        ) == "dev-x-1"

    def test_configured_default_assignee_is_honoured(self, monkeypatch):
        _roster(monkeypatch, AGENTS)
        scope = _Scope(_Cfg(default_assignee="ops-x"))
        assert collaboration.route_assignee(scope, "Write the launch note") == "ops-x"

    def test_never_routes_to_the_orchestrator_when_a_worker_exists(self, monkeypatch):
        _roster(monkeypatch, AGENTS)
        assert collaboration.route_assignee(_Scope(), "Anything") != "pm-x"

    def test_no_agents_returns_none_rather_than_guessing(self, monkeypatch):
        _roster(monkeypatch, [("ceo", "human", None, "human:ceo@x")])
        assert collaboration.route_assignee(_Scope(), "Anything") is None


class TestTransferValidation:
    def test_unknown_handle_is_refused_with_the_valid_list(self, monkeypatch):
        _roster(monkeypatch, AGENTS)
        with pytest.raises(collaboration.CollaborationError) as excinfo:
            collaboration.transfer_task(
                _Scope(), task_id="t_1", author="dev-x-1",
                to_handle="nobody", reason="x",
            )
        message = str(excinfo.value)
        assert "@nobody" in message and "@qa" in message

    def test_transferring_to_a_human_is_refused_with_the_right_action(
        self, monkeypatch
    ):
        """A human cannot be dispatched, so this must not silently succeed."""
        _roster(monkeypatch, AGENTS)
        with pytest.raises(collaboration.CollaborationError) as excinfo:
            collaboration.transfer_task(
                _Scope(), task_id="t_1", author="dev-x-1",
                to_handle="ceo", reason="x",
            )
        assert "escalate_to_human" in str(excinfo.value)

    def test_reason_is_required(self, monkeypatch):
        _roster(monkeypatch, AGENTS)
        with pytest.raises(collaboration.CollaborationError):
            collaboration.transfer_task(
                _Scope(), task_id="t_1", author="dev-x-1",
                to_handle="qa", reason="   ",
            )


class TestEscalationValidation:
    def test_escalating_to_an_agent_is_refused(self, monkeypatch):
        _roster(monkeypatch, AGENTS)
        with pytest.raises(collaboration.CollaborationError) as excinfo:
            collaboration.escalate_to_human(
                _Scope(), task_id="t_1", author="qa-x",
                handles=["qa"], reason="x",
            )
        assert "transfer_task" in str(excinfo.value)

    def test_unknown_human_is_refused(self, monkeypatch):
        _roster(monkeypatch, AGENTS)
        with pytest.raises(collaboration.CollaborationError):
            collaboration.escalate_to_human(
                _Scope(), task_id="t_1", author="qa-x",
                handles=["ghost"], reason="x",
            )


class TestHandleNormalisation:
    def test_at_prefix_and_case_are_tolerated(self):
        assert collaboration._normalize(["@QA", "ops"]) == ["qa", "ops"]

    def test_duplicates_collapse(self):
        assert collaboration._normalize(["qa", "@qa"]) == ["qa"]

    def test_empty_is_refused(self):
        with pytest.raises(collaboration.CollaborationError):
            collaboration._normalize(["", "  "])


class TestQuestionAnswerLifecycle:
    def test_pm_answer_resumes_the_original_worker_without_question_loop(
        self, pmo_project
    ):
        scope = pmo_project.scope
        with kanban_db.connect_closing(board=scope.board_slug) as conn:
            task_id = kanban_db.create_task(
                conn,
                title="QA: clarify acceptance boundary",
                assignee="acme-dev-1",
                project_id=scope.project_id,
                board=scope.board_slug,
            )

        asked = collaboration.request_info(
            scope,
            task_id=task_id,
            author="acme-dev-1",
            handles=["pm"],
            question="Which documented compatibility target should this ticket use?",
            pause=True,
        )

        with kanban_db.connect_closing(board=scope.board_slug) as conn:
            waiting = kanban_db.get_task(conn, task_id)
        assert waiting is not None
        assert waiting.status == "blocked"
        assert waiting.assignee == "acme-dev-1"
        pending = collaboration.pending_questions(
            scope, task_id=task_id, target_identity="pm-acme"
        )
        assert len(pending) == 1
        assert pending[0].question_id == f"q_{asked.comment_id}"

        with pytest.raises(collaboration.CollaborationError, match="earlier question"):
            collaboration.request_info(
                scope,
                task_id=task_id,
                author="acme-dev-1",
                handles=["pm"],
                question="Which documented compatibility target should this ticket use?",
                pause=True,
            )

        with pytest.raises(collaboration.CollaborationError, match="pmo_answer"):
            collaboration.request_info(
                scope,
                task_id=task_id,
                author="pm-acme",
                handles=["dev-1"],
                question="Which documented compatibility target should this ticket use?",
            )

        answered = collaboration.answer_info(
            scope,
            task_id=task_id,
            author="pm-acme",
            answer="Use the compatibility target already stated in the ticket criteria.",
        )

        with kanban_db.connect_closing(board=scope.board_slug) as conn:
            resumed = kanban_db.get_task(conn, task_id)
            visible = [
                item
                for item in kanban_db.list_comments(conn, task_id)
                if item.author != "pmo-system"
            ]
        assert resumed is not None
        assert resumed.status == "ready"
        assert resumed.assignee == "acme-dev-1"
        assert answered.new_assignee == "acme-dev-1"
        assert visible[-1].author == "pm-acme"
        assert visible[-1].body.startswith("@dev-1 Answer:")
        assert collaboration.pending_questions(scope, task_id=task_id) == ()

        with pytest.raises(collaboration.CollaborationError, match="no pending question"):
            collaboration.answer_info(
                scope,
                task_id=task_id,
                author="pm-acme",
                answer="A duplicate answer must not reopen the ticket.",
            )

    def test_answer_cannot_repeat_the_question(self, pmo_project):
        scope = pmo_project.scope
        question = "Which documented compatibility target should this ticket use?"
        with kanban_db.connect_closing(board=scope.board_slug) as conn:
            task_id = kanban_db.create_task(
                conn,
                title="QA: reject repeated question",
                assignee="acme-dev-1",
                project_id=scope.project_id,
                board=scope.board_slug,
            )
        collaboration.request_info(
            scope,
            task_id=task_id,
            author="acme-dev-1",
            handles=["pm"],
            question=question,
            pause=True,
        )

        with pytest.raises(collaboration.CollaborationError, match="repeats"):
            collaboration.answer_info(
                scope, task_id=task_id, author="pm-acme", answer=question
            )


class TestHumanReviewLifecycle:
    def test_human_handoff_finishes_ready_with_actionable_instructions(
        self, pmo_project, monkeypatch
    ):
        scope = pmo_project.scope
        original_roster = collaboration.mentions.roster

        def roster(with_scope):
            return original_roster(with_scope) + (
                collaboration.mentions.Recipient(
                    "reviewer",
                    "human",
                    "reviewer",
                    None,
                    "human:reviewer@example.test",
                ),
            )

        monkeypatch.setattr(collaboration.mentions, "roster", roster)
        with kanban_db.connect_closing(board=scope.board_slug) as conn:
            task_id = kanban_db.create_task(
                conn,
                title="QA: human review handoff",
                assignee="acme-dev-1",
                project_id=scope.project_id,
                board=scope.board_slug,
            )

        result = collaboration.escalate_to_human(
            scope,
            task_id=task_id,
            author="acme-dev-1",
            handles=["reviewer"],
            reason="Review the proposed setting and attach the recorded decision.",
        )

        with kanban_db.connect_closing(board=scope.board_slug) as conn:
            task = kanban_db.get_task(conn, task_id)
            visible = [
                item
                for item in kanban_db.list_comments(conn, task_id)
                if item.author != "pmo-system"
            ]
        assert task is not None
        assert task.status == "ready"
        assert task.assignee == "human:reviewer@example.test"
        assert result.new_assignee == "human:reviewer@example.test"
        assert "Requested action:" in visible[-1].body
