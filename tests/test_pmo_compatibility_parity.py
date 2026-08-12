"""Behavioral parity checks for the additive Datansh PM-OS profile.

These tests deliberately exercise Hermes-owned capability paths.  PM-OS must
specialize an ordinary profile without replacing memory, skill learning, the
gateway toolset, or any of the normal agent tools.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import threading
from unittest.mock import MagicMock, patch

import pytest

from gateway.config import PlatformConfig
from gateway.platforms.base import BasePlatformAdapter
from gateway.platform_registry import PlatformEntry, platform_registry
from gateway.profile_routing import match_profile_route
from hermes_cli import kanban_db
from hermes_cli.profile_distribution import install_distribution
from plugins.platforms.pmo import adapter as pmo_adapter
from tools import approval as hermes_approval
from tools import kanban_tools as _kanban_tools  # noqa: F401 - registers native tools
from tools.delegate_tool import delegate_task
from tools.memory_tool import MemoryStore, memory_tool
from tools.registry import registry
from tools.skill_manager_tool import skill_manage
from toolsets import resolve_toolset


REPO_ROOT = Path(__file__).resolve().parents[1]
DIST_ROOT = REPO_ROOT / "distributions" / "datansh-pm-os"

pytest_plugins = ("tests.pmo_fixtures",)


def _install_profile(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    default_home = tmp_path / ".hermes"
    default_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(default_home))
    plan = install_distribution(str(DIST_ROOT))
    monkeypatch.setenv("HERMES_HOME", str(plan.target_dir))
    return plan.target_dir


def test_pmo_gateway_toolset_keeps_the_normal_hermes_capabilities():
    previous = platform_registry.get("pmo")
    platform_registry.register(
        PlatformEntry(
            name="pmo",
            label="Datansh PM-OS parity probe",
            adapter_factory=lambda cfg: cfg,
            check_fn=lambda: True,
        )
    )
    try:
        cli_tools = set(resolve_toolset("hermes-cli"))
        pmo_tools = set(resolve_toolset("hermes-pmo"))
    finally:
        platform_registry.unregister("pmo")
        if previous is not None:
            platform_registry.register(previous)

    required = {
        "terminal",
        "read_file",
        "write_file",
        "browser_navigate",
        "memory",
        "skills_list",
        "skill_view",
        "skill_manage",
        "delegate_task",
        "session_search",
        "cronjob",
    }
    assert required <= cli_tools
    assert required <= pmo_tools
    assert pmo_tools == cli_tools


def test_installed_pmo_profile_can_write_memory_and_learn_a_skill(
    tmp_path, monkeypatch
):
    profile_home = _install_profile(tmp_path, monkeypatch)

    store = MemoryStore()
    store.load_from_disk()
    memory_result = json.loads(
        memory_tool(
            action="add",
            content="PM reviews must cite ticket evidence before closure.",
            store=store,
        )
    )
    assert memory_result["success"] is True
    assert "ticket evidence" in (
        profile_home / "memories" / "MEMORY.md"
    ).read_text(encoding="utf-8")

    skill_result = json.loads(
        skill_manage(
            action="create",
            name="learned-release-check",
            content=(
                "---\n"
                "name: learned-release-check\n"
                "description: Use when checking a release candidate.\n"
                "version: 1.0.0\n"
                "---\n\n"
                "# Release check\n\nVerify tests and attach the evidence.\n"
            ),
        )
    )
    assert skill_result["success"] is True
    learned = profile_home / "skills" / "learned-release-check" / "SKILL.md"
    assert learned.is_file()
    assert "attach the evidence" in learned.read_text(encoding="utf-8")

    revised = learned.read_text(encoding="utf-8").replace(
        "Verify tests and attach the evidence.",
        "Verify tests, attach the evidence, and record rollback steps.",
    )
    edit_result = json.loads(
        skill_manage(
            action="edit",
            name="learned-release-check",
            content=revised,
        )
    )
    assert edit_result["success"] is True
    assert "rollback steps" in learned.read_text(encoding="utf-8")

    # The distribution stays additive after Hermes creates normal profile
    # state: there is still no replacement runtime/config/tool/MCP payload.
    assert not (profile_home / "pmo.db").exists()
    assert not (profile_home / "mcp.json").exists()


def test_pmo_founder_message_runs_through_inherited_gateway_turn(pmo_project):
    channels = pmo_adapter.ensure_project_conversations(pmo_project.scope)
    adapter = pmo_adapter.PmoPlatformAdapter(
        PlatformConfig(enabled=True, extra={"agent_author": "agent:pm-acme"})
    )
    routes = list(channels.profile_routes())

    class Runner:
        @staticmethod
        def _profile_name_for_source(source):
            route = match_profile_route(
                routes,
                platform=source.platform.value,
                chat_id=source.chat_id,
                thread_id=source.thread_id,
                parent_chat_id=source.parent_chat_id,
            )
            return route.profile if route else None

    adapter.gateway_runner = Runner()
    captured = []

    async def exercise():
        delivered = asyncio.Event()

        async def normal_gateway_handler(event):
            captured.append(event)
            delivered.set()
            return None

        adapter.set_message_handler(normal_gateway_handler)
        posted = await adapter.post_authenticated_message(
            board_slug=channels.board_slug,
            thread_id=channels.founders_office_thread_id,
            actor_id="ceo",
            actor_name="CEO",
            body="@pm decompose the authenticated health endpoint",
            project_slug=pmo_project.slug,
        )
        await asyncio.wait_for(delivered.wait(), timeout=2)
        return posted

    posted = asyncio.run(exercise())

    assert pmo_adapter.PmoPlatformAdapter.handle_message is BasePlatformAdapter.handle_message
    assert posted.dispatched is True
    assert len(captured) == 1
    assert captured[0].source.profile == "pm-acme"
    assert captured[0].source.thread_id == channels.founders_office_thread_id
    assert captured[0].raw_message["trusted_instruction"] is True


def test_pmo_profile_can_use_normal_hermes_delegation(tmp_path, monkeypatch):
    _install_profile(tmp_path, monkeypatch)
    previous = platform_registry.get("pmo")
    platform_registry.register(
        PlatformEntry(
            name="pmo",
            label="Datansh PM-OS delegation probe",
            adapter_factory=lambda cfg: cfg,
            check_fn=lambda: True,
        )
    )
    parent = MagicMock()
    parent.base_url = "https://example.invalid/v1"
    parent.api_key = "test-key"
    parent.provider = "openrouter"
    parent.api_mode = "chat_completions"
    parent.model = "test/model"
    parent.platform = "pmo"
    parent.providers_allowed = None
    parent.providers_ignored = None
    parent.providers_order = None
    parent.provider_sort = None
    parent._session_db = None
    parent._delegate_depth = 0
    parent._active_children = []
    parent._active_children_lock = threading.Lock()
    parent._print_fn = None
    parent.tool_progress_callback = None
    parent.thinking_callback = None
    parent.enabled_toolsets = ["hermes-pmo"]
    parent.disabled_toolsets = []
    parent.session_estimated_cost_usd = 0.0
    parent.session_cost_status = "unknown"
    parent.session_cost_source = "none"

    try:
        with patch("run_agent.AIAgent") as agent_class:
            child = MagicMock()
            child.model = "test/model"
            child.session_prompt_tokens = 10
            child.session_completion_tokens = 5
            child.session_estimated_cost_usd = 0.0
            child.run_conversation.return_value = {
                "final_response": "Delegated verification complete.",
                "completed": True,
                "interrupted": False,
                "api_calls": 1,
                "messages": [{"role": "assistant", "content": "complete"}],
            }
            agent_class.return_value = child

            result = json.loads(
                delegate_task(
                    goal="Independently verify the PM acceptance evidence",
                    parent_agent=parent,
                )
            )
    finally:
        platform_registry.unregister("pmo")
        if previous is not None:
            platform_registry.register(previous)

    assert result["results"][0]["status"] == "completed"
    _, child_kwargs = agent_class.call_args
    assert child_kwargs["enabled_toolsets"] == ["hermes-pmo"]
    assert "delegation" in child_kwargs["disabled_toolsets"]


def test_pmo_tool_calls_keep_the_normal_human_approval_gate(monkeypatch):
    monkeypatch.setattr(hermes_approval, "_YOLO_MODE_FROZEN", False)
    hermes_approval.clear_session("pmo-parity")
    session_token = hermes_approval.set_current_session_key("pmo-parity")
    interactive_token = hermes_approval.set_hermes_interactive_context(True)
    try:
        approved = hermes_approval.request_tool_approval(
            "write_file",
            "PM change touches a protected deployment file",
            rule_key="pmo-parity-accept",
            approval_callback=lambda *args, **kwargs: "once",
        )
        denied = hermes_approval.request_tool_approval(
            "terminal",
            "PM command changes production state",
            rule_key="pmo-parity-deny",
            approval_callback=lambda *args, **kwargs: "deny",
        )
    finally:
        hermes_approval.reset_hermes_interactive_context(interactive_token)
        hermes_approval.reset_current_session_key(session_token)
        hermes_approval.clear_session("pmo-parity")

    assert approved["approved"] is True
    assert denied["approved"] is False
    assert denied["outcome"] == "denied"


def test_pmo_worker_executes_through_normal_kanban_agent_tools(
    pmo_project, monkeypatch
):
    with kanban_db.connect_closing(board=pmo_project.board_slug) as conn:
        task_id = kanban_db.create_task(
            conn,
            title="Implement compatibility probe",
            assignee="acme-dev-1",
            created_by="pm-acme",
            board=pmo_project.board_slug,
            project_id=pmo_project.project_id,
        )
        claimed = kanban_db.claim_task(conn, task_id, claimer="acme-dev-1")
    assert claimed is not None

    monkeypatch.setenv("HERMES_KANBAN_TASK", task_id)
    monkeypatch.setenv("HERMES_KANBAN_BOARD", pmo_project.board_slug)
    monkeypatch.setenv("HERMES_KANBAN_RUN_ID", str(claimed.current_run_id))
    monkeypatch.setenv("HERMES_KANBAN_CLAIM_LOCK", str(claimed.claim_lock))
    monkeypatch.setenv("HERMES_PROFILE", "acme-dev-1")
    monkeypatch.setenv("HERMES_SESSION_ID", "pmo-worker-parity")

    heartbeat = json.loads(registry.dispatch("kanban_heartbeat", {"note": "tests running"}))
    comment = json.loads(
        registry.dispatch(
            "kanban_comment",
            {"task_id": task_id, "body": "Compatibility evidence is ready."},
        )
    )
    completed = json.loads(
        registry.dispatch(
            "kanban_complete",
            {
                "summary": "Normal Kanban worker tool completed the PM task.",
                "metadata": {"tests_run": ["compatibility parity"]},
            },
        )
    )

    assert heartbeat["ok"] is True
    assert comment["ok"] is True
    assert completed["ok"] is True
    with kanban_db.connect_closing(board=pmo_project.board_slug) as conn:
        task = kanban_db.get_task(conn, task_id)
        run = kanban_db.latest_run(conn, task_id)
        comments = kanban_db.list_comments(conn, task_id)
    assert task is not None and task.status == "done"
    assert run is not None and run.outcome == "completed"
    assert run.metadata["worker_session_id"] == "pmo-worker-parity"
    assert comments[-1].author == "acme-dev-1"
