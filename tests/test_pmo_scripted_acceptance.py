"""Revised day-one acceptance using scripted models and production PMO APIs."""

from __future__ import annotations

from functools import partial

from hermes_cli import kanban_db
from plugins.pmo import approval, comment, mentions, workflow
from tests.pmo_harness import FakeModel, HermesHistory, ScriptedAgent, Text, ToolCall
from toolsets import resolve_toolset


pytest_plugins = ("tests.pmo_fixtures",)


def _history(pmo_project, name: str) -> HermesHistory:
    return HermesHistory(
        pmo_project.workspace.parent / f"{name}-state.db",
        f"{pmo_project.slug}-{name}",
    )


def _create_handler(pmo_project):
    return partial(
        workflow.create_draft,
        project_ref=pmo_project.slug,
        board_slug=pmo_project.board_slug,
        actor_profile=f"pm-{pmo_project.slug}",
    )


def _finalize_handler(pmo_project):
    return partial(
        workflow.finalize_ticket,
        project_ref=pmo_project.slug,
        board_slug=pmo_project.board_slug,
        actor_profile=f"pm-{pmo_project.slug}",
    )


def test_scripted_day_one_decompose_approve_finalize_work_and_review(pmo_project):
    # The CFO instruction enters through the production comment router.  Its
    # durable route selects the configured PM profile and reply channel.
    posted = mentions.post_comment(
        pmo_project.scope,
        task_id=pmo_project.founders_office_task_id,
        author="cfo",
        body="@pm we need SSO before the 15th. Budget is tight.",
        request_id="acceptance-instruction",
    )
    wakes: list[tuple[str | None, dict[str, object]]] = []
    delivered = mentions.drain_pending(
        pmo_project.scope,
        lambda recipient, context: wakes.append((recipient.profile, context)),
    )
    assert posted.handles == ("pm",)
    assert delivered
    assert wakes == [
        (
            "pm-acme",
            {
                "event": "pmo_mention",
                "project_id": pmo_project.project_id,
                "board": "acme",
                "task_id": pmo_project.founders_office_task_id,
                "source_comment_id": posted.comment_id,
                "author": "cfo",
                "body": "@pm we need SSO before the 15th. Budget is tight.",
                "reply_via": "task_comment",
            },
        )
    ]

    # A strict provider decomposes the request into an epic and four native
    # draft tickets. References are resolved from earlier production results.
    pm_model = FakeModel(
        [
            ToolCall(
                "pmo_create_draft",
                {
                    "title": "Deliver Acme SSO",
                    "outcome": "Acme users can sign in with the corporate identity provider.",
                    "assignee": "pm-acme",
                    "acceptance_criteria": ["All child tickets are complete"],
                    "evidence": ["Linked child ticket results"],
                },
            ),
            ToolCall(
                "pmo_create_draft",
                {
                    "title": "Implement SSO redirect",
                    "outcome": "Unauthenticated users enter the provider flow.",
                    "assignee": "acme-dev-1",
                    "acceptance_criteria": ["Redirect preserves the return URL"],
                    "evidence": ["Focused redirect test output"],
                    "parent_task_ids": ["$1.task_id"],
                    "labels": ["spend"],
                },
            ),
            ToolCall(
                "pmo_create_draft",
                {
                    "title": "Implement SSO callback",
                    "outcome": "Valid provider callbacks establish a user session.",
                    "assignee": "acme-dev-2",
                    "acceptance_criteria": ["Invalid state is rejected"],
                    "evidence": ["Focused callback test output"],
                    "parent_task_ids": ["$1.task_id"],
                },
            ),
            ToolCall(
                "pmo_create_draft",
                {
                    "title": "Document SSO operations",
                    "outcome": "Operators can configure and recover the integration.",
                    "assignee": "acme-dev-1",
                    "acceptance_criteria": ["Runbook includes rollback"],
                    "evidence": ["Runbook review link"],
                    "parent_task_ids": ["$1.task_id"],
                },
            ),
            ToolCall(
                "pmo_create_draft",
                {
                    "title": "Review SSO delivery",
                    "outcome": "The complete SSO flow has independent review evidence.",
                    "assignee": "acme-qa",
                    "acceptance_criteria": ["Redirect and callback tests are reviewed"],
                    "evidence": ["QA review verdict"],
                    "parent_task_ids": ["$1.task_id", "$2.task_id"],
                },
            ),
            ToolCall(
                "pmo_raise_approval",
                {
                    "target_task_id": "$2.task_id",
                    "title": "Approve SSO provider spend",
                    "detail": "The provider contract consumes quarterly budget.",
                    "raised_by": "cfo",
                    "required_rank": 70,
                    "approver_profile": "cfo",
                },
            ),
            Text("The epic and four draft tickets are ready; provider spend awaits approval."),
        ]
    )
    normal_pm_tools = tuple(
        sorted(
            set(resolve_toolset("hermes-cli"))
            | {"pmo_create_draft", "pmo_raise_approval", "pmo_comment"}
        )
    )
    pm = ScriptedAgent(
        model=pm_model,
        tools={
            "pmo_create_draft": _create_handler(pmo_project),
            "pmo_raise_approval": partial(
                approval.raise_approval, board_slug=pmo_project.board_slug
            ),
        },
        offered_tools=normal_pm_tools,
        history=_history(pmo_project, "pm-decompose"),
        execution_lanes={
            "pmo_create_draft": "registry",
            "pmo_raise_approval": "registry",
        },
    )
    pm.run(wakes[0][1]["body"], metadata=wakes[0][1])
    pm_model.assert_script_exhausted()
    assert all(
        {"delegate_task", "memory", "skill_manage", "terminal", "read_file"}
        <= set(request.offered_tools)
        for request in pm_model.calls()
    )
    epic, redirect, callback, docs, qa_review, gate = pm.outputs

    with kanban_db.connect_closing(board="acme") as conn:
        drafts = [kanban_db.get_task(conn, item.task_id) for item in pm.outputs[:5]]
    assert [item.status for item in drafts] == ["triage"] * 5
    assert {item.assignee for item in drafts[1:]} == {
        "acme-dev-1",
        "acme-dev-2",
        "acme-qa",
    }

    # Native approval linkage prevents finalization until a human escalates
    # and a sufficiently-ranked human makes the terminal decision.
    escalated = approval.escalate_approval(
        board_slug="acme",
        approval_id=gate.approval_id,
        to_rank=100,
        to_approver_profile="ceo",
        reason="Over the quarterly cap",
        actor="cfo",
        actor_rank=70,
    )
    assert escalated.required_rank == 100
    approved = approval.decide_approval(
        board_slug="acme",
        approval_id=gate.approval_id,
        decision="approved",
        actor="ceo",
        actor_rank=100,
        note="Approved inside the revised annual plan.",
    )
    assert approved.status == "approved"

    finalize_model = FakeModel(
        [
            *[
                ToolCall("pmo_finalize", {"task_id": item.task_id})
                for item in (epic, redirect, callback, docs, qa_review)
            ],
            Text("All five cards passed the finalization gate."),
        ]
    )
    finalizer = ScriptedAgent(
        model=finalize_model,
        tools={"pmo_finalize": _finalize_handler(pmo_project)},
        offered_tools=("pmo_finalize", "pmo_comment"),
        history=_history(pmo_project, "pm-finalize"),
    )
    finalizer.run("Approval is complete; finalize the SSO work.")
    assert all(result.status in {"ready", "todo"} for result in finalizer.outputs)

    # The epic releases its native child dependencies. A worker then claims
    # one implementation ticket and persists the normal run metadata.
    with kanban_db.connect_closing(board="acme") as conn:
        assert kanban_db.complete_task(
            conn,
            epic.task_id,
            result="Decomposition accepted.",
            summary="Four scoped delivery tickets created.",
        )
        claimed = kanban_db.claim_task(conn, redirect.task_id, claimer="acme-dev-1")
        assert claimed is not None and claimed.status == "running"

    worker_model = FakeModel(
        [
            ToolCall(
                "pmo_comment",
                {
                    "task_id": redirect.task_id,
                    "comment_type": "handoff",
                    "author": "acme-dev-1",
                    "summary": "SSO redirect implementation and tests are complete.",
                    "owner": "acme-qa",
                    "evidence": ["pytest tests/test_sso_redirect.py -q: passed"],
                },
            ),
            ToolCall(
                "kanban_complete",
                {
                    "task_id": redirect.task_id,
                    "result": "Redirect implementation complete.",
                    "summary": "Return URL is preserved and invalid destinations are rejected.",
                    "metadata": {"tests_run": ["tests/test_sso_redirect.py"]},
                },
            ),
            Text("Implementation is complete and handed to QA."),
        ]
    )

    def complete_native(**kwargs):
        with kanban_db.connect_closing(board="acme") as conn:
            return kanban_db.complete_task(conn, **kwargs)

    worker = ScriptedAgent(
        model=worker_model,
        tools={
            "pmo_comment": partial(comment.add_structured_comment, board_slug="acme"),
            "kanban_complete": complete_native,
        },
        offered_tools=("pmo_comment", "kanban_complete"),
        history=_history(pmo_project, "worker"),
    )
    worker.run("Implement the redirect ticket.")
    assert worker.outputs[-1] is True

    # Completion of the implementation releases the QA card. QA uses the
    # same native claim/complete boundary and records its review as a durable
    # task comment rather than an agent-to-agent message.
    with kanban_db.connect_closing(board="acme") as conn:
        qa_claim = kanban_db.claim_task(conn, qa_review.task_id, claimer="acme-qa")
        assert qa_claim is not None and qa_claim.status == "running"

    reviewer_model = FakeModel(
        [
            ToolCall(
                "pmo_comment",
                {
                    "task_id": redirect.task_id,
                    "comment_type": "review",
                    "author": "acme-qa",
                    "summary": "Redirect behavior and evidence match acceptance criteria.",
                    "verdict": "approved",
                    "evidence": ["Reviewed focused test output"],
                },
            ),
            ToolCall(
                "kanban_complete",
                {
                    "task_id": qa_review.task_id,
                    "result": "QA approved.",
                    "summary": "Redirect ticket independently reviewed.",
                },
            ),
            Text("QA review completed with an approved verdict."),
        ]
    )
    reviewer = ScriptedAgent(
        model=reviewer_model,
        tools={
            "pmo_comment": partial(comment.add_structured_comment, board_slug="acme"),
            "kanban_complete": complete_native,
        },
        offered_tools=("pmo_comment", "kanban_complete"),
        history=_history(pmo_project, "reviewer"),
    )
    reviewer.run("Review the completed redirect work.")

    with kanban_db.connect_closing(board="acme") as conn:
        redirect_task = kanban_db.get_task(conn, redirect.task_id)
        review_task = kanban_db.get_task(conn, qa_review.task_id)
        redirect_comments = kanban_db.list_comments(conn, redirect.task_id)
        redirect_runs = kanban_db.list_runs(conn, task_id=redirect.task_id)
    assert redirect_task.status == "done"
    assert review_task.status == "done"
    assert any("[pmo:handoff]" in item.body for item in redirect_comments)
    assert any("[pmo:review]" in item.body for item in redirect_comments)
    assert any(run.outcome == "completed" for run in redirect_runs)
