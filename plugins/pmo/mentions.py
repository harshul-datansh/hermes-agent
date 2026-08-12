"""Project-scoped ``@`` routing on top of native Kanban comments.

The module deliberately has no message table or watcher database.  A source
comment and its routing/receipt markers are ordinary Kanban comments, so a
restart can reconstruct pending work using the existing comment/event audit
trail.  Gateway delivery is a narrow injected callback.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Callable, Iterable

from agent.redact import redact_sensitive_text
from hermes_cli import kanban_db

from plugins.pmo.project_scope import ProjectScope, ScopeViolation


MENTION_RE = re.compile(
    r"(?<![\w@])@([a-z0-9][a-z0-9._-]{1,31})\b(?![/\\])"
)
ROUTE_MARKER = "[pmo:mention-route:v1]"
DELIVERED_MARKER = "[pmo:mention-delivered:v1]"
READ_MARKER = "[pmo:notification-read:v1]"
EGRESS_MARKER = "[pmo:notification-egress:v1]"
SYSTEM_MARKERS = (ROUTE_MARKER, DELIVERED_MARKER, READ_MARKER, EGRESS_MARKER)
RESERVED_HANDLES = ("pm", "founders-office")


@dataclass(frozen=True)
class Recipient:
    handle: str
    kind: str
    role: str
    profile: str | None
    # Storage identity for humans ("human:<email>"). Lets a client map a
    # comment/message author back to the handle it would type, instead of
    # re-deriving the handle from an email and hoping the rules match.
    principal: str | None = None


@dataclass(frozen=True)
class MentionRoute:
    notification_id: int
    source_comment_id: int
    task_id: str
    recipient: Recipient
    author: str
    body: str
    urgency: str
    link: str
    created_at: int


@dataclass(frozen=True)
class PostResult:
    comment_id: int
    handles: tuple[str, ...]
    notification_ids: tuple[int, ...]


class UnknownMention(ValueError):
    def __init__(self, unknown: Iterable[str], valid_handles: Iterable[str]):
        self.unknown = tuple(unknown)
        self.valid_handles = tuple(valid_handles)
        super().__init__(
            "unknown mention(s): " + ", ".join(f"@{item}" for item in self.unknown)
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "error": "unknown_mention",
            "unknown": list(self.unknown),
            "valid_handles": list(self.valid_handles),
        }


def _without_code(text: str) -> str:
    """Remove fenced and inline code while preserving ordinary prose."""

    # An unclosed fence consumes the remainder, which is the safe routing
    # choice for malformed pasted code.
    text = re.sub(r"(?ms)^[ \t]*(?:```|~~~).*?(?:^[ \t]*(?:```|~~~)[ \t]*$|\Z)", " ", text)
    return re.sub(r"`+[^`\n]*`+", " ", text)


def parse(body: str) -> list[str]:
    """Extract code-free, lowercase handles, deduped in source order."""

    seen: set[str] = set()
    result: list[str] = []
    for match in MENTION_RE.finditer(_without_code(str(body or ""))):
        handle = match.group(1)
        if handle not in seen:
            seen.add(handle)
            result.append(handle)
    return result


def human_handle(principal: str) -> str:
    """Derive a mention handle from a ``human:<email>`` principal.

    ``human:ceo@datansh.local`` -> ``ceo``. The local part is what people
    already call each other, and it matches the handle grammar in
    :data:`MENTION_RE`.
    """
    text = str(principal or "").strip()
    if text.startswith("human:"):
        text = text[6:]
    local = text.split("@", 1)[0].strip().lower()
    cleaned = re.sub(r"[^a-z0-9._-]+", "-", local).strip("-._")
    return cleaned[:32]


def _domain_label(principal: str) -> str:
    text = str(principal or "")
    if text.startswith("human:"):
        text = text[6:]
    _, _, domain = text.partition("@")
    label = domain.split(".", 1)[0].strip().lower()
    return re.sub(r"[^a-z0-9]+", "", label)[:16]


def roster(scope: ProjectScope) -> tuple[Recipient, ...]:
    """Return the only handles nameable inside this project.

    Three sources, all real and all project-local:

    * the orchestrator profile from ``project.yaml`` (``@pm``);
    * the agent roster from ``project.yaml``;
    * **the human members from ``access.yaml``** — the same file authorization
      reads, so anyone who can act in the project can also be addressed in it.

    Humans used to be a single hardcoded ``@founders-office`` placeholder with
    no profile, which meant a project with fifteen real members had exactly one
    nameable person. ``@founders-office`` is kept as a deliberate broadcast
    handle for "whoever is on point", not as a stand-in for individuals.
    """
    recipients = [
        Recipient("pm", "agent", "project manager", scope.config.orchestrator.profile),
        Recipient("founders-office", "human", "founders office", None),
    ]
    for agent in scope.config.agents:
        if agent.handle in RESERVED_HANDLES:
            continue
        recipients.append(Recipient(agent.handle, "agent", agent.role, agent.profile))

    # Agents are declared deliberately in project.yaml and are dispatched *by*
    # handle, so they own the bare name on a collision. A colliding human falls
    # back to ``<local>.<domain-label>`` — deterministic, still readable, and
    # never silently unaddressable. `hermes pmo doctor` reports collisions.
    taken = {item.handle for item in recipients}
    for member in _human_members(scope):
        principal, role = member
        base = human_handle(principal)
        if not base:
            continue
        handle = base
        if handle in taken:
            suffix = _domain_label(principal)
            handle = f"{base}.{suffix}" if suffix else ""
            if not handle or handle in taken:
                continue
        taken.add(handle)
        recipients.append(Recipient(handle, "human", role, None, principal))

    # Validation is intentionally defensive: duplicate aliases must never fan
    # one comment out to an arbitrary profile.
    handles = [item.handle for item in recipients]
    duplicates = sorted({item for item in handles if handles.count(item) > 1})
    if duplicates:
        raise ValueError("duplicate project handle(s): " + ", ".join(duplicates))
    return tuple(recipients)


def _human_members(scope: ProjectScope) -> list[tuple[str, str]]:
    """``(principal, role)`` for every human member of this project.

    Reads the project's own ``access.yaml``. A malformed or missing policy
    must not take the whole mention system down — agents remain addressable
    and ``hermes pmo doctor`` reports the policy problem separately.
    """
    try:
        from plugins.pmo.access_policy import load_access_policy

        policy = load_access_policy(scope)
    except Exception:  # noqa: BLE001 - mentions degrade, never break the board
        return []
    members: list[tuple[str, str]] = []
    for rule in policy.members:
        principal = str(getattr(rule, "principal", ""))
        if not principal.startswith("human:"):
            continue
        members.append((principal, str(getattr(rule, "role", "") or "member")))
    members.sort(key=lambda item: item[0])
    return members


def autocomplete(scope: ProjectScope, prefix: str = "") -> list[dict[str, str | None]]:
    value = prefix.lower().lstrip("@").strip()
    return [
        {
            "handle": item.handle,
            "kind": item.kind,
            "role": item.role,
            "profile": item.profile,
            "principal": item.principal,
        }
        for item in roster(scope)
        if item.handle.startswith(value)
    ]


def resolve(
    scope: ProjectScope, handles: Iterable[str]
) -> tuple[list[Recipient], list[str]]:
    available = {item.handle: item for item in roster(scope)}
    found: list[Recipient] = []
    unknown: list[str] = []
    for raw in handles:
        handle = str(raw).strip().lower().lstrip("@")
        recipient = available.get(handle)
        if recipient is None:
            if handle and handle not in unknown:
                unknown.append(handle)
        elif recipient not in found:
            found.append(recipient)
    return found, unknown


def _payload(comment_body: str, marker: str) -> dict[str, object] | None:
    first, separator, rest = comment_body.partition("\n")
    if first.strip() != marker or not separator:
        return None
    try:
        value = json.loads(rest)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _marker(marker: str, payload: dict[str, object]) -> str:
    return marker + "\n" + json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _task(scope: ProjectScope, conn, task_id: str):
    task = kanban_db.get_task(conn, task_id)
    if task is None:
        raise ValueError(f"unknown task '{task_id}'")
    if task.project_id != scope.project_id:
        raise ScopeViolation(
            f"task '{task_id}' is not bound to project '{scope.project_id}'"
        )
    return task


def enqueue_notification(
    scope: ProjectScope,
    *,
    task_id: str,
    source_comment_id: int,
    author: str,
    recipient: Recipient,
    body: str,
    urgency: str = "normal",
    request_id: str | None = None,
) -> int:
    """Append one durable, unread notification marker to a native task."""

    if urgency not in {"normal", "high"}:
        raise ValueError("urgency must be 'normal' or 'high'")
    data: dict[str, object] = {
        "author": author,
        "body": redact_sensitive_text(body, force=True),
        "kind": recipient.kind,
        "link": f"/pmo/boards/{scope.board_slug}/tasks/{task_id}",
        "profile": recipient.profile,
        "recipient": recipient.handle,
        "request_id": request_id,
        "source_comment_id": int(source_comment_id),
        "task_id": task_id,
        "urgency": urgency,
    }
    with kanban_db.connect_closing(board=scope.board_slug) as conn:
        _task(scope, conn, task_id)
        return kanban_db.add_comment(conn, task_id, "pmo-system", _marker(ROUTE_MARKER, data))


def post_comment(
    scope: ProjectScope,
    *,
    task_id: str,
    author: str,
    body: str,
    request_id: str | None = None,
) -> PostResult:
    """Validate all mentions, then persist the comment and durable fan-out."""

    clean_body = redact_sensitive_text(str(body or "").strip(), force=True)
    if not clean_body:
        raise ValueError("comment body is required")
    handles = parse(clean_body)
    recipients, unknown = resolve(scope, handles)
    if unknown:
        raise UnknownMention(unknown, (item.handle for item in roster(scope)))

    with kanban_db.connect_closing(board=scope.board_slug) as conn:
        _task(scope, conn, task_id)
        if request_id:
            for comment in kanban_db.list_comments(conn, task_id):
                routed = _payload(comment.body, ROUTE_MARKER)
                if routed and routed.get("request_id") == request_id:
                    source = int(routed["source_comment_id"])
                    ids = tuple(
                        item.id
                        for item in kanban_db.list_comments(conn, task_id)
                        if (data := _payload(item.body, ROUTE_MARKER))
                        and data.get("request_id") == request_id
                    )
                    return PostResult(source, tuple(handles), ids)
        source_id = kanban_db.add_comment(conn, task_id, author.strip(), clean_body)
        notification_ids: list[int] = []
        for recipient in recipients:
            payload = {
                "author": author.strip(),
                "body": clean_body,
                "kind": recipient.kind,
                "link": f"/pmo/boards/{scope.board_slug}/tasks/{task_id}",
                "profile": recipient.profile,
                "recipient": recipient.handle,
                "request_id": request_id,
                "source_comment_id": source_id,
                "task_id": task_id,
                "urgency": "high" if recipient.handle == "founders-office" else "normal",
            }
            notification_ids.append(
                kanban_db.add_comment(
                    conn, task_id, "pmo-system", _marker(ROUTE_MARKER, payload)
                )
            )
    return PostResult(source_id, tuple(handles), tuple(notification_ids))


def _all_routes(scope: ProjectScope) -> tuple[list[MentionRoute], set[tuple[str, int]], set[int], set[int]]:
    routes: list[MentionRoute] = []
    delivered: set[tuple[str, int]] = set()
    read: set[int] = set()
    egressed: set[int] = set()
    available = {item.handle: item for item in roster(scope)}
    with kanban_db.connect_closing(board=scope.board_slug) as conn:
        for task in kanban_db.list_tasks(conn, include_archived=True, order_by="created"):
            if task.project_id != scope.project_id:
                continue
            for comment in kanban_db.list_comments(conn, task.id):
                if data := _payload(comment.body, DELIVERED_MARKER):
                    delivered.add((str(data.get("recipient", "")), int(data.get("notification_id", 0))))
                    continue
                if data := _payload(comment.body, READ_MARKER):
                    read.add(int(data.get("notification_id", 0)))
                    continue
                if data := _payload(comment.body, EGRESS_MARKER):
                    egressed.add(int(data.get("notification_id", 0)))
                    continue
                data = _payload(comment.body, ROUTE_MARKER)
                if not data:
                    continue
                handle = str(data.get("recipient", ""))
                recipient = available.get(handle)
                if recipient is None:
                    # A roster removal makes old pending work undeliverable by
                    # design; it cannot leak to a newly guessed destination.
                    continue
                routes.append(
                    MentionRoute(
                        notification_id=comment.id,
                        source_comment_id=int(data.get("source_comment_id", 0)),
                        task_id=task.id,
                        recipient=recipient,
                        author=str(data.get("author", "")),
                        body=str(data.get("body", "")),
                        urgency=str(data.get("urgency", "normal")),
                        link=str(data.get("link", "")),
                        created_at=comment.created_at,
                    )
                )
    routes.sort(key=lambda item: (item.created_at, item.notification_id))
    return routes, delivered, read, egressed


def pending_routes(scope: ProjectScope, *, limit: int = 100) -> list[MentionRoute]:
    routes, delivered, _, _ = _all_routes(scope)
    bounded = max(0, min(int(limit), 1000))
    return [
        item for item in routes
        if item.recipient.kind == "agent"
        and (item.recipient.handle, item.notification_id) not in delivered
        and item.author.casefold() not in {
            item.recipient.handle.casefold(),
            (item.recipient.profile or "").casefold(),
        }
    ][:bounded]


WakeCallback = Callable[[Recipient, dict[str, object]], None]


def drain_pending(
    scope: ProjectScope, wake: WakeCallback, *, limit: int = 100
) -> list[int]:
    """Deliver an oldest-first bounded restart backlog through ``wake``."""

    delivered: list[int] = []
    for route in pending_routes(scope, limit=limit):
        context = {
            "event": "pmo_mention",
            "project_id": scope.project_id,
            "board": scope.board_slug,
            "task_id": route.task_id,
            "source_comment_id": route.source_comment_id,
            "author": route.author,
            "body": route.body,
            "reply_via": "task_comment",
        }
        wake(route.recipient, context)
        with kanban_db.connect_closing(board=scope.board_slug) as conn:
            kanban_db.add_comment(
                conn,
                route.task_id,
                "pmo-system",
                _marker(
                    DELIVERED_MARKER,
                    {
                        "notification_id": route.notification_id,
                        "recipient": route.recipient.handle,
                    },
                ),
            )
        delivered.append(route.notification_id)
    return delivered


def mark_notification_delivered(
    scope: ProjectScope,
    *,
    task_id: str,
    notification_id: int,
    recipient: str,
) -> bool:
    """Record delivery by a native dispatcher or another durable wake path."""

    routes, delivered, _, _ = _all_routes(scope)
    route = next(
        (
            item
            for item in routes
            if item.notification_id == int(notification_id)
            and item.task_id == task_id
            and item.recipient.handle == recipient
        ),
        None,
    )
    if route is None:
        return False
    if (recipient, int(notification_id)) in delivered:
        return True
    with kanban_db.connect_closing(board=scope.board_slug) as conn:
        _task(scope, conn, task_id)
        kanban_db.add_comment(
            conn,
            task_id,
            "pmo-system",
            _marker(
                DELIVERED_MARKER,
                {
                    "notification_id": int(notification_id),
                    "recipient": recipient,
                },
            ),
        )
    return True


def mark_notification_read(scope: ProjectScope, *, notification_id: int, user: str) -> bool:
    routes, _, read, _ = _all_routes(scope)
    route = next((item for item in routes if item.notification_id == int(notification_id)), None)
    if route is None or route.recipient.handle != user:
        return False
    if route.notification_id in read:
        return True
    with kanban_db.connect_closing(board=scope.board_slug) as conn:
        kanban_db.add_comment(
            conn,
            route.task_id,
            user,
            _marker(READ_MARKER, {"notification_id": route.notification_id, "user": user}),
        )
    return True


def mark_notification_egressed(
    scope: ProjectScope, *, notification_id: int, channel: str
) -> bool:
    routes, _, _, egressed = _all_routes(scope)
    route = next((item for item in routes if item.notification_id == int(notification_id)), None)
    if route is None:
        return False
    if route.notification_id in egressed:
        return True
    with kanban_db.connect_closing(board=scope.board_slug) as conn:
        kanban_db.add_comment(
            conn,
            route.task_id,
            "pmo-system",
            _marker(
                EGRESS_MARKER,
                {"channel": channel, "notification_id": route.notification_id},
            ),
        )
    return True


def inbox(
    scope: ProjectScope, *, user: str, unread_only: bool = False
) -> list[dict[str, object]]:
    routes, _, read, egressed = _all_routes(scope)
    result: list[dict[str, object]] = []
    for route in reversed(routes):
        if route.recipient.handle != user:
            continue
        is_read = route.notification_id in read
        if unread_only and is_read:
            continue
        result.append(
            {
                "id": route.notification_id,
                "task_id": route.task_id,
                "author": route.author,
                "body": route.body,
                "urgency": route.urgency,
                "link": route.link,
                "created_at": route.created_at,
                "read": is_read,
                "egressed": route.notification_id in egressed,
            }
        )
    return result
