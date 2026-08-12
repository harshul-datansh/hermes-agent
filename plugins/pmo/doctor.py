"""Read-only PM-OS health projection and bounded native restart drain."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Sequence

from hermes_cli import kanban_db, kanban_diagnostics
from hermes_cli.profiles import profile_exists

from . import cost, git_flow, mentions, performance, project_scope


@dataclass(frozen=True)
class DoctorCheck:
    name: str
    status: str
    detail: str
    action: str | None = None


@dataclass(frozen=True)
class DoctorReport:
    project_id: str
    board_slug: str
    healthy: bool
    checks: tuple[DoctorCheck, ...]
    stale_claim_task_ids: tuple[str, ...]
    pending_mentions: int
    worktree_candidates: int
    attributed_cost_usd: str
    unattributed_run_ids: tuple[int, ...]
    performance: dict[str, int | float]


@dataclass(frozen=True)
class DrainReport:
    limit: int
    stale_claims_seen: tuple[str, ...]
    reclaimed: tuple[str, ...]
    pending_mentions_seen: tuple[int, ...]
    delivered_mentions: tuple[int, ...]
    truncated: bool


def _contract_check(script: str, *, timeout_seconds: float = 15.0) -> DoctorCheck:
    root = Path(__file__).resolve().parents[2]
    path = root / "scripts" / script
    if not path.is_file():
        return DoctorCheck(script, "error", f"missing contract checker: {path}")
    try:
        result = subprocess.run(
            [sys.executable, str(path)],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return DoctorCheck(script, "warning", "contract check timed out", f"run {path} manually")
    output = (result.stdout or result.stderr or "").strip().splitlines()
    detail = output[-1] if output else f"exit {result.returncode}"
    return DoctorCheck(
        script,
        "ok" if result.returncode == 0 else "error",
        detail,
        None if result.returncode == 0 else f"{sys.executable} {path}",
    )


def _auto_decompose_check() -> DoctorCheck:
    """PM-OS is incompatible with the gateway's auto-decompose sweep.

    ``gateway/kanban_watchers.py`` sweeps ``list_triage_ids()`` on every board
    and hands each task to the auxiliary LLM, which replaces its title and
    body. PM-OS parks approvals and human tasks in ``triage`` precisely
    because the dispatcher ignores that column, so every PM-OS record is a
    guaranteed target. A rewritten approval loses the required rank, target
    task and raiser that live in its body: it reports "is not a PM-OS
    approval" and can never be decided.

    ``kanban.auto_decompose`` defaults to **True**, so a fresh deployment is
    broken until this is turned off. Requirement #4 makes the PM agent the
    decomposer anyway — the background sweeper is not wanted here.
    """
    try:
        from hermes_cli.config import cfg_get, load_config

        cfg = load_config()
        enabled = cfg_get(cfg, "kanban", "auto_decompose", default=True)
    except Exception as exc:  # noqa: BLE001 - doctor reports, never crashes
        return DoctorCheck(
            "auto_decompose", "warning", f"could not read config: {exc}"
        )
    if bool(enabled):
        return DoctorCheck(
            "auto_decompose",
            "error",
            "kanban.auto_decompose is enabled; the gateway sweep will rewrite "
            "PM-OS approvals and human tasks and make them undecidable",
            "set kanban.auto_decompose: false in this HERMES_HOME's config.yaml",
        )
    return DoctorCheck(
        "auto_decompose", "ok", "gateway triage sweep is disabled"
    )



def _thread_check(scope: project_scope.ProjectScope, tasks: list[Any]) -> DoctorCheck:
    try:
        from plugins.platforms.pmo.adapter import resolve_conversation
    except Exception as exc:
        return DoctorCheck("threads", "error", f"PMO platform unavailable: {exc}")
    found: set[str] = set()
    for task in tasks:
        if "datansh-pmo-conversation.v1" not in str(task.body or ""):
            continue
        try:
            found.add(resolve_conversation(board_slug=scope.board_slug, thread_id=task.id).kind)
        except ValueError:
            continue
    missing = {"founders_office", "client"} - found
    return (
        DoctorCheck("threads", "ok", "founder and client threads are valid")
        if not missing
        else DoctorCheck("threads", "warning", f"missing/invalid threads: {', '.join(sorted(missing))}", "hermes pmo setup")
    )


def build_report(
    scope: project_scope.ProjectScope,
    *,
    now: int | None = None,
    insights_total_usd: Decimal | str | None = None,
    run_contract_checks: bool = True,
) -> DoctorReport:
    """Inspect native state without reclaiming, deleting, or repairing it."""

    timestamp = int(now or time.time())
    checks: list[DoctorCheck] = [
        DoctorCheck("binding", "ok", f"board '{scope.board_slug}' is bound to '{scope.slug}'"),
        DoctorCheck("scope", "ok", f"config and workspace valid at {scope.primary_path}"),
    ]
    profiles = [scope.config.orchestrator.profile, *(agent.profile for agent in scope.config.agents)]
    missing_profiles = sorted(profile for profile in profiles if not profile_exists(profile))
    checks.append(
        DoctorCheck("profiles", "ok", "all configured profiles exist")
        if not missing_profiles
        else DoctorCheck("profiles", "warning", "missing profiles: " + ", ".join(missing_profiles), "hermes profile list")
    )

    stale: list[str] = []
    diagnostics_count = 0
    with kanban_db.connect_closing(board=scope.board_slug) as conn:
        tasks = [
            task
            for task in kanban_db.list_tasks(conn, include_archived=True, order_by="created")
            if task.project_id == scope.project_id
        ]
        checks.append(_thread_check(scope, tasks))
        checks.append(_auto_decompose_check())
        missing_worktrees: list[str] = []
        retry_warnings: list[str] = []
        for task in tasks:
            if task.status == "running" and task.claim_expires is not None and task.claim_expires < timestamp:
                stale.append(task.id)
            if task.workspace_kind == "worktree" and task.status == "running" and (
                not task.workspace_path or not Path(task.workspace_path).is_dir()
            ):
                missing_worktrees.append(task.id)
            if task.consecutive_failures:
                retry_warnings.append(f"{task.id}:{task.consecutive_failures}/{task.max_retries or 'default'}")
            diagnostics_count += len(
                kanban_diagnostics.compute_task_diagnostics(
                    task,
                    kanban_db.list_events(conn, task.id),
                    kanban_db.list_runs(conn, task.id),
                    now=timestamp,
                )
            )
    checks.append(
        DoctorCheck("worktrees", "ok", "active native worktrees are present")
        if not missing_worktrees
        else DoctorCheck("worktrees", "error", "missing active worktrees: " + ", ".join(missing_worktrees), "hermes pmo git gc")
    )
    checks.append(
        DoctorCheck("stale_claims", "ok", "no expired running claims")
        if not stale
        else DoctorCheck("stale_claims", "warning", f"{len(stale)} expired claim(s)", "hermes pmo doctor --drain")
    )
    checks.append(
        DoctorCheck("retries", "ok", "no active failure streaks")
        if not retry_warnings
        else DoctorCheck("retries", "warning", ", ".join(retry_warnings), "inspect native task diagnostics")
    )
    checks.append(DoctorCheck("kanban_diagnostics", "ok" if not diagnostics_count else "warning", f"{diagnostics_count} active native diagnostic(s)"))

    pending = mentions.pending_routes(scope, limit=scope.config.board.restart_drain_limit + 1)
    checks.append(
        DoctorCheck("restart_backlog", "ok", "no pending mention deliveries")
        if not pending
        else DoctorCheck("restart_backlog", "warning", f"{len(pending)} pending mention delivery item(s)", "hermes pmo doctor --drain")
    )
    gc = git_flow.gc_worktrees(scope=scope, dry_run=True, now=timestamp)
    checks.append(DoctorCheck("worktree_gc", "ok" if not gc else "warning", f"{len(gc)} stale worktree candidate(s)", "hermes pmo git gc" if gc else None))

    spend = cost.project_spend(scope, now=timestamp)
    budget = scope.config.budget
    budget_status = "ok"
    budget_detail = f"month attributed {_decimal_text(spend.cost_usd)} {budget.currency}"
    if budget.monthly_cap is not None and spend.cost_usd >= budget.monthly_cap:
        budget_status = "error"
        budget_detail += f"; cap {_decimal_text(budget.monthly_cap)} reached"
    checks.append(DoctorCheck("budget", budget_status, budget_detail))
    if insights_total_usd is not None:
        reconciliation = cost.doctor_reconciliation(
            scope, insights_total_usd=insights_total_usd, now=timestamp
        )
        checks.append(
            DoctorCheck(
                "reconciliation",
                "ok" if reconciliation.healthy else "warning",
                f"drift {reconciliation.drift_percent.quantize(Decimal('0.1'))}%",
                "compare Hermes Insights session attribution" if not reconciliation.healthy else None,
            )
        )
    else:
        checks.append(DoctorCheck("reconciliation", "warning" if spend.unattributed_run_ids else "ok", f"{len(spend.unattributed_run_ids)} unattributed run(s)"))

    if run_contract_checks:
        checks.extend(
            [
                _contract_check("check_pmo_capability_contract.py"),
                _contract_check("check_pmo_kanban_sync.py"),
            ]
        )
    perf = performance.measure_board(scope.board_slug, repeats=3)
    checks.append(
        DoctorCheck(
            "performance",
            "ok",
            (
                f"board p50 {perf.board_query_p50_ms:.1f}ms / "
                f"p95 {perf.board_query_p95_ms:.1f}ms "
                f"({perf.ticket_count} tickets); thread p50 "
                f"{perf.thread_load_p50_ms:.1f}ms "
                f"({perf.largest_thread_messages} messages)"
            ),
            "python scripts/pmo_loadgen.py" if perf.ticket_count >= 2_000 else None,
        )
    )
    healthy = not any(item.status == "error" for item in checks)
    return DoctorReport(
        scope.project_id,
        scope.board_slug,
        healthy,
        tuple(checks),
        tuple(stale),
        len(pending),
        len(gc),
        _decimal_text(spend.cost_usd),
        spend.unattributed_run_ids,
        {
            "board_query_p50_ms": perf.board_query_p50_ms,
            "board_query_p95_ms": perf.board_query_p95_ms,
            "thread_load_p50_ms": perf.thread_load_p50_ms,
            "ticket_count": perf.ticket_count,
            "largest_thread_messages": perf.largest_thread_messages,
        },
    )


def _decimal_text(value: Decimal) -> str:
    return format(value, "f")


def restart_drain(
    scope: project_scope.ProjectScope,
    *,
    wake: Callable | None = None,
    limit: int | None = None,
    apply: bool = False,
    now: int | None = None,
) -> DrainReport:
    """Bound recovery work while delegating mutations to native APIs."""

    bounded = min(max(int(limit or scope.config.board.restart_drain_limit), 1), 500)
    timestamp = int(now or time.time())
    stale: list[str] = []
    reclaimed: list[str] = []
    with kanban_db.connect_closing(board=scope.board_slug) as conn:
        for task in kanban_db.list_tasks(conn, status="running", include_archived=False, order_by="created"):
            if task.project_id == scope.project_id and task.claim_expires is not None and task.claim_expires < timestamp:
                stale.append(task.id)
        selected = stale[:bounded]
        if apply:
            for task_id in selected:
                if kanban_db.reclaim_task(conn, task_id, reason="bounded PMO restart drain"):
                    reclaimed.append(task_id)
    remaining = max(0, bounded - len(stale[:bounded]))
    pending = mentions.pending_routes(scope, limit=max(1, remaining + 1)) if remaining else []
    delivered: list[int] = []
    if apply and wake is not None and remaining:
        delivered = mentions.drain_pending(scope, wake, limit=remaining)
    return DrainReport(
        bounded,
        tuple(stale[:bounded]),
        tuple(reclaimed),
        tuple(item.notification_id for item in pending[:remaining]),
        tuple(delivered),
        len(stale) > bounded or len(pending) > remaining,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--board", required=True)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--no-contract-checks", action="store_true")
    parser.add_argument("--drain", action="store_true", help="Apply a bounded stale-claim drain; mention delivery remains gateway-owned")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args(argv)
    try:
        scope = project_scope.resolve(args.project, board_slug=args.board)
        report = build_report(scope, run_contract_checks=not args.no_contract_checks)
        drain = restart_drain(scope, limit=args.limit, apply=True) if args.drain else None
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"pmo doctor: {exc}", file=sys.stderr)
        return 2
    payload = asdict(report)
    if drain is not None:
        payload["drain"] = asdict(drain)
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        for item in report.checks:
            print(f"[{item.status.upper()}] {item.name}: {item.detail}")
    return 0 if report.healthy else 1


if __name__ == "__main__":
    raise SystemExit(main())
