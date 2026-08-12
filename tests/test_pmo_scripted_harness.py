"""Contracts for the deterministic plan-017 model harness and fixtures."""

from __future__ import annotations

import time

import pytest

from hermes_state import SessionDB
from hermes_cli import kanban_db
from plugins.pmo import approval, mentions, workflow
from tests.pmo_harness import (
    AssistantTurn,
    FakeModel,
    HermesHistory,
    ScriptExhausted,
    ScriptedAgent,
    Text,
    ToolCall,
    trace_stages,
)


pytest_plugins = ("tests.pmo_fixtures",)


def _history(tmp_path, name: str = "turn") -> HermesHistory:
    return HermesHistory(tmp_path / "state.db", f"pmo-{name}")


def test_fake_model_records_full_ordered_trace_and_real_session_history(tmp_path):
    model = FakeModel(
        [
            ToolCall("double", {"value": 4}),
            Text("The answer is 8."),
        ]
    )
    history = _history(tmp_path)
    agent = ScriptedAgent(
        model=model,
        tools={"double": lambda value: value * 2},
        offered_tools=("double",),
        history=history,
        execution_lanes={"double": "registry"},
    )

    assert agent.run("Calculate it.") == "The answer is 8."
    model.assert_script_exhausted()
    assert trace_stages(agent) == (
        "provider_request",
        "normalized_assistant_message",
        "policy_decision",
        "execution_lane",
        "tool_result",
        "persisted_history",
        "next_provider_request",
        "provider_request",
        "normalized_assistant_message",
        "persisted_history",
        "finalizer_outcome",
    )
    assert model.calls()[1].messages[-1]["role"] == "tool"

    reopened = SessionDB(db_path=tmp_path / "state.db")
    durable = reopened.get_messages_as_conversation("pmo-turn")
    assert [item["role"] for item in durable] == ["user", "assistant", "tool", "assistant"]
    assert durable[-1]["content"] == "The answer is 8."


def test_fake_model_fails_loudly_when_agent_requests_an_unscripted_turn(tmp_path):
    model = FakeModel([ToolCall("noop", {})])
    agent = ScriptedAgent(
        model=model,
        tools={"noop": lambda: "ok"},
        offered_tools=("noop",),
        history=_history(tmp_path),
    )

    with pytest.raises(ScriptExhausted, match="scripted turns exhausted"):
        agent.run("Keep going until final text.")


def test_pm_toolset_excludes_messaging_tools(tmp_path):
    executed: list[str] = []
    model = FakeModel(
        [
            ToolCall("send_message", {"body": "bypass comments"}),
            Text("I used the ticket channel instead."),
        ]
    )
    agent = ScriptedAgent(
        model=model,
        tools={"send_message": lambda **_: executed.append("bad")},
        offered_tools=("pmo_comment",),
        history=_history(tmp_path),
    )

    agent.run("Tell the other agent.")

    assert executed == []
    assert model.calls()[0].offered_tools == ("pmo_comment",)
    decisions = [event.value for event in agent.trace if event.stage == "policy_decision"]
    lanes = [event.value for event in agent.trace if event.stage == "execution_lane"]
    assert decisions[0].allowed is False
    assert decisions[0].reason == "tool_not_offered"
    assert lanes == ["refused"]


def test_parallel_independent_calls_are_both_persisted(tmp_path):
    model = FakeModel(
        [
            AssistantTurn(
                tool_calls=(
                    ToolCall("echo", {"value": "one"}),
                    ToolCall("echo", {"value": "two"}),
                )
            ),
            Text("Both completed."),
        ]
    )
    agent = ScriptedAgent(
        model=model,
        tools={"echo": lambda value: value},
        offered_tools=("echo",),
        history=_history(tmp_path),
        execution_lanes={"echo": "parallel-registry"},
    )

    agent.run("Run both.")

    assert agent.outputs == ["one", "two"]
    assert [
        event.value for event in agent.trace if event.stage == "execution_lane"
    ] == ["parallel-registry", "parallel-registry"]
    assert [item["role"] for item in model.calls()[1].messages[-2:]] == ["tool", "tool"]


def test_cross_project_handle_is_unknown(pmo_two_projects):
    acme, beta = pmo_two_projects

    assert acme.board_slug != beta.board_slug
    assert {item.handle for item in mentions.roster(acme.scope)} >= {"pm", "dev-1", "qa"}
    assert {item.handle for item in mentions.roster(beta.scope)} == {
        "pm",
        "founders-office",
        "beta-dev",
    }
    _, unknown = mentions.resolve(beta.scope, ["dev-1"])
    assert unknown == ["dev-1"]


def test_draft_tickets_are_not_dispatchable(pmo_project):
    draft = workflow.create_draft(
        project_ref=pmo_project.slug,
        board_slug=pmo_project.board_slug,
        actor_profile="pm-acme",
        title="Implement guarded draft",
        outcome="The draft remains gated until the PM finalizes it.",
        assignee="acme-dev-1",
        acceptance_criteria=["The worker cannot claim a draft"],
        evidence=["Native claim result"],
    )

    with kanban_db.connect_closing(board=pmo_project.board_slug) as conn:
        stored = kanban_db.get_task(conn, draft.task_id)
        claimed = kanban_db.claim_task(conn, draft.task_id, claimer="acme-dev-1")
    assert stored is not None and stored.status == "triage"
    assert claimed is None


def test_cfo_can_escalate_to_ceo(pmo_project):
    with kanban_db.connect_closing(board=pmo_project.board_slug) as conn:
        target = kanban_db.create_task(
            conn,
            title="Approve provider spend",
            project_id=pmo_project.project_id,
            board=pmo_project.board_slug,
        )
    request = approval.raise_approval(
        board_slug=pmo_project.board_slug,
        target_task_id=target,
        title="Approve provider spend",
        detail="The contract exceeds the team threshold.",
        raised_by="cfo",
        required_rank=70,
        approver_profile="cfo",
    )

    escalated = approval.escalate_approval(
        board_slug=pmo_project.board_slug,
        approval_id=request.approval_id,
        to_rank=100,
        to_approver_profile="ceo",
        reason="Over the quarterly cap",
        actor="cfo",
        actor_rank=70,
    )

    assert escalated.required_rank == 100
    assert escalated.approver_profile == "ceo"


def test_fake_clock_advances_without_sleep(fake_clock):
    before = time.time()
    fake_clock.advance(minutes=30, seconds=5)
    assert time.time() - before == 1_805
