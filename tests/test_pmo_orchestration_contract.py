"""Behavior contracts for PM planning, consultation, and delegation."""

from __future__ import annotations

import asyncio
import json

import pytest

from gateway.config import PlatformConfig
from gateway.session_context import reset_session_vars, set_session_vars
from hermes_cli import kanban_db
from plugins.platforms.pmo.adapter import (
    PmoPlatformAdapter,
    _inbox_root,
    _load_queued_message,
    create_project_conversation,
    ensure_project_conversations,
)
from plugins.pmo import planning, workflow
from plugins.pmo.sandbox import terminal_sandbox_overrides
from plugins.pmo.tool_scope import (
    bind_active_scope,
    pre_tool_scope_hook,
    reset_active_scope,
)


pytest_plugins = ("tests.pmo_fixtures",)


def test_founder_plan_and_specialist_reply_are_native_discussion(pmo_project):
    scope = pmo_project.scope
    conversations = ensure_project_conversations(scope)
    planning.record_context_review(
        scope,
        thread_id=conversations.founders_office_thread_id,
        author=f"agent:{scope.config.orchestrator.profile}",
        request="Add the requested administration workflow",
        context="Configured skills, project memory, repository scope, and board state loaded.",
    )
    plan = planning.record_plan(
        scope,
        thread_id=conversations.founders_office_thread_id,
        author=f"agent:{scope.config.orchestrator.profile}",
        objective="Add the requested administration workflow",
        discussion="Inspect workflow coverage, product boundaries, sequencing, and test evidence.",
    )
    loaded = planning.require_plan(
        scope, thread_id=plan.thread_id, plan_id=plan.plan_id
    )
    assert loaded.objective == "Add the requested administration workflow"

    subchat = create_project_conversation(scope, title="Feature specialist review")
    request_id = planning.record_consultation(
        scope,
        plan=plan,
        consultation_thread_id=subchat.thread_id,
        author=f"agent:{scope.config.orchestrator.profile}",
        handle="dev-1",
        profile="acme-dev-1",
        question="Which files and tests define the requested change boundary?",
    )
    assert request_id > 0
    with kanban_db.connect_closing(board=scope.board_slug) as conn:
        request = next(
            comment for comment in kanban_db.list_comments(conn, subchat.thread_id)
            if comment.id == request_id
        )
    assert "Plan objective:\nAdd the requested administration workflow" in request.body
    assert "PM repository analysis and proposed sequencing:" in request.body
    assert "Configured skills, project memory, repository scope" in request.body
    assert "do not run full test suites, production builds" in request.body
    assert planning.consultation_request(
        scope, consultation_thread_id=plan.thread_id
    ) is None
    with pytest.raises(planning.PlanningError, match="wait for its sub-chat response"):
        planning.require_consultation_response(scope, plan=plan)

    with kanban_db.connect_closing(board=scope.board_slug) as conn:
        notice_id = kanban_db.add_comment(
            conn,
            subchat.thread_id,
            author="agent:acme-dev-1",
            body=(
                "ℹ Codex context notice: auto-compaction was raised. "
                "Opt back out: hermes config set compression false."
            ),
        )
    assert planning.record_consultation_response(
        scope,
        consultation_thread_id=subchat.thread_id,
        author="agent:acme-dev-1",
        response_comment_id=notice_id,
    ) is False
    with pytest.raises(planning.PlanningError, match="wait for its sub-chat response"):
        planning.require_consultation_response(scope, plan=plan)

    with kanban_db.connect_closing(board=scope.board_slug) as conn:
        response_id = kanban_db.add_comment(
            conn,
            subchat.thread_id,
            author="agent:acme-dev-1",
            body=(
                "Keep route authorization server-side. Acme Portal — OWN_AGENT_OK."
            ),
        )
    assert planning.record_consultation_response(
        scope,
        consultation_thread_id=subchat.thread_id,
        author="agent:acme-dev-1",
        response_comment_id=response_id,
    )
    linked = planning.require_consultation_response(scope, plan=plan)
    assert linked["profile"] == "acme-dev-1"
    with kanban_db.connect_closing(board=scope.board_slug) as conn:
        parent_reply = kanban_db.list_comments(conn, plan.thread_id)[-1]
    assert "Specialist recommendation:" in parent_reply.body
    assert "Keep route authorization server-side" in parent_reply.body


def test_pm_cannot_own_an_implementation_ticket(pmo_project):
    scope = pmo_project.scope
    with pytest.raises(workflow.WorkflowError, match="PM is read-only"):
        workflow.create_draft(
            project_ref=scope.project_id,
            board_slug=scope.board_slug,
            actor_profile=scope.config.orchestrator.profile,
            title="Implement the approved workflow",
            outcome="Users can complete the documented administration workflow.",
            assignee=scope.config.orchestrator.profile,
            acceptance_criteria=("The approved option is selectable",),
            evidence=("Focused verification passes",),
        )


def test_global_peer_pm_routes_to_peer_but_foreign_agent_is_denied(pmo_two_projects):
    from tools import pmo_collab_tools

    acme, beta = pmo_two_projects
    acme_conversations = ensure_project_conversations(acme.scope)
    beta_conversations = ensure_project_conversations(beta.scope)
    set_session_vars(
        platform="pmo",
        chat_id=acme.board_slug,
        thread_id=acme_conversations.global_founders_office_thread_id,
        profile=acme.scope.config.orchestrator.profile,
        session_id="global-peer-pm-test",
    )
    try:
        peer = json.loads(
            pmo_collab_tools._handle_peer_pm(
                {
                    "target_pm": beta.scope.config.orchestrator.profile,
                    "request": "Reply with the Beta project name.",
                }
            )
        )
        assert peer["ok"] is True
        assert peer["source_project_id"] == acme.project_id
        assert peer["target_project_id"] == beta.project_id
        with kanban_db.connect_closing(board=beta.board_slug) as conn:
            comments = kanban_db.list_comments(
                conn, beta_conversations.global_founders_office_thread_id
            )
        assert comments[-1].author == f"agent:{acme.scope.config.orchestrator.profile}"
        assert "Reply with the Beta project name" in comments[-1].body
    finally:
        reset_session_vars()

    set_session_vars(
        platform="pmo",
        chat_id=beta.board_slug,
        thread_id=beta_conversations.global_founders_office_thread_id,
        profile=beta.scope.config.orchestrator.profile,
        session_id="global-foreign-agent-denial-test",
    )
    try:
        context = json.loads(
            pmo_collab_tools._handle_context(
                {"request": "Try to consult Acme's dev-1 from Beta."}
            )
        )
        assert context["ok"] is True
        plan = json.loads(
            pmo_collab_tools._handle_plan(
                {
                    "objective": "Verify agent isolation",
                    "discussion": "Attempt only the explicitly requested foreign handle.",
                }
            )
        )
        denied = json.loads(
            pmo_collab_tools._handle_consult(
                {
                    "plan_id": plan["plan_id"],
                    "handle": "dev-1",
                    "question": "Report Acme status.",
                }
            )
        )
        assert denied["ok"] is False
        assert denied["error"] == "@dev-1 is not a project agent"
    finally:
        reset_session_vars()


def test_pm_tool_policy_blocks_implementation_and_direct_ticket_creation(pmo_project):
    scope = pmo_project.scope
    conversations = ensure_project_conversations(scope)
    token = bind_active_scope(
        scope, board_dir=kanban_db.board_dir(scope.board_slug).resolve()
    )
    set_session_vars(
        platform="pmo",
        chat_id=scope.board_slug,
        thread_id=conversations.founders_office_thread_id,
        profile=scope.config.orchestrator.profile,
        session_id="pm-policy-test",
    )
    try:
        for tool_name in ("terminal", "write_file", "patch", "execute_code", "delegate_task"):
            result = pre_tool_scope_hook(tool_name=tool_name, args={})
            assert result and result["action"] == "block"
            assert "read-only" in result["message"]
        direct = pre_tool_scope_hook(
            tool_name="kanban_create",
            args={"title": "Bypass plan", "assignee": "acme-dev-1"},
        )
        assert direct and direct["action"] == "block"
        assert "pmo_create_ticket" in direct["message"]
    finally:
        reset_active_scope(token)
        reset_session_vars()


def test_founder_session_exposes_pmo_tools_without_worker_environment(pmo_project, monkeypatch):
    from tools import pmo_collab_tools

    scope = pmo_project.scope
    conversations = ensure_project_conversations(scope)
    monkeypatch.delenv("HERMES_KANBAN_BOARD", raising=False)
    set_session_vars(
        platform="pmo",
        chat_id=scope.board_slug,
        thread_id=conversations.founders_office_thread_id,
        profile=scope.config.orchestrator.profile,
    )
    try:
        assert pmo_collab_tools._check_pmo_collab() is True
        assert pmo_collab_tools._env_task_and_board() == ("", scope.board_slug)
    finally:
        reset_session_vars()


def test_pm_terminal_mounts_repository_and_board_read_only(pmo_project):
    scope = pmo_project.scope
    board_dir = kanban_db.board_dir(scope.board_slug).resolve()
    overrides = terminal_sandbox_overrides(
        scope, board_dir=board_dir, read_only=True
    )
    volumes = overrides["docker_volumes"]
    assert any(item.endswith(":/workspace:ro") for item in volumes)
    assert any(
        item.endswith(f":/opt/data/kanban/boards/{scope.board_slug}:ro")
        for item in volumes
    )
    assert overrides["sandbox_key"].endswith("-readonly")


def test_bootstrap_gives_pm_a_managed_charter_and_leaf_toolset(pmo_project):
    import yaml
    from hermes_cli import profiles
    from hermes_cli.tools_config import _get_platform_tools
    from plugins.pmo import agent_soul
    from tools import pmo_collab_tools  # noqa: F401 - register the leaf toolset

    scope = pmo_project.scope
    profile_dir = profiles.get_profile_dir(scope.config.orchestrator.profile)
    config = yaml.safe_load((profile_dir / "config.yaml").read_text(encoding="utf-8"))
    soul = (profile_dir / "SOUL.md").read_text(encoding="utf-8")

    assert config["platform_toolsets"]["cli"] == ["pmo-collab", "no_mcp"]
    assert config["platform_toolsets"]["pmo"] == ["pmo-collab", "no_mcp"]
    assert config["agent"]["toolset_allowlist"] == {
        "cli": ["pmo-collab"],
        "pmo": ["pmo-collab"],
    }
    assert _get_platform_tools(
        config, "pmo", include_default_mcp_servers=False
    ) == {"pmo-collab"}
    assert agent_soul.PMO_SOUL_MARKER in soul
    assert "You own the plan and the board, never the implementation." in soul


def test_worker_keeps_pmo_tools_and_can_collaborate_in_founders_subchat(pmo_project):
    """A consulted specialist must receive PMO collaboration tools as well."""

    import yaml
    from hermes_cli import profiles
    from hermes_cli.tools_config import _get_platform_tools
    from plugins.pmo import agents
    from tools import pmo_collab_tools  # noqa: F401 - register the toolset

    worker_profile = "acme-dev-1"
    profiles.create_profile(
        worker_profile,
        no_alias=True,
        no_skills=True,
        description="Acme specialist for Founder’s Office consultation tests",
    )
    assert agents.enable_collab_toolset(worker_profile) is True
    config = yaml.safe_load(
        (profiles.get_profile_dir(worker_profile) / "config.yaml").read_text(
            encoding="utf-8"
        )
    )
    enabled = _get_platform_tools(config, "pmo", include_default_mcp_servers=False)

    assert "pmo-collab" in enabled
    assert "hermes-pmo" in config["platform_toolsets"]["pmo"]
    assert config.get("agent", {}).get("toolset_allowlist", {}).get("pmo") is None


def test_worker_pmo_schema_excludes_pm_planning_and_ticket_controls(pmo_project):
    """A consultation recipient can respond, but cannot impersonate its PM."""

    import yaml
    from hermes_cli import profiles
    from hermes_cli.tools_config import _get_platform_tools
    from model_tools import get_tool_definitions
    from plugins.platforms.pmo.adapter import ensure_project_conversations
    from plugins.pmo import agents
    from tools import pmo_collab_tools  # noqa: F401 - register the toolset

    scope = pmo_project.scope
    conversations = ensure_project_conversations(scope)
    worker_profile = "acme-dev-1"
    profiles.create_profile(
        worker_profile,
        no_alias=True,
        no_skills=True,
        description="Acme specialist PMO schema test",
    )
    agents.enable_collab_toolset(worker_profile)
    config = yaml.safe_load(
        (profiles.get_profile_dir(worker_profile) / "config.yaml").read_text(
            encoding="utf-8"
        )
    )
    set_session_vars(
        platform="pmo",
        chat_id=scope.board_slug,
        thread_id=conversations.founders_office_thread_id,
        profile=worker_profile,
    )
    try:
        names = {
            item["function"]["name"]
            for item in get_tool_definitions(
                _get_platform_tools(config, "pmo", include_default_mcp_servers=False),
                quiet_mode=True,
                skip_tool_search_assembly=True,
            )
        }
        assert {
            "pmo_handles", "pmo_ask", "pmo_answer", "pmo_transfer", "pmo_escalate"
        } <= names
        assert not names & {
            "pmo_repo_read",
            "pmo_repo_search",
            "pmo_repo_status",
            "pmo_plan",
            "pmo_consult",
            "pmo_create_agent",
            "pmo_create_ticket",
            "pmo_ask_human",
        }
    finally:
        reset_session_vars()


def test_pm_has_bounded_repository_analysis_tools(pmo_project):
    from tools import pmo_collab_tools

    scope = pmo_project.scope
    conversations = ensure_project_conversations(scope)
    (pmo_project.workspace / "README.md").write_text(
        "# Acme\n\nThe requested administration workflow is not implemented yet.\n", encoding="utf-8"
    )
    set_session_vars(
        platform="pmo",
        chat_id=scope.board_slug,
        thread_id=conversations.founders_office_thread_id,
        profile=scope.config.orchestrator.profile,
    )
    try:
        assert pmo_collab_tools._check_pmo_pm() is True
        read = json.loads(pmo_collab_tools._handle_repo_read({"path": "README.md"}))
        assert read["ok"] is True
        assert "requested administration workflow" in read["content"]
        search = json.loads(pmo_collab_tools._handle_repo_search({"query": "administration"}))
        assert search["ok"] is True
        assert search["matches"][0]["path"] == "README.md"

        set_session_vars(profile="acme-dev-1")
        assert pmo_collab_tools._check_pmo_pm() is False
    finally:
        reset_session_vars()


def test_pm_model_schema_is_the_collaboration_leaf_only(pmo_project):
    import yaml
    from hermes_cli import profiles
    from hermes_cli.tools_config import _get_platform_tools
    from model_tools import get_tool_definitions
    from tools import pmo_collab_tools  # noqa: F401 - register the leaf toolset

    scope = pmo_project.scope
    conversations = ensure_project_conversations(scope)
    config = yaml.safe_load(
        (profiles.get_profile_dir(scope.config.orchestrator.profile) / "config.yaml").read_text(
            encoding="utf-8"
        )
    )
    set_session_vars(
        platform="pmo",
        chat_id=scope.board_slug,
        thread_id=conversations.founders_office_thread_id,
        profile=scope.config.orchestrator.profile,
    )
    try:
        enabled = sorted(
            _get_platform_tools(config, "pmo", include_default_mcp_servers=False)
        )
        names = {
            item["function"]["name"]
            for item in get_tool_definitions(
                enabled, quiet_mode=True, skip_tool_search_assembly=True
            )
        }
        assert enabled == ["pmo-collab"]
        assert {
            "pmo_context", "pmo_repo_read", "pmo_plan", "pmo_consult",
            "pmo_peer_pm",
            "pmo_create_agent", "pmo_request_details",
            "pmo_request_ticket_confirmation", "pmo_create_ticket", "pmo_ask_human",
        } <= names
        assert all(name.startswith("pmo_") for name in names)
        assert not names & {"terminal", "write_file", "patch", "execute_code", "delegate_task", "kanban_create"}
    finally:
        reset_session_vars()


def test_pm_tools_plan_consult_and_delegate_without_implementing(pmo_project):
    from plugins.pmo import bootstrap
    from tools import pmo_collab_tools

    scope = pmo_project.scope
    # The PM must be able to name a real project human as a ticket owner, not
    # only the generic founders-office broadcast handle.
    bootstrap.grant_project_admin(scope, "ceo@datansh.local")
    conversations = ensure_project_conversations(scope)
    set_session_vars(
        platform="pmo",
        chat_id=scope.board_slug,
        thread_id=conversations.founders_office_thread_id,
        profile=scope.config.orchestrator.profile,
        session_id="pm-orchestration-test",
    )
    try:
        context = json.loads(
            pmo_collab_tools._handle_context({"request": "Add the requested administration workflow"})
        )
        assert context["ok"] is True
        assert "Effective project configuration" in context["context"]

        plan = json.loads(
            pmo_collab_tools._handle_plan(
                {
                    "objective": "Add the requested administration workflow",
                    "discussion": (
                        "First inspect the change boundaries, then ask a project "
                        "engineer for repository-specific findings before delegating."
                    ),
                }
            )
        )
        assert plan["ok"] is True

        premature_ticket = json.loads(
            pmo_collab_tools._handle_create_ticket(
                {"plan_id": plan["plan_id"], "confirmation_id": "missing"}
            )
        )
        assert premature_ticket["ok"] is False
        assert "ticket-detail" in premature_ticket["error"] or "developer" in premature_ticket["error"]

        premature_human_question = json.loads(
            pmo_collab_tools._handle_ask_human(
                {
                    "plan_id": plan["plan_id"],
                    "handles": ["ceo"],
                    "question": "Should this be released now?",
                }
            )
        )
        assert premature_human_question["ok"] is False
        assert "sub-chat response" in premature_human_question["error"]

        created_agent = json.loads(
            pmo_collab_tools._handle_create_agent(
                {"plan_id": plan["plan_id"], "role": "research", "count": 1}
            )
        )
        assert created_agent["ok"] is True
        assert created_agent["agents"][0]["profile"] == "research-acme"

        consult = json.loads(
            pmo_collab_tools._handle_consult(
                {
                    "plan_id": plan["plan_id"],
                    "handle": "dev-1",
                    "question": "Which files and tests define the requested change boundary?",
                }
            )
        )
        assert consult["ok"] is True
        queue_path = _inbox_root() / "pending" / f"{consult['queue_id']}.json"
        queued = _load_queued_message(queue_path)
        assert queued.thread_id == consult["thread_id"]
        assert queued.target_profile == "acme-dev-1"

        duplicate = json.loads(
            pmo_collab_tools._handle_consult(
                {
                    "plan_id": plan["plan_id"],
                    "handle": "dev-1",
                    "question": "Please answer the same pending consultation.",
                }
            )
        )
        assert duplicate["ok"] is True
        assert duplicate["status"] == "already_queued"
        assert duplicate["thread_id"] == consult["thread_id"]
        assert duplicate["queue_id"] is None

        premature_details = json.loads(
            pmo_collab_tools._handle_request_details(
                {
                    "plan_id": plan["plan_id"],
                    "questions": [
                        {
                            "id": "release_scope",
                            "question": "Which release scope should the ticket use?",
                            "options": [
                                {"id": "admin", "label": "Admin only", "description": "Localize administration."},
                                {"id": "all", "label": "All surfaces", "description": "Localize every surface."},
                            ],
                        }
                    ],
                }
            )
        )
        assert premature_details["ok"] is False
        assert "sub-chat response" in premature_details["error"]

        # The worker's real PMO delivery path must make its sub-chat reply
        # visible to the parent plan.  Without this link, a PM would be
        # permanently unable to ask a human even after consulting a colleague.
        set_session_vars(
            platform="pmo",
            chat_id=scope.board_slug,
            thread_id=consult["thread_id"],
            profile="acme-dev-1",
            session_id="pm-specialist-reply-test",
        )
        specialist_reply = asyncio.run(
            PmoPlatformAdapter(PlatformConfig(enabled=True)).send(
                scope.board_slug,
                (
                    "The implementation boundary is the configured registry and shared navigation. "
                    "Keep the approved decision in project configuration, preserve route authorization, "
                    "and verify the workflow plus fallback behavior in the focused integration suite."
                ),
                metadata={"thread_id": consult["thread_id"]},
            )
        )
        assert specialist_reply.success is True
        resumed = [
            _load_queued_message(path)
            for path in (_inbox_root() / "pending").glob("*.json")
        ]
        assert any(
            item.thread_id == conversations.founders_office_thread_id
            and item.target_profile == scope.config.orchestrator.profile
            for item in resumed
        )

        set_session_vars(
            platform="pmo",
            chat_id=scope.board_slug,
            thread_id=conversations.founders_office_thread_id,
            profile=scope.config.orchestrator.profile,
            session_id="pm-orchestration-test",
        )
        human_question = json.loads(
            pmo_collab_tools._handle_ask_human(
                {
                    "plan_id": plan["plan_id"],
                    "handles": ["founders-office"],
                    "question": "Should the requested workflow be included in the next release?",
                }
            )
        )
        assert human_question["ok"] is True
        assert "founders-office" in human_question["notified"]

        detail_request = json.loads(
            pmo_collab_tools._handle_request_details(
                {
                    "plan_id": plan["plan_id"],
                    "questions": [
                        {
                            "id": "release_scope",
                            "question": "Which release scope should the ticket use?",
                            "options": [
                                {"id": "admin", "label": "Admin only", "description": "Apply the change to the administration surface."},
                                {"id": "all", "label": "All surfaces", "description": "Include every user-facing surface."},
                            ],
                        }
                    ],
                }
            )
        )
        assert detail_request["ok"] is True

        premature_confirmation = json.loads(
            pmo_collab_tools._handle_request_ticket_confirmation(
                {
                    "plan_id": plan["plan_id"],
                    "title": "Implement the approved administration workflow",
                    "product_context": "Administrators cannot complete the requested product journey.",
                    "user_impact": "Administrators can complete the documented workflow without a workaround.",
                    "outcome": "The approved administration workflow is available.",
                    "assignee": "dev-1",
                    "acceptance_criteria": ["The approved option is selectable"],
                    "evidence": ["Focused verification passes"],
                    "context_summary": "Repository and developer consultation context.",
                }
            )
        )
        assert premature_confirmation["ok"] is False
        assert "answer" in premature_confirmation["error"]

        with kanban_db.connect_closing(board=scope.board_slug) as conn:
            kanban_db.add_comment(
                conn,
                conversations.founders_office_thread_id,
                author="human:ceo@datansh.local",
                body=(
                    planning.DETAIL_RESPONSE_MARKER
                    + "\n"
                    + json.dumps(
                        {
                            "schema": "datansh-pmo-founder-detail-response.v1",
                            "plan_id": plan["plan_id"],
                            "request_id": detail_request["request_id"],
                            "answers": [
                                {
                                    "question_id": "release_scope",
                                    "choice_id": "admin",
                                    "answer": "Admin only",
                                }
                            ],
                        }
                    )
                    + "\n\n@pm Use the administration-only scope."
                ),
            )

        invalid_labels = json.loads(
            pmo_collab_tools._handle_request_ticket_confirmation(
                {
                    "plan_id": plan["plan_id"],
                    "title": "Implement the approved administration workflow",
                    "product_context": "Administrators cannot complete the requested product journey.",
                    "user_impact": "Administrators can complete the documented workflow without a workaround.",
                    "outcome": "The approved administration workflow is available.",
                    "assignee": "dev-1",
                    "acceptance_criteria": ["The approved option is selectable"],
                    "evidence": ["Focused verification passes"],
                    "labels": ["unknown-feature-label"],
                    "context_summary": "Repository and developer consultation context.",
                }
            )
        )
        assert invalid_labels["ok"] is False
        assert "unknown ticket label" in invalid_labels["error"]
        assert "before asking the founder" in invalid_labels["error"]

        invalid_pm_owner = json.loads(
            pmo_collab_tools._handle_request_ticket_confirmation(
                {
                    "plan_id": plan["plan_id"],
                    "title": "Implement the approved product change",
                    "product_context": "Users need the approved behavior available in the product.",
                    "user_impact": "Users can complete the documented workflow.",
                    "outcome": "The approved behavior is available and verified.",
                    "assignee": "pm",
                    "acceptance_criteria": ["The documented behavior is verifiable"],
                    "evidence": ["Focused verification passes"],
                    "context_summary": "Use the completed developer consultation to choose ownership.",
                }
            )
        )
        assert invalid_pm_owner["ok"] is False
        assert "do not ask the founder to choose a developer" in invalid_pm_owner["error"]

        confirmation = json.loads(
            pmo_collab_tools._handle_request_ticket_confirmation(
                {
                    "plan_id": plan["plan_id"],
                    "title": "Implement the approved administration workflow",
                    "product_context": "Administrators cannot complete the requested product journey.",
                    "user_impact": "Administrators can complete the documented workflow without a workaround.",
                    "outcome": "The approved administration workflow is available.",
                    "assignee": "dev-1",
                    "acceptance_criteria": ["The approved option is selectable"],
                    "evidence": ["Focused verification passes"],
                    "constraints": ["Administration surface only"],
                    "context_summary": (
                        "Use project skills and the bounded project context; "
                        "the developer identified the configuration registry and navigation boundary; "
                        "the founder selected the administration-only scope."
                    ),
                }
            )
        )
        assert confirmation["ok"] is True

        unapproved = json.loads(
            pmo_collab_tools._handle_create_ticket(
                {
                    "plan_id": plan["plan_id"],
                    "confirmation_id": confirmation["confirmation_id"],
                }
            )
        )
        assert unapproved["ok"] is False
        assert "approve" in unapproved["error"]

        with kanban_db.connect_closing(board=scope.board_slug) as conn:
            kanban_db.add_comment(
                conn,
                conversations.founders_office_thread_id,
                author="human:ceo@datansh.local",
                body=(
                    planning.TICKET_DECISION_MARKER
                    + "\n"
                    + json.dumps(
                        {
                            "schema": "datansh-pmo-founder-ticket-decision.v1",
                            "plan_id": plan["plan_id"],
                            "confirmation_id": confirmation["confirmation_id"],
                            "decision": "approved",
                            "feedback": "",
                        }
                    )
                    + "\n\n@pm Approved."
                ),
            )

        ticket = json.loads(
            pmo_collab_tools._handle_create_ticket(
                {
                    "plan_id": plan["plan_id"],
                    "confirmation_id": confirmation["confirmation_id"],
                }
            )
        )
        assert ticket["ok"] is True
        assert ticket["assignee"] == "acme-dev-1"
        with kanban_db.connect_closing(board=scope.board_slug) as conn:
            task = kanban_db.get_task(conn, ticket["task_id"])
            comments = kanban_db.list_comments(conn, ticket["task_id"])
        assert task.assignee == "acme-dev-1"
        assert task.assignee != scope.config.orchestrator.profile
        assert any(comment.body.startswith(planning.TICKET_CONTEXT_MARKER) for comment in comments)
        assert any("administration-only scope" in comment.body for comment in comments)

        human_confirmation = json.loads(
            pmo_collab_tools._handle_request_ticket_confirmation(
                {
                    "plan_id": plan["plan_id"],
                    "title": "Complete the approved administrative setup",
                    "product_context": "The project needs a human-owned administrative step completed before delivery can continue.",
                    "user_impact": "The approved workflow can proceed without an agent performing a human-only action.",
                    "outcome": "The requested setting is applied and the result is recorded for the project team.",
                    "assignee": "ceo",
                    "acceptance_criteria": ["The requested setting is validated and recorded"],
                    "evidence": ["A board comment links the setting and validation result"],
                    "human_steps": [
                        "Open the relevant administration screen for this project.",
                        "Apply the approved setting and record any validation response.",
                        "Verify the setting appears without changing unrelated project configuration.",
                        "Attach the reference and a brief completion note to this ticket.",
                    ],
                    "context_summary": "The founder approved a bounded human-owned administrative step after developer review.",
                }
            )
        )
        assert human_confirmation["ok"] is True
        with kanban_db.connect_closing(board=scope.board_slug) as conn:
            kanban_db.add_comment(
                conn,
                conversations.founders_office_thread_id,
                author="human:ceo@datansh.local",
                body=(
                    planning.TICKET_DECISION_MARKER + "\n" + json.dumps(
                        {
                            "schema": "datansh-pmo-founder-ticket-decision.v1",
                            "plan_id": plan["plan_id"],
                            "confirmation_id": human_confirmation["confirmation_id"],
                            "decision": "approved",
                            "feedback": "",
                        }
                    ) + "\n\n@pm Approved."
                ),
            )
        human_ticket = json.loads(
            pmo_collab_tools._handle_create_ticket(
                {"plan_id": plan["plan_id"], "confirmation_id": human_confirmation["confirmation_id"]}
            )
        )
        assert human_ticket["ok"] is True
        assert human_ticket["kind"] == "human_task"
        assert human_ticket["assignee"] == "human:ceo@datansh.local"
        with kanban_db.connect_closing(board=scope.board_slug) as conn:
            human_task = kanban_db.get_task(conn, human_ticket["task_id"])
        assert human_task.status == "triage"
        assert workflow.is_human_task(human_task) is True
        assert "Human hand-off steps:" in human_task.body
        assert "Open the relevant administration screen" in human_task.body
    finally:
        reset_session_vars()


def test_reconcile_projects_exactly_one_scope_into_each_profile_db(pmo_project):
    from hermes_cli import profiles, projects_db
    from plugins.pmo import agents

    scope = pmo_project.scope
    profiles_to_check = [
        scope.config.orchestrator.profile,
        *(item.profile for item in scope.config.agents),
    ]
    for profile in profiles_to_check:
        profiles.get_profile_dir(profile).mkdir(parents=True, exist_ok=True)
    agents.reconcile_profiles(scope)
    for profile in profiles_to_check:
        db_path = profiles.get_profile_dir(profile) / "projects.db"
        with projects_db.connect_closing(db_path=db_path) as conn:
            records = projects_db.list_projects(conn, include_archived=True)
        assert [(item.id, item.board_slug) for item in records] == [
            (scope.project_id, scope.board_slug)
        ]
