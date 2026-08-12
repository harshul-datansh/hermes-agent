from __future__ import annotations

import asyncio
from contextlib import nullcontext
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest
from starlette.requests import Request
from starlette.responses import Response

from hermes_cli import projects_db, web_server
from plugins.pmo import access_policy, project_scope


def _request(*, project_id: str, board: str) -> Request:
    query = urlencode({"project_id": project_id, "board": board}).encode()
    request = Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/api/plugins/pmo/tab-policy",
            "raw_path": b"/api/plugins/pmo/tab-policy",
            "query_string": query,
            "headers": [(b"x-datansh-project-id", project_id.encode())],
            "client": ("127.0.0.1", 1234),
            "server": ("127.0.0.1", 8787),
            "state": {"session": SimpleNamespace(user_id="ceo@datansh.local")},
        }
    )
    return request


@pytest.fixture
def project_boundary(monkeypatch, tmp_path):
    project = SimpleNamespace(
        id="project-one",
        slug="project-one",
        name="Project One",
        board_slug="project-one-board",
    )
    scope = SimpleNamespace(
        project_id=project.id,
        slug=project.slug,
        board_slug=project.board_slug,
        config=SimpleNamespace(
            orchestrator=SimpleNamespace(profile="pm-project-one")
        ),
        workspace=tmp_path,
        folders=(tmp_path,),
    )
    monkeypatch.setattr(projects_db, "connect_closing", lambda: nullcontext(object()))
    monkeypatch.setattr(projects_db, "get_project", lambda _conn, ref: project if ref == project.id else None)
    monkeypatch.setattr(project_scope, "resolve", lambda project_id, board_slug: scope)
    monkeypatch.setattr(
        access_policy,
        "principal_from_connection",
        lambda _request: access_policy.Principal("human:ceo@datansh.local", "human"),
    )
    monkeypatch.setattr(
        access_policy,
        "evaluate",
        lambda *_args, **_kwargs: SimpleNamespace(allowed=True),
    )
    return project, scope


def test_matching_project_and_board_reaches_pmo_handler(project_boundary):
    project, scope = project_boundary
    request = _request(project_id=project.id, board=project.board_slug)
    reached = False

    async def call_next(_request):
        nonlocal reached
        reached = True
        return Response(status_code=204)

    response = asyncio.run(
        web_server._project_scoped_core_request(request, call_next)
    )

    assert response.status_code == 204
    assert reached is True
    assert request.state.datansh_project_scope is scope


def test_mismatched_board_is_rejected_before_pmo_handler(project_boundary):
    project, _scope = project_boundary
    request = _request(project_id=project.id, board="another-project-board")
    reached = False

    async def call_next(_request):
        nonlocal reached
        reached = True
        return Response(status_code=204)

    response = asyncio.run(
        web_server._project_scoped_core_request(request, call_next)
    )

    assert response.status_code == 403
    assert reached is False
    assert b"Board does not belong to the selected project" in response.body


def test_websocket_scope_accepts_matching_project_profile_and_board(project_boundary):
    project, scope = project_boundary
    ws = SimpleNamespace(
        query_params={
            "project_id": project.id,
            "profile": scope.config.orchestrator.profile,
            "board": project.board_slug,
        },
        state=SimpleNamespace(
            datansh_ws_user_id="ceo@datansh.local",
            datansh_ws_principal_kind="human",
        ),
    )

    assert web_server._ws_project_scope_reason(ws) is None
    assert ws.state.datansh_project_scope is scope


def test_websocket_scope_rejects_another_projects_profile(project_boundary):
    project, _scope = project_boundary
    ws = SimpleNamespace(
        query_params={
            "project_id": project.id,
            "profile": "pm-project-two",
            "board": project.board_slug,
        },
        state=SimpleNamespace(
            datansh_ws_user_id="ceo@datansh.local",
            datansh_ws_principal_kind="human",
        ),
    )

    assert web_server._ws_project_scope_reason(ws) == "project_profile_mismatch"
