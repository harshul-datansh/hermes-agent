"""Reusable isolated fixtures for PM-OS L1-L3 tests."""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

from hermes_cli import kanban_db
from plugins.pmo import bootstrap, project_scope


@dataclass(frozen=True)
class PmoTestProject:
    slug: str
    name: str
    board_slug: str
    project_id: str
    workspace: Path
    scope: project_scope.ProjectScope
    founders_office_task_id: str
    identities: tuple[str, ...] = ("ceo", "cfo", "pm")


@dataclass
class FakeClock:
    current: float = 1_800_000_000.0

    def time(self) -> float:
        return self.current

    def advance(self, *, seconds: float = 0, minutes: float = 0, hours: float = 0) -> float:
        self.current += float(seconds) + float(minutes) * 60 + float(hours) * 3600
        return self.current


def _project_yaml(slug: str, name: str, *, agents: tuple[tuple[str, str, str], ...]) -> str:
    roster = "\n".join(
        f"  - handle: {handle}\n    profile: {profile}\n    role: {role}"
        for handle, profile, role in agents
    )
    return f"""version: 1
project:
  name: {name}
  slug: {slug}
orchestrator:
  profile: pm-{slug}
  auto_decompose: true
  auto_promote_children: true
agents:
{roster}
board:
  columns: [todo, in_progress, blocked, review, done]
  labels: [spend, release, bug]
approvals:
  rules:
    - when: {{label: spend}}
      required_rank: 70
escalation:
  pm_retry_limit: 2
  blocked_timeout_minutes: 30
scope:
  folders: ['.']
  deny: ['.env', '.git/**']
"""


def make_pmo_test_project(
    root: Path,
    *,
    slug: str,
    name: str,
    agents: tuple[tuple[str, str, str], ...] | None = None,
) -> PmoTestProject:
    workspace = root / slug
    workspace.mkdir(parents=True)
    subprocess.run(
        ["git", "init", "--quiet", str(workspace)],
        check=True,
        capture_output=True,
        text=True,
    )
    result = bootstrap.bootstrap_project(slug=slug, name=name, workspace=workspace)
    configured_agents = agents or (
        ("dev-1", f"{slug}-dev-1", "Engineer"),
        ("qa", f"{slug}-qa", "Reviewer"),
        ("dev-2", f"{slug}-dev-2", "Engineer"),
    )
    result.config_path.write_text(
        _project_yaml(slug, name, agents=configured_agents), encoding="utf-8"
    )
    scope = project_scope.resolve(slug, board_slug=result.board_slug)
    with kanban_db.connect_closing(board=result.board_slug) as conn:
        thread_task = kanban_db.create_task(
            conn,
            title="Founders Office",
            body="Durable project leadership thread.",
            assignee=f"pm-{slug}",
            created_by="pmo-bootstrap",
            triage=True,
            board=result.board_slug,
            project_id=result.project_id,
        )
    return PmoTestProject(
        slug=slug,
        name=name,
        board_slug=result.board_slug,
        project_id=result.project_id,
        workspace=workspace,
        scope=scope,
        founders_office_task_id=thread_task,
    )


@pytest.fixture()
def pmo_project(tmp_path) -> PmoTestProject:
    return make_pmo_test_project(tmp_path, slug="acme", name="Acme Portal")


@pytest.fixture()
def pmo_two_projects(tmp_path) -> tuple[PmoTestProject, PmoTestProject]:
    return (
        make_pmo_test_project(tmp_path, slug="acme", name="Acme Portal"),
        make_pmo_test_project(
            tmp_path,
            slug="beta",
            name="Beta Console",
            agents=(("beta-dev", "beta-dev", "Engineer"),),
        ),
    )


@pytest.fixture()
def fake_clock(monkeypatch) -> FakeClock:
    clock = FakeClock()
    monkeypatch.setattr(time, "time", clock.time)
    return clock
