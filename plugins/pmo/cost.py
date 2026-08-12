"""Project cost attribution and budget gates over native Hermes records.

Hermes already records authoritative session usage and already stamps
``worker_session_id`` into the metadata produced by ``kanban_complete``.  This
module joins those records to project-bound Kanban runs.  It adds no billing
database, model router, dispatcher, or alternate task state.

PMO-owned completion helpers also put a normalized ``usage`` object into the
existing ``task_runs.metadata`` seam.  Money is represented by :class:`Decimal`
in memory and serialized as a string so totals do not accumulate float error.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import os
from typing import Any, Iterable, Mapping

from agent.redact import redact_sensitive_text
from hermes_cli import kanban_db
from hermes_state import SessionDB

from . import project_scope


COST_MARKER = "[pmo:cost:v1]"
SOFT_CAP_MARKER = "[pmo:budget-soft-cap:v1]"
HARD_CAP_MARKER = "[pmo:budget-hard-cap:v1]"
MONTHLY_CAP_MARKER = "[pmo:budget-monthly-cap:v1]"
SYNTHETIC_MARKER = "[pmo:synthetic-cost-entry:v1]"
CONVERSATION_MARKER = "datansh-pmo-conversation.v1"
CFO_REQUIRED_RANK = 50


class BudgetBlocked(RuntimeError):
    """Raised after a native task is stopped at a configured hard limit."""

    def __init__(self, task_id: str, reason: str, approval_id: str | None = None):
        self.task_id = task_id
        self.reason = reason
        self.approval_id = approval_id
        suffix = f"; approval {approval_id}" if approval_id else ""
        super().__init__(f"budget blocked task '{task_id}': {reason}{suffix}")


def _money(value: Any, *, label: str = "cost") -> Decimal:
    try:
        amount = Decimal(str(value if value is not None else "0"))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{label} must be a decimal value") from exc
    if not amount.is_finite() or amount < 0:
        raise ValueError(f"{label} must be a finite non-negative decimal value")
    return amount


def _money_text(value: Decimal) -> str:
    return format(value, "f")


def _count(value: Any, *, label: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a non-negative integer")
    try:
        result = int(value or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a non-negative integer") from exc
    if result < 0:
        raise ValueError(f"{label} must be a non-negative integer")
    return result


@dataclass(frozen=True)
class UsageRecord:
    session_id: str
    model: str
    provider: str
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    cost_usd: Decimal
    turns: int
    tool_calls: int
    wall_seconds: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "session_id", str(self.session_id or "").strip())
        object.__setattr__(self, "model", str(self.model or "unknown").strip() or "unknown")
        object.__setattr__(self, "provider", str(self.provider or "").strip())
        for name in (
            "input_tokens",
            "cached_input_tokens",
            "output_tokens",
            "turns",
            "tool_calls",
            "wall_seconds",
        ):
            object.__setattr__(self, name, _count(getattr(self, name), label=name))
        object.__setattr__(self, "cost_usd", _money(self.cost_usd))

    def to_metadata(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "model": self.model,
            "provider": self.provider,
            "input_tokens": self.input_tokens,
            "cached_input_tokens": self.cached_input_tokens,
            "output_tokens": self.output_tokens,
            "cost_usd": _money_text(self.cost_usd),
            "turns": self.turns,
            "tool_calls": self.tool_calls,
            "wall_seconds": self.wall_seconds,
        }

    @classmethod
    def from_metadata(cls, value: Mapping[str, Any]) -> "UsageRecord":
        return cls(
            session_id=str(value.get("session_id") or ""),
            model=str(value.get("model") or "unknown"),
            provider=str(value.get("provider") or ""),
            input_tokens=_count(value.get("input_tokens"), label="input_tokens"),
            cached_input_tokens=_count(
                value.get("cached_input_tokens"), label="cached_input_tokens"
            ),
            output_tokens=_count(value.get("output_tokens"), label="output_tokens"),
            cost_usd=_money(value.get("cost_usd")),
            turns=_count(value.get("turns"), label="turns"),
            tool_calls=_count(value.get("tool_calls"), label="tool_calls"),
            wall_seconds=_count(value.get("wall_seconds"), label="wall_seconds"),
        )


def usage_from_session(session_id: str, *, session_db: SessionDB | None = None) -> UsageRecord:
    """Project Hermes' public session summary into the PMO usage contract."""

    clean_session = str(session_id or "").strip()
    if not clean_session:
        raise ValueError("session id is required for cost attribution")
    owns_db = session_db is None
    db = session_db or SessionDB(read_only=True)
    try:
        session = db.get_session(clean_session)
        if session is None:
            raise ValueError(f"Hermes session does not exist: {clean_session}")
        messages = db.get_messages(clean_session)
    finally:
        if owns_db:
            db.close()

    actual = session.get("actual_cost_usd")
    estimated = session.get("estimated_cost_usd")
    selected_cost = actual if actual not in (None, "") and _money(actual) > 0 else estimated
    ended = session.get("ended_at")
    started = session.get("started_at")
    wall_seconds = max(0, int(float(ended or started or 0) - float(started or 0)))
    turns = sum(message.get("role") == "user" for message in messages)
    return UsageRecord(
        session_id=clean_session,
        model=str(session.get("model") or "unknown"),
        provider=str(session.get("billing_provider") or ""),
        input_tokens=_count(session.get("input_tokens"), label="input_tokens"),
        cached_input_tokens=_count(
            session.get("cache_read_tokens"), label="cached_input_tokens"
        ),
        output_tokens=_count(session.get("output_tokens"), label="output_tokens"),
        cost_usd=_money(selected_cost),
        turns=turns,
        tool_calls=_count(session.get("tool_call_count"), label="tool_calls"),
        wall_seconds=wall_seconds,
    )


def completion_metadata(
    usage: UsageRecord, existing: Mapping[str, Any] | None = None, **facts: Any
) -> dict[str, Any]:
    """Merge usage into Kanban's public completion metadata object."""

    result = dict(existing or {})
    result.update({key: value for key, value in facts.items() if value is not None})
    result["worker_session_id"] = usage.session_id
    result["session_id"] = usage.session_id
    result["usage"] = usage.to_metadata()
    return result


def _usage_from_run(
    run: kanban_db.Run,
    *,
    task_session_id: str | None,
    session_db: SessionDB | None,
) -> tuple[UsageRecord | None, bool]:
    metadata = run.metadata or {}
    embedded = metadata.get("usage")
    if isinstance(embedded, Mapping):
        try:
            return UsageRecord.from_metadata(embedded), True
        except ValueError:
            return None, True
    session_id = str(
        metadata.get("worker_session_id")
        or metadata.get("session_id")
        or task_session_id
        or ""
    ).strip()
    if not session_id or session_db is None:
        return None, False
    try:
        return usage_from_session(session_id, session_db=session_db), False
    except (OSError, RuntimeError, ValueError):
        return None, False


def _month_bounds(now: float | None = None) -> tuple[int, int]:
    current = datetime.fromtimestamp(now, tz=timezone.utc) if now is not None else datetime.now(timezone.utc)
    start = current.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if start.month == 12:
        following = start.replace(year=start.year + 1, month=1)
    else:
        following = start.replace(month=start.month + 1)
    return int(start.timestamp()), int(following.timestamp())


def _agent_kind(scope: project_scope.ProjectScope, profile: str | None) -> str:
    clean = str(profile or "").strip()
    if clean == scope.config.orchestrator.profile:
        return "pm"
    for agent in scope.config.agents:
        if agent.profile == clean:
            return "reviewer" if "review" in agent.role.casefold() else "worker"
    return "worker"


@dataclass(frozen=True)
class RunSpend:
    run_id: int
    task_id: str
    attributed_task_id: str
    profile: str
    agent_kind: str
    outcome: str
    usage: UsageRecord


@dataclass(frozen=True)
class AgentSpend:
    profile: str
    runs: int
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    cost_usd: Decimal


@dataclass(frozen=True)
class ModelSpend:
    """Usage totals grouped by billing provider and model identifier."""

    model: str
    provider: str
    runs: int
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    cost_usd: Decimal


@dataclass(frozen=True)
class ProjectSpend:
    project_id: str
    currency: str
    cost_usd: Decimal
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    completed_tickets: int
    runs: tuple[RunSpend, ...]
    ticket_costs: Mapping[str, Decimal]
    agents: Mapping[str, AgentSpend]
    unattributed_run_ids: tuple[int, ...] = ()
    models: Mapping[str, ModelSpend] = field(default_factory=dict)

    @property
    def cache_hit_rate(self) -> Decimal:
        denominator = self.input_tokens + self.cached_input_tokens
        return (
            Decimal(self.cached_input_tokens) / Decimal(denominator)
            if denominator
            else Decimal("0")
        )

    @property
    def cost_per_completed_ticket(self) -> Decimal:
        return (
            self.cost_usd / Decimal(self.completed_tickets)
            if self.completed_tickets
            else Decimal("0")
        )


def project_spend(
    scope: project_scope.ProjectScope,
    *,
    conn=None,
    session_db: SessionDB | None = None,
    now: float | None = None,
) -> ProjectSpend:
    """Aggregate month-to-date spend through public Kanban/session APIs."""

    owns_conn = conn is None
    board_conn = conn or kanban_db.connect(board=scope.board_slug)
    owns_session = session_db is None
    sessions = session_db
    if sessions is None:
        try:
            sessions = SessionDB(read_only=True)
        except Exception:
            sessions = None
            owns_session = False
    start, end = _month_bounds(now)
    records: list[RunSpend] = []
    missing: list[int] = []
    seen_joined_sessions: set[str] = set()
    completed_ids: set[str] = set()
    ticket_costs: dict[str, Decimal] = {}
    agent_totals: dict[str, dict[str, Any]] = {}
    model_totals: dict[tuple[str, str], dict[str, Any]] = {}

    def add_usage(
        usage: UsageRecord,
        *,
        profile: str,
        record: RunSpend,
        add_ticket: bool,
        attributed_task_id: str,
    ) -> None:
        """Add one task-run or PMO session to the shared spend projections."""

        records.append(record)
        if add_ticket:
            ticket_costs[attributed_task_id] = (
                ticket_costs.get(attributed_task_id, Decimal("0"))
                + usage.cost_usd
            )
        totals = agent_totals.setdefault(
            profile,
            {"runs": 0, "input": 0, "cached": 0, "output": 0, "cost": Decimal("0")},
        )
        totals["runs"] += 1
        totals["input"] += usage.input_tokens
        totals["cached"] += usage.cached_input_tokens
        totals["output"] += usage.output_tokens
        totals["cost"] += usage.cost_usd
        model_key = (usage.provider, usage.model)
        model = model_totals.setdefault(
            model_key,
            {"runs": 0, "input": 0, "cached": 0, "output": 0, "cost": Decimal("0")},
        )
        model["runs"] += 1
        model["input"] += usage.input_tokens
        model["cached"] += usage.cached_input_tokens
        model["output"] += usage.output_tokens
        model["cost"] += usage.cost_usd
    try:
        tasks = [
            task
            for task in kanban_db.list_tasks(
                board_conn, include_archived=True, order_by="created"
            )
            if task.project_id == scope.project_id
        ]
        for task in tasks:
            is_synthetic = str(task.body or "").startswith(SYNTHETIC_MARKER)
            # Founder/client conversations are durable native Kanban records,
            # but their sticky promote/block lifecycle intentionally creates
            # run rows without model usage. They are communication containers,
            # not unattributed worker executions.
            if CONVERSATION_MARKER in str(task.body or ""):
                continue
            for run in kanban_db.list_runs(board_conn, task.id, include_active=False):
                occurred = run.ended_at or run.started_at
                if occurred < start or occurred >= end:
                    continue
                usage, embedded = _usage_from_run(
                    run, task_session_id=task.session_id, session_db=sessions
                )
                if usage is None:
                    missing.append(run.id)
                    continue
                if not embedded and usage.session_id:
                    if usage.session_id in seen_joined_sessions:
                        continue
                    seen_joined_sessions.add(usage.session_id)
                metadata = run.metadata or {}
                attributed = str(metadata.get("attributed_task_id") or task.id)
                kind = str(metadata.get("agent_kind") or _agent_kind(scope, run.profile))
                profile = str(run.profile or task.assignee or "unassigned")
                record = RunSpend(
                    run.id,
                    task.id,
                    attributed,
                    profile,
                    kind,
                    str(run.outcome or run.status),
                    usage,
                )
                add_usage(
                    usage,
                    profile=profile,
                    record=record,
                    add_ticket=True,
                    attributed_task_id=attributed,
                )
                if not is_synthetic and run.outcome == "completed":
                    completed_ids.add(task.id)

        # Founder’s Office, Client Collaboration, Global Founder’s Office, and
        # user-created PMO conversations are native Kanban tasks but their live
        # model turns are recorded in Hermes' gateway session store rather than
        # task_runs. Join those sessions by the explicit PMO board/thread pair
        # so subscription-priced Codex usage still contributes token analytics.
        conversation_thread_ids = {
            task.id
            for task in tasks
            if CONVERSATION_MARKER in str(task.body or "")
        }
        if sessions is not None and conversation_thread_ids:
            for session in sessions.list_gateway_sessions(
                platform="pmo", active_only=False
            ):
                if str(session.get("chat_id") or "") != scope.board_slug:
                    continue
                thread_id = str(session.get("thread_id") or "")
                if thread_id not in conversation_thread_ids:
                    continue
                session_id = str(session.get("id") or "").strip()
                if not session_id or session_id in seen_joined_sessions:
                    continue
                occurred = session.get("ended_at") or session.get("started_at")
                if occurred is None or float(occurred) < start or float(occurred) >= end:
                    continue
                try:
                    usage = usage_from_session(session_id, session_db=sessions)
                except (OSError, RuntimeError, ValueError):
                    continue
                seen_joined_sessions.add(session_id)
                profile = str(session.get("profile_name") or "unknown")
                record = RunSpend(
                    -len(records) - 1,
                    thread_id,
                    thread_id,
                    profile,
                    _agent_kind(scope, profile),
                    "conversation",
                    usage,
                )
                add_usage(
                    usage,
                    profile=profile,
                    record=record,
                    add_ticket=False,
                    attributed_task_id=thread_id,
                )
    finally:
        if owns_conn:
            board_conn.close()
        if owns_session and sessions is not None:
            sessions.close()

    agents = {
        profile: AgentSpend(
            profile,
            values["runs"],
            values["input"],
            values["cached"],
            values["output"],
            values["cost"],
        )
        for profile, values in agent_totals.items()
    }
    models = {
        f"{provider}:{model}" if provider else model: ModelSpend(
            model,
            provider,
            values["runs"],
            values["input"],
            values["cached"],
            values["output"],
            values["cost"],
        )
        for (provider, model), values in model_totals.items()
    }
    return ProjectSpend(
        project_id=scope.project_id,
        currency=scope.config.budget.currency,
        cost_usd=sum((record.usage.cost_usd for record in records), Decimal("0")),
        input_tokens=sum(record.usage.input_tokens for record in records),
        cached_input_tokens=sum(record.usage.cached_input_tokens for record in records),
        output_tokens=sum(record.usage.output_tokens for record in records),
        completed_tickets=len(completed_ids),
        runs=tuple(records),
        ticket_costs=ticket_costs,
        agents=agents,
        unattributed_run_ids=tuple(missing),
        models=models,
    )


def model_route_for_assignee(
    scope: project_scope.ProjectScope,
    assignee: str,
    *,
    blocked_recurrences: int = 0,
) -> str | None:
    """Select a configured route; Hermes still performs the actual routing."""

    # Root defaults fill gaps only; an agent-level or project-level pin still
    # remains the more specific routing decision.
    from plugins.pmo import admin_workspace

    models = admin_workspace.effective_configuration(scope)["effective"]["models"]
    # The escalation override stays first: it is a deliberate safety valve for
    # a ticket that keeps re-blocking, and it must beat any per-agent pin.
    if blocked_recurrences >= 2 and models.get("escalation_override"):
        return models["escalation_override"]
    if assignee == scope.config.orchestrator.profile:
        return models.get("pm") or scope.config.orchestrator.model
    for configured in scope.config.agents:
        if configured.profile == assignee:
            # Most specific wins: an agent pinned to a project-owned provider
            # (``model: openai-codex-hedgi``) overrides the role default, so a
            # single agent can be moved to a different account or model
            # without disturbing the rest of the roster.
            if configured.model:
                return configured.model
            if "review" in configured.role.casefold():
                return models.get("reviewer") or models.get("worker")
            return models.get("worker")
    return models.get("worker")


def promotion_budget_reasons(
    scope: project_scope.ProjectScope,
    conn,
    task_row,
    *,
    session_db: SessionDB | None = None,
    now: float | None = None,
) -> tuple[str, ...]:
    """Return hard refusals checked immediately before native promotion."""

    budget = scope.config.budget
    if budget.monthly_cap is None and budget.per_ticket_hard_cap is None:
        return ()
    spend = project_spend(scope, conn=conn, session_db=session_db, now=now)
    reasons: list[str] = []
    if budget.monthly_cap is not None and spend.cost_usd >= budget.monthly_cap:
        reasons.append(
            f"monthly budget reached ({_money_text(spend.cost_usd)} "
            f"{budget.currency} of {_money_text(budget.monthly_cap)})"
        )
    ticket_cost = spend.ticket_costs.get(task_row.id, Decimal("0"))
    if budget.per_ticket_hard_cap is not None and ticket_cost >= budget.per_ticket_hard_cap:
        reasons.append(
            f"ticket hard cap reached ({_money_text(ticket_cost)} "
            f"{budget.currency} of {_money_text(budget.per_ticket_hard_cap)})"
        )
    return tuple(reasons)


def _existing_marker(conn, task_id: str, marker: str) -> bool:
    return any(marker in comment.body for comment in kanban_db.list_comments(conn, task_id))


def _record_synthetic_usage(
    conn,
    *,
    scope: project_scope.ProjectScope,
    usage: UsageRecord,
    attributed_task_id: str,
    agent_kind: str,
) -> str:
    identity = f"Target task: {attributed_task_id}\nSession: {usage.session_id or 'unavailable'}"
    for existing in kanban_db.list_tasks(
        conn, include_archived=True, order_by="created"
    ):
        if (
            existing.project_id == scope.project_id
            and str(existing.body or "").startswith(SYNTHETIC_MARKER)
            and identity in str(existing.body or "")
        ):
            return existing.id
    task_id = kanban_db.create_task(
        conn,
        title=f"Cost attribution: {attributed_task_id}",
        body=(
            f"{SYNTHETIC_MARKER}\n{identity}"
        ),
        assignee=scope.config.orchestrator.profile,
        created_by="pmo-budget",
        session_id=usage.session_id or None,
        idempotency_key=(
            f"pmo-cost:{scope.project_id}:{attributed_task_id}:"
            f"{usage.session_id or usage.model}"
        ),
        board=scope.board_slug,
        project_id=scope.project_id,
    )
    task = kanban_db.get_task(conn, task_id)
    if task is not None and task.status in {"ready", "running"}:
        kanban_db.complete_task(
            conn,
            task_id,
            summary=f"Attributed session spend to {attributed_task_id}",
            metadata=completion_metadata(
                usage,
                attributed_task_id=attributed_task_id,
                agent_kind=agent_kind,
                synthetic_cost_entry=True,
            ),
            expected_run_id=task.current_run_id,
        )
        kanban_db.archive_task(conn, task_id)
    return task_id


def _cfo_profile(scope: project_scope.ProjectScope) -> str | None:
    for agent in scope.config.agents:
        if agent.role.strip().casefold() in {"cfo", "chief financial officer"}:
            return agent.profile
    return None


def _raise_cfo_approval(
    *,
    scope: project_scope.ProjectScope,
    task_id: str,
    usage: UsageRecord,
    total: Decimal,
) -> str | None:
    cfo = _cfo_profile(scope)
    if not cfo:
        return None
    from . import approval

    with kanban_db.connect_closing(board=scope.board_slug) as check_conn:
        for parent_id in kanban_db.parent_ids(check_conn, task_id):
            parent = kanban_db.get_task(check_conn, parent_id)
            if parent is not None and HARD_CAP_MARKER in str(parent.body or ""):
                return parent.id
    view = approval.raise_approval(
        board_slug=scope.board_slug,
        target_task_id=task_id,
        title="CFO budget overage",
        detail=(
            f"{HARD_CAP_MARKER}\nTicket spend: {_money_text(total)} USD\n"
            f"Latest session: {usage.session_id or 'unavailable'}\n"
            "Approve an explicit overage or cancel/re-scope the ticket."
        ),
        raised_by="pmo-budget",
        required_rank=CFO_REQUIRED_RANK,
        approver_profile=cfo,
    )
    return view.approval_id


def complete_task_with_usage(
    conn,
    *,
    scope: project_scope.ProjectScope,
    task_id: str,
    usage: UsageRecord,
    summary: str,
    result: str | None = None,
    metadata: Mapping[str, Any] | None = None,
    expected_run_id: int | None = None,
) -> bool:
    """Apply run/ticket caps, then use Kanban's existing completion seam."""

    task = kanban_db.get_task(conn, task_id)
    if task is None or task.project_id != scope.project_id:
        raise ValueError(f"task '{task_id}' is not in project '{scope.slug}'")
    budget = scope.config.budget
    before = project_spend(scope, conn=conn)
    existing = before.ticket_costs.get(task_id, Decimal("0"))
    projected = existing + usage.cost_usd
    hard_reason: str | None = None
    if usage.turns > budget.per_run_max_turns:
        hard_reason = (
            f"run used {usage.turns} turns; configured maximum is "
            f"{budget.per_run_max_turns}"
        )
    elif budget.per_ticket_hard_cap is not None and projected >= budget.per_ticket_hard_cap:
        hard_reason = (
            f"ticket projected spend {_money_text(projected)} {budget.currency} "
            f"reached hard cap {_money_text(budget.per_ticket_hard_cap)}"
        )
    elif (
        budget.on_soft_cap == "block"
        and budget.per_ticket_soft_cap is not None
        and projected >= budget.per_ticket_soft_cap
    ):
        hard_reason = (
            f"ticket projected spend {_money_text(projected)} {budget.currency} "
            f"reached blocking soft cap {_money_text(budget.per_ticket_soft_cap)}"
        )

    if hard_reason:
        clean_reason = redact_sensitive_text(hard_reason, force=True)
        if not _existing_marker(conn, task_id, HARD_CAP_MARKER):
            kanban_db.add_comment(
                conn,
                task_id,
                "pmo-budget",
                f"{HARD_CAP_MARKER}\n@pm {clean_reason}",
            )
        stopped = kanban_db.block_task(
            conn,
            task_id,
            reason=clean_reason,
            kind="needs_input",
            expected_run_id=expected_run_id,
        )
        if stopped:
            _record_synthetic_usage(
                conn,
                scope=scope,
                usage=usage,
                attributed_task_id=task_id,
                agent_kind=_agent_kind(scope, task.assignee),
            )
        approval_id = None
        if budget.on_hard_cap == "escalate":
            approval_id = _raise_cfo_approval(
                scope=scope, task_id=task_id, usage=usage, total=projected
            )
        raise BudgetBlocked(task_id, clean_reason, approval_id)

    ok = kanban_db.complete_task(
        conn,
        task_id,
        result=result,
        summary=redact_sensitive_text(str(summary), force=True),
        metadata=completion_metadata(
            usage,
            metadata,
            agent_kind=_agent_kind(scope, task.assignee),
        ),
        expected_run_id=expected_run_id,
    )
    if not ok:
        return False
    if budget.per_ticket_soft_cap is not None and projected >= budget.per_ticket_soft_cap:
        if not _existing_marker(conn, task_id, SOFT_CAP_MARKER):
            kanban_db.add_comment(
                conn,
                task_id,
                "pmo-budget",
                (
                    f"{SOFT_CAP_MARKER}\n@pm Ticket spend is "
                    f"{_money_text(projected)} {budget.currency}; soft cap is "
                    f"{_money_text(budget.per_ticket_soft_cap)}."
                ),
            )
    if budget.monthly_cap is not None:
        projected_month = before.cost_usd + usage.cost_usd
        percent = projected_month / budget.monthly_cap * Decimal("100")
        for threshold in budget.alert_at_percent:
            marker = f"{MONTHLY_CAP_MARKER}:{threshold}"
            if percent >= threshold and not _existing_marker(conn, task_id, marker):
                kanban_db.add_comment(
                    conn,
                    task_id,
                    "pmo-budget",
                    (
                        f"{marker}\n@pm Project month-to-date spend is "
                        f"{_money_text(projected_month)} {budget.currency} "
                        f"({percent.quantize(Decimal('0.1'))}% of "
                        f"{_money_text(budget.monthly_cap)})."
                    ),
                )
    return True


def record_pm_thread_turn(
    *,
    scope: project_scope.ProjectScope,
    usage: UsageRecord,
    thread_task_id: str,
) -> str:
    """Attribute a non-ticket PM discussion turn through a native hidden card."""

    with kanban_db.connect_closing(board=scope.board_slug) as conn:
        if kanban_db.get_task(conn, thread_task_id) is None:
            raise ValueError(f"thread task does not exist: {thread_task_id}")
        return _record_synthetic_usage(
            conn,
            scope=scope,
            usage=usage,
            attributed_task_id=thread_task_id,
            agent_kind="pm",
        )


@dataclass(frozen=True)
class Reconciliation:
    attributed_usd: Decimal
    insights_usd: Decimal
    drift_usd: Decimal
    drift_percent: Decimal
    healthy: bool
    unattributed_run_ids: tuple[int, ...] = field(default_factory=tuple)


def doctor_reconciliation(
    scope: project_scope.ProjectScope,
    *,
    insights_total_usd: Decimal | str,
    session_db: SessionDB | None = None,
    now: float | None = None,
) -> Reconciliation:
    """Compare project attribution with a caller-provided Hermes Insights total."""

    spend = project_spend(scope, session_db=session_db, now=now)
    insights = _money(insights_total_usd, label="Hermes Insights total")
    drift = abs(insights - spend.cost_usd)
    drift_percent = drift / insights * Decimal("100") if insights else Decimal("0")
    return Reconciliation(
        attributed_usd=spend.cost_usd,
        insights_usd=insights,
        drift_usd=drift,
        drift_percent=drift_percent,
        healthy=drift_percent <= Decimal("5") and not spend.unattributed_run_ids,
        unattributed_run_ids=spend.unattributed_run_ids,
    )


def expensive_model_warnings(
    config: project_scope.ProjectConfig,
    *,
    warning_fn=None,
) -> tuple[str, ...]:
    """Return deduplicated Hermes expensive-model warnings for bootstrap."""

    if warning_fn is None:
        from hermes_cli.model_cost_guard import expensive_model_warning

        warning_fn = expensive_model_warning
    routes: Iterable[str | None] = (
        config.models.pm or config.orchestrator.model,
        config.models.worker,
        config.models.reviewer,
        config.models.summariser,
        config.models.escalation_override,
    )
    messages: list[str] = []
    seen: set[str] = set()
    for route in routes:
        if not route or route in seen:
            continue
        seen.add(route)
        warning = warning_fn(route)
        if warning is not None:
            messages.append(str(warning.message))
    return tuple(messages)


def _hook_scope(task_id: str, args: Mapping[str, Any] | None = None):
    board = str(
        (args or {}).get("board")
        or os.environ.get("HERMES_KANBAN_BOARD")
        or ""
    ).strip()
    if not board or not task_id or not kanban_db.board_exists(board):
        return None
    with kanban_db.connect_closing(board=board) as conn:
        task = kanban_db.get_task(conn, task_id)
    if task is None or not task.project_id:
        return None
    try:
        return project_scope.resolve(task.project_id, board_slug=board)
    except project_scope.ProjectScopeError:
        return None


def _projected_hard_reason(
    scope: project_scope.ProjectScope,
    *,
    task_id: str,
    usage: UsageRecord,
) -> str | None:
    budget = scope.config.budget
    if usage.turns > budget.per_run_max_turns:
        return (
            f"run used {usage.turns} turns; configured maximum is "
            f"{budget.per_run_max_turns}"
        )
    spend = project_spend(scope)
    projected = spend.ticket_costs.get(task_id, Decimal("0")) + usage.cost_usd
    if budget.per_ticket_hard_cap is not None and projected >= budget.per_ticket_hard_cap:
        return (
            f"ticket projected spend {_money_text(projected)} {budget.currency} "
            f"reached hard cap {_money_text(budget.per_ticket_hard_cap)}"
        )
    if (
        budget.on_soft_cap == "block"
        and budget.per_ticket_soft_cap is not None
        and projected >= budget.per_ticket_soft_cap
    ):
        return (
            f"ticket projected spend {_money_text(projected)} {budget.currency} "
            f"reached blocking soft cap {_money_text(budget.per_ticket_soft_cap)}"
        )
    return None


def pre_tool_budget_hook(
    *,
    tool_name: str,
    args: Mapping[str, Any] | None = None,
    task_id: str = "",
    session_id: str = "",
    **_kwargs: Any,
) -> dict[str, str] | None:
    """Prevent a normal ``kanban_complete`` from bypassing a hard PMO cap.

    The subsequent ``on_session_end`` observer performs the native block and
    CFO escalation. Returning a standard pre-tool directive here leaves the
    core tool and approval runtimes untouched.
    """

    if tool_name != "kanban_complete" or not task_id or not session_id:
        return None
    scope = _hook_scope(task_id, args)
    if scope is None:
        return None
    try:
        usage = usage_from_session(session_id)
        reason = _projected_hard_reason(scope, task_id=task_id, usage=usage)
    except (OSError, RuntimeError, ValueError):
        return None
    if reason is None:
        return None
    return {
        "action": "block",
        "message": (
            f"PM-OS budget gate: {reason}. The run will be routed to the PM "
            "through the native blocked/approval workflow at session end."
        ),
    }


def _post_completed_notices(
    conn,
    *,
    scope: project_scope.ProjectScope,
    task_id: str,
    spend: ProjectSpend,
) -> None:
    budget = scope.config.budget
    ticket_total = spend.ticket_costs.get(task_id, Decimal("0"))
    if (
        budget.per_ticket_soft_cap is not None
        and ticket_total >= budget.per_ticket_soft_cap
        and not _existing_marker(conn, task_id, SOFT_CAP_MARKER)
    ):
        kanban_db.add_comment(
            conn,
            task_id,
            "pmo-budget",
            (
                f"{SOFT_CAP_MARKER}\n@pm Ticket spend is "
                f"{_money_text(ticket_total)} {budget.currency}; soft cap is "
                f"{_money_text(budget.per_ticket_soft_cap)}."
            ),
        )
    if budget.monthly_cap is None:
        return
    percent = spend.cost_usd / budget.monthly_cap * Decimal("100")
    for threshold in budget.alert_at_percent:
        marker = f"{MONTHLY_CAP_MARKER}:{threshold}"
        if percent >= threshold and not _existing_marker(conn, task_id, marker):
            kanban_db.add_comment(
                conn,
                task_id,
                "pmo-budget",
                (
                    f"{marker}\n@pm Project month-to-date spend is "
                    f"{_money_text(spend.cost_usd)} {budget.currency} "
                    f"({percent.quantize(Decimal('0.1'))}% of "
                    f"{_money_text(budget.monthly_cap)})."
                ),
            )


def capture_completed_session(
    *,
    scope: project_scope.ProjectScope,
    task_id: str,
    usage: UsageRecord,
) -> bool:
    """Backfill usage through Kanban's public completed-task edit seam."""

    with kanban_db.connect_closing(board=scope.board_slug) as conn:
        task = kanban_db.get_task(conn, task_id)
        run = kanban_db.latest_run(conn, task_id)
        if task is None or task.project_id != scope.project_id or task.status != "done":
            return False
        if run is None:
            return False
        existing = dict(run.metadata or {})
        embedded = existing.get("usage")
        if not isinstance(embedded, Mapping):
            ok = kanban_db.edit_completed_task_result(
                conn,
                task_id,
                result=task.result or run.summary or "Completed",
                summary=run.summary or task.result or "Completed",
                metadata=completion_metadata(
                    usage,
                    existing,
                    agent_kind=_agent_kind(scope, task.assignee),
                ),
            )
            if not ok:
                return False
        spend = project_spend(scope, conn=conn)
        _post_completed_notices(conn, scope=scope, task_id=task_id, spend=spend)
        return True


def on_session_end_budget_hook(
    *,
    session_id: str,
    task_id: str = "",
    **_kwargs: Any,
) -> None:
    """Capture normal worker usage and enforce a pre-tool budget refusal."""

    scope = _hook_scope(task_id)
    if scope is None or not session_id:
        return
    try:
        usage = usage_from_session(session_id)
    except (OSError, RuntimeError, ValueError):
        return
    with kanban_db.connect_closing(board=scope.board_slug) as conn:
        task = kanban_db.get_task(conn, task_id)
        if task is None or task.project_id != scope.project_id:
            return
        status = task.status
    if status == "done":
        capture_completed_session(scope=scope, task_id=task_id, usage=usage)
        return
    if status == "blocked":
        reason = _projected_hard_reason(scope, task_id=task_id, usage=usage)
        with kanban_db.connect_closing(board=scope.board_slug) as conn:
            _record_synthetic_usage(
                conn,
                scope=scope,
                usage=usage,
                attributed_task_id=task_id,
                agent_kind=_agent_kind(scope, task.assignee),
            )
        if reason and scope.config.budget.on_hard_cap == "escalate":
            total = project_spend(scope).ticket_costs.get(task_id, usage.cost_usd)
            _raise_cfo_approval(
                scope=scope, task_id=task_id, usage=usage, total=total
            )
        return
    if status not in {"ready", "running"}:
        return
    reason = _projected_hard_reason(scope, task_id=task_id, usage=usage)
    if reason is None:
        # A worker turn can end without invoking complete/block. Preserve its
        # spend now; the synthetic entry is idempotent for this task/session.
        with kanban_db.connect_closing(board=scope.board_slug) as conn:
            _record_synthetic_usage(
                conn,
                scope=scope,
                usage=usage,
                attributed_task_id=task_id,
                agent_kind=_agent_kind(scope, task.assignee),
            )
        return
    with kanban_db.connect_closing(board=scope.board_slug) as conn:
        task = kanban_db.get_task(conn, task_id)
        if task is None or task.status not in {"ready", "running"}:
            return
        if not _existing_marker(conn, task_id, HARD_CAP_MARKER):
            kanban_db.add_comment(
                conn, task_id, "pmo-budget", f"{HARD_CAP_MARKER}\n@pm {reason}"
            )
        stopped = kanban_db.block_task(
            conn,
            task_id,
            reason=reason,
            kind="needs_input",
            expected_run_id=task.current_run_id,
        )
        if stopped:
            _record_synthetic_usage(
                conn,
                scope=scope,
                usage=usage,
                attributed_task_id=task_id,
                agent_kind=_agent_kind(scope, task.assignee),
            )
    if stopped and scope.config.budget.on_hard_cap == "escalate":
        total = project_spend(scope).ticket_costs.get(task_id, usage.cost_usd)
        _raise_cfo_approval(
            scope=scope,
            task_id=task_id,
            usage=usage,
            total=total,
        )
