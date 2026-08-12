"""Agent-to-agent collaboration on the board.

Requirement #7 makes task comments the *only* channel between agents. That
gives them a way to talk, but talking is not collaborating: an agent also
needs to hand work over, pull in a specialist, or escalate to a named human —
and each of those is a state change, not just a message.

This module gives those moves one shape:

* every action writes an ``@`` comment, so the board stays the single audit
  trail and nothing happens off-channel;
* actions that change ownership also move ``tasks.assignee``, so the board
  reflects who actually holds the work;
* recipients are resolved against the project roster, so an agent cannot
  hand work to a handle that does not exist.

It adds no message store, no scheduler and no second dispatcher — the comment
is the message and Kanban is the state.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Iterable, Sequence

from hermes_cli import kanban_db

from plugins.pmo import mentions
from plugins.pmo.project_scope import ProjectScope


class CollaborationError(ValueError):
    """Refused before anything was written."""


@dataclass(frozen=True)
class CollaborationResult:
    action: str
    task_id: str
    comment_id: int
    handles: tuple[str, ...]
    notification_ids: tuple[int, ...]
    previous_assignee: str | None = None
    new_assignee: str | None = None


QUESTION_MARKER = "[pmo:collaboration-question:v1]"
ANSWER_MARKER = "[pmo:collaboration-answer:v1]"


@dataclass(frozen=True)
class PendingQuestion:
    question_id: str
    task_id: str
    question_comment_id: int
    requester_author: str
    requester_handle: str
    requester_profile: str | None
    target_handles: tuple[str, ...]
    original_assignee: str | None
    question: str


# Keywords that route a decomposed child to a specialist. Ordered: the first
# role whose keywords match wins, so "write tests for the deploy script" goes
# to qa rather than ops.
ROLE_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("qa", ("test", "tests", "testing", "verify", "verification", "qa",
            "regression", "coverage", "assert")),
    ("ops", ("deploy", "deployment", "infra", "infrastructure", "release",
             "rollout", "monitor", "monitoring", "alert", "runbook",
             "provision", "pipeline", "ci/cd")),
    ("design", ("design", "ux", "ui", "mockup", "wireframe", "figma",
                "accessibility", "a11y")),
    ("data", ("analytics", "metric", "metrics", "dashboard", "report",
              "query", "sql", "etl")),
    ("research", ("research", "investigate", "spike", "evaluate", "compare",
                  "benchmark")),
)


def _roster_by_handle(scope: ProjectScope) -> dict[str, object]:
    return {item.handle: item for item in mentions.roster(scope)}


def _agent_profiles(scope: ProjectScope) -> dict[str, str]:
    """``handle -> profile`` for agents that can actually be dispatched."""
    return {
        item.handle: item.profile
        for item in mentions.roster(scope)
        if item.kind == "agent" and item.profile
    }


def route_assignee(scope: ProjectScope, title: str, body: str = "") -> str | None:
    """Pick the project agent best suited to a piece of work.

    Upstream's decomposer resolves assignees against *every installed Hermes
    profile* and falls back to the global ``kanban.default_assignee`` — which
    in a PM-OS install is unset, so children land on the literal profile
    ``default``. That profile is not in any project roster, so the dispatcher
    can never route the work and the ticket sits unclaimed. This re-routes
    within the project instead.
    """
    agents = _agent_profiles(scope)
    if not agents:
        return None
    # Match the **title only**. Bodies carry acceptance criteria and evidence
    # requirements that mention testing, dashboards and design in passing, so
    # scanning them sent "Renew observability vendor contract" to @design.
    # The title is the work; the body is detail about the work.
    haystack = str(title or "").casefold()
    for role, keywords in ROLE_KEYWORDS:
        if role not in agents:
            continue
        if any(word in haystack for word in keywords):
            return agents[role]

    configured = str(getattr(scope.config.board, "default_assignee", "") or "").strip()
    if configured and configured in set(agents.values()):
        return configured
    # Prefer an engineer, then any agent that is not the orchestrator.
    for handle in sorted(agents):
        if handle.startswith("dev"):
            return agents[handle]
    orchestrator = scope.config.orchestrator.profile
    for handle in sorted(agents):
        if agents[handle] != orchestrator:
            return agents[handle]
    return None


def reroute_unassigned_children(
    scope: ProjectScope, child_ids: Iterable[str], *, actor: str
) -> list[dict]:
    """Give decomposed children a real project agent.

    Called right after ``decompose_task``. Only touches children whose
    assignee is outside the project roster, so a deliberate assignment made
    by the orchestrator is never overwritten.
    """
    roster_profiles = set(_agent_profiles(scope).values())
    roster_profiles.add(scope.config.orchestrator.profile)
    changes: list[dict] = []
    with kanban_db.connect_closing(board=scope.board_slug) as conn:
        for task_id in child_ids:
            task = kanban_db.get_task(conn, task_id)
            if task is None:
                continue
            change: dict = {"task_id": task_id}

            # An unbound child is invisible to every project-scoped surface:
            # skipped by the portfolio rollup, rejected by mention routing with
            # ScopeViolation, unreachable from collaboration. ``create_task``
            # inherits ``project_id`` from the board's metadata whenever the
            # board is pinned, so this should not happen through the PM-OS
            # decompose endpoint — it did happen via the gateway's
            # auto-decompose sweep, which is now disabled. Report rather than
            # silently patch: quietly rewriting the column would hide a
            # recurrence of the same orphaning bug.
            if not str(task.project_id or "").strip():
                change["unbound_project"] = True

            current = str(task.assignee or "").strip()
            if (
                current not in roster_profiles
                and not current.startswith(("human:", "dashboard:"))
            ):
                target = route_assignee(scope, task.title or "", task.body or "")
                if target and target != current:
                    if kanban_db.assign_task(conn, task_id, target):
                        change["from"] = current or None
                        change["to"] = target
            if len(change) > 1:
                changes.append(change)
    return changes


def _post(
    scope: ProjectScope, *, task_id: str, author: str, body: str
) -> mentions.PostResult:
    try:
        return mentions.post_comment(
            scope, task_id=task_id, author=author, body=body
        )
    except mentions.UnknownMention as exc:
        raise CollaborationError(
            "No such handle in this project: "
            + ", ".join("@" + h for h in exc.unknown)
            + ". Valid handles: "
            + ", ".join("@" + h for h in exc.valid_handles)
        ) from exc


def _marker(marker: str, payload: dict[str, object]) -> str:
    return marker + "\n" + json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _marker_payload(body: str, marker: str) -> dict[str, object] | None:
    first, separator, rest = str(body or "").partition("\n")
    if first.strip() != marker or not separator:
        return None
    try:
        value = json.loads(rest)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _recipient_for_identity(scope: ProjectScope, identity: str):
    wanted = str(identity or "").strip().casefold()
    for recipient in mentions.roster(scope):
        identities = {
            recipient.handle.casefold(),
            str(recipient.profile or "").casefold(),
            str(recipient.principal or "").casefold(),
        }
        if wanted and wanted in identities:
            return recipient
    return None


def pending_questions(
    scope: ProjectScope, *, task_id: str, target_identity: str | None = None
) -> tuple[PendingQuestion, ...]:
    """Return unresolved, project-scoped questions on one ticket."""

    with kanban_db.connect_closing(board=scope.board_slug) as conn:
        task = kanban_db.get_task(conn, task_id)
        if task is None or task.project_id != scope.project_id:
            raise CollaborationError(
                f"task {task_id} not found in project {scope.project_id}"
            )
        rows = kanban_db.list_comments(conn, task_id)

    resolved: set[str] = set()
    questions: list[PendingQuestion] = []
    for comment in rows:
        if data := _marker_payload(comment.body, ANSWER_MARKER):
            resolved.add(str(data.get("question_id") or ""))
            continue
        data = _marker_payload(comment.body, QUESTION_MARKER)
        if not data:
            continue
        question_id = str(data.get("question_id") or "")
        if not question_id:
            continue
        questions.append(
            PendingQuestion(
                question_id=question_id,
                task_id=task_id,
                question_comment_id=int(data.get("question_comment_id") or 0),
                requester_author=str(data.get("requester_author") or ""),
                requester_handle=str(data.get("requester_handle") or ""),
                requester_profile=(
                    str(data.get("requester_profile"))
                    if data.get("requester_profile")
                    else None
                ),
                target_handles=tuple(
                    str(item) for item in (data.get("target_handles") or [])
                ),
                original_assignee=(
                    str(data.get("original_assignee"))
                    if data.get("original_assignee")
                    else None
                ),
                question=str(data.get("question") or ""),
            )
        )

    target = _recipient_for_identity(scope, target_identity or "")
    target_handle = target.handle if target is not None else str(target_identity or "")
    return tuple(
        question
        for question in questions
        if question.question_id not in resolved
        and (not target_identity or target_handle in question.target_handles)
    )


def request_info(
    scope: ProjectScope, *, task_id: str, author: str, handles: Sequence[str],
    question: str, pause: bool = False,
) -> CollaborationResult:
    """Ask for a fact, pause the ticket, and preserve worker ownership."""
    if not question.strip():
        raise CollaborationError("a question is required")
    targets = _normalize(handles)
    recipients, unknown = mentions.resolve(scope, targets)
    if unknown:
        raise CollaborationError(
            "No such handle in this project: "
            + ", ".join("@" + handle for handle in unknown)
        )
    requester = _recipient_for_identity(scope, author)
    if requester is None:
        raise CollaborationError(
            f"question author {author!r} is not a member of this project"
        )
    unresolved = pending_questions(scope, task_id=task_id)
    if any(
        item.requester_handle == requester.handle
        or (
            requester.profile
            and item.requester_profile == requester.profile
        )
        for item in unresolved
    ):
        raise CollaborationError(
            "your earlier question is still pending on this ticket; wait for "
            "the answer instead of posting it again"
        )
    if pending_questions(scope, task_id=task_id, target_identity=author):
        raise CollaborationError(
            "you are the recipient of a pending question on this ticket; "
            "answer it with pmo_answer instead of asking it again"
        )
    with kanban_db.connect_closing(board=scope.board_slug) as conn:
        task = kanban_db.get_task(conn, task_id)
        if task is None or task.project_id != scope.project_id:
            raise CollaborationError(
                f"task {task_id} not found in project {scope.project_id}"
            )
        if task.status in {"done", "archived", "cancelled"}:
            raise CollaborationError(f"cannot ask from a {task.status} ticket")
        previous = str(task.assignee or "") or None
    body = " ".join(f"@{h}" for h in targets) + f" Question: {question.strip()}"
    result = _post(scope, task_id=task_id, author=author, body=body)
    question_id = f"q_{result.comment_id}"
    with kanban_db.connect_closing(board=scope.board_slug) as conn:
        kanban_db.add_comment(
            conn,
            task_id,
            "pmo-system",
            _marker(
                QUESTION_MARKER,
                {
                    "question_id": question_id,
                    "question_comment_id": result.comment_id,
                    "requester_author": author,
                    "requester_handle": requester.handle,
                    "requester_profile": requester.profile,
                    "target_handles": [recipient.handle for recipient in recipients],
                    "original_assignee": previous,
                    "question": question.strip(),
                },
            ),
        )
        task = kanban_db.get_task(conn, task_id)
        if pause and task is not None and task.status in {"running", "ready"}:
            if not kanban_db.block_task(
                conn,
                task_id,
                reason=f"waiting for answer to {question_id}",
                kind="needs_input",
            ):
                raise CollaborationError("could not pause the ticket for an answer")
    return CollaborationResult(
        "request_info", task_id, result.comment_id,
        tuple(result.handles), tuple(result.notification_ids),
        previous_assignee=previous, new_assignee=previous,
    )


def answer_info(
    scope: ProjectScope,
    *,
    task_id: str,
    author: str,
    answer: str,
    question_id: str | None = None,
) -> CollaborationResult:
    """Answer one pending question and resume its original project worker."""

    clean_answer = str(answer or "").strip()
    if not clean_answer:
        raise CollaborationError("an answer is required")
    answerer = _recipient_for_identity(scope, author)
    if answerer is None:
        raise CollaborationError(f"answer author {author!r} is not in this project")
    candidates = list(
        pending_questions(scope, task_id=task_id, target_identity=author)
    )
    if question_id:
        candidates = [item for item in candidates if item.question_id == question_id]
    if not candidates:
        raise CollaborationError("no pending question for you exists on this ticket")
    if len(candidates) > 1 and not question_id:
        raise CollaborationError(
            "more than one question is pending; pass question_id explicitly"
        )
    pending = candidates[0]
    normalized_answer = " ".join(clean_answer.casefold().split()).removeprefix(
        "question: "
    )
    normalized_question = " ".join(pending.question.casefold().split())
    if normalized_answer == normalized_question:
        raise CollaborationError(
            "the response repeats the question; provide the requested answer"
        )

    result = _post(
        scope,
        task_id=task_id,
        author=author,
        body=f"@{pending.requester_handle} Answer: {clean_answer}",
    )
    with kanban_db.connect_closing(board=scope.board_slug) as conn:
        kanban_db.add_comment(
            conn,
            task_id,
            "pmo-system",
            _marker(
                ANSWER_MARKER,
                {
                    "question_id": pending.question_id,
                    "answer_comment_id": result.comment_id,
                    "answerer_author": author,
                    "answerer_handle": answerer.handle,
                    "answer": clean_answer,
                },
            ),
        )
        task = kanban_db.get_task(conn, task_id)
        if task is None:
            raise CollaborationError(f"task {task_id} no longer exists")
        if pending.original_assignee and task.assignee != pending.original_assignee:
            if not kanban_db.reassign_task(
                conn,
                task_id,
                pending.original_assignee,
                reclaim_first=False,
                reason=f"answer received for {pending.question_id}",
            ):
                raise CollaborationError("could not restore the requesting worker")
        if task.status == "blocked" and not kanban_db.unblock_task(conn, task_id):
            raise CollaborationError("the answer landed but the ticket could not resume")
    for notification_id, handle in zip(result.notification_ids, result.handles):
        mentions.mark_notification_delivered(
            scope,
            task_id=task_id,
            notification_id=notification_id,
            recipient=handle,
        )
    return CollaborationResult(
        "answer_info",
        task_id,
        result.comment_id,
        tuple(result.handles),
        tuple(result.notification_ids),
        previous_assignee=pending.original_assignee,
        new_assignee=pending.original_assignee,
    )


def transfer_task(
    scope: ProjectScope, *, task_id: str, author: str, to_handle: str, reason: str,
) -> CollaborationResult:
    """Hand a ticket to another agent, moving ownership with the message.

    A comment alone would leave ``tasks.assignee`` pointing at the previous
    agent, so the dispatcher would keep giving the work back to whoever just
    handed it away.
    """
    if not reason.strip():
        raise CollaborationError("a reason is required so the board explains itself")
    handle = _normalize([to_handle])[0]
    entry = _roster_by_handle(scope).get(handle)
    if entry is None:
        raise CollaborationError(
            f"unknown handle @{handle}; valid: "
            + ", ".join("@" + h for h in sorted(_roster_by_handle(scope)))
        )
    if entry.kind != "agent" or not entry.profile:
        raise CollaborationError(
            f"@{handle} is not a dispatchable agent; use escalate_to_human instead"
        )

    with kanban_db.connect_closing(board=scope.board_slug) as conn:
        task = kanban_db.get_task(conn, task_id)
        if task is None:
            raise CollaborationError(f"task {task_id} not found on {scope.board_slug}")
        previous = str(task.assignee or "") or None

    body = (
        f"@{handle} Transferring this ticket to you. Reason: {reason.strip()}"
    )
    result = _post(scope, task_id=task_id, author=author, body=body)

    with kanban_db.connect_closing(board=scope.board_slug) as conn:
        # Block, then reassign, then unblock — deliberately the same shape as
        # ``escalate_to_human`` below, because that is the sequence the
        # dispatcher demonstrably tolerates.
        #
        # The obvious implementation, ``reassign_task(reclaim_first=True)``,
        # looks right and fails in production. A handoff happens *while* the
        # sending agent holds the claim, so reclaiming pulls the claim out
        # from under a process that is still alive; the dispatcher then sees a
        # live pid that no longer owns its task, records ``crashed``, respawns
        # the *same* agent, and after three rounds gives up with the ticket
        # blocked and still owned by the sender. The handoff comment is on the
        # board the whole time, so it reads as though the transfer worked.
        #
        # ``block_task`` releases the claim the way the dispatcher expects, so
        # the sending worker ends its run cleanly. Unblocking last means the
        # ticket only becomes claimable once ownership has already moved.
        transition_reason = f"transferred to @{handle}: {reason.strip()[:200]}"
        try:
            kanban_db.block_task(
                conn, task_id, reason=transition_reason[:500], kind="handoff",
            )
        except Exception:  # noqa: BLE001 - not all states are blockable
            pass
        # ``reclaim_first=False`` is load-bearing. ``reclaim_task`` calls
        # ``_terminate_reclaimed_worker`` — it kills the process holding the
        # claim, which here is *the agent making this call*. Reclaiming was
        # therefore suicide: the worker died inside the tool call, before the
        # reassign ran, so ownership never moved and the dispatcher recorded a
        # crash. Blocking above already left the task non-running, so there is
        # nothing to reclaim and ``assign_task`` will accept it.
        if not kanban_db.reassign_task(
            conn, task_id, entry.profile,
            reclaim_first=False,
            reason=transition_reason,
        ):
            raise CollaborationError(
                f"could not reassign {task_id} to {entry.profile}"
            )
        # Back into the dispatchable pool, now owned by the receiving agent.
        try:
            kanban_db.unblock_task(conn, task_id)
        except Exception:  # noqa: BLE001 - the reassign is the durable part
            pass
    return CollaborationResult(
        "transfer_task", task_id, result.comment_id,
        tuple(result.handles), tuple(result.notification_ids),
        previous_assignee=previous, new_assignee=entry.profile,
    )


def escalate_to_human(
    scope: ProjectScope, *, task_id: str, author: str, handles: Sequence[str],
    reason: str, block: bool = True,
) -> CollaborationResult:
    """Hand a ticket to named humans and stop agent work on it.

    Requirement #6: when an agent cannot resolve something it goes to a human.
    Blocking matters — leaving the ticket dispatchable means an agent picks it
    straight back up and loops on the thing it could not do.
    """
    if not reason.strip():
        raise CollaborationError("a reason is required")
    targets = _normalize(handles)
    roster = _roster_by_handle(scope)
    humans = []
    for handle in targets:
        entry = roster.get(handle)
        if entry is None:
            raise CollaborationError(
                f"unknown handle @{handle}; valid: "
                + ", ".join("@" + h for h in sorted(roster))
            )
        if entry.kind != "human":
            raise CollaborationError(
                f"@{handle} is an agent, not a human; use transfer_task instead"
            )
        humans.append(entry)

    with kanban_db.connect_closing(board=scope.board_slug) as conn:
        task = kanban_db.get_task(conn, task_id)
        if task is None:
            raise CollaborationError(f"task {task_id} not found on {scope.board_slug}")
        previous = str(task.assignee or "") or None

    body = (
        " ".join(f"@{h}" for h in targets)
        + " Human review requested.\n"
        + f"Requested action: {reason.strip()}\n"
        + "Complete or comment on this ticket with the decision, changes required, "
        + "and any evidence the project agent needs to continue."
    )
    result = _post(scope, task_id=task_id, author=author, body=body)

    new_assignee = None
    with kanban_db.connect_closing(board=scope.board_slug) as conn:
        # Block *before* reassigning. Blocking releases any live claim, and a
        # running task cannot be reassigned; doing it the other way round
        # fails on exactly the case that matters — an agent escalating work it
        # is currently holding.
        if block:
            try:
                kanban_db.block_task(
                    conn, task_id, reason=reason.strip()[:500],
                    kind="needs_input",
                )
            except Exception:  # noqa: BLE001 - the comment is the durable record
                pass
        principal = getattr(humans[0], "principal", None)
        if principal:
            # Assigning the human principal is what keeps the dispatcher from
            # treating this as agent work; ``is_human_task`` reads this column.
            try:
                # Same reason as the transfer path: reclaiming would kill the
                # escalating agent mid-call, and the block above has already
                # taken the task out of ``running``. This failure was masked
                # here — the ticket ends blocked either way, so a lost
                # reassign left it correctly stopped but still owned by the
                # agent instead of by the human it was escalated to.
                if kanban_db.reassign_task(
                    conn, task_id, principal, reclaim_first=False,
                    reason=f"escalated to {principal}",
                ):
                    new_assignee = principal
            except Exception:  # noqa: BLE001 - the block + comment already landed
                pass
        # The block above is only a claim-safe handoff primitive. A ticket left
        # blocked is not useful work for its human owner, so finish in the
        # actionable pool. Human principals are excluded from agent dispatch.
        if block and new_assignee:
            try:
                kanban_db.unblock_task(conn, task_id)
            except Exception:  # noqa: BLE001 - assignment + comment are durable
                pass
    return CollaborationResult(
        "escalate_to_human", task_id, result.comment_id,
        tuple(result.handles), tuple(result.notification_ids),
        previous_assignee=previous, new_assignee=new_assignee,
    )


def _normalize(handles: Sequence[str]) -> list[str]:
    cleaned = []
    for raw in handles:
        handle = str(raw or "").strip().lstrip("@").lower()
        if handle and handle not in cleaned:
            cleaned.append(handle)
    if not cleaned:
        raise CollaborationError("at least one @handle is required")
    return cleaned
