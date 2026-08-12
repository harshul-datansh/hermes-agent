"""Founder-office planning and specialist-consultation records.

Plans and consultations are native Kanban comments on Founder\'s Office
conversation tasks.  The board therefore remains the single audit trail and
there is no second planning database for the dashboard and agents to drift
between.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from agent.redact import redact_sensitive_text
from hermes_cli import kanban_db


PLAN_MARKER = "[pmo:founder-plan:v1]"
CONTEXT_REVIEW_MARKER = "[pmo:founder-context-review:v1]"
CONSULT_MARKER = "[pmo:founder-consult:v1]"
CONSULT_RESPONSE_MARKER = "[pmo:founder-consult-response:v1]"
TICKET_MARKER = "[pmo:founder-plan-ticket:v1]"
DETAIL_REQUEST_MARKER = "[pmo:founder-detail-request:v1]"
DETAIL_RESPONSE_MARKER = "[pmo:founder-detail-response:v1]"
TICKET_CONFIRMATION_MARKER = "[pmo:founder-ticket-confirmation:v1]"
TICKET_DECISION_MARKER = "[pmo:founder-ticket-decision:v1]"
TICKET_CONTEXT_MARKER = "[pmo:founder-ticket-context:v1]"


class PlanningError(ValueError):
    """A planning invariant was not satisfied."""


@dataclass(frozen=True)
class FounderPlan:
    plan_id: str
    thread_id: str
    objective: str
    discussion: str
    author: str


@dataclass(frozen=True)
class DetailRequest:
    request_id: str
    plan_id: str
    questions: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class TicketConfirmation:
    confirmation_id: str
    plan_id: str
    proposal: dict[str, Any]


def _clean(value: Any, *, label: str, limit: int = 16_000) -> str:
    text = redact_sensitive_text(str(value or "").strip(), force=True)
    if not text:
        raise PlanningError(f"{label} is required")
    if len(text) > limit:
        raise PlanningError(f"{label} exceeds {limit} characters")
    return text


def _payload(marker: str, data: dict[str, Any], prose: str) -> str:
    encoded = json.dumps(data, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return f"{marker}\n{encoded}\n\n{prose}"


def _parse(body: str, marker: str) -> dict[str, Any] | None:
    lines = str(body or "").splitlines()
    if len(lines) < 2 or lines[0].strip() != marker:
        return None
    try:
        value = json.loads(lines[1])
    except (TypeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _founder_conversation(scope: Any, thread_id: str):
    from plugins.platforms.pmo.adapter import resolve_conversation

    conversation = resolve_conversation(
        board_slug=scope.board_slug, thread_id=_clean(thread_id, label="thread id", limit=200)
    )
    if conversation.project_id != scope.project_id or conversation.kind not in {
        "founders_office", "global_founders_office"
    }:
        raise PlanningError("planning is only available in this project's Founder's Office")
    return conversation


def record_plan(
    scope: Any,
    *,
    thread_id: str,
    author: str,
    objective: str,
    discussion: str,
) -> FounderPlan:
    """Persist a visible planning discussion before any ticket is created."""

    conversation = _founder_conversation(scope, thread_id)
    require_context_review(scope, thread_id=conversation.thread_id)
    clean_objective = _clean(objective, label="plan objective")
    clean_discussion = _clean(discussion, label="planning discussion")
    clean_author = _clean(author, label="plan author", limit=200)
    data = {
        "schema": "datansh-pmo-founder-plan.v1",
        "thread_id": conversation.thread_id,
        "objective": clean_objective,
        "discussion": clean_discussion,
        "author": clean_author,
    }
    body = _payload(
        PLAN_MARKER,
        data,
        f"Planning: {clean_objective}\n\nDiscussion and rationale:\n{clean_discussion}",
    )
    with kanban_db.connect_closing(board=scope.board_slug) as conn:
        comment_id = kanban_db.add_comment(
            conn, conversation.thread_id, author=clean_author, body=body
        )
    return FounderPlan(str(comment_id), conversation.thread_id, clean_objective, clean_discussion, clean_author)


def record_context_review(
    scope: Any,
    *,
    thread_id: str,
    author: str,
    request: str,
    context: str,
) -> int:
    """Record the project-scoped memory/config/skills context loaded before planning."""

    conversation = _founder_conversation(scope, thread_id)
    data = {
        "schema": "datansh-pmo-founder-context-review.v1",
        "thread_id": conversation.thread_id,
        "request": _clean(request, label="context request", limit=4_000),
        "context": _clean(context, label="project context", limit=16_000),
    }
    with kanban_db.connect_closing(board=scope.board_slug) as conn:
        return kanban_db.add_comment(
            conn,
            conversation.thread_id,
            author=_clean(author, label="context author", limit=200),
            body=_payload(
                CONTEXT_REVIEW_MARKER,
                data,
                "Loaded project skills, configuration, memory/knowledge, decisions, roster, and board context.",
            ),
        )


def require_context_review(scope: Any, *, thread_id: str) -> dict[str, Any]:
    conversation = _founder_conversation(scope, thread_id)
    with kanban_db.connect_closing(board=scope.board_slug) as conn:
        comments = kanban_db.list_comments(conn, conversation.thread_id)
    for comment in reversed(comments):
        data = _parse(comment.body, CONTEXT_REVIEW_MARKER)
        if data and str(data.get("thread_id") or "") == conversation.thread_id:
            return {**data, "comment_id": str(comment.id)}
    raise PlanningError("call pmo_context to load project skills, repository-adjacent memory, and configuration before pmo_plan")


def require_plan(scope: Any, *, thread_id: str, plan_id: str) -> FounderPlan:
    conversation = _founder_conversation(scope, thread_id)
    clean_id = _clean(plan_id, label="plan id", limit=80)
    with kanban_db.connect_closing(board=scope.board_slug) as conn:
        comments = kanban_db.list_comments(conn, conversation.thread_id)
    for comment in comments:
        if str(comment.id) != clean_id:
            continue
        data = _parse(comment.body, PLAN_MARKER)
        if not data or data.get("schema") != "datansh-pmo-founder-plan.v1":
            break
        if str(data.get("thread_id") or "") != conversation.thread_id:
            break
        return FounderPlan(
            clean_id,
            conversation.thread_id,
            _clean(data.get("objective"), label="plan objective"),
            _clean(data.get("discussion"), label="planning discussion"),
            _clean(data.get("author"), label="plan author", limit=200),
        )
    raise PlanningError(
        "a valid Founder\'s Office plan is required before this action; call pmo_plan first"
    )


def record_consultation(
    scope: Any,
    *,
    plan: FounderPlan,
    consultation_thread_id: str,
    author: str,
    handle: str,
    profile: str,
    question: str,
) -> int:
    """Link a specialist sub-chat to its parent plan and persist the question."""

    subchat = _founder_conversation(scope, consultation_thread_id)
    clean_question = _clean(question, label="consultation question")
    data = {
        "schema": "datansh-pmo-founder-consult.v1",
        "plan_id": plan.plan_id,
        "plan_thread_id": plan.thread_id,
        "consultation_thread_id": subchat.thread_id,
        "handle": _clean(handle, label="agent handle", limit=80).lstrip("@"),
        "profile": _clean(profile, label="agent profile", limit=200),
        "question": clean_question,
    }
    # The developer must be able to answer from the same evidence the PM used.
    # A consultation containing only a short question caused specialists to
    # bounce back to @pm for the plan/ticket contract, stalling the mandatory
    # pre-founder gate. Keep the evidence bounded and redacted, but include the
    # plan, the latest project context review, and any referenced ticket bodies.
    review = require_context_review(scope, thread_id=plan.thread_id)
    evidence = [
        f"Plan objective:\n{plan.objective}",
        f"PM repository analysis and proposed sequencing:\n{plan.discussion}",
        "Project skills, configuration, memory, decisions, roster, and board context:\n"
        + str(review.get("context") or "")[:16_000],
    ]
    task_ids = list(dict.fromkeys(re.findall(r"\bt_[a-z0-9]{4,64}\b", clean_question.casefold())))[:5]
    if task_ids:
        with kanban_db.connect_closing(board=scope.board_slug) as conn:
            for task_id in task_ids:
                task = kanban_db.get_task(conn, task_id)
                if task is None or str(task.project_id or "") != str(scope.project_id):
                    continue
                evidence.append(
                    f"Referenced ticket {task.id} ({task.status}, assignee={task.assignee or 'unassigned'}):\n"
                    f"Title: {task.title}\n{task.body[:16_000]}"
                )
    consultation_evidence = redact_sensitive_text("\n\n".join(evidence), force=True)[:40_000]
    request = _payload(
        CONSULT_MARKER,
        data,
        (
            f"@{data['handle']} Specialist consultation for plan {plan.plan_id}.\n\n"
            "Consultation only: inspect and analyse the repository, but do not change files "
            "or implement the work in this sub-chat. Keep this review bounded: do not run "
            "full test suites, production builds, dev servers, or browser walkthroughs; cite "
            "the existing repository and test evidence and return the recommendation promptly.\n\n"
            f"Question:\n{clean_question}\n\n"
            f"Authoritative planning evidence:\n{consultation_evidence}"
        ),
    )
    with kanban_db.connect_closing(board=scope.board_slug) as conn:
        request_id = kanban_db.add_comment(
            conn, subchat.thread_id, author=author, body=request
        )
        kanban_db.add_comment(
            conn,
            plan.thread_id,
            author=author,
            body=_payload(
                CONSULT_MARKER,
                {**data, "request_comment_id": int(request_id)},
                f"Opened specialist sub-chat with @{data['handle']}: {subchat.title}",
            ),
        )
    return int(request_id)


def consultation_request(scope: Any, *, consultation_thread_id: str) -> dict[str, Any] | None:
    _founder_conversation(scope, consultation_thread_id)
    with kanban_db.connect_closing(board=scope.board_slug) as conn:
        comments = kanban_db.list_comments(conn, consultation_thread_id)
    for comment in comments:
        data = _parse(comment.body, CONSULT_MARKER)
        if (
            data
            and data.get("schema") == "datansh-pmo-founder-consult.v1"
            and str(data.get("consultation_thread_id") or "")
            == str(consultation_thread_id)
        ):
            return data
    return None


def consultation_thread_ids(scope: Any) -> frozenset[str]:
    """Return every specialist child thread on a project in one DB pass."""

    from plugins.platforms.pmo.adapter import list_project_conversations

    ids: set[str] = set()
    conversations = [
        item
        for item in list_project_conversations(scope)
        if item.kind in {"founders_office", "global_founders_office"}
    ]
    with kanban_db.connect_closing(board=scope.board_slug) as conn:
        for conversation in conversations:
            for comment in kanban_db.list_comments(conn, conversation.thread_id):
                data = _parse(comment.body, CONSULT_MARKER)
                if (
                    data
                    and data.get("schema") == "datansh-pmo-founder-consult.v1"
                    and str(data.get("consultation_thread_id") or "")
                    == conversation.thread_id
                ):
                    ids.add(conversation.thread_id)
                    break
    return frozenset(ids)


def pending_consultation(
    scope: Any, *, plan: FounderPlan, handle: str
) -> dict[str, Any] | None:
    """Return the newest unanswered consultation for one developer.

    Model retries must not fan out duplicate sub-chats while a specialist is
    still working. A consultation is answered only by a validated response
    marker; transient progress/status messages never satisfy this check.
    """

    clean_handle = str(handle or "").strip().lstrip("@").casefold()
    with kanban_db.connect_closing(board=scope.board_slug) as conn:
        comments = kanban_db.list_comments(conn, plan.thread_id)
    answered = {
        str(data.get("consultation_thread_id") or "")
        for comment in comments
        if (data := _parse(comment.body, CONSULT_RESPONSE_MARKER))
        and str(data.get("plan_id") or "") == plan.plan_id
    }
    for comment in reversed(comments):
        data = _parse(comment.body, CONSULT_MARKER)
        if not data or str(data.get("plan_id") or "") != plan.plan_id:
            continue
        if str(data.get("handle") or "").casefold() != clean_handle:
            continue
        thread_id = str(data.get("consultation_thread_id") or "")
        if thread_id and thread_id not in answered:
            return data
    return None


def consultation_records(scope: Any, *, thread_id: str) -> tuple[dict[str, Any], ...]:
    """Return the consultations owned by one Founder conversation.

    A consultation is stored twice: its full request lives in the child
    conversation and a small link marker lives in the parent.  The dashboard
    needs the latter as a stable nesting boundary so child execution never has
    to be flattened into the parent transcript.
    """

    conversation = _founder_conversation(scope, thread_id)
    with kanban_db.connect_closing(board=scope.board_slug) as conn:
        comments = kanban_db.list_comments(conn, conversation.thread_id)
    responses: dict[str, tuple[Any, dict[str, Any]]] = {}
    for comment in comments:
        data = _parse(comment.body, CONSULT_RESPONSE_MARKER)
        consultation_thread_id = str((data or {}).get("consultation_thread_id") or "")
        if data and consultation_thread_id:
            responses[consultation_thread_id] = (comment, data)
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for comment in comments:
        data = _parse(comment.body, CONSULT_MARKER)
        consultation_thread_id = str((data or {}).get("consultation_thread_id") or "")
        if (
            not data
            or data.get("schema") != "datansh-pmo-founder-consult.v1"
            or str(data.get("plan_thread_id") or "") != conversation.thread_id
            or not consultation_thread_id
            or consultation_thread_id in seen
        ):
            continue
        seen.add(consultation_thread_id)
        response_comment, response = responses.get(consultation_thread_id, (None, {}))
        records.append(
            {
                **data,
                "delegation_comment_id": int(comment.id),
                "created_at": int(comment.created_at),
                "response_comment_id": response.get("response_comment_id"),
                "response_marker_comment_id": (
                    int(response_comment.id) if response_comment is not None else None
                ),
                "completed_at": (
                    int(response_comment.created_at) if response_comment is not None else None
                ),
            }
        )
    return tuple(records)


def latest_plan(scope: Any, *, thread_id: str) -> FounderPlan | None:
    """Return the newest durable plan for a Founder conversation, if any."""

    conversation = _founder_conversation(scope, thread_id)
    with kanban_db.connect_closing(board=scope.board_slug) as conn:
        comments = kanban_db.list_comments(conn, conversation.thread_id)
    for comment in reversed(comments):
        data = _parse(comment.body, PLAN_MARKER)
        if not data or str(data.get("thread_id") or "") != conversation.thread_id:
            continue
        return FounderPlan(
            str(comment.id),
            conversation.thread_id,
            _clean(data.get("objective"), label="plan objective"),
            _clean(data.get("discussion"), label="planning discussion"),
            _clean(data.get("author"), label="plan author", limit=200),
        )
    return None


def pending_latest_consultations(
    scope: Any, *, thread_id: str
) -> tuple[dict[str, Any], ...]:
    """Return unanswered specialist work for the conversation's newest plan."""

    plan = latest_plan(scope, thread_id=thread_id)
    if plan is None:
        return ()
    return tuple(
        item
        for item in consultation_records(scope, thread_id=thread_id)
        if str(item.get("plan_id") or "") == plan.plan_id
        and not item.get("response_comment_id")
    )


def record_consultation_response(
    scope: Any,
    *,
    consultation_thread_id: str,
    author: str,
    response_comment_id: int,
) -> int | bool:
    """Mark a specialist response on the parent plan's visible discussion."""

    request = consultation_request(scope, consultation_thread_id=consultation_thread_id)
    if not request:
        return False
    with kanban_db.connect_closing(board=scope.board_slug) as conn:
        response = _comment_by_id(
            kanban_db.list_comments(conn, consultation_thread_id),
            str(response_comment_id),
        )
    if response is None:
        return False
    expected_author = f"agent:{str(request.get('profile') or '').strip()}".casefold()
    actual_author = str(response.author or "").strip().casefold()
    response_body = str(response.body or "").strip()
    operational_notice = any(
        needle in response_body.casefold()
        for needle in (
            "auto-compaction was raised",
            "opt back out: hermes config",
            "gateway shutting down",
            "current task will be interrupted",
            "working —",
            "working -",
            "receiving stream response",
            "waiting for non-streaming api response",
        )
    )
    if (
        actual_author != expected_author
        # Concise specialist answers (project names, version checks, yes/no
        # findings) are valid evidence. Keep only a small anti-noise floor;
        # operational progress notices are rejected explicitly above.
        or len(response_body) < 20
        or operational_notice
        or response_body.startswith("[pmo:")
    ):
        return False
    parent_thread = str(request.get("plan_thread_id") or "")
    plan_id = str(request.get("plan_id") or "")
    require_plan(scope, thread_id=parent_thread, plan_id=plan_id)
    marker_data = {
        "schema": "datansh-pmo-founder-consult-response.v1",
        "plan_id": plan_id,
        "plan_thread_id": parent_thread,
        "consultation_thread_id": consultation_thread_id,
        "handle": request.get("handle"),
        "profile": request.get("profile"),
        "response_comment_id": int(response_comment_id),
    }
    with kanban_db.connect_closing(board=scope.board_slug) as conn:
        for comment in kanban_db.list_comments(conn, parent_thread):
            existing = _parse(comment.body, CONSULT_RESPONSE_MARKER)
            if existing and existing == marker_data:
                return False
        routed_response = redact_sensitive_text(response_body[:8_000], force=True)
        marker_id = kanban_db.add_comment(
            conn,
            parent_thread,
            author=author,
            body=_payload(
                CONSULT_RESPONSE_MARKER,
                marker_data,
                (
                    f"@{request.get('handle')} replied in the specialist sub-chat.\n\n"
                    "Specialist recommendation:\n"
                    f"{routed_response}"
                ),
            ),
        )
    return int(marker_id)


def require_consultation_response(scope: Any, *, plan: FounderPlan) -> dict[str, Any]:
    with kanban_db.connect_closing(board=scope.board_slug) as conn:
        comments = kanban_db.list_comments(conn, plan.thread_id)
    for comment in reversed(comments):
        data = _parse(comment.body, CONSULT_RESPONSE_MARKER)
        if data and str(data.get("plan_id") or "") == plan.plan_id:
            return data
    raise PlanningError(
        "ask a project developer with pmo_consult and wait for its sub-chat response before asking a human"
    )


def _is_human_author(author: str) -> bool:
    value = str(author or "").strip().casefold()
    return bool(value) and not value.startswith(("agent:", "system", "pmo:"))


def _comment_by_id(comments: list[Any], comment_id: str) -> Any | None:
    return next((comment for comment in comments if str(comment.id) == str(comment_id)), None)


def _clean_items(value: Any, *, label: str, required: bool = False, limit: int = 20) -> list[str]:
    items = [value] if isinstance(value, str) else list(value or [])
    cleaned = [_clean(item, label=label, limit=2_000) for item in items]
    if required and not cleaned:
        raise PlanningError(f"at least one {label} is required")
    if len(cleaned) > limit:
        raise PlanningError(f"no more than {limit} {label} entries are allowed")
    return cleaned


def record_detail_request(
    scope: Any,
    *,
    plan: FounderPlan,
    author: str,
    questions: Any,
) -> DetailRequest:
    """Ask the founder 1–3 Codex-style questions after developer consultation."""

    require_consultation_response(scope, plan=plan)
    raw_questions = list(questions or [])
    if not 1 <= len(raw_questions) <= 3:
        raise PlanningError("ask between 1 and 3 ticket-detail questions")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_questions, start=1):
        if not isinstance(raw, dict):
            raise PlanningError("each ticket-detail question must be an object")
        question_id = _clean(raw.get("id") or f"question_{index}", label="question id", limit=60)
        if question_id in seen:
            raise PlanningError(f"duplicate question id: {question_id}")
        seen.add(question_id)
        raw_options = list(raw.get("options") or [])
        if not 2 <= len(raw_options) <= 3:
            raise PlanningError("each question must have 2 or 3 choices; the UI adds Other automatically")
        options: list[dict[str, str]] = []
        for option_index, raw_option in enumerate(raw_options, start=1):
            if not isinstance(raw_option, dict):
                raise PlanningError("each ticket-detail option must be an object")
            options.append({
                "id": _clean(raw_option.get("id") or f"option_{option_index}", label="option id", limit=60),
                "label": _clean(raw_option.get("label"), label="option label", limit=100),
                "description": _clean(raw_option.get("description"), label="option description", limit=500),
            })
        normalized.append({
            "id": question_id,
            "question": _clean(raw.get("question"), label="question", limit=500),
            "options": options,
        })
    data = {
        "schema": "datansh-pmo-founder-detail-request.v1",
        "plan_id": plan.plan_id,
        "questions": normalized,
    }
    prose = "I need these details before I can propose a ticket for approval."
    with kanban_db.connect_closing(board=scope.board_slug) as conn:
        request_id = kanban_db.add_comment(
            conn, plan.thread_id, author=_clean(author, label="request author", limit=200),
            body=_payload(DETAIL_REQUEST_MARKER, data, prose),
        )
    return DetailRequest(str(request_id), plan.plan_id, tuple(normalized))


def require_detail_response(scope: Any, *, plan: FounderPlan) -> dict[str, Any]:
    """Return the newest valid human answer to the newest detail request."""

    require_consultation_response(scope, plan=plan)
    with kanban_db.connect_closing(board=scope.board_slug) as conn:
        comments = kanban_db.list_comments(conn, plan.thread_id)
    requests = [
        (comment, _parse(comment.body, DETAIL_REQUEST_MARKER)) for comment in comments
    ]
    requests = [
        (comment, data) for comment, data in requests
        if data and str(data.get("plan_id") or "") == plan.plan_id
    ]
    if not requests:
        raise PlanningError("call pmo_request_details after developer consultation")
    request, request_data = requests[-1]
    for comment in reversed(comments):
        data = _parse(comment.body, DETAIL_RESPONSE_MARKER)
        if not data or str(data.get("plan_id") or "") != plan.plan_id:
            continue
        if str(data.get("request_id") or "") != str(request.id):
            continue
        if not _is_human_author(comment.author):
            continue
        answers = data.get("answers")
        if not isinstance(answers, list) or len(answers) != len(request_data.get("questions") or []):
            continue
        return {**data, "comment_id": str(comment.id), "author": comment.author}
    raise PlanningError("wait for the founder to answer the structured ticket-detail questions")


def _proposal(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PlanningError("ticket proposal is required")
    assignee = _clean(value.get("assignee"), label="ticket assignee", limit=100).lstrip("@").lower()
    human_steps = _clean_items(value.get("human_steps"), label="human hand-off step")
    if assignee.startswith(("human:", "dashboard:")) and not human_steps:
        raise PlanningError("human-owned tickets require at least one human hand-off step")
    return {
        "title": _clean(value.get("title"), label="ticket title", limit=300),
        "product_context": _clean(
            value.get("product_context"), label="product context", limit=4_000
        ),
        "user_impact": _clean(
            value.get("user_impact"), label="user impact", limit=4_000
        ),
        "outcome": _clean(value.get("outcome"), label="ticket outcome", limit=4_000),
        "assignee": assignee,
        "acceptance_criteria": _clean_items(value.get("acceptance_criteria"), label="acceptance criterion", required=True),
        "evidence": _clean_items(value.get("evidence"), label="evidence requirement", required=True),
        "constraints": _clean_items(value.get("constraints"), label="constraint"),
        "dependencies": _clean_items(value.get("dependencies"), label="dependency"),
        "parent_task_ids": _clean_items(value.get("parent_task_ids"), label="parent task id"),
        "labels": _clean_items(value.get("labels"), label="label"),
        "touches": _clean_items(value.get("touches"), label="touch path"),
        "estimate_hours": float(value["estimate_hours"]) if value.get("estimate_hours") is not None else None,
        "priority": int(value.get("priority") or 0),
        "context_summary": _clean(value.get("context_summary"), label="context summary", limit=8_000),
        "human_steps": human_steps,
    }


def record_ticket_confirmation(
    scope: Any,
    *,
    plan: FounderPlan,
    author: str,
    proposal: dict[str, Any],
) -> TicketConfirmation:
    """Post the exact ticket proposal the founder must approve."""

    consultation = require_consultation_response(scope, plan=plan)
    detail_response = require_detail_response(scope, plan=plan)
    clean_proposal = _proposal(proposal)
    configured_labels = {
        str(label).strip()
        for label in getattr(getattr(scope.config, "board", None), "labels", ())
        if str(label).strip()
    }
    unknown_labels = sorted(set(clean_proposal["labels"]) - configured_labels)
    if unknown_labels:
        legal = ", ".join(sorted(configured_labels)) or "(none configured)"
        raise PlanningError(
            f"unknown ticket label(s): {', '.join(unknown_labels)}; legal labels: {legal}. "
            "Revise the proposal before asking the founder for approval."
        )
    data = {
        "schema": "datansh-pmo-founder-ticket-confirmation.v1",
        "plan_id": plan.plan_id,
        "consultation_response_id": str(consultation.get("response_comment_id") or ""),
        "detail_response_id": str(detail_response.get("comment_id") or ""),
        "proposal": clean_proposal,
    }
    prose = (
        f"Ticket approval requested: {clean_proposal['title']}\n\n"
        f"Owner: @{clean_proposal['assignee']}\n\n"
        f"Product context: {clean_proposal['product_context']}\n\n"
        f"User impact: {clean_proposal['user_impact']}\n\n"
        f"Outcome: {clean_proposal['outcome']}"
    )
    with kanban_db.connect_closing(board=scope.board_slug) as conn:
        confirmation_id = kanban_db.add_comment(
            conn, plan.thread_id, author=_clean(author, label="confirmation author", limit=200),
            body=_payload(TICKET_CONFIRMATION_MARKER, data, prose),
        )
    return TicketConfirmation(str(confirmation_id), plan.plan_id, clean_proposal)


def require_ticket_approval(
    scope: Any,
    *,
    plan: FounderPlan,
    confirmation_id: str,
) -> TicketConfirmation:
    """Require an explicit human approval of the exact current proposal."""

    require_detail_response(scope, plan=plan)
    clean_id = _clean(confirmation_id, label="confirmation id", limit=80)
    with kanban_db.connect_closing(board=scope.board_slug) as conn:
        comments = kanban_db.list_comments(conn, plan.thread_id)
    confirmation_comment = _comment_by_id(comments, clean_id)
    data = _parse(confirmation_comment.body, TICKET_CONFIRMATION_MARKER) if confirmation_comment else None
    if not data or str(data.get("plan_id") or "") != plan.plan_id:
        raise PlanningError("a valid ticket confirmation from pmo_request_ticket_confirmation is required")
    for comment in reversed(comments):
        decision = _parse(comment.body, TICKET_DECISION_MARKER)
        if not decision or str(decision.get("plan_id") or "") != plan.plan_id:
            continue
        if str(decision.get("confirmation_id") or "") != clean_id or not _is_human_author(comment.author):
            continue
        choice = str(decision.get("decision") or "").strip().casefold()
        if choice != "approved":
            feedback = str(decision.get("feedback") or "").strip()
            raise PlanningError(
                "the founder requested ticket changes; revise the proposal and request confirmation again"
                + (f": {feedback}" if feedback else "")
            )
        return TicketConfirmation(clean_id, plan.plan_id, _proposal(data.get("proposal")))
    raise PlanningError("wait for the founder to approve the proposed ticket")


def ticket_context(scope: Any, *, plan: FounderPlan, confirmation: TicketConfirmation) -> dict[str, Any]:
    """Build the auditable context bundle attached to the approved ticket."""

    detail = require_detail_response(scope, plan=plan)
    with kanban_db.connect_closing(board=scope.board_slug) as conn:
        parent_comments = kanban_db.list_comments(conn, plan.thread_id)
    consultation = next(
        (
            data for comment in reversed(parent_comments)
            if (data := _parse(comment.body, CONSULT_RESPONSE_MARKER))
            and str(data.get("plan_id") or "") == plan.plan_id
        ),
        {},
    )
    specialist_body = ""
    consultation_thread = str(consultation.get("consultation_thread_id") or "")
    response_id = str(consultation.get("response_comment_id") or "")
    if consultation_thread and response_id:
        with kanban_db.connect_closing(board=scope.board_slug) as conn:
            reply = _comment_by_id(kanban_db.list_comments(conn, consultation_thread), response_id)
        specialist_body = redact_sensitive_text(str(getattr(reply, "body", "") or "")[:8_000], force=True)
    return {
        "schema": "datansh-pmo-founder-ticket-context.v1",
        "plan_id": plan.plan_id,
        "confirmation_id": confirmation.confirmation_id,
        "objective": plan.objective,
        "planning_discussion": plan.discussion,
        "specialist": {
            "handle": consultation.get("handle"),
            "profile": consultation.get("profile"),
            "response": specialist_body,
        },
        "founder_answers": detail.get("answers"),
        "context_summary": confirmation.proposal.get("context_summary"),
    }


def record_ticket(scope: Any, *, plan: FounderPlan, author: str, task_id: str, title: str) -> None:
    with kanban_db.connect_closing(board=scope.board_slug) as conn:
        kanban_db.add_comment(
            conn,
            plan.thread_id,
            author=author,
            body=_payload(
                TICKET_MARKER,
                {
                    "schema": "datansh-pmo-founder-plan-ticket.v1",
                    "plan_id": plan.plan_id,
                    "task_id": task_id,
                },
                f"Created delegated ticket {task_id}: {title}",
            ),
        )


def attach_ticket_context(
    scope: Any,
    *,
    task_id: str,
    author: str,
    context: dict[str, Any],
) -> int:
    """Attach the approved plan, consultation, and founder answers to a ticket."""

    with kanban_db.connect_closing(board=scope.board_slug) as conn:
        return kanban_db.add_comment(
            conn,
            _clean(task_id, label="task id", limit=100),
            author=_clean(author, label="context author", limit=200),
            body=_payload(
                TICKET_CONTEXT_MARKER,
                context,
                "Founder-approved delivery context is attached to this ticket.",
            ),
        )


__all__ = [
    "CONSULT_MARKER",
    "CONSULT_RESPONSE_MARKER",
    "CONTEXT_REVIEW_MARKER",
    "DETAIL_REQUEST_MARKER",
    "DETAIL_RESPONSE_MARKER",
    "DetailRequest",
    "FounderPlan",
    "PLAN_MARKER",
    "PlanningError",
    "TICKET_CONFIRMATION_MARKER",
    "TICKET_CONTEXT_MARKER",
    "TICKET_DECISION_MARKER",
    "TicketConfirmation",
    "attach_ticket_context",
    "consultation_records",
    "consultation_thread_ids",
    "latest_plan",
    "pending_latest_consultations",
    "record_consultation",
    "pending_consultation",
    "record_context_review",
    "record_consultation_response",
    "record_plan",
    "record_detail_request",
    "record_ticket_confirmation",
    "record_ticket",
    "require_consultation_response",
    "require_context_review",
    "require_detail_response",
    "require_plan",
    "require_ticket_approval",
    "ticket_context",
]
