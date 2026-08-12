"""Runtime smoke tests for the copied PM-OS dashboard plugin."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from hermes_cli import web_server


REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = REPO_ROOT / "plugins" / "pmo" / "dashboard"


def test_pmo_is_discovered_as_an_independent_bundled_dashboard_plugin():
    plugins = web_server._discover_dashboard_plugins()
    pmo = next(plugin for plugin in plugins if plugin["name"] == "pmo")

    assert pmo["source"] == "bundled"
    assert pmo["tab"]["path"] == "/pmo"
    assert pmo["has_api"] is True
    assert Path(pmo["_dir"]).resolve() == PLUGIN_ROOT.resolve()
    assert (PLUGIN_ROOT / pmo["entry"]).is_file()
    assert (PLUGIN_ROOT / pmo["css"]).is_file()


def test_dashboard_documentation_route_is_not_shadowed_by_fastapi_docs():
    assert web_server.app.docs_url == "/api/docs"
    assert web_server.app.redoc_url == "/api/redoc"


def test_pmo_backend_router_imports_and_keeps_native_kanban_routes():
    path = PLUGIN_ROOT / "plugin_api.py"
    spec = importlib.util.spec_from_file_location("pmo_dashboard_runtime", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    route_paths = {getattr(route, "path", "") for route in module.router.routes}
    assert "/boards" in route_paths
    assert "/tasks" in route_paths
    assert "/events" in route_paths
    assert "/workers/active" in route_paths
    assert "/diagnostics" in route_paths
    assert "/portfolio" in route_paths
    assert "/projects/{project_ref}/overview" in route_paths
    assert "/projects/{project_ref}/conversations" in route_paths
    assert "/conversations/{thread_id}" in route_paths
    assert "/approvals" in route_paths
    assert "/timeline/{task_id}" in route_paths
    assert "/notifications" in route_paths
    assert "/mentions/autocomplete" in route_paths
    assert "/access" in route_paths
    assert "/decisions" in route_paths
    assert "/spend" in route_paths
    assert (
        "/administration/projects/{project_ref}/credentials/{handle}/{provider}"
        in route_paths
    )
    assert "/administration/projects/{project_ref}/runtime" in route_paths
    assert "/administration/global/configuration" in route_paths
