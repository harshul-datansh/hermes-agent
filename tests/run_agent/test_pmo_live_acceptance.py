"""Opt-in metered L4 acceptance for the Datansh PM-OS profile.

The normal test suite skips this file.  With ``HERMES_LIVE_TESTS=1`` it runs
one bounded real-model turn through :class:`run_agent.AIAgent`, applies the
model's decomposition through the production PMO workflow, and records usage
for the CI cost trend.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess

import pytest
import yaml


LIVE = os.environ.get("HERMES_LIVE_TESTS") == "1"
API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
MODEL = os.environ.get("PMO_LIVE_MODEL", "")

pytestmark = [
    pytest.mark.skipif(not LIVE, reason="live-only: set HERMES_LIVE_TESTS=1"),
    pytest.mark.skipif(not API_KEY, reason="OPENROUTER_API_KEY is not configured"),
    pytest.mark.skipif(not MODEL, reason="PMO_LIVE_MODEL is not configured"),
    pytest.mark.integration,
]

REPO_ROOT = Path(__file__).resolve().parents[2]
DIST_ROOT = REPO_ROOT / "distributions" / "datansh-pm-os"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )


def _json_object(text: str) -> dict:
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if match is None:
        raise AssertionError(f"live PM did not return a JSON object: {text[:500]}")
    value = json.loads(match.group(0))
    assert isinstance(value, dict)
    return value


def test_live_pm_decomposes_into_native_tickets(tmp_path, monkeypatch):
    from hermes_cli import kanban_db
    from hermes_cli.profile_distribution import install_distribution
    from plugins.pmo import bootstrap, workflow
    from run_agent import AIAgent

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    default_home = tmp_path / ".hermes"
    default_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(default_home))
    profile = install_distribution(str(DIST_ROOT)).target_dir
    monkeypatch.setenv("HERMES_HOME", str(profile))
    monkeypatch.setenv("HERMES_KANBAN_HOME", str(profile))

    workspace = tmp_path / "scratch-project"
    workspace.mkdir()
    _git(workspace, "init", "--quiet")
    (workspace / "README.md").write_text("# Live PMO scratch project\n", encoding="utf-8")
    _git(workspace, "add", "README.md")
    _git(
        workspace,
        "-c",
        "user.name=Datansh PMO Live",
        "-c",
        "user.email=pmo-live@datansh.local",
        "commit",
        "--quiet",
        "-m",
        "Initial scratch project",
    )

    boot = bootstrap.bootstrap_project(
        slug="live-pmo",
        name="Live PMO acceptance",
        workspace=workspace,
    )
    config = yaml.safe_load(boot.config_path.read_text(encoding="utf-8"))
    config["agents"] = [
        {"handle": "dev", "profile": "dev-live", "role": "worker"},
        {"handle": "review", "profile": "review-live", "role": "reviewer"},
    ]
    config["budget"]["monthly_cap"] = "0.25"
    config["budget"]["per_run_max_turns"] = 1
    boot.config_path.write_text(
        yaml.safe_dump(config, sort_keys=False), encoding="utf-8"
    )

    prompt = (
        "Return JSON only. Decompose this outcome into 2 to 5 independently "
        "verifiable engineering tickets: 'Add an authenticated health endpoint "
        "with tests and concise operator documentation.' Use exactly this shape: "
        '{"tickets":[{"title":"...","outcome":"...","assignee":"dev-live",'
        '"acceptance_criteria":["..."],"evidence":["..."]}]}. '
        "Every ticket needs at least one concrete acceptance criterion and evidence item."
    )
    agent = AIAgent(
        base_url=os.environ.get("PMO_LIVE_BASE_URL", "https://openrouter.ai/api/v1"),
        api_key=API_KEY,
        provider="openrouter",
        api_mode="chat_completions",
        model=MODEL,
        max_iterations=1,
        max_tokens=1_200,
        enabled_toolsets=[],
        quiet_mode=True,
        load_soul_identity=True,
        session_id="pmo-live-acceptance",
    )
    response = agent.run_conversation(prompt)
    payload = _json_object(str(response))
    tickets = payload.get("tickets")
    assert isinstance(tickets, list)
    assert 2 <= len(tickets) <= 5

    created: list[str] = []
    for item in tickets:
        assert isinstance(item, dict)
        criteria = item.get("acceptance_criteria")
        evidence = item.get("evidence")
        assert isinstance(criteria, list) and criteria
        assert isinstance(evidence, list) and evidence
        result = workflow.create_draft(
            project_ref=boot.project_slug,
            board_slug=boot.board_slug,
            actor_profile="pm-live-pmo",
            title=str(item.get("title") or "").strip(),
            outcome=str(item.get("outcome") or "").strip(),
            assignee="dev-live",
            acceptance_criteria=[str(value) for value in criteria],
            evidence=[str(value) for value in evidence],
        )
        created.append(result.task_id)

    with kanban_db.connect_closing(board=boot.board_slug) as conn:
        rows = [kanban_db.get_task(conn, task_id) for task_id in created]
    assert all(row is not None and row.status == "triage" for row in rows)
    assert all("Acceptance criteria:" in row.body for row in rows if row is not None)

    max_usd = float(os.environ.get("PMO_LIVE_MAX_USD", "0.25"))
    estimated_cost = float(getattr(agent, "session_estimated_cost_usd", 0.0) or 0.0)
    assert estimated_cost <= max_usd
    record = {
        "schema": "datansh-pmo-live-cost.v1",
        "provider": "openrouter",
        "model": MODEL,
        "input_tokens": int(getattr(agent, "session_input_tokens", 0) or 0),
        "output_tokens": int(getattr(agent, "session_output_tokens", 0) or 0),
        "cache_read_tokens": int(getattr(agent, "session_cache_read_tokens", 0) or 0),
        "api_calls": int(getattr(agent, "session_api_calls", 0) or 0),
        "estimated_cost_usd": estimated_cost,
        "ticket_count": len(created),
    }
    result_path = Path(os.environ.get("PMO_LIVE_RESULT_PATH", tmp_path / "pmo-live-cost.json"))
    result_path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")

