from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hermes_cli import kanban_db


def _load_plugin():
    source = Path(__file__).resolve().parents[2] / "plugins" / "pmo" / "dashboard" / "plugin_api.py"
    name = f"pmo_security_plugin_{id(source)}"
    spec = importlib.util.spec_from_file_location(name, source)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def pmo_client(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_KANBAN_HOME", str(home))

    # Every PMO route resolves through ``_pmo_scope``, which requires a board
    # bound to a real project — an unbound board is refused with 403. Building
    # the same project fixture the rest of the suite uses keeps these
    # attachment tests exercising the production path instead of a shape that
    # can no longer exist.
    from tests.pmo_fixtures import make_pmo_test_project

    project = make_pmo_test_project(tmp_path / "projects", slug="acme", name="Acme")
    monkeypatch.setenv("HERMES_KANBAN_BOARD", project.board_slug)

    module = _load_plugin()
    app = FastAPI()
    app.dependency_overrides[module._route_access_guard] = lambda: None
    app.include_router(module.router, prefix="/api/plugins/pmo")
    client = TestClient(app)
    client.pmo_board = project.board_slug
    client.pmo_project_id = project.project_id
    return module, client


def _task(client):
    response = client.post(
        "/api/plugins/pmo/tasks",
        params={"board": getattr(client, "pmo_board", None)},
        json={"title": "attachment target"},
    )
    assert response.status_code == 200, response.text
    return response.json()["task"]["id"]


def test_type_allowlist_uses_sniffed_bytes_and_generated_storage(pmo_client):
    _module, client = pmo_client
    task_id = _task(client)
    response = client.post(
        f"/api/plugins/pmo/tasks/{task_id}/attachments",
        files={"file": ("looks-like.txt", b"%PDF-1.7 fixture", "text/plain")},
    )
    assert response.status_code == 200, response.text
    attachment = response.json()["attachment"]
    assert attachment["filename"] == "looks-like.txt"
    assert attachment["content_type"] == "application/pdf"
    stored = Path(attachment["stored_path"])
    assert stored.name.startswith("pmo_") and stored.suffix == ".pdf"
    assert stored.resolve().is_relative_to(kanban_db.attachments_root().resolve())


def test_non_allowlisted_binary_and_traversal_filename_rejected(pmo_client):
    _module, client = pmo_client
    task_id = _task(client)
    binary = client.post(
        f"/api/plugins/pmo/tasks/{task_id}/attachments",
        files={"file": ("payload.png", b"MZ\x00\x01binary", "image/png")},
    )
    assert binary.status_code == 400
    traversal = client.post(
        f"/api/plugins/pmo/tasks/{task_id}/attachments",
        files={"file": ("../../secret.txt", b"text", "text/plain")},
    )
    assert traversal.status_code == 400


def test_size_cap_is_enforced_while_streaming(pmo_client, monkeypatch):
    module, client = pmo_client
    monkeypatch.setattr(module, "KANBAN_ATTACHMENT_MAX_BYTES", 8)
    task_id = _task(client)
    response = client.post(
        f"/api/plugins/pmo/tasks/{task_id}/attachments",
        files={"file": ("large.txt", b"123456789", "text/plain")},
    )
    assert response.status_code == 413
    assert list(kanban_db.attachments_root().rglob("pmo_*")) == []


def test_download_forces_attachment_and_nosniff(pmo_client):
    _module, client = pmo_client
    task_id = _task(client)
    uploaded = client.post(
        f"/api/plugins/pmo/tasks/{task_id}/attachments",
        files={"file": ("note.txt", b"hello", "text/plain")},
    ).json()["attachment"]
    response = client.get(f"/api/plugins/pmo/attachments/{uploaded['id']}")
    assert response.status_code == 200
    assert response.headers["content-disposition"].startswith("attachment;")
    assert response.headers["x-content-type-options"] == "nosniff"


def test_render_and_link_safety_contract_is_present():
    source = (
        Path(__file__).resolve().parents[2] / "plugins" / "pmo" / "dashboard" / "dist" / "index.js"
    ).read_text(encoding="utf-8")
    assert "escapeHtml(working)" in source
    assert "sanitizeMarkdownHtml(renderMarkdown" in source
    assert "/^(https?:\\/\\/|mailto:)/i.test(href)" in source
    assert "javascript:" not in source[source.index("function sanitizeMarkdownAttrs"):source.index("function sanitizeMarkdownHtml")]
    assert '"img"' not in source[source.index("const MARKDOWN_ALLOWED_TAGS"):source.index("function escapeAttribute")]


def test_manifest_sri_matches_bundle():
    root = Path(__file__).resolve().parents[2] / "plugins" / "pmo" / "dashboard"
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    expected = "sha384-" + base64.b64encode(
        hashlib.sha384((root / manifest["entry"]).read_bytes()).digest()
    ).decode("ascii")
    assert manifest["integrity"] == expected
