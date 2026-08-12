"""Tests for the project/portfolio read projection."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from hermes_cli import kanban_db, projects_db


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE = REPO_ROOT / "plugins" / "pmo" / "portfolio.py"


def _load_portfolio():
    spec = importlib.util.spec_from_file_location("pmo_portfolio", MODULE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def projects(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.delenv("HERMES_KANBAN_HOME", raising=False)
    monkeypatch.delenv("HERMES_KANBAN_DB", raising=False)
    created = []
    with projects_db.connect_closing() as conn:
        for slug in ("alpha", "beta"):
            workspace = tmp_path / slug
            workspace.mkdir()
            pid = projects_db.create_project(
                conn,
                name=slug.title(),
                slug=slug,
                primary_path=str(workspace),
                board_slug=slug,
            )
            created.append((pid, slug))
    for pid, slug in created:
        kanban_db.create_board(slug, name=slug.title(), project_id=pid)
    return created


def test_portfolio_filters_server_side_and_sorts_needs_you_first(projects):
    portfolio = _load_portfolio()
    alpha_id, _ = projects[0]
    beta_id, _ = projects[1]
    with kanban_db.connect_closing(board="alpha") as conn:
        kanban_db.create_task(conn, title="Done", project_id=alpha_id, board="alpha")
    with kanban_db.connect_closing(board="beta") as conn:
        target = kanban_db.create_task(conn, title="Ship", project_id=beta_id, board="beta")
        approval = kanban_db.create_task(
            conn,
            title="Approve",
            body="[pmo:approval:v1]\nInitial required rank: 70\n",
            project_id=beta_id,
            triage=True,
            board="beta",
        )
        kanban_db.link_tasks(conn, approval, target)

    only_alpha = portfolio.portfolio(
        visible_project_ids={alpha_id}, actor_rank=100, use_cache=False
    )
    both = portfolio.portfolio(
        visible_project_ids={alpha_id, beta_id}, actor_rank=100, use_cache=False
    )
    low_rank = portfolio.portfolio(
        visible_project_ids={alpha_id, beta_id}, actor_rank=50, use_cache=False
    )

    assert [row.slug for row in only_alpha] == ["alpha"]
    assert both[0].slug == "beta" and both[0].needs_you == 1
    assert next(row for row in low_rank if row.slug == "beta").needs_you == 0


def test_progress_excludes_archived_cancellations_and_cache_is_brief(projects):
    portfolio = _load_portfolio()
    alpha_id, _ = projects[0]
    with kanban_db.connect_closing(board="alpha") as conn:
        active = kanban_db.create_task(conn, title="Active", project_id=alpha_id, board="alpha")
        cancelled = kanban_db.create_task(conn, title="Cancelled", project_id=alpha_id, board="alpha")
        assert kanban_db.archive_task(conn, cancelled)

    first = portfolio.portfolio(visible_project_ids={alpha_id}, use_cache=True)[0]
    with kanban_db.connect_closing(board="alpha") as conn:
        kanban_db.create_task(conn, title="Later", project_id=alpha_id, board="alpha")
    cached = portfolio.portfolio(visible_project_ids={alpha_id}, use_cache=True)[0]
    refreshed = portfolio.portfolio(visible_project_ids={alpha_id}, use_cache=False)[0]

    assert first.total == 1 and first.progress == "0/1"
    assert cached.total == 1
    assert refreshed.total == 2


def test_delivery_rollup_excludes_native_conversation_records(projects):
    portfolio = _load_portfolio()
    alpha_id, _ = projects[0]
    body = (
        '<!-- {"kind":"founders_office","project_id":"' + alpha_id
        + '","schema":"datansh-pmo-conversation.v1"} -->\n\n'
        "Persistent native conversation"
    )
    with kanban_db.connect_closing(board="alpha") as conn:
        kanban_db.create_task(
            conn,
            title="Founder's Office",
            body=body,
            project_id=alpha_id,
            initial_status="blocked",
            board="alpha",
        )
        kanban_db.create_task(
            conn, title="Real delivery", project_id=alpha_id, board="alpha"
        )

    row = portfolio.portfolio(
        visible_project_ids={alpha_id}, use_cache=False
    )[0]

    assert row.total == 1
    assert row.blocked == 0


def test_overview_requires_explicit_visibility(projects):
    portfolio = _load_portfolio()
    alpha_id, _ = projects[0]
    with pytest.raises(PermissionError):
        portfolio.project_overview(project_ref="alpha", visible_project_ids=set())
    assert portfolio.project_overview(
        project_ref="alpha", visible_project_ids={alpha_id}
    ).slug == "alpha"
