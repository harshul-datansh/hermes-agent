"""Build a deterministic, isolated PM-OS demo without invoking an agent.

The demo uses the same project, Kanban, approval, conversation, cost, and
project-context APIs as a real project.  Its only special property is its
explicit Hermes home: all state is rooted below a verified demo directory and
the caller's active Hermes home is never selected.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
import shutil
import stat
import subprocess
import sys
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Iterator, Sequence

import yaml

from hermes_constants import reset_hermes_home_override, set_hermes_home_override
from hermes_cli import kanban_db
from plugins.pmo import approval, bootstrap, cost, project_context, project_scope


DEMO_MARKER = ".datansh-pmo-demo.json"
DEMO_SCHEMA = "datansh-pmo-demo.v1"
DEMO_SLUG = "acme"
DEMO_NAME = "Acme Portal"

# Single source of truth for the demo roster.  The project config and the
# Hermes profiles the dispatcher spawns must not drift: ``tasks.assignee``
# holds a *profile name*, and the dispatcher runs ``hermes -p <assignee>``.
DEMO_ORCHESTRATOR_PROFILE = "pm-acme"
DEMO_AGENTS: tuple[dict[str, str], ...] = (
    {"handle": "dev-1", "profile": "acme-dev-1", "role": "Engineer"},
    {"handle": "dev-2", "profile": "acme-dev-2", "role": "Engineer"},
    {"handle": "qa", "profile": "acme-qa", "role": "Reviewer"},
    {"handle": "ops", "profile": "acme-ops", "role": "Operations"},
)


@dataclass(frozen=True)
class DemoResult:
    root: Path
    hermes_home: Path
    workspace: Path
    board_slug: str
    project_id: str
    founders_office_thread_id: str
    client_thread_id: str
    approval_id: str
    status_counts: dict[str, int]


def default_demo_root() -> Path:
    return Path.home() / ".hermes-demo"


def _resolved_root(root: str | Path) -> Path:
    candidate = Path(root).expanduser().resolve()
    if candidate == Path.home().resolve() or candidate == Path(candidate.anchor):
        raise ValueError("demo root must be a dedicated child directory")
    return candidate


def _marker_path(root: Path) -> Path:
    return root / DEMO_MARKER


def _write_marker(root: Path) -> None:
    payload = {"schema": DEMO_SCHEMA, "root": str(root)}
    _marker_path(root).write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


@contextmanager
def isolated_hermes_state(hermes_home: Path) -> Iterator[None]:
    """Select all Hermes/Kanban roots temporarily and restore the caller."""

    keys = ("HERMES_HOME", "HERMES_KANBAN_HOME", "HERMES_KANBAN_DB", "HERMES_KANBAN_BOARD")
    previous = {key: os.environ.get(key) for key in keys}
    os.environ["HERMES_HOME"] = str(hermes_home)
    os.environ["HERMES_KANBAN_HOME"] = str(hermes_home)
    os.environ.pop("HERMES_KANBAN_DB", None)
    os.environ.pop("HERMES_KANBAN_BOARD", None)
    token = set_hermes_home_override(hermes_home)
    try:
        yield
    finally:
        reset_hermes_home_override(token)
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _verify_demo_root(root: Path) -> None:
    marker = _marker_path(root)
    if not marker.is_file() or marker.is_symlink():
        raise ValueError(f"refusing reset: verified demo marker is absent at {marker}")
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"refusing reset: invalid demo marker at {marker}") from exc
    if payload != {"schema": DEMO_SCHEMA, "root": str(root)}:
        raise ValueError(f"refusing reset: demo marker does not identify {root}")


def reset_demo(root: str | Path | None = None) -> bool:
    """Remove exactly one marker-verified demo root, never an arbitrary path."""

    selected = _resolved_root(root or default_demo_root())
    if not selected.exists():
        return False
    _verify_demo_root(selected)

    def make_writable(function, path, _error_info):
        os.chmod(path, stat.S_IWRITE)
        function(path)

    try:
        shutil.rmtree(selected, onerror=make_writable)
    except PermissionError as exc:
        # Windows keeps a mandatory lock on open files, so a running dashboard
        # or gateway holding the demo's HERMES_HOME makes the tree undeletable
        # (WinError 32).  The bare errno is unactionable — name the cause and
        # the fix instead.  Same class as `hermes_logging`'s cross-process log
        # lock and the worktree-removal guard in plan 011 §7.
        locked = getattr(exc, "filename", None) or selected
        raise RuntimeError(
            f"cannot reset the demo: {locked} is locked by another process.\n"
            "A Hermes process is still using this demo home. Stop it first:\n"
            "  - stop the dashboard/gateway started against this demo root, then\n"
            f"  - re-run: hermes pmo demo --reset --root {selected}\n"
            "If nothing is running, a crashed worker may still hold the handle; "
            "close it or reboot before retrying."
        ) from exc
    return True


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )


def _create_repo(workspace: Path) -> None:
    workspace.mkdir(parents=True)
    _git(workspace, "init", "--quiet")
    (workspace / "README.md").write_text(
        "# Acme Portal\n\nDeterministic PM-OS demo application.\n", encoding="utf-8"
    )
    (workspace / "app.py").write_text(
        "def health():\n    return {'status': 'ok'}\n", encoding="utf-8"
    )
    (workspace / "test_app.py").write_text(
        "from app import health\n\ndef test_health():\n    assert health() == {'status': 'ok'}\n",
        encoding="utf-8",
    )
    _git(workspace, "add", "README.md", "app.py", "test_app.py")
    _git(
        workspace,
        "-c",
        "user.name=Datansh Demo",
        "-c",
        "user.email=demo@datansh.local",
        "commit",
        "--quiet",
        "-m",
        "Initial Acme Portal demo",
    )


DEMO_ROLE_CREDENTIALS = (
    ("ceo@datansh.local", "project_admin", "Datansh-CEO-2026!", 100),
    ("cfo@datansh.local", "contributor", "Datansh-CFO-2026!", 70),
    ("manager@datansh.local", "pm", "Datansh-Manager-2026!", 0),
    ("backend-developer@datansh.local", "contributor", "Datansh-Backend-2026!", None),
    ("frontend-developer@datansh.local", "contributor", "Datansh-Frontend-2026!", None),
    ("qa@datansh.local", "contributor", "Datansh-QA-2026!", None),
    ("client@datansh.local", "viewer", "Datansh-Client-2026!", None),
    ("hr@datansh.local", "viewer", "Datansh-HR-2026!", None),
    ("data-analytics@datansh.local", "contributor", "Datansh-Data-2026!", None),
    ("maintainer@datansh.local", "contributor", "Datansh-Maintainer-2026!", None),
    ("admin@datansh.local", "project_admin", "Datansh-Admin-2026!", 100),
    ("cto@datansh.local", "contributor", "Datansh-CTO-2026!", 80),
    ("lead@datansh.local", "project_admin", "Datansh-Lead-2026!", 0),
)
DEMO_LOGIN_USERNAME = DEMO_ROLE_CREDENTIALS[0][0]
DEMO_LOGIN_PASSWORD = DEMO_ROLE_CREDENTIALS[0][2]


def _configure_dashboard_auth(hermes_home: Path) -> None:
    """Configure a dashboard auth provider inside the demo's isolated home.

    Without this the demo prints a start command that cannot work: the
    dashboard refuses any non-loopback bind when no auth provider is
    registered, so ``hermes dashboard --host 0.0.0.0`` exits with
    "no auth providers are registered" and the printed credentials are
    unusable.

    Only the password hash is written -- never the plaintext -- matching the
    provider's own guidance that plaintext should not sit at rest.

    Password hashes are enrolled beside the project access policy.  The
    Datansh provider reloads those hashes at login, while each project's
    ``access.yaml`` remains the source of truth for membership and roles.
    """
    import secrets as _secrets

    config_path = hermes_home / "config.yaml"
    if config_path.exists():
        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    else:
        data = {}
    dashboard_cfg = data.setdefault("dashboard", {})
    if not isinstance(dashboard_cfg, dict):
        dashboard_cfg = {}
        data["dashboard"] = dashboard_cfg
    dashboard_cfg.pop("basic_auth", None)
    dashboard_cfg["datansh_auth"] = {
        "enabled": True,
        "secret": _secrets.token_hex(32),
        "session_ttl_seconds": 43200,
    }
    config_path.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )


def _materialize_worktree(conn, scope: project_scope.ProjectScope, task_id: str) -> None:
    """Give a seeded ``running`` task the worktree a dispatched task would have.

    ``claim_task`` only flips status and takes the claim lock.  The linked git
    worktree is materialized one layer up, in the dispatcher
    (``_dispatch_once_locked`` → ``_resolve_worktree_workspace`` →
    ``set_workspace_path``).  The demo claims directly, so without this it
    produces dispatcher-state *without* the dispatcher's side effect, and
    ``hermes pmo doctor`` correctly reports ``missing active worktrees``.

    Mirrors the dispatcher's sequence exactly so the demo board is
    indistinguishable from a real one.

    Upstream coupling: ``_resolve_worktree_workspace`` is private.  It is the
    same function the dispatcher uses, and re-implementing worktree resolution
    would be a second, drifting copy (ADR-012).  Recorded in
    ``plugins/pmo/UPSTREAM.md``; re-check on every upstream merge.
    """
    task = kanban_db.get_task(conn, task_id)
    if task is None or task.workspace_kind != "worktree":
        return
    workspace, branch_name = kanban_db._resolve_worktree_workspace(
        task, board=scope.board_slug
    )
    kanban_db.set_workspace_path(conn, task_id, str(workspace))
    kanban_db.set_branch_name(
        conn, task_id, branch_name or (task.branch_name or "").strip() or f"wt/{task_id}"
    )


def _create_agent_profiles() -> list[str]:
    """Create the Hermes profiles the demo roster references.

    Without these the board looks fully populated but nothing can actually be
    dispatched: the dispatcher spawns ``hermes -p <assignee>`` and the profile
    has to exist on disk.  ``hermes pmo doctor`` reports the gap as
    ``missing profiles``.

    Must run inside :func:`isolated_hermes_state`.  ``_get_profiles_root()``
    resolves to ``HERMES_HOME/profiles`` whenever HERMES_HOME sits outside
    ``~/.hermes``, so the demo's profiles stay inside the demo root.

    ``no_alias=True`` is required: wrapper scripts are written to
    ``~/.local/bin``, which is *not* isolated and would leak demo profiles
    into the real environment.
    """
    from hermes_cli import profiles as profiles_mod

    created: list[str] = []
    names = [DEMO_ORCHESTRATOR_PROFILE] + [agent["profile"] for agent in DEMO_AGENTS]
    for name in names:
        if profiles_mod.profile_exists(name):
            continue
        profiles_mod.create_profile(
            name,
            no_alias=True,
            no_skills=True,
            description=f"Datansh PM-OS demo profile ({name})",
        )
        created.append(name)
    return created


def _configure_project(config_path: Path) -> None:
    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    data["agents"] = [dict(agent) for agent in DEMO_AGENTS]
    data["board"]["labels"] = ["spend", "release", "bug"]
    data["approvals"]["rules"] = [
        {"when": {"label": "spend"}, "required_rank": 70}
    ]
    config_path.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )


def _configure_access(access_path: Path) -> None:
    principals = tuple(
        (email, project_role, rank)
        for email, project_role, _password, rank in DEMO_ROLE_CREDENTIALS
    )
    payload = {
        "version": 1,
        "members": [
            {"principal": f"human:{email}", "role": role}
            for email, role, _rank in principals
        ],
        "org_ranks": [
            {"principal": f"human:{email}", "rank": rank}
            for email, _role, rank in principals
            if rank is not None
        ],
    }
    access_path.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )


def _configure_project_credentials(access_path: Path) -> None:
    """Seed demo password hashes beside the project's access policy."""

    from plugins.dashboard_auth.basic import hash_password

    payload = {
        "version": 1,
        "credentials": [
            {
                "principal": f"human:{email}",
                "password_hash": hash_password(password),
            }
            for email, _role, password, _rank in DEMO_ROLE_CREDENTIALS
        ],
    }
    access_path.with_name("credentials.yaml").write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )


def _task(
    conn,
    scope: project_scope.ProjectScope,
    title: str,
    *,
    assignee: str = "acme-dev-1",
    triage: bool = False,
    parents: tuple[str, ...] = (),
    initial_status: str = "running",
) -> str:
    return kanban_db.create_task(
        conn,
        title=title,
        body="[pmo:demo:v1]\nSeeded fixture content; no agent was invoked.",
        assignee=assignee,
        created_by="pmo-demo",
        triage=triage,
        parents=parents,
        initial_status=initial_status,
        max_runtime_seconds=3600,
        max_retries=3,
        board=scope.board_slug,
        project_id=scope.project_id,
        idempotency_key=f"pmo-demo:{title}",
    )


def _usage(index: int) -> cost.UsageRecord:
    return cost.UsageRecord(
        session_id=f"demo-session-{index:02d}",
        model="demo-fixture",
        provider="none",
        input_tokens=900 + index * 31,
        cached_input_tokens=100 + index * 7,
        output_tokens=240 + index * 13,
        cost_usd=Decimal("0.08") + Decimal(index) / Decimal("100"),
        turns=2 + index % 4,
        tool_calls=1 + index % 3,
        wall_seconds=35 + index * 4,
    )


def _seed_board(scope: project_scope.ProjectScope) -> tuple[str, str]:
    with kanban_db.connect_closing(board=scope.board_slug) as conn:
        epic = _task(conn, scope, "Draft: account migration epic", triage=True)
        _task(conn, scope, "Draft: billing reconciliation", triage=True)
        _task(conn, scope, "Draft: launch communications", triage=True)

        for title in (
            "Todo: implement organization switcher",
            "Todo: validate audit export",
            "Todo: add release checklist",
            "Todo: update support runbook",
        ):
            _task(conn, scope, title, assignee="acme-dev-2", parents=(epic,))

        running_one = _task(conn, scope, "In progress: tenant-aware navigation")
        running_two = _task(
            conn, scope, "In progress: SSO callback hardening", assignee="acme-dev-2"
        )
        if kanban_db.claim_task(conn, running_one, claimer="pmo-demo:1") is None:
            raise RuntimeError("could not seed first running task")
        _materialize_worktree(conn, scope, running_one)
        if kanban_db.claim_task(conn, running_two, claimer="pmo-demo:2") is None:
            raise RuntimeError("could not seed second running task")
        _materialize_worktree(conn, scope, running_two)

        # The copied PMO dashboard already owns the audited drag/drop status
        # seam for states that have no dedicated Kanban verb.  Reuse that
        # existing plugin seam to show a genuine native review card.
        from plugins.pmo.dashboard.plugin_api import _set_status_direct

        review_id = _task(conn, scope, "Review: verify launch rollback evidence", assignee="acme-qa")
        if not _set_status_direct(conn, review_id, "review"):
            raise RuntimeError("could not seed the native review status")
        kanban_db.add_comment(
            conn,
            review_id,
            "acme-qa",
            "[pmo:rework:v1]\nOne rework pass completed; final evidence is ready for review.",
        )

        blocked_id = _task(conn, scope, "Blocked: production DNS ownership")
        kanban_db.block_task(
            conn,
            blocked_id,
            reason="Waiting for the registrar owner to confirm the production zone.",
            kind="needs_input",
        )
        kanban_db.add_comment(
            conn,
            blocked_id,
            "acme-dev-1",
            "[pmo:demo-blocker:v1]\n@pm I retried the safe checks; human ownership is required.",
        )

        for index in range(1, 12):
            done_id = _task(
                conn,
                scope,
                f"Done day -{15-index:02d}: release increment {index:02d}",
                assignee="acme-qa" if index % 3 == 0 else "acme-dev-1",
            )
            cost.complete_task_with_usage(
                conn,
                scope=scope,
                task_id=done_id,
                usage=_usage(index),
                summary=f"Verified release increment {index:02d} with fixture evidence.",
                result="All deterministic acceptance checks passed.",
                metadata={"demo_historical_day": 15 - index},
            )
            if index == 7:
                kanban_db.add_comment(
                    conn,
                    done_id,
                    "acme-qa",
                    "[pmo:rework:v1]\nRequested one focused rework pass before acceptance.",
                )

        cancelled_id = _task(conn, scope, "Cancelled: replace analytics vendor", triage=True)
        kanban_db.add_comment(
            conn,
            cancelled_id,
            "pm-acme",
            "[pmo:lifecycle:v1]\nAction: cancel\nReason: retained the current vendor.",
        )
        kanban_db.archive_task(conn, cancelled_id)

        approval_target = _task(
            conn, scope, "Todo: approve annual observability commitment", assignee="acme-ops"
        )

    request = approval.raise_approval(
        board_slug=scope.board_slug,
        target_task_id=approval_target,
        title="Annual observability commitment",
        detail="CFO reviewed the spend; CEO approval is required for the annual term.",
        raised_by="human:cfo@datansh.local",
        required_rank=70,
        approver_profile="human:cfo@datansh.local",
    )
    escalated = approval.escalate_approval(
        board_slug=scope.board_slug,
        approval_id=request.approval_id,
        to_rank=100,
        to_approver_profile="human:ceo@datansh.local",
        reason="Annual commitment exceeds the CFO delegation threshold.",
        actor="human:cfo@datansh.local",
        actor_rank=70,
    )
    return blocked_id, escalated.approval_id


def _seed_conversations(scope: project_scope.ProjectScope):
    from plugins.platforms.pmo.adapter import ensure_project_conversations

    conversations = ensure_project_conversations(scope)
    messages = (
        ("human:ceo@datansh.local", "@pm Prepare the portal launch for the design partners."),
        ("pm-acme", "I split the work into release, security, and operations outcomes."),
        ("human:cto@datansh.local", "Keep the SSO callback within the existing trust boundary."),
        ("pm-acme", "Captured as a constraint on the security ticket."),
        ("human:cfo@datansh.local", "What is the expected run-rate after launch?"),
        ("pm-acme", "The current fixture forecast is visible in Spend."),
        ("human:lead@datansh.local", "DNS is blocked on registrar ownership."),
        ("pm-acme", "Escalation opened; no worker retry is scheduled."),
        ("human:cfo@datansh.local", "I approve the monthly amount, not an annual commitment."),
        ("pm-acme", "The annual approval has been escalated to CEO."),
        ("human:ceo@datansh.local", "Leave it pending so the approval flow is visible."),
        ("pm-acme", "Done. The decision remains explicit and auditable."),
    )
    with kanban_db.connect_closing(board=scope.board_slug) as conn:
        for author, body in messages:
            kanban_db.add_comment(
                conn, conversations.founders_office_thread_id, author, body
            )
        kanban_db.add_comment(
            conn,
            conversations.client_thread_id,
            "pmo-demo",
            "Client collaboration is isolated from trusted founder instructions.",
        )
    return conversations


def _seed_context(workspace: Path, approval_id: str) -> None:
    decisions = (
        ("Keep PM instructions in Founder's Office", "Avoid ambiguous task-side instructions."),
        ("Use native Kanban dependencies", "Preserve Hermes dispatch and worktree behavior."),
        ("Escalate annual spend to CEO", "The CFO delegation covers monthly spend only."),
    )
    for title, rationale in decisions:
        project_context.record_decision(
            workspace,
            title=title,
            context="Acme Portal launch demo",
            decision=title,
            rationale=rationale,
            decided_by="human:ceo@datansh.local",
            approval_id=approval_id if "spend" in title.casefold() else None,
        )
    facts = (
        ("convention", "All customer-facing dates are rendered in the account timezone."),
        ("gotcha", "The staging SSO callback uses a separate registered redirect URI."),
        ("contact", "The registrar owner is the operations lead."),
        ("env", "The demo test suite runs with the standard Python interpreter."),
        ("risk", "An annual vendor term requires CEO approval."),
        ("convention", "Founder's Office is the only trusted instruction channel."),
        ("gotcha", "Archived Kanban cards represent cancelled work in the demo."),
        ("risk", "DNS changes need a documented rollback value."),
    )
    for kind, body in facts:
        project_context.add_knowledge(
            workspace,
            kind=kind,
            body=body,
            created_by="pm-acme",
            source="pmo-demo",
            confidence="confirmed",
        )


def create_demo(root: str | Path | None = None, *, replace: bool = False) -> DemoResult:
    selected = _resolved_root(root or default_demo_root())
    if selected.exists():
        if not replace:
            raise ValueError(
                f"demo root already exists: {selected}; use `hermes pmo demo --reset`"
            )
        reset_demo(selected)
    selected.mkdir(parents=True)
    _write_marker(selected)
    hermes_home = selected / "hermes-home"
    workspace = selected / "acme-portal"
    hermes_home.mkdir()
    _create_repo(workspace)

    with isolated_hermes_state(hermes_home):
        result = bootstrap.bootstrap_project(
            slug=DEMO_SLUG, name=DEMO_NAME, workspace=workspace
        )
        _configure_project(result.config_path)
        _configure_access(result.access_path)
        _configure_project_credentials(result.access_path)
        _create_agent_profiles()
        _configure_dashboard_auth(hermes_home)
        scope = project_scope.resolve(DEMO_SLUG, board_slug=result.board_slug)
        conversations = _seed_conversations(scope)
        _blocked_id, approval_id = _seed_board(scope)
        _seed_context(workspace, approval_id)
        with kanban_db.connect_closing(board=scope.board_slug) as conn:
            counts: dict[str, int] = {}
            for task in kanban_db.list_tasks(conn, include_archived=True):
                counts[task.status] = counts.get(task.status, 0) + 1

    return DemoResult(
        root=selected,
        hermes_home=hermes_home,
        workspace=workspace,
        board_slug=scope.board_slug,
        project_id=scope.project_id,
        founders_office_thread_id=conversations.founders_office_thread_id,
        client_thread_id=conversations.client_thread_id,
        approval_id=approval_id,
        status_counts=counts,
    )


def _print_ready(result: DemoResult) -> None:
    print("Demo ready.   http://127.0.0.1:8787/pmo")
    print(f"  Isolated HERMES_HOME: {result.hermes_home}")
    print(
        "  Start the authenticated dashboard: "
        "hermes dashboard --host 0.0.0.0 --port 8787 --no-open"
    )
    print("  Demo project accounts (the same identity may belong to many projects):")
    for email, project_role, password, rank in DEMO_ROLE_CREDENTIALS:
        rank_text = f", rank {rank}" if rank is not None else ""
        print(f"    {email:<38} {password:<28} {project_role}{rank_text}")
    print()
    print("  Password hashes live in the project's .datansh/credentials.yaml;")
    print("  membership and roles remain in .datansh/access.yaml.")
    print()
    print("Try: Founder's Office -> pending annual commitment -> CEO decision")
    print(f"Reset: hermes pmo demo --reset --root {result.root}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=None, help="Dedicated demo root")
    parser.add_argument("--reset", action="store_true", help="Reset the verified demo root")
    args = parser.parse_args(argv)
    try:
        if args.reset:
            removed = reset_demo(args.root)
            print("Demo reset." if removed else "Demo root is already absent.")
            return 0
        result = create_demo(args.root)
    except (OSError, RuntimeError, subprocess.SubprocessError, ValueError) as exc:
        print(f"pmo demo: {exc}", file=sys.stderr)
        return 2
    _print_ready(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
