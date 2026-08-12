"""Kanban-native ticket lifecycle for Datansh PM-OS.

PM-OS gives existing Hermes states product-facing meanings instead of adding a
second state machine: ``triage`` is Draft, ``todo`` is Queued, ``ready`` is
claimable, and ``archived`` retains cancelled work.  Every mutation below goes
through :mod:`hermes_cli.kanban_db`; project-specific facts live in the ticket
body, native links, and comments.

Rework deliberately preserves the old card.  Hermes does not expose a public
``review -> triage`` store operation, so rejecting/reopening creates one
idempotent triage successor and archives the prior card.  This retains its
comments, runs, worktree history, and audit trail without writing task status
directly or changing upstream Kanban.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from agent.redact import redact_sensitive_text
from hermes_cli import kanban_db
from hermes_cli.profiles import normalize_profile_name

from . import cost, project_scope, ticket


TICKET_META_MARKER = "[pmo:ticket-meta:v1]"
HUMAN_TASK_MARKER = "[pmo:human-task:v1]"

# Principals that denote a person rather than an agent profile. This prefix
# lives in the ``assignee`` column, which agents cannot rewrite.
HUMAN_PRINCIPAL_PREFIXES = ("human:", "dashboard:")
LIFECYCLE_MARKER = "[pmo:lifecycle:v1]"
APPROVAL_MARKER = "[pmo:approval:v1]"
REWORK_MARKER = "[pmo:rework:v1]"

def protected_record_kind(task_row) -> str | None:
    """Name the PM-OS record type that must never be LLM-rewritten.

    Approvals and human tasks are *records*, not rough ideas. Their title and
    body are structured metadata other code parses back out — an approval's
    required rank, target task and raiser all live there. PM-OS parks them in
    ``triage`` because the dispatcher ignores that column, but the triage
    specifier's only skip condition is ``status != "triage"``, which makes
    every PM-OS record a guaranteed target. Rewriting one erases the metadata,
    so the record reports "is not a PM-OS approval" and can never be decided.
    """
    body = str(getattr(task_row, "body", "") or "")
    if APPROVAL_MARKER in body:
        return "approval"
    if is_human_task(task_row):
        return "human-task"
    return None


def is_human_task(task_row) -> bool:
    """Is this task owned by a person rather than an agent profile?

    Checks the ``assignee`` column **as well as** the body marker. The marker
    alone is not durable: a specifier agent rewrites the body during the
    ``specified`` step and drops it, at which point a human task silently
    reclassified as an agent task, was promoted to ``ready``, and entered the
    dispatcher's claim set with an assignee no profile matches — where it
    retries as nonspawnable forever instead of reaching a person.

    The assignee prefix is structural: it lives in a column, and nothing in
    the agent's prose-rewriting path can touch it.
    """
    assignee = str(getattr(task_row, "assignee", "") or "").strip().lower()
    if assignee.startswith(HUMAN_PRINCIPAL_PREFIXES):
        return True
    return HUMAN_TASK_MARKER in str(getattr(task_row, "body", "") or "")


STATUS_LABELS: Mapping[str, str] = {
    "triage": "Draft",
    "todo": "Queued",
    "scheduled": "Scheduled",
    "ready": "Ready",
    "running": "In progress",
    "blocked": "Blocked",
    "review": "Review",
    "done": "Done",
    "archived": "Archived",
}

# This is a PM-facing action contract. Native automatic transitions such as
# todo -> ready and ready -> running remain owned by Kanban and are not exposed
# as PM actions here.
LEGAL_ACTIONS: Mapping[str, tuple[str, ...]] = {
    "triage": ("finalize", "cancel", "archive"),
    "todo": ("cancel", "archive"),
    "scheduled": ("cancel", "archive"),
    "ready": ("cancel", "archive"),
    "running": ("cancel", "archive"),
    "blocked": ("cancel", "archive"),
    "review": ("reopen", "cancel", "archive"),
    "done": ("reopen", "archive"),
    "archived": ("reopen",),
}

_CHECKBOX = re.compile(r"^\s*-\s*\[(?: |x|X)\]\s+\S", re.MULTILINE)
_GERUND = re.compile(r"^[A-Za-z]+ing\b", re.IGNORECASE)


class WorkflowError(ValueError):
    """Base error for a refused PM lifecycle operation."""


class IllegalTransition(WorkflowError):
    """A structured refusal naming the legal actions from the current state."""

    def __init__(self, task_id: str, status: str, action: str):
        self.task_id = task_id
        self.status = status
        self.action = action
        self.legal_actions = LEGAL_ACTIONS.get(status, ())
        legal = ", ".join(self.legal_actions) or "none"
        super().__init__(
            f"cannot {action} task '{task_id}' from {status}; legal actions: {legal}"
        )


class FinalizeRefused(WorkflowError):
    """Finalize validation failed without mutating the draft."""

    def __init__(self, task_id: str, reasons: Iterable[str]):
        self.task_id = task_id
        self.reasons = tuple(str(reason) for reason in reasons)
        super().__init__(f"cannot finalize task '{task_id}': " + "; ".join(self.reasons))


@dataclass(frozen=True)
class BodyContract:
    acceptance_count: int
    evidence_count: int
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class WorkflowResult:
    board_slug: str
    project_id: str
    task_id: str
    action: str
    previous_status: str
    status: str
    idempotent: bool = False
    successor_task_id: str | None = None
    warnings: tuple[str, ...] = ()


def _required_text(value: str | None, *, label: str) -> str:
    clean = str(value or "").strip()
    if not clean:
        raise WorkflowError(f"{label} must not be empty")
    return redact_sensitive_text(clean, force=True)


def _section(text: str, headings: tuple[str, ...]) -> str:
    lines = text.splitlines()
    normalized = {heading.casefold() for heading in headings}
    start: int | None = None
    for index, line in enumerate(lines):
        value = line.strip().rstrip(":").casefold()
        if value in normalized:
            start = index + 1
            break
    if start is None:
        return ""
    selected: list[str] = []
    for line in lines[start:]:
        stripped = line.strip()
        if stripped.startswith("## ") or (
            stripped.endswith(":") and not stripped.startswith("-")
        ):
            break
        if stripped == TICKET_META_MARKER:
            break
        selected.append(line)
    return "\n".join(selected)


def validate_body(title: str, body: str | None) -> BodyContract:
    """Validate the short, stable PM ticket contract.

    Both the plan's Markdown headings and ``ticket.format_ticket_body``'s
    existing headings are accepted so the lifecycle strengthens the current
    PM edge without invalidating already-created tickets.
    """

    clean_title = _required_text(title, label="title")
    if len(clean_title) > 90:
        raise WorkflowError("title must be 90 characters or fewer")
    text = str(body or "")
    acceptance = _section(text, ("## acceptance", "acceptance criteria"))
    evidence = _section(
        text,
        ("## closure evidence", "## evidence", "evidence required to close"),
    )
    acceptance_count = len(_CHECKBOX.findall(acceptance))
    evidence_count = len(_CHECKBOX.findall(evidence))
    reasons: list[str] = []
    if acceptance_count < 1:
        reasons.append("at least one acceptance checkbox is required")
    if evidence_count < 1:
        reasons.append("at least one closure-evidence checkbox is required")
    if reasons:
        raise WorkflowError("; ".join(reasons))
    warnings: list[str] = []
    if not _section(text, ("## out of scope", "scope and constraints")):
        warnings.append("out-of-scope guidance is absent")
    if _GERUND.match(clean_title):
        warnings.append("title may not be imperative")
    return BodyContract(acceptance_count, evidence_count, tuple(warnings))


def _scope(project_ref: str, board_slug: str, workspace: str | Path | None):
    return project_scope.resolve(project_ref, board_slug=board_slug, workspace=workspace)


def _require_pm(scope, actor_profile: str) -> str:
    actor = normalize_profile_name(_required_text(actor_profile, label="actor profile"))
    expected = normalize_profile_name(scope.config.orchestrator.profile)
    if actor != expected:
        raise PermissionError(
            f"only project PM profile '{expected}' may mutate this lifecycle; got '{actor}'"
        )
    return actor


def _task(conn, scope, task_id: str):
    clean_id = _required_text(task_id, label="task id")
    task_row = kanban_db.get_task(conn, clean_id)
    if task_row is None:
        raise WorkflowError(f"task '{clean_id}' does not exist on board '{scope.board_slug}'")
    if task_row.project_id != scope.project_id:
        raise WorkflowError(
            f"task '{clean_id}' belongs to project {task_row.project_id!r}, "
            f"not '{scope.project_id}'"
        )
    return task_row


def _configured_profiles(scope) -> set[str]:
    profiles = {normalize_profile_name(scope.config.orchestrator.profile)}
    profiles.update(normalize_profile_name(agent.profile) for agent in scope.config.agents)
    return profiles


def _worker_profiles(scope) -> set[str]:
    """Dispatchable implementers; the PM owns the board and is never one."""

    orchestrator = normalize_profile_name(scope.config.orchestrator.profile)
    return {
        normalize_profile_name(agent.profile)
        for agent in scope.config.agents
        if normalize_profile_name(agent.profile) != orchestrator
    }


def _metadata_block(
    *,
    labels: Iterable[str],
    estimate_hours: float | None = None,
    touches: Iterable[str] = (),
) -> str:
    clean_labels = sorted({str(label).strip() for label in labels if str(label).strip()})
    lines = [TICKET_META_MARKER, f"Labels: {','.join(clean_labels)}"]
    if estimate_hours is not None:
        if float(estimate_hours) < 0:
            raise WorkflowError("estimate hours must be non-negative")
        lines.append(f"Estimate hours: {float(estimate_hours):g}")
    clean_touches = sorted({str(item).strip().replace("\\", "/") for item in touches if str(item).strip()})
    if clean_touches:
        lines.append("Touches: " + json.dumps(clean_touches, separators=(",", ":")))
    return "\n".join(lines)


def _metadata(body: str | None) -> dict[str, str]:
    text = str(body or "")
    _, marker, tail = text.partition(TICKET_META_MARKER)
    if not marker:
        return {}
    result: dict[str, str] = {}
    for line in tail.splitlines()[1:]:
        if line.startswith("["):
            break
        key, separator, value = line.partition(":")
        if separator:
            result[key.strip().casefold().replace(" ", "_")] = value.strip()
    return result


def _labels(body: str | None) -> set[str]:
    raw = _metadata(body).get("labels", "")
    return {item.strip() for item in raw.split(",") if item.strip()}


def _touches(body: str | None) -> tuple[str, ...]:
    raw = _metadata(body).get("touches", "")
    if not raw:
        return ()
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return ()
    if not isinstance(value, list):
        return ()
    return tuple(str(item) for item in value if str(item).strip())


def _touch_prefix(pattern: str) -> str:
    value = pattern.replace("\\", "/").strip().lstrip("./")
    wildcard = min((value.find(char) for char in "*?[" if char in value), default=len(value))
    return value[:wildcard].rstrip("/")


def _patterns_overlap(left: str, right: str) -> bool:
    a, b = _touch_prefix(left), _touch_prefix(right)
    if not a or not b:
        return left == right
    return a == b or a.startswith(b + "/") or b.startswith(a + "/")


def _touch_overlap_warnings(conn, scope, task_row) -> tuple[str, ...]:
    declared = _touches(task_row.body)
    if not declared:
        return ()
    warnings: list[str] = []
    for other in kanban_db.list_tasks(conn, include_archived=False, order_by="created"):
        if (
            other.id == task_row.id
            or other.project_id != scope.project_id
            or other.status not in {"todo", "ready", "running", "review"}
        ):
            continue
        overlaps = sorted(
            {f"{left} ↔ {right}" for left in declared for right in _touches(other.body) if _patterns_overlap(left, right)}
        )
        if overlaps:
            warnings.append(
                f"touches overlap with {other.id} ({other.status}): " + ", ".join(overlaps)
            )
    return tuple(warnings)


def touch_overlap_warnings_for_tasks(
    scope, tasks: Iterable[object]
) -> dict[str, tuple[str, ...]]:
    """Return advisory overlap warnings for one already-loaded project view."""

    active = [
        task
        for task in tasks
        if getattr(task, "project_id", None) == scope.project_id
        and getattr(task, "status", None) in {"todo", "ready", "running", "review"}
        and _touches(getattr(task, "body", None))
    ]
    result: dict[str, tuple[str, ...]] = {}
    for task in active:
        declared = _touches(task.body)
        warnings: list[str] = []
        for other in active:
            if other.id == task.id:
                continue
            overlaps = sorted(
                {
                    f"{left} ↔ {right}"
                    for left in declared
                    for right in _touches(other.body)
                    if _patterns_overlap(left, right)
                }
            )
            if overlaps:
                warnings.append(
                    f"touches overlap with {other.id} ({other.status}): "
                    + ", ".join(overlaps)
                )
        if warnings:
            result[task.id] = tuple(warnings)
    return result


def _validate_labels(scope, labels: Iterable[str]) -> tuple[str, ...]:
    clean = tuple(sorted({str(label).strip() for label in labels if str(label).strip()}))
    configured = tuple(scope.config.board.labels)
    unknown = sorted(set(clean) - set(configured))
    if unknown:
        legal = ", ".join(configured) or "(none configured)"
        raise WorkflowError(
            f"unknown label(s): {', '.join(unknown)}; legal labels: {legal}"
        )
    return clean


def _idempotency_key(scope, title: str, assignee: str, parents: Iterable[str]) -> str:
    material = "\0".join(
        [scope.project_id, title.strip(), normalize_profile_name(assignee), *sorted(parents)]
    )
    return "pmo-draft:" + hashlib.sha256(material.encode("utf-8")).hexdigest()


def create_draft(
    *,
    project_ref: str,
    board_slug: str,
    actor_profile: str,
    title: str,
    outcome: str,
    product_context: str | None = None,
    user_impact: str | None = None,
    assignee: str,
    acceptance_criteria: Iterable[str],
    evidence: Iterable[str],
    constraints: Iterable[str] = (),
    dependencies: Iterable[str] = (),
    parent_task_ids: Iterable[str] = (),
    labels: Iterable[str] = (),
    touches: Iterable[str] = (),
    estimate_hours: float | None = None,
    priority: int = 0,
    max_runtime_seconds: int = 3_600,
    max_retries: int = 3,
    idempotency_key: str | None = None,
    workspace: str | Path | None = None,
) -> WorkflowResult:
    """Create one PM-owned draft using native triage and idempotency."""

    scope = _scope(project_ref, board_slug, workspace)
    actor = _require_pm(scope, actor_profile)
    clean_assignee = normalize_profile_name(_required_text(assignee, label="assignee"))
    if clean_assignee not in _worker_profiles(scope):
        legal = ", ".join(sorted(_worker_profiles(scope))) or "(no worker agents provisioned)"
        raise WorkflowError(
            f"assignee '{clean_assignee}' is not a project worker: {legal}; "
            "the PM is read-only and cannot own implementation tickets"
        )
    clean_labels = _validate_labels(scope, labels)
    parents = tuple(_required_text(item, label="parent task id") for item in parent_task_ids)
    body = ticket.format_ticket_body(
        outcome=outcome,
        product_context=product_context,
        user_impact=user_impact,
        constraints=constraints,
        acceptance_criteria=acceptance_criteria,
        dependencies=dependencies,
        parent_task_ids=parents,
        evidence=evidence,
    )
    body = body + "\n\n" + _metadata_block(
        labels=clean_labels, estimate_hours=estimate_hours, touches=touches
    )
    contract = validate_body(title, body)
    idem = idempotency_key or _idempotency_key(scope, title, clean_assignee, parents)
    model_override = cost.model_route_for_assignee(scope, clean_assignee)
    with kanban_db.connect_closing(board=scope.board_slug) as conn:
        task_id = kanban_db.create_task(
            conn,
            title=title,
            body=body,
            assignee=clean_assignee,
            created_by=actor,
            priority=int(priority),
            parents=parents,
            triage=True,
            idempotency_key=idem,
            max_runtime_seconds=int(max_runtime_seconds),
            max_retries=int(max_retries),
            model_override=model_override,
            board=scope.board_slug,
            project_id=scope.project_id,
        )
        created = _task(conn, scope, task_id)
    return WorkflowResult(
        scope.board_slug,
        scope.project_id,
        task_id,
        "draft",
        created.status,
        created.status,
        idempotent=created.idempotency_key == idem and created.created_by != actor,
        warnings=contract.warnings,
    )


def create_human_draft(
    *,
    project_ref: str,
    board_slug: str,
    actor_profile: str,
    created_by: str | None = None,
    title: str,
    outcome: str,
    product_context: str | None = None,
    user_impact: str | None = None,
    assignee: str,
    acceptance_criteria: Iterable[str],
    evidence: Iterable[str],
    constraints: Iterable[str] = (),
    dependencies: Iterable[str] = (),
    parent_task_ids: Iterable[str] = (),
    labels: Iterable[str] = (),
    touches: Iterable[str] = (),
    human_steps: Iterable[str] = (),
    priority: int = 0,
    idempotency_key: str | None = None,
    workspace: str | Path | None = None,
) -> WorkflowResult:
    """Create a PM-owned, non-dispatchable human task in native triage.

    Human tasks deliberately use the same ticket contract and Kanban storage
    as agent work.  Their ``human:``/``dashboard:`` assignee is not a Hermes
    profile, so the dispatcher will never claim them; the PM still finalizes
    the card before it becomes visible in the copied Kanban board.
    """

    scope = _scope(project_ref, board_slug, workspace)
    actor = _require_pm(scope, actor_profile)
    clean_assignee = _required_text(assignee, label="human assignee")
    if not clean_assignee.startswith(("human:", "dashboard:")):
        raise WorkflowError("human assignee must be a human: or dashboard: principal")
    clean_labels = _validate_labels(scope, labels)
    # ``acceptance_criteria`` may be a single-use iterable.  It is also the
    # backward-compatible hand-off instruction set for the dashboard form.
    acceptance_items = tuple(acceptance_criteria)
    parents = tuple(_required_text(item, label="parent task id") for item in parent_task_ids)
    clean_steps = tuple(_required_text(item, label="human hand-off step") for item in human_steps)
    # The dashboard's existing human-task form predates explicit step capture.
    # Its acceptance criteria remain actionable hand-off instructions, while
    # agent-created human work is required to supply human_steps by planning.
    if not clean_steps:
        clean_steps = tuple(_required_text(item, label="acceptance criterion") for item in acceptance_items)
    body = ticket.format_ticket_body(
        outcome=outcome,
        product_context=product_context,
        user_impact=user_impact,
        constraints=constraints,
        acceptance_criteria=acceptance_items,
        dependencies=dependencies,
        parent_task_ids=parents,
        evidence=evidence,
    )
    body += "\n\nHuman hand-off steps:\n" + "\n".join(
        f"{index}. {step}" for index, step in enumerate(clean_steps, start=1)
    )
    body += "\n\n" + _metadata_block(
        labels=clean_labels, touches=touches
    ) + "\n" + HUMAN_TASK_MARKER
    contract = validate_body(title, body)
    idem = idempotency_key or _idempotency_key(scope, title, clean_assignee, parents)
    with kanban_db.connect_closing(board=scope.board_slug) as conn:
        task_id = kanban_db.create_task(
            conn,
            title=title,
            body=body,
            assignee=clean_assignee,
            created_by=_required_text(created_by or actor, label="created by"),
            priority=int(priority),
            parents=parents,
            triage=True,
            idempotency_key=idem,
            board=scope.board_slug,
            project_id=scope.project_id,
        )
        created = _task(conn, scope, task_id)
    return WorkflowResult(
        scope.board_slug,
        scope.project_id,
        task_id,
        "human-draft",
        created.status,
        created.status,
        idempotent=created.idempotency_key == idem and created.created_by != actor,
        warnings=contract.warnings,
    )


def _approval_rank(task_row) -> int | None:
    if not str(task_row.body or "").startswith(APPROVAL_MARKER):
        return None
    match = re.search(r"^Initial required rank:\s*(\d+)\s*$", task_row.body or "", re.MULTILINE)
    return int(match.group(1)) if match else 0


def _matching_rule_ranks(scope, task_row) -> list[int]:
    metadata = _metadata(task_row.body)
    labels = _labels(task_row.body)
    matched: list[int] = []
    for rule in scope.config.approvals.rules:
        conditions = dict(rule.when)
        ok = True
        for key, value in conditions.items():
            if key == "label":
                ok = ok and str(value) in labels
            elif key == "estimate_hours_gt":
                try:
                    ok = ok and float(metadata.get("estimate_hours", "-inf")) > float(value)
                except ValueError:
                    ok = False
            else:
                ok = False
        if ok:
            matched.append(int(rule.required_rank))
    return matched


def _finalize_reasons(conn, scope, task_row) -> tuple[list[str], BodyContract | None]:
    reasons: list[str] = []
    contract: BodyContract | None = None
    try:
        contract = validate_body(task_row.title, task_row.body)
    except WorkflowError as exc:
        reasons.append(str(exc))
    assignee = normalize_profile_name(task_row.assignee) if task_row.assignee else ""
    human_task = is_human_task(task_row)
    if not assignee:
        reasons.append("assignee is required")
    elif human_task and not assignee.startswith(("human:", "dashboard:")):
        reasons.append("human task assignee must be a human principal")
    elif not human_task and assignee not in _worker_profiles(scope):
        reasons.append(
            f"assignee '{assignee}' is not in the project roster of workers; "
            "the PM cannot own implementation tickets"
        )

    approval_tasks = []
    for parent_id in kanban_db.parent_ids(conn, task_row.id):
        parent = kanban_db.get_task(conn, parent_id)
        if parent is not None and _approval_rank(parent) is not None:
            approval_tasks.append(parent)
    for approval_task in approval_tasks:
        if approval_task.status not in {"done", "archived"}:
            reasons.append(
                f"approval '{approval_task.id}' is {approval_task.status}, not approved"
            )
    for required_rank in _matching_rule_ranks(scope, task_row):
        if not any(
            approval.status == "done" and (_approval_rank(approval) or 0) >= required_rank
            for approval in approval_tasks
        ):
            reasons.append(f"approval rule requires an approved rank-{required_rank} gate")
    reasons.extend(cost.promotion_budget_reasons(scope, conn, task_row))
    return reasons, contract


def finalize_ticket(
    *,
    project_ref: str,
    board_slug: str,
    task_id: str,
    actor_profile: str,
    workspace: str | Path | None = None,
) -> WorkflowResult:
    """Validate and atomically promote a native triage draft."""

    scope = _scope(project_ref, board_slug, workspace)
    actor = _require_pm(scope, actor_profile)
    with kanban_db.connect_closing(board=scope.board_slug) as conn:
        current = _task(conn, scope, task_id)
        if current.status != "triage":
            if current.status in {"todo", "ready", "running", "blocked", "review", "done"}:
                return WorkflowResult(
                    scope.board_slug,
                    scope.project_id,
                    current.id,
                    "finalize",
                    current.status,
                    current.status,
                    idempotent=True,
                )
            raise IllegalTransition(current.id, current.status, "finalize")
        reasons, contract = _finalize_reasons(conn, scope, current)
        if reasons:
            raise FinalizeRefused(current.id, reasons)
        if contract is not None:
            contract = BodyContract(
                contract.acceptance_count,
                contract.evidence_count,
                (*contract.warnings, *_touch_overlap_warnings(conn, scope, current)),
            )
        promoted = kanban_db.specify_triage_task(conn, current.id, author=actor)
        after = _task(conn, scope, current.id)
        if not promoted and after.status == "triage":
            raise WorkflowError(f"task '{current.id}' changed while it was being finalized")
        if not promoted and after.status not in {"todo", "ready"}:
            raise IllegalTransition(after.id, after.status, "finalize")
    return WorkflowResult(
        scope.board_slug,
        scope.project_id,
        current.id,
        "finalize",
        current.status,
        after.status,
        idempotent=not promoted,
        warnings=contract.warnings if contract else (),
    )


def _lifecycle_comment(action: str, actor: str, reason: str) -> str:
    return "\n".join(
        [
            LIFECYCLE_MARKER,
            f"Action: {action}",
            f"Actor: {actor}",
            f"Reason: {' '.join(reason.splitlines())}",
        ]
    )


def _archive(
    *,
    project_ref: str,
    board_slug: str,
    task_id: str,
    actor_profile: str,
    reason: str,
    action: str,
    workspace: str | Path | None = None,
) -> WorkflowResult:
    scope = _scope(project_ref, board_slug, workspace)
    actor = _require_pm(scope, actor_profile)
    clean_reason = _required_text(reason, label=f"{action} reason")
    with kanban_db.connect_closing(board=scope.board_slug) as conn:
        current = _task(conn, scope, task_id)
        if current.status == "archived":
            return WorkflowResult(
                scope.board_slug,
                scope.project_id,
                current.id,
                action,
                "archived",
                "archived",
                idempotent=True,
            )
        if action not in LEGAL_ACTIONS.get(current.status, ()):
            raise IllegalTransition(current.id, current.status, action)
        if current.status == "running":
            if not kanban_db.reclaim_task(conn, current.id, reason=clean_reason):
                latest = _task(conn, scope, current.id)
                if latest.status == "running":
                    raise WorkflowError(f"could not reclaim running task '{current.id}'")
        kanban_db.add_comment(conn, current.id, actor, _lifecycle_comment(action, actor, clean_reason))
        changed = kanban_db.archive_task(conn, current.id)
        after = _task(conn, scope, current.id)
        if not changed and after.status != "archived":
            raise WorkflowError(f"task '{current.id}' changed while it was being archived")
    return WorkflowResult(
        scope.board_slug,
        scope.project_id,
        current.id,
        action,
        current.status,
        after.status,
        idempotent=not changed,
    )


def cancel_ticket(**kwargs) -> WorkflowResult:
    """Cancel work while retaining its native card, comments, links, and runs."""

    return _archive(action="cancel", **kwargs)


def archive_ticket(**kwargs) -> WorkflowResult:
    """Archive a card through the same recoverable native terminal state."""

    return _archive(action="archive", **kwargs)


def _rework_count(body: str | None) -> int:
    matches = re.findall(r"^Rework count:\s*(\d+)\s*$", str(body or ""), re.MULTILINE)
    return max((int(value) for value in matches), default=0)


def reopen_ticket(
    *,
    project_ref: str,
    board_slug: str,
    task_id: str,
    actor_profile: str,
    reason: str,
    workspace: str | Path | None = None,
) -> WorkflowResult:
    """Create an idempotent triage successor and retain the prior card."""

    scope = _scope(project_ref, board_slug, workspace)
    actor = _require_pm(scope, actor_profile)
    clean_reason = _required_text(reason, label="reopen reason")
    with kanban_db.connect_closing(board=scope.board_slug) as conn:
        source = _task(conn, scope, task_id)
        if source.status == "triage":
            return WorkflowResult(
                scope.board_slug,
                scope.project_id,
                source.id,
                "reopen",
                "triage",
                "triage",
                idempotent=True,
                successor_task_id=source.id,
            )
        if "reopen" not in LEGAL_ACTIONS.get(source.status, ()):
            raise IllegalTransition(source.id, source.status, "reopen")
        count = _rework_count(source.body) + 1
        successor_body = (source.body or "").rstrip() + "\n\n" + "\n".join(
            [REWORK_MARKER, f"Rework count: {count}", f"Reason: {' '.join(clean_reason.splitlines())}"]
        )
        parent_ids = tuple(dict.fromkeys([*kanban_db.parent_ids(conn, source.id), source.id]))
        successor_id = kanban_db.create_task(
            conn,
            title=source.title,
            body=successor_body,
            assignee=source.assignee,
            created_by=actor,
            priority=source.priority,
            parents=parent_ids,
            triage=True,
            idempotency_key=f"pmo-reopen:{scope.project_id}:{source.id}",
            max_runtime_seconds=source.max_runtime_seconds,
            skills=source.skills,
            max_retries=source.max_retries,
            model_override=source.model_override,
            provider_override=source.provider_override,
            reasoning_effort=source.reasoning_effort,
            goal_mode=source.goal_mode,
            goal_max_turns=source.goal_max_turns,
            board=scope.board_slug,
            project_id=scope.project_id,
        )
        successor = _task(conn, scope, successor_id)
        already_linked = any(
            REWORK_MARKER in comment.body
            for comment in kanban_db.list_comments(conn, source.id)
        )
        if not already_linked:
            kanban_db.add_comment(
                conn,
                source.id,
                actor,
                _lifecycle_comment("reopen", actor, clean_reason)
                + f"\nSuccessor: {successor.id}\n{REWORK_MARKER}\nRework count: {count}",
            )
            if count >= 3:
                kanban_db.add_comment(
                    conn,
                    successor.id,
                    actor,
                    f"@pm Rework escalation: this specification has been reopened {count} times. "
                    "Review the full predecessor comment trail before finalizing again.",
                )
        archived = source.status == "archived" or kanban_db.archive_task(conn, source.id)
        after_source = _task(conn, scope, source.id)
        if not archived or after_source.status != "archived":
            raise WorkflowError(f"could not retain predecessor '{source.id}' as archived")
        if not already_linked:
            kanban_db.add_comment(
                conn,
                successor.id,
                actor,
                f"{LIFECYCLE_MARKER}\nAction: reopened-from\nPredecessor: {source.id}\n"
                f"Reason: {' '.join(clean_reason.splitlines())}",
            )
    return WorkflowResult(
        scope.board_slug,
        scope.project_id,
        source.id,
        "reopen",
        source.status,
        successor.status,
        idempotent=already_linked,
        successor_task_id=successor.id,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--board", required=True)
    parser.add_argument("--actor", required=True, help="Project PM Hermes profile")
    parser.add_argument("--workspace", default=None)
    sub = parser.add_subparsers(dest="command", required=True)

    finalize = sub.add_parser("finalize")
    finalize.add_argument("task_id")
    for name in ("cancel", "archive", "reopen"):
        command = sub.add_parser(name)
        command.add_argument("task_id")
        command.add_argument("--reason", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    common = {
        "project_ref": args.project,
        "board_slug": args.board,
        "task_id": args.task_id,
        "actor_profile": args.actor,
        "workspace": args.workspace,
    }
    try:
        if args.command == "finalize":
            result = finalize_ticket(**common)
        elif args.command == "cancel":
            result = cancel_ticket(**common, reason=args.reason)
        elif args.command == "archive":
            result = archive_ticket(**common, reason=args.reason)
        else:
            result = reopen_ticket(**common, reason=args.reason)
    except (OSError, PermissionError, RuntimeError, WorkflowError) as exc:
        payload = {"error": str(exc), "type": type(exc).__name__}
        if isinstance(exc, IllegalTransition):
            payload["legal_actions"] = list(exc.legal_actions)
        if isinstance(exc, FinalizeRefused):
            payload["reasons"] = list(exc.reasons)
        print(json.dumps(payload, sort_keys=True), file=sys.stderr)
        return 2
    print(json.dumps(asdict(result), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
