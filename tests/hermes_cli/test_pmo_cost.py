"""Plan 012: cost attribution, budgets, and additive model routing."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
import time

import pytest

from hermes_cli import kanban_db
from plugins.platforms.pmo.adapter import ensure_project_conversations
from plugins.pmo import cost, project_scope


pytest_plugins = ("tests.pmo_fixtures",)


def _usage(
    *, cost_usd: str = "1.2500", turns: int = 4, session_id: str = "sess-cost-1"
) -> cost.UsageRecord:
    return cost.UsageRecord(
        session_id=session_id,
        model="anthropic/claude-sonnet-4.6",
        provider="anthropic",
        input_tokens=1_000,
        cached_input_tokens=800,
        output_tokens=200,
        cost_usd=Decimal(cost_usd),
        turns=turns,
        tool_calls=7,
        wall_seconds=12,
    )


def _scope_with(pmo_project, *, budget=None, models=None, agents=None):
    config = pmo_project.scope.config.model_copy(
        update={
            "budget": budget or pmo_project.scope.config.budget,
            "models": models or pmo_project.scope.config.models,
            "agents": agents or pmo_project.scope.config.agents,
        }
    )
    return replace(pmo_project.scope, config=config)


def _running_task(pmo_project, *, assignee=None):
    with kanban_db.connect_closing(board=pmo_project.board_slug) as conn:
        task_id = kanban_db.create_task(
            conn,
            title="Implement bounded work",
            body="Cost test",
            assignee=assignee or f"{pmo_project.slug}-dev-1",
            created_by=f"pm-{pmo_project.slug}",
            initial_status="running",
            board=pmo_project.board_slug,
            project_id=pmo_project.project_id,
        )
        return task_id


def test_usage_recorded_on_run_end_and_cost_is_decimal_string(pmo_project):
    task_id = _running_task(pmo_project)
    with kanban_db.connect_closing(board=pmo_project.board_slug) as conn:
        task = kanban_db.get_task(conn, task_id)
        assert task is not None
        assert cost.complete_task_with_usage(
            conn,
            scope=pmo_project.scope,
            task_id=task_id,
            usage=_usage(cost_usd="0.9142"),
            summary="Focused tests pass",
            expected_run_id=task.current_run_id,
        )
        run = kanban_db.latest_run(conn, task_id)

    assert run is not None
    assert run.metadata["worker_session_id"] == "sess-cost-1"
    assert run.metadata["usage"]["cost_usd"] == "0.9142"
    assert isinstance(run.metadata["usage"]["cost_usd"], str)


def test_cached_tokens_recorded_separately_and_aggregated(pmo_project):
    task_id = _running_task(pmo_project)
    with kanban_db.connect_closing(board=pmo_project.board_slug) as conn:
        task = kanban_db.get_task(conn, task_id)
        cost.complete_task_with_usage(
            conn,
            scope=pmo_project.scope,
            task_id=task_id,
            usage=_usage(),
            summary="Done",
            expected_run_id=task.current_run_id,
        )

    spend = cost.project_spend(pmo_project.scope)
    assert spend.input_tokens == 1_000
    assert spend.cached_input_tokens == 800
    assert spend.output_tokens == 200
    assert spend.cache_hit_rate == Decimal(800) / Decimal(1_800)
    assert spend.cost_usd == Decimal("1.2500")


def test_normal_kanban_completion_is_backfilled_through_public_edit_seam(pmo_project):
    task_id = _running_task(pmo_project)
    with kanban_db.connect_closing(board=pmo_project.board_slug) as conn:
        assert kanban_db.complete_task(
            conn,
            task_id,
            summary="Normal worker completion",
            metadata={"worker_session_id": "normal-session"},
        )

    assert cost.capture_completed_session(
        scope=pmo_project.scope,
        task_id=task_id,
        usage=_usage(session_id="normal-session", cost_usd="0.50"),
    )
    with kanban_db.connect_closing(board=pmo_project.board_slug) as conn:
        run = kanban_db.latest_run(conn, task_id)
    assert run.metadata["usage"]["cost_usd"] == "0.50"
    assert run.metadata["worker_session_id"] == "normal-session"


def test_pm_thread_turn_gets_synthetic_native_run(pmo_project):
    synthetic_id = cost.record_pm_thread_turn(
        scope=pmo_project.scope,
        usage=_usage(session_id="pm-thread-session"),
        thread_task_id=pmo_project.founders_office_task_id,
    )
    with kanban_db.connect_closing(board=pmo_project.board_slug) as conn:
        task = kanban_db.get_task(conn, synthetic_id)
        run = kanban_db.latest_run(conn, synthetic_id)

    assert task is not None and task.status == "archived"
    assert run is not None
    assert run.metadata["attributed_task_id"] == pmo_project.founders_office_task_id
    assert run.metadata["agent_kind"] == "pm"
    assert cost.project_spend(pmo_project.scope).cost_usd == Decimal("1.2500")


def test_project_spend_includes_openai_codex_pmo_session_token_usage(pmo_project):
    """Subscription-priced Codex turns still appear in model analytics."""

    now = time.time()
    conversations = ensure_project_conversations(pmo_project.scope)
    session = {
        "id": "codex-pmo-session",
        "source": "pmo",
        "chat_id": pmo_project.board_slug,
        "thread_id": conversations.founders_office_thread_id,
        "profile_name": "pm-acme",
        "model": "gpt-5.6-luna",
        "billing_provider": "openai-codex",
        "input_tokens": 12_345,
        "cache_read_tokens": 4_321,
        "output_tokens": 678,
        "actual_cost_usd": 0,
        "estimated_cost_usd": 0,
        "tool_call_count": 3,
        "started_at": now,
        "ended_at": None,
    }

    class SessionUsage:
        def list_gateway_sessions(self, *, platform=None, active_only=False):
            return [session]

        def get_session(self, session_id):
            return session if session_id == session["id"] else None

        def get_messages(self, session_id):
            return [{"role": "user"}, {"role": "assistant"}]

    spend = cost.project_spend(pmo_project.scope, session_db=SessionUsage(), now=now)

    assert spend.cost_usd == Decimal("0")
    assert spend.input_tokens == 12_345
    assert spend.cached_input_tokens == 4_321
    assert spend.output_tokens == 678
    model = spend.models["openai-codex:gpt-5.6-luna"]
    assert model.input_tokens == 12_345
    assert model.output_tokens == 678
    assert model.cost_usd == Decimal("0")


def test_hard_cap_blocks_run_and_preserves_usage_attribution(pmo_project):
    scope = _scope_with(
        pmo_project,
        budget=project_scope.BudgetConfig(per_ticket_hard_cap=Decimal("1.00")),
    )
    task_id = _running_task(pmo_project)
    with kanban_db.connect_closing(board=pmo_project.board_slug) as conn:
        task = kanban_db.get_task(conn, task_id)
        with pytest.raises(cost.BudgetBlocked):
            cost.complete_task_with_usage(
                conn,
                scope=scope,
                task_id=task_id,
                usage=_usage(cost_usd="1.01"),
                summary="Would otherwise complete",
                expected_run_id=task.current_run_id,
            )
        blocked = kanban_db.get_task(conn, task_id)
        comments = kanban_db.list_comments(conn, task_id)

    assert blocked is not None and blocked.status == "blocked"
    assert any(cost.HARD_CAP_MARKER in comment.body for comment in comments)
    assert cost.project_spend(scope).ticket_costs[task_id] == Decimal("1.01")


def test_per_run_turn_limit_blocks(pmo_project):
    scope = _scope_with(
        pmo_project,
        budget=project_scope.BudgetConfig(per_run_max_turns=3),
    )
    task_id = _running_task(pmo_project)
    with kanban_db.connect_closing(board=pmo_project.board_slug) as conn:
        task = kanban_db.get_task(conn, task_id)
        with pytest.raises(cost.BudgetBlocked, match="configured maximum"):
            cost.complete_task_with_usage(
                conn,
                scope=scope,
                task_id=task_id,
                usage=_usage(turns=4),
                summary="Too many turns",
                expected_run_id=task.current_run_id,
            )


def test_soft_cap_warns_only(pmo_project):
    scope = _scope_with(
        pmo_project,
        budget=project_scope.BudgetConfig(per_ticket_soft_cap=Decimal("1.00")),
    )
    task_id = _running_task(pmo_project)
    with kanban_db.connect_closing(board=pmo_project.board_slug) as conn:
        task = kanban_db.get_task(conn, task_id)
        assert cost.complete_task_with_usage(
            conn,
            scope=scope,
            task_id=task_id,
            usage=_usage(cost_usd="1.01"),
            summary="Done with warning",
            expected_run_id=task.current_run_id,
        )
        done = kanban_db.get_task(conn, task_id)
        comments = kanban_db.list_comments(conn, task_id)

    assert done is not None and done.status == "done"
    assert any(cost.SOFT_CAP_MARKER in comment.body for comment in comments)


def test_hard_cap_escalate_raises_native_cfo_approval(pmo_project):
    cfo = project_scope.AgentConfig(handle="cfo", profile="acme-cfo", role="CFO")
    scope = _scope_with(
        pmo_project,
        budget=project_scope.BudgetConfig(
            per_ticket_hard_cap=Decimal("1.00"), on_hard_cap="escalate"
        ),
        agents=(*pmo_project.scope.config.agents, cfo),
    )
    task_id = _running_task(pmo_project)
    with kanban_db.connect_closing(board=pmo_project.board_slug) as conn:
        task = kanban_db.get_task(conn, task_id)
        with pytest.raises(cost.BudgetBlocked) as raised:
            cost.complete_task_with_usage(
                conn,
                scope=scope,
                task_id=task_id,
                usage=_usage(cost_usd="1.01"),
                summary="Needs overage",
                expected_run_id=task.current_run_id,
            )

    assert raised.value.approval_id
    with kanban_db.connect_closing(board=pmo_project.board_slug) as conn:
        approval_task = kanban_db.get_task(conn, raised.value.approval_id)
        assert approval_task is not None
        assert approval_task.status == "triage"
        assert cost.HARD_CAP_MARKER in approval_task.body
        assert task_id in kanban_db.child_ids(conn, approval_task.id)


def test_monthly_cap_refuses_promotion(pmo_project):
    scope = _scope_with(
        pmo_project,
        budget=project_scope.BudgetConfig(monthly_cap=Decimal("1.00")),
    )
    cost.record_pm_thread_turn(
        scope=scope,
        usage=_usage(cost_usd="1.00", session_id="month-cap"),
        thread_task_id=pmo_project.founders_office_task_id,
    )
    with kanban_db.connect_closing(board=pmo_project.board_slug) as conn:
        draft_id = kanban_db.create_task(
            conn,
            title="Draft",
            body="Draft",
            assignee="acme-dev-1",
            created_by="pm-acme",
            triage=True,
            board=pmo_project.board_slug,
            project_id=pmo_project.project_id,
        )
        draft = kanban_db.get_task(conn, draft_id)
        reasons = cost.promotion_budget_reasons(scope, conn, draft)

    assert reasons and "monthly budget reached" in reasons[0]


def test_cost_attributes_to_project_ticket_and_agent(pmo_project):
    task_id = _running_task(pmo_project, assignee="acme-dev-1")
    with kanban_db.connect_closing(board=pmo_project.board_slug) as conn:
        task = kanban_db.get_task(conn, task_id)
        cost.complete_task_with_usage(
            conn,
            scope=pmo_project.scope,
            task_id=task_id,
            usage=_usage(cost_usd="2.3456"),
            summary="Done",
            expected_run_id=task.current_run_id,
        )
    spend = cost.project_spend(pmo_project.scope)

    assert spend.project_id == pmo_project.project_id
    assert spend.ticket_costs[task_id] == Decimal("2.3456")
    assert spend.agents["acme-dev-1"].cost_usd == Decimal("2.3456")
    assert spend.cost_per_completed_ticket == Decimal("2.3456")


def test_models_are_strict_decimal_budget_and_moa_is_excluded():
    config = project_scope.ProjectConfig.model_validate(
        {
            "version": 1,
            "project": {"name": "Acme", "slug": "acme"},
            "orchestrator": {"profile": "pm-acme"},
            "models": {"worker": "anthropic/claude-sonnet-4.6"},
            "budget": {"monthly_cap": "500.00"},
        }
    )
    assert config.budget.monthly_cap == Decimal("500.00")
    assert config.models.worker == "anthropic/claude-sonnet-4.6"
    with pytest.raises(ValueError, match="MoA"):
        project_scope.ModelsConfig(worker="moa:strategy")


def test_role_model_selection_uses_native_task_override_contract(pmo_project):
    models = project_scope.ModelsConfig(
        pm="anthropic/opus",
        worker="anthropic/sonnet",
        reviewer="anthropic/opus",
        escalation_override="anthropic/stronger",
    )
    scope = _scope_with(pmo_project, models=models)
    assert cost.model_route_for_assignee(scope, "pm-acme") == "anthropic/opus"
    assert cost.model_route_for_assignee(scope, "acme-dev-1") == "anthropic/sonnet"
    assert cost.model_route_for_assignee(scope, "acme-qa") == "anthropic/opus"
    assert (
        cost.model_route_for_assignee(scope, "acme-dev-1", blocked_recurrences=2)
        == "anthropic/stronger"
    )


def test_bootstrap_expensive_model_warning_uses_hermes_guard(pmo_project):
    models = project_scope.ModelsConfig(worker="vendor/expensive")
    config = pmo_project.scope.config.model_copy(update={"models": models})

    class Warning:
        message = "expensive route"

    assert cost.expensive_model_warnings(
        config, warning_fn=lambda model: Warning() if model == "vendor/expensive" else None
    ) == ("expensive route",)


def test_doctor_reconciliation_flags_more_than_five_percent(pmo_project):
    cost.record_pm_thread_turn(
        scope=pmo_project.scope,
        usage=_usage(cost_usd="1.00", session_id="doctor"),
        thread_task_id=pmo_project.founders_office_task_id,
    )
    healthy = cost.doctor_reconciliation(
        pmo_project.scope, insights_total_usd="1.04"
    )
    unhealthy = cost.doctor_reconciliation(
        pmo_project.scope, insights_total_usd="2.00"
    )
    assert healthy.healthy
    assert not unhealthy.healthy
    assert unhealthy.drift_percent == Decimal("50.0")


def test_pre_tool_hook_blocks_normal_completion_at_hard_cap(pmo_project, monkeypatch):
    monkeypatch.setattr(cost, "_hook_scope", lambda task_id, args=None: pmo_project.scope)
    monkeypatch.setattr(cost, "usage_from_session", lambda session_id: _usage())
    monkeypatch.setattr(
        cost,
        "_projected_hard_reason",
        lambda scope, task_id, usage: "hard cap reached",
    )
    directive = cost.pre_tool_budget_hook(
        tool_name="kanban_complete",
        args={"board": pmo_project.board_slug},
        task_id="t_test",
        session_id="sess",
    )
    assert directive["action"] == "block"
    assert "hard cap reached" in directive["message"]
