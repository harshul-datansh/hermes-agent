"""Transferring a ticket the sending agent is currently running.

This is the normal case, not an edge case: an agent decides mid-run that the
work belongs to someone else, and it holds the claim at that moment.

The original implementation used ``reassign_task(reclaim_first=True)``, which
reads correctly and fails in production. Reclaiming pulls the claim out from
under a process that is still alive, so the dispatcher sees a live pid that no
longer owns its task, records ``crashed``, respawns the *same* agent on the
*same* ticket, and after three rounds gives up — leaving the ticket blocked
and still owned by the sender, with a handoff comment on the board that makes
it look as though the transfer worked.

Unit tests could not catch it because they called the handler outside a
worker, where there is no claim to pull. It took a live multi-hop run:
``crashed`` -> ``claimed`` -> ``spawned`` -> ``crashed`` -> ``gave_up``.
"""

from __future__ import annotations

import pytest

from hermes_cli import kanban_db


@pytest.fixture()
def project(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_KANBAN_HOME", str(home))
    from tests.pmo_fixtures import make_pmo_test_project

    return make_pmo_test_project(tmp_path / "src", slug="tx", name="Transfer Test")


def _ticket(project, assignee: str) -> str:
    from plugins.pmo import ticket as ticket_mod

    return ticket_mod.create_ticket(
        board_slug=project.board_slug,
        title="Work that belongs to someone else",
        outcome="Move this ticket to the right specialist.",
        assignee=assignee,
        acceptance_criteria=["It ends up owned by the receiving agent."],
        evidence=["The board shows the handoff."],
    ).task_id


class TestTransferFromAClaimedTask:
    def test_ownership_moves_and_the_ticket_stays_dispatchable(
        self, project, monkeypatch
    ):
        """The receiving agent must actually be able to be dispatched.

        A transfer that leaves the ticket ``blocked`` is not a transfer — it
        is a stall that needs a human to notice and clear.
        """
        from plugins.pmo import collaboration

        scope = project.scope
        task_id = _ticket(project, "tx-dev-1")

        with kanban_db.connect_closing(board=scope.board_slug) as conn:
            claimed = kanban_db.claim_task(conn, task_id, claimer="test:1")
        assert claimed, "fixture must reproduce a running, claimed ticket"

        result = collaboration.transfer_task(
            scope, task_id=task_id, author="tx-dev-1",
            to_handle="qa", reason="needs independent verification",
        )
        assert result.new_assignee == "tx-qa"

        with kanban_db.connect_closing(board=scope.board_slug) as conn:
            task = kanban_db.get_task(conn, task_id)
        assert task.assignee == "tx-qa"
        assert task.status in {"ready", "todo"}, (
            f"ticket left in {task.status!r}; the receiving agent will never "
            "be dispatched"
        )

    def test_the_handoff_is_on_the_board(self, project):
        from plugins.pmo import collaboration

        scope = project.scope
        task_id = _ticket(project, "tx-dev-1")
        with kanban_db.connect_closing(board=scope.board_slug) as conn:
            kanban_db.claim_task(conn, task_id, claimer="test:1")

        collaboration.transfer_task(
            scope, task_id=task_id, author="tx-dev-1",
            to_handle="qa", reason="needs independent verification",
        )
        with kanban_db.connect_closing(board=scope.board_slug) as conn:
            bodies = [c.body for c in kanban_db.list_comments(conn, task_id)]
        assert any("@qa" in b and "Transferring" in b for b in bodies)
        assert any("needs independent verification" in b for b in bodies)

    def test_transfer_works_when_nothing_holds_the_claim(self, project):
        """The unclaimed path must keep working — it is how the dashboard
        transfers a ticket nobody has picked up yet."""
        from plugins.pmo import collaboration

        scope = project.scope
        task_id = _ticket(project, "tx-dev-1")
        result = collaboration.transfer_task(
            scope, task_id=task_id, author="pm-tx",
            to_handle="qa", reason="better suited",
        )
        assert result.new_assignee == "tx-qa"
        with kanban_db.connect_closing(board=scope.board_slug) as conn:
            task = kanban_db.get_task(conn, task_id)
        assert task.assignee == "tx-qa"
        assert task.status in {"ready", "todo"}

    def test_a_second_transfer_still_moves_ownership(self, project):
        """dev -> qa -> ops is the shape of a real multi-hop chain."""
        from plugins.pmo import collaboration

        scope = project.scope
        task_id = _ticket(project, "tx-dev-1")
        collaboration.transfer_task(
            scope, task_id=task_id, author="tx-dev-1",
            to_handle="qa", reason="verify it",
        )
        with kanban_db.connect_closing(board=scope.board_slug) as conn:
            kanban_db.claim_task(conn, task_id, claimer="test:2")
        collaboration.transfer_task(
            scope, task_id=task_id, author="tx-qa",
            to_handle="dev-2", reason="failed verification, back to an engineer",
        )
        with kanban_db.connect_closing(board=scope.board_slug) as conn:
            task = kanban_db.get_task(conn, task_id)
        assert task.assignee == "tx-dev-2"
        assert task.status in {"ready", "todo"}


class TestClaimReleaseSequence:
    """Pins *how* the claim is released, because the end state cannot.

    Every assertion in the class above passes on the broken implementation
    too: reclaim-then-assign and block-then-assign-then-unblock leave exactly
    the same rows behind. The difference is only visible to the dispatcher,
    which watches a live process — so a database-level test cannot see it at
    all, and asserting on the end state gives false confidence.

    So this asserts the call sequence. Normally that would be testing the
    implementation rather than the behaviour; here the sequence *is* the
    contract with the dispatcher, and it is the only part of the fix a test
    can actually hold onto.
    """

    def test_claim_is_released_by_blocking_not_by_reclaiming(
        self, project, monkeypatch
    ):
        from plugins.pmo import collaboration

        scope = project.scope
        task_id = _ticket(project, "tx-dev-1")
        with kanban_db.connect_closing(board=scope.board_slug) as conn:
            kanban_db.claim_task(conn, task_id, claimer="test:1")

        calls: list[str] = []
        for name in ("block_task", "unblock_task", "reassign_task"):
            original = getattr(kanban_db, name)

            def spy(*args, _name=name, _original=original, **kwargs):
                calls.append(_name)
                return _original(*args, **kwargs)

            monkeypatch.setattr(collaboration.kanban_db, name, spy)

        collaboration.transfer_task(
            scope, task_id=task_id, author="tx-dev-1",
            to_handle="qa", reason="needs independent verification",
        )

        assert "block_task" in calls, (
            "the claim must be released through block_task; reclaiming it "
            "from a live worker is what the dispatcher reports as a crash"
        )
        assert calls.index("block_task") < calls.index("reassign_task")
        assert calls.index("reassign_task") < calls.index("unblock_task"), (
            "unblocking before ownership moves makes the ticket claimable by "
            "the agent that just handed it away"
        )


class TestEscalateStillBlocks:
    def test_escalation_blocks_so_no_agent_loops_on_it(self, project):
        """The contrast that made the transfer bug visible: escalate is
        *supposed* to end blocked, transfer is not."""
        from plugins.pmo import collaboration

        scope = project.scope
        task_id = _ticket(project, "tx-dev-1")
        with kanban_db.connect_closing(board=scope.board_slug) as conn:
            kanban_db.claim_task(conn, task_id, claimer="test:1")

        collaboration.escalate_to_human(
            scope, task_id=task_id, author="tx-dev-1",
            handles=["founders-office"], reason="needs a business decision",
        )
        with kanban_db.connect_closing(board=scope.board_slug) as conn:
            task = kanban_db.get_task(conn, task_id)
        assert task.status == "blocked"
