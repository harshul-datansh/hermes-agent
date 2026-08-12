"""Tests for the PM-OS bootstrap edge utility."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP_PATH = REPO_ROOT / "plugins" / "pmo" / "bootstrap.py"


def _load_bootstrap():
    spec = importlib.util.spec_from_file_location("pmo_bootstrap", BOOTSTRAP_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def isolated_hermes_home(tmp_path, monkeypatch):
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    return hermes_home


def test_bootstrap_creates_and_reuses_existing_hermes_records(
    tmp_path, isolated_hermes_home
):
    bootstrap = _load_bootstrap()
    workspace = tmp_path / "project"
    workspace.mkdir()

    first = bootstrap.bootstrap_project(slug="acme", name="Acme", workspace=workspace)
    second = bootstrap.bootstrap_project(slug="acme", name="Acme", workspace=workspace)

    assert first.created_project is True
    assert first.created_board is True
    assert second.project_id == first.project_id
    assert second.created_project is False
    assert second.created_board is False
    assert first.created_access is True
    assert second.created_access is False
    assert (isolated_hermes_home / "projects.db").exists()
    assert (isolated_hermes_home / "kanban" / "boards" / "acme" / "kanban.db").exists()
    access_path = workspace / ".datansh" / "access.yaml"
    access = yaml.safe_load(access_path.read_text(encoding="utf-8"))
    assert access["members"] == [
        {"principal": "dashboard:local-operator", "role": "project_admin"}
    ]
    assert access["org_ranks"] == [
        {"principal": "dashboard:local-operator", "rank": 100}
    ]
    assert first.created_profiles == ("pm-acme", "pm-acme-client")
    assert second.created_profiles == ()
    assert (isolated_hermes_home / "profiles" / "pm-acme").is_dir()
    assert (isolated_hermes_home / "profiles" / "pm-acme-client").is_dir()
    config = yaml.safe_load(
        (isolated_hermes_home / "config.yaml").read_text(encoding="utf-8")
    )
    gateway = config["gateway"]
    assert gateway["multiplex_profiles"] is True
    assert {
        item["profile"]
        for item in gateway["profile_routes"]
        if item["platform"] == "pmo"
    } == {
        "pm-acme",
        "pm-acme-client",
    }


def test_bootstrap_never_overwrites_existing_access_policy(tmp_path, isolated_hermes_home):
    bootstrap = _load_bootstrap()
    workspace = tmp_path / "project"
    workspace.mkdir()

    first = bootstrap.bootstrap_project(slug="acme", name="Acme", workspace=workspace)
    custom = first.access_path.read_text(encoding="utf-8").replace(
        "project_admin", "viewer"
    )
    first.access_path.write_text(custom, encoding="utf-8")

    second = bootstrap.bootstrap_project(slug="acme", name="Acme", workspace=workspace)

    assert second.created_access is False
    assert second.access_path.read_text(encoding="utf-8") == custom


def test_bootstrap_refuses_to_retarget_an_existing_project(tmp_path, isolated_hermes_home):
    bootstrap = _load_bootstrap()
    first_workspace = tmp_path / "first"
    second_workspace = tmp_path / "second"
    first_workspace.mkdir()
    second_workspace.mkdir()
    bootstrap.bootstrap_project(slug="acme", name="Acme", workspace=first_workspace)

    with pytest.raises(ValueError, match="refusing to retarget"):
        bootstrap.bootstrap_project(slug="acme", name="Acme", workspace=second_workspace)
