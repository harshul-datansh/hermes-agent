"""Behavior tests for the additive PM-OS dashboard projections."""

from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from hermes_cli import kanban_db
from plugins.platforms.pmo.adapter import (
    create_project_conversation,
    ensure_project_conversations,
)
from plugins.pmo import (
    approval,
    bootstrap,
    github_app,
    mentions,
    planning,
    project_scope,
    workflow,
)
from plugins.pmo.dashboard.plugin_api import router


def _client(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.delenv("HERMES_KANBAN_HOME", raising=False)
    monkeypatch.delenv("HERMES_KANBAN_DB", raising=False)
    workspace = tmp_path / "acme"
    workspace.mkdir()
    result = bootstrap.bootstrap_project(
        slug="acme", name="Acme Portal", workspace=workspace
    )
    scope = project_scope.resolve(result.project_id, board_slug=result.board_slug)
    conversations = ensure_project_conversations(scope)
    kanban_db.set_current_board(result.board_slug)
    app = FastAPI()
    app.state.auth_required = False
    app.include_router(router)
    return TestClient(app), scope, conversations


def test_github_setup_callback_returns_to_pmo_dashboard(tmp_path, monkeypatch):
    client, _scope, _conversations = _client(tmp_path, monkeypatch)

    monkeypatch.setattr(
        github_app,
        "finalize_install",
        lambda **_kwargs: (
            object(),
            github_app.InstallationInfo(101, "hermes-test-org", "selected", "write"),
        ),
    )
    monkeypatch.setattr(
        github_app,
        "load_settings",
        lambda: github_app.GitHubAppSettings(
            app_slug="datansh-hermes-test",
            app_id=4509111,
            client_id="Iv23test",
            callback_url="http://127.0.0.1:8787/api/plugins/pmo/github-app/callback",
            api_url="https://api.github.com",
            private_key="test-only",
        ),
    )

    response = client.get(
        "/github-app/callback?state=" + ("s" * 32) + "&installation_id=101",
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "http://127.0.0.1:8787/pmo#pmo/onboarding"


def test_pm_screen_endpoints_derive_from_native_records(tmp_path, monkeypatch):
    client, scope, conversations = _client(tmp_path, monkeypatch)
    dangerous_title = '<img src=x onerror="alert(1)"> blocked release'
    with kanban_db.connect_closing(board=scope.board_slug) as conn:
        task_id = kanban_db.create_task(
            conn,
            title=dangerous_title,
            body="Acceptance criteria:\n- stays native",
            project_id=scope.project_id,
            created_by="tester",
            initial_status="blocked",
            board=scope.board_slug,
        )
        kanban_db.add_comment(
            conn,
            task_id,
            "tester",
            "[unsafe](javascript:alert(1)) <script>alert(2)</script>",
        )
        for index, touches in enumerate(("src/api/**", "src/api/routes.py")):
            kanban_db.create_task(
                conn,
                title=f"Overlapping change {index}",
                body=(
                    f"{workflow.TICKET_META_MARKER}\n"
                    "Labels:\n"
                    f'Touches: ["{touches}"]'
                ),
                project_id=scope.project_id,
                created_by="tester",
                board=scope.board_slug,
            )
    gate = approval.raise_approval(
        board_slug=scope.board_slug,
        target_task_id=task_id,
        title="Release exception",
        detail="Allow the native release",
        raised_by="human:requester",
        required_rank=70,
        approver_profile="dashboard:local-operator",
    )
    mention_result = mentions.post_comment(
        scope,
        task_id=task_id,
        author="pm",
        body="@founders-office please review",
    )
    query = f"?board={scope.board_slug}"

    portfolio = client.get("/portfolio" + query)
    assert portfolio.status_code == 200
    assert portfolio.json()["projects"][0]["project_id"] == scope.project_id

    overview = client.get(f"/projects/{scope.project_id}/overview" + query)
    assert overview.status_code == 200
    attention_titles = [item["title"] for item in overview.json()["needs_attention"]]
    assert dangerous_title in attention_titles
    assert "Founder's Office" not in attention_titles
    assert "Client Collaboration" not in attention_titles
    overlap_items = [
        item for item in overview.json()["needs_attention"] if item["kind"] == "overlap"
    ]
    assert len(overlap_items) == 2
    assert all("touches overlap" in item["detail"] for item in overlap_items)

    approvals = client.get("/approvals" + query)
    assert approvals.status_code == 200
    assert approvals.json()["approvals"][0]["approval_id"] == gate.approval_id
    assert approvals.json()["approvals"][0]["can_decide"] is True

    timeline = client.get(f"/timeline/{task_id}" + query)
    assert timeline.status_code == 200
    summaries = [item["summary"] for item in timeline.json()["items"]]
    assert any("javascript:alert(1)" in summary for summary in summaries)

    notifications = client.get("/notifications" + query)
    assert notifications.status_code == 200
    assert notifications.json()["notifications"][0]["id"] in mention_result.notification_ids
    assert notifications.json()["notifications"][0]["link"].startswith("/pmo/")

    access = client.get("/access" + query)
    assert access.status_code == 200
    assert access.json()["members"][0]["principal"] == "dashboard:local-operator"

    decisions = client.get("/decisions" + query)
    assert decisions.status_code == 200
    assert "Datansh project context" in decisions.json()["context"]

    spend = client.get("/spend" + query)
    assert spend.status_code == 200
    assert spend.json()["currency"] == "USD"
    assert spend.json()["cost_usd"] == "0"

    threads = client.get(f"/projects/{scope.project_id}/conversations" + query)
    assert threads.status_code == 200
    assert threads.json()["conversations"][0]["kind"] == "founders_office"
    thread_ids = {item["thread_id"] for item in threads.json()["conversations"]}
    assert conversations.founders_office_thread_id in thread_ids
    detail = client.get(
        f"/conversations/{conversations.founders_office_thread_id}" + query
    )
    assert detail.status_code == 200
    assert detail.json()["conversation"]["kind"] == "founders_office"
    chat_payload = '<svg onload="alert(3)">chat</svg>'
    with kanban_db.connect_closing(board=scope.board_slug) as conn:
        kanban_db.add_comment(
            conn,
            conversations.founders_office_thread_id,
            "human:test",
            chat_payload,
        )
    detail = client.get(
        f"/conversations/{conversations.founders_office_thread_id}" + query
    )
    assert detail.status_code == 200
    assert detail.json()["messages"][-1]["body"] == chat_payload


def test_overview_activity_is_human_readable_filterable_and_paginated(tmp_path, monkeypatch):
    client, scope, _conversations = _client(tmp_path, monkeypatch)
    with kanban_db.connect_closing(board=scope.board_slug) as conn:
        task_id = kanban_db.create_task(
            conn,
            title="Improve customer onboarding",
            body="A customer-facing delivery ticket.",
            project_id=scope.project_id,
            created_by=scope.config.orchestrator.profile,
            board=scope.board_slug,
        )
        for index in range(10):
            kanban_db.add_comment(
                conn, task_id, scope.config.orchestrator.profile, f"Customer update {index}"
            )

    query = f"?board={scope.board_slug}&activity_limit=3&activity_filter=discussion&activity_query=onboarding"
    response = client.get(f"/projects/{scope.project_id}/overview" + query)

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["activity_total"] == 10
    assert len(data["activity"]) == 3
    assert "discussion" in data["activity_filters"]
    first = data["activity"][0]
    assert first == {
        "id": first["id"],
        "timestamp": first["timestamp"],
        "category": "discussion",
        "summary": "Added a ticket update",
        "source": "Project manager",
        "actor": "Project manager",
        "context": "Ticket · Improve customer onboarding",
        "task_id": task_id,
        "ticket_href": f"#pmo/board?board={scope.board_slug}&task={task_id}",
    }
    assert "kind" not in first and "title" not in first

    second = client.get(
        f"/projects/{scope.project_id}/overview?board={scope.board_slug}&activity_limit=3&activity_filter=discussion&activity_offset=3"
    )
    assert second.status_code == 200
    assert second.json()["activity_offset"] == 3
    assert len(second.json()["activity"]) == 3


def test_portfolio_bootstraps_before_a_board_is_selected(tmp_path, monkeypatch):
    client, scope, _ = _client(tmp_path, monkeypatch)
    kanban_db.set_current_board(kanban_db.DEFAULT_BOARD)

    response = client.get("/portfolio")

    assert response.status_code == 200
    assert [item["project_id"] for item in response.json()["projects"]] == [
        scope.project_id
    ]


def test_administration_workspace_keeps_chat_and_actions_project_scoped(tmp_path, monkeypatch):
    client, scope, _ = _client(tmp_path, monkeypatch)

    catalog = client.get("/administration")
    assert catalog.status_code == 200
    assert catalog.json()["can_access"] is True
    assert catalog.json()["projects"][0]["project_id"] == scope.project_id

    posted = client.post(
        f"/administration/projects/{scope.project_id}/messages",
        json={"body": "Please create a QA agent and use OpenAI Codex."},
    )
    assert posted.status_code == 200
    messages = posted.json()["messages"]
    assert messages[0]["kind"] == "human"
    assert messages[1]["kind"] == "assistant"
    assert any(item["kind"] == "connect_codex" for item in messages[1]["actions"])

    read_back = client.get(f"/administration/projects/{scope.project_id}/messages")
    assert read_back.status_code == 200
    assert len(read_back.json()["messages"]) == 2


def test_administration_runtime_configuration_is_project_scoped(tmp_path, monkeypatch):
    client, scope, _ = _client(tmp_path, monkeypatch)

    updated = client.put(
        f"/administration/projects/{scope.project_id}/runtime",
        json={
            "skills": ["pmo", "browser"],
            "plugins": ["github"],
            "channels": ["pmo", "slack"],
        },
    )

    assert updated.status_code == 200
    assert updated.json()["runtime"] == {
        "skills": ["pmo", "browser"],
        "plugins": ["github"],
        "channels": ["pmo", "slack"],
    }
    read_back = client.get(f"/administration/projects/{scope.project_id}/messages")
    assert read_back.status_code == 200
    configuration = read_back.json()["project"]["configuration"]
    assert configuration["project"]["runtime"] == updated.json()["runtime"]
    assert configuration["effective"]["runtime"]["skills"] == ["pmo", "browser"]
    config = project_scope.resolve(scope.project_id, board_slug=scope.board_slug).config
    assert config.runtime.skills == ("pmo", "browser")
    assert config.models.pm is None


def test_global_runtime_defaults_combine_with_project_settings_without_duplicates(
    tmp_path, monkeypatch
):
    client, scope, _ = _client(tmp_path, monkeypatch)

    saved = client.put(
        "/administration/global/configuration",
        json={
            "models": {"worker": "gpt-5.6"},
            "runtime": {
                "skills": ["pmo", "browser"],
                "plugins": ["github"],
                "channels": ["pmo", "slack"],
            },
        },
    )
    assert saved.status_code == 200

    project = client.put(
        f"/administration/projects/{scope.project_id}/runtime",
        json={"skills": ["browser", "qa"], "plugins": ["github", "linear"], "channels": ["slack", "email"]},
    )
    assert project.status_code == 200
    detail = client.get(f"/administration/projects/{scope.project_id}/messages")
    configuration = detail.json()["project"]["configuration"]
    assert configuration["effective"]["runtime"] == {
        "skills": ["pmo", "browser", "qa"],
        "plugins": ["github", "linear"],
        "channels": ["pmo", "slack", "email"],
    }
    assert configuration["effective"]["models"]["worker"] == "gpt-5.6"
    assert "access" not in configuration["global"]


def test_administration_credentials_show_precedence_and_remove_only_project_override(
    tmp_path, monkeypatch
):
    client, scope, _ = _client(tmp_path, monkeypatch)
    from hermes_cli import profiles

    home = tmp_path / ".hermes"
    pm_profile = profiles.get_profile_dir(scope.config.orchestrator.profile)
    global_state = {
        "version": 1,
        "providers": {"openai-codex": {"tokens": {"access_token": "global"}}},
    }
    project_state = {
        "version": 1,
        "active_provider": "openai-codex",
        "providers": {"openai-codex": {"tokens": {"access_token": "project"}}},
    }
    (home / "auth.json").write_text(json.dumps(global_state))
    (pm_profile / "auth.json").write_text(json.dumps(project_state))

    before = client.get(f"/administration/projects/{scope.project_id}/messages")
    assert before.status_code == 200
    providers = before.json()["project"]["health"]["providers"]
    pm = next(row for row in providers if row["handle"] == "pm")
    assert pm["openai_codex_source"] == "project"

    removed = client.delete(
        f"/administration/projects/{scope.project_id}/credentials/pm/openai-codex"
    )
    assert removed.status_code == 200
    assert removed.json()["removed"] is True
    pm = next(row for row in removed.json()["providers"] if row["handle"] == "pm")
    assert pm["openai_codex_source"] == "global"
    assert json.loads((home / "auth.json").read_text()) == global_state


def test_approval_action_rechecks_rank_and_is_immutable(tmp_path, monkeypatch):
    client, scope, _ = _client(tmp_path, monkeypatch)
    with kanban_db.connect_closing(board=scope.board_slug) as conn:
        task_id = kanban_db.create_task(
            conn,
            title="Ship",
            project_id=scope.project_id,
            initial_status="blocked",
            board=scope.board_slug,
        )
    gate = approval.raise_approval(
        board_slug=scope.board_slug,
        target_task_id=task_id,
        title="Ship",
        detail="Production release",
        raised_by="human:requester",
        required_rank=70,
        approver_profile="dashboard:local-operator",
    )
    url = f"/approvals/{gate.approval_id}/decision?board={scope.board_slug}"
    first = client.post(url, json={"decision": "approved", "note": "Reviewed"})
    assert first.status_code == 200
    assert first.json()["status"] == "approved"
    second = client.post(url, json={"decision": "rejected", "note": "Changed mind"})
    assert second.status_code == 409


def test_conversation_load_is_bounded_and_can_scroll_back(tmp_path, monkeypatch):
    client, scope, conversations = _client(tmp_path, monkeypatch)
    created: list[int] = []
    with kanban_db.connect_closing(board=scope.board_slug) as conn:
        for index in range(150):
            created.append(
                kanban_db.add_comment(
                    conn,
                    conversations.founders_office_thread_id,
                    "human:test",
                    f"message-{index:03d}",
                )
            )

    base = (
        f"/conversations/{conversations.founders_office_thread_id}"
        f"?board={scope.board_slug}&limit=100"
    )
    newest = client.get(base)
    assert newest.status_code == 200
    payload = newest.json()
    assert [item["id"] for item in payload["messages"]] == created[-100:]
    assert payload["pagination"]["has_more"] is True
    assert payload["pagination"]["before"] == created[-100]

    older = client.get(base + f"&before={created[-100]}")
    assert older.status_code == 200
    assert [item["id"] for item in older.json()["messages"]] == created[:50]
    assert older.json()["pagination"]["has_more"] is False


def test_founders_office_folders_multiple_chats_and_global_fanout(
    tmp_path, monkeypatch
):
    client, scope, conversations = _client(tmp_path, monkeypatch)
    query = f"?board={scope.board_slug}"

    created = client.post(
        f"/projects/{scope.project_id}/conversations{query}",
        json={"title": "Launch planning", "kind": "founders_office"},
    )
    assert created.status_code == 200
    assert created.json()["conversation"]["title"] == "Launch planning"

    listed = client.get(f"/projects/{scope.project_id}/conversations{query}")
    assert listed.status_code == 200
    titles = {item["title"] for item in listed.json()["conversations"]}
    assert {"Founder's Office", "Launch planning", "Global Founder's Office"} <= titles

    # Portfolio-wide chat authorization must not depend on whichever board a
    # CLI process last selected. The handler evaluates access project by
    # project after the identity-only router guard.
    kanban_db.create_board("unbound", name="Unbound board")
    kanban_db.set_current_board("unbound")
    folders = client.get("/founders-office/folders")
    assert folders.status_code == 200
    folder = folders.json()["folders"][0]
    assert folder["project_id"] == scope.project_id
    assert folder["pm_profile"] == "pm-acme"
    assert any(item["title"] == "Launch planning" for item in folder["conversations"])
    assert all("agent_activity" in item for item in folder["conversations"])
    expected_agents = {"pm", *(agent.handle for agent in scope.config.agents)}
    assert expected_agents <= {
        item["handle"] for item in folder["founder_participants"]
        if item["kind"] == "agent"
    }
    assert expected_agents <= {
        item["handle"] for item in folders.json()["global"]["participants"]
        if item["kind"] == "agent"
    }

    new_global = client.post(
        "/founders-office/global/conversations",
        json={"title": "Portfolio architecture"},
    )
    assert new_global.status_code == 200
    global_id = new_global.json()["conversation"]["thread_id"]
    assert len(global_id) == 32
    assert new_global.json()["bindings"][0]["project_id"] == scope.project_id

    refreshed = client.get("/founders-office/folders").json()
    global_titles = {item["title"] for item in refreshed["global"]["conversations"]}
    assert {"Global Founder's Office", "Portfolio architecture"} <= global_titles

    named_post = client.post(
        f"/founders-office/global/conversations/{global_id}/messages",
        json={"body": "@dev-1 inspect the shared contract", "project_ids": [scope.project_id]},
    )
    assert named_post.status_code == 200
    named_chat = client.get(f"/founders-office/global/conversations/{global_id}")
    assert named_chat.status_code == 200
    assert named_chat.json()["conversation"]["title"] == "Portfolio architecture"
    assert named_chat.json()["messages"][-1]["body"] == "@dev-1 inspect the shared contract"

    native_global_thread = new_global.json()["bindings"][0]["thread_id"]
    planning.record_context_review(
        scope,
        thread_id=native_global_thread,
        author="agent:pm-acme",
        request="Review the shared architecture",
        context="Project-scoped repository and configuration context.",
    )
    plan = planning.record_plan(
        scope,
        thread_id=native_global_thread,
        author="agent:pm-acme",
        objective="Review the shared architecture",
        discussion="Use the developer's repository findings before deciding.",
    )
    child = create_project_conversation(scope, title="Global architecture review")
    planning.record_consultation(
        scope,
        plan=plan,
        consultation_thread_id=child.thread_id,
        author="agent:pm-acme",
        handle="dev-1",
        profile="dev-acme-1",
        question="Which repository constraint changes the decision?",
    )
    specialist_result = (
        "Keep the public API compatible, preserve the existing mobile response shape, "
        "and add Arabic fields additively so current clients continue to work while "
        "the new administration workflow rolls out."
    )
    with kanban_db.connect_closing(board=scope.board_slug) as conn:
        response_id = kanban_db.add_comment(
            conn, child.thread_id, "agent:dev-acme-1", specialist_result
        )
    planning.record_consultation_response(
        scope,
        consultation_thread_id=child.thread_id,
        author="agent:dev-acme-1",
        response_comment_id=response_id,
    )
    named_detail = client.get(f"/founders-office/global/conversations/{global_id}").json()
    assert named_detail["subchats"][0]["thread_id"] == child.thread_id
    assert named_detail["subchats"][0]["result"] == specialist_result
    assert all(message["id"] != response_id for message in named_detail["messages"])

    posted = client.post(
        "/founders-office/global/messages",
        json={"body": "@pm report launch readiness", "project_ids": [scope.project_id]},
    )
    assert posted.status_code == 200
    assert posted.json()["persisted"] is True
    assert posted.json()["dispatched_project_ids"] == [scope.project_id]

    global_chat = client.get("/founders-office/global")
    assert global_chat.status_code == 200
    assert global_chat.json()["conversation"]["thread_id"] == "global"
    assert global_chat.json()["messages"][-1]["body"] == "@pm report launch readiness"
    assert global_chat.json()["messages"][-1]["projects"] == [scope.project_id]
    assert "activities" in global_chat.json()

    detail = client.get(
        f"/conversations/{conversations.founders_office_thread_id}" + query
    )
    assert detail.status_code == 200
    assert detail.json()["agent_activity"]["state"] == "idle"
    assert detail.json()["agent_activity"]["api_calls"] == 0


def test_global_chat_targets_exact_pm_and_rejects_unknown_pm(tmp_path, monkeypatch):
    client, acme, acme_conversations = _client(tmp_path, monkeypatch)
    beta_workspace = tmp_path / "beta"
    beta_workspace.mkdir()
    beta_boot = bootstrap.bootstrap_project(
        slug="beta", name="Beta Console", workspace=beta_workspace
    )
    beta = project_scope.resolve(beta_boot.project_id, board_slug=beta_boot.board_slug)
    beta_conversations = ensure_project_conversations(beta)

    targeted = client.post(
        "/founders-office/global/messages",
        json={
            "body": "@pm-acme report only Acme status",
            "project_ids": [acme.project_id, beta.project_id],
        },
    )
    assert targeted.status_code == 200
    assert targeted.json()["dispatched_project_ids"] == [acme.project_id]
    with kanban_db.connect_closing(board=acme.board_slug) as conn:
        acme_comments = kanban_db.list_comments(
            conn, acme_conversations.global_founders_office_thread_id
        )
    with kanban_db.connect_closing(board=beta.board_slug) as conn:
        beta_comments = kanban_db.list_comments(
            conn, beta_conversations.global_founders_office_thread_id
        )
    assert any("report only Acme status" in item.body for item in acme_comments)
    assert not any("report only Acme status" in item.body for item in beta_comments)

    unknown = client.post(
        "/founders-office/global/messages",
        json={
            "body": "@pm-does-not-exist report status",
            "project_ids": [acme.project_id, beta.project_id],
        },
    )
    assert unknown.status_code == 422
    assert "Unknown project manager" in unknown.json()["detail"]


def test_founder_consultation_is_nested_in_parent_and_hidden_from_folders(
    tmp_path, monkeypatch
):
    client, scope, conversations = _client(tmp_path, monkeypatch)
    parent_thread_id = conversations.founders_office_thread_id
    planning.record_context_review(
        scope,
        thread_id=parent_thread_id,
        author="agent:pm-acme",
        request="Review the repository before planning.",
        context="Project-scoped repository context.",
    )
    plan = planning.record_plan(
        scope,
        thread_id=parent_thread_id,
        author="agent:pm-acme",
        objective="Add Arabic campaign administration",
        discussion="Ask engineering to verify the existing contract first.",
    )
    child = create_project_conversation(
        scope, title=f"Plan {plan.plan_id} · @dev-1 · Review campaign contracts"
    )
    planning.record_consultation(
        scope,
        plan=plan,
        consultation_thread_id=child.thread_id,
        author="agent:pm-acme",
        handle="dev-1",
        profile="dev-acme-1",
        question="What contract must be confirmed before implementation?",
    )
    with kanban_db.connect_closing(board=scope.board_slug) as conn:
        response_id = kanban_db.add_comment(
            conn,
            child.thread_id,
            author="agent:dev-acme-1",
            body=(
                "Confirm the mobile read endpoint, lifecycle transitions, bilingual "
                "content schema, scheduling timezone, audience targeting, and admin "
                "authorization before treating the current frontend types as authoritative."
            ),
        )
    planning.record_consultation_response(
        scope,
        consultation_thread_id=child.thread_id,
        author="agent:dev-acme-1",
        response_comment_id=response_id,
    )

    query = f"?board={scope.board_slug}"
    detail = client.get(f"/conversations/{parent_thread_id}" + query)
    assert detail.status_code == 200
    subchat = detail.json()["subchats"][0]
    assert subchat["thread_id"] == child.thread_id
    assert subchat["handle"] == "dev-1"
    assert subchat["status"] == "completed"
    assert "mobile read endpoint" in subchat["result"]
    assert "agent_activity" in subchat

    folders = client.get("/founders-office/folders")
    assert folders.status_code == 200
    visible_threads = {
        item["thread_id"]
        for folder in folders.json()["folders"]
        for item in folder["conversations"]
    }
    assert parent_thread_id in visible_threads
    assert child.thread_id not in visible_threads


def test_project_access_and_human_delegation_are_role_scoped(tmp_path, monkeypatch):
    client, scope, _ = _client(tmp_path, monkeypatch)
    query = f"?board={scope.board_slug}"

    access = client.get("/access" + query)
    assert access.status_code == 200
    assert access.json()["actor"]["project_role"] == "project_admin"
    assert access.json()["can_manage_members"] is True
    assert access.json()["can_manage_ranks"] is True

    member = client.put(
        "/access/members" + query,
        json={"principal": "human:pm@example.test", "role": "pm"},
    )
    assert member.status_code == 200
    assert any(row["principal"] == "human:pm@example.test" for row in member.json()["members"])

    delegated = client.post(
        "/human-tasks" + query,
        json={
            "title": "Confirm registrar ownership",
            "outcome": "Human confirms registrar ownership",
            "assignee": "human:cfo@example.test",
            "acceptance_criteria": ["confirmation is recorded"],
            "evidence": ["comment with registrar reference"],
        },
    )
    assert delegated.status_code == 200
    task_id = delegated.json()["task_id"]
    with kanban_db.connect_closing(board=scope.board_slug) as conn:
        task = kanban_db.get_task(conn, task_id)
    assert task is not None
    assert task.status == "triage"
    assert task.assignee == "human:cfo@example.test"

    finalized = client.post(f"/tasks/{task_id}/finalize" + query)
    assert finalized.status_code == 200
    assert finalized.json()["status"] in {"todo", "ready"}


def test_board_task_writes_reject_foreign_project_and_agent(tmp_path, monkeypatch):
    client, scope, _ = _client(tmp_path, monkeypatch)
    query = f"?board={scope.board_slug}"

    foreign_agent = client.post(
        "/tasks" + query,
        json={"title": "Cross-project work", "assignee": "pm-other-project"},
    )
    foreign_project = client.post(
        "/tasks" + query,
        json={"title": "Wrong project", "project_id": "another-project"},
    )
    local = client.post(
        "/tasks" + query,
        json={"title": "Local work", "assignee": "pm-acme"},
    )

    assert foreign_agent.status_code == 400
    assert "not in project" in foreign_agent.json()["detail"]
    assert foreign_project.status_code == 400
    assert "must match" in foreign_project.json()["detail"]
    assert local.status_code == 200
    task_id = local.json()["task"]["id"]
    reassigned = client.post(
        f"/tasks/{task_id}/reassign" + query,
        json={"profile": "pm-other-project"},
    )
    assert reassigned.status_code == 400
