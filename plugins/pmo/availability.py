"""Temporary human availability and delegation as append-only project context.

This module creates no PMO database or authority service. Trusted callers pass
their authenticated human identity/rank; grants and their use are durably
audited in ``.datansh/availability.jsonl`` and approval decisions retain an
``acting as`` annotation in native Kanban comments.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Sequence

from plugins.pmo import approval, project_context
from plugins.pmo.project_scope import ProjectScope, resolve


AVAILABILITY_FILE = "availability.jsonl"
IDENTITY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,127}$")
STATUSES = {"away", "limited"}
MAX_RECORDS = 10_000


@dataclass(frozen=True)
class Availability:
    id: str
    user_id: str
    from_ts: int
    to_ts: int
    status: str
    actor: str


@dataclass(frozen=True)
class Delegation:
    id: str
    delegator: str
    delegate: str
    rank: int
    from_ts: int
    to_ts: int
    actor: str
    revoked: bool = False


def _identity(value: str, *, label: str) -> str:
    clean = str(value or "").strip()
    if not IDENTITY_RE.fullmatch(clean):
        raise ValueError(f"{label} must be a simple project identity")
    return clean


def _rank(value: int, *, label: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be between 0 and 100")
    rank = int(value)
    if not 0 <= rank <= 100:
        raise ValueError(f"{label} must be between 0 and 100")
    return rank


def _window(from_ts: int, to_ts: int) -> tuple[int, int]:
    start, end = int(from_ts), int(to_ts)
    if start < 0 or end <= start:
        raise ValueError("availability window must have to_ts greater than from_ts")
    return start, end


def _path(scope: ProjectScope) -> Path:
    return project_context.state_directory(scope.workspace) / AVAILABILITY_FILE


def _read(scope: ProjectScope) -> list[dict[str, Any]]:
    path = _path(scope)
    if path.is_symlink():
        raise ValueError(f"refusing symlinked availability log: {path}")
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            if len(records) >= MAX_RECORDS:
                raise ValueError(f"availability log exceeds {MAX_RECORDS} records")
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"invalid availability event at line {line_number}: {exc.msg}"
                ) from exc
            if not isinstance(item, dict):
                raise ValueError(f"invalid availability event at line {line_number}")
            records.append(item)
    return records


def _append(scope: ProjectScope, event: dict[str, Any]) -> None:
    path = _path(scope)
    if path.is_symlink():
        raise ValueError(f"refusing symlinked availability log: {path}")
    payload = (json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n").encode()
    flags = os.O_APPEND | os.O_CREAT | os.O_WRONLY | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o640)
    try:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short write while appending availability event")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _human(actor_kind: str) -> None:
    if str(actor_kind).strip().casefold() != "human":
        raise PermissionError("only a human may set availability or delegate authority")


def set_availability(
    scope: ProjectScope,
    *,
    user_id: str,
    from_ts: int,
    to_ts: int,
    status: str,
    actor: str,
    actor_kind: str = "human",
) -> Availability:
    _human(actor_kind)
    user = _identity(user_id, label="user id")
    clean_actor = _identity(actor, label="actor")
    if clean_actor.casefold() != user.casefold():
        raise PermissionError("only a user may set their own availability")
    start, end = _window(from_ts, to_ts)
    clean_status = str(status).strip().casefold()
    if clean_status not in STATUSES:
        raise ValueError(f"status must be one of {sorted(STATUSES)}")
    item = Availability(uuid.uuid4().hex, user, start, end, clean_status, clean_actor)
    _append(
        scope,
        {"record_type": "availability_set", "recorded_at": int(time.time()), **asdict(item)},
    )
    return item


def list_availability(
    scope: ProjectScope, *, at: int | None = None
) -> list[Availability]:
    """Return active availability windows, or all windows when ``at`` is omitted."""

    result: list[Availability] = []
    for item in _read(scope):
        if item.get("record_type") != "availability_set":
            continue
        try:
            view = Availability(
                str(item["id"]),
                str(item["user_id"]),
                int(item["from_ts"]),
                int(item["to_ts"]),
                str(item["status"]),
                str(item["actor"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("availability log contains a malformed availability record") from exc
        if at is None or view.from_ts <= int(at) < view.to_ts:
            result.append(view)
    return result


def _delegations(scope: ProjectScope) -> list[Delegation]:
    records = _read(scope)
    revoked = {
        str(item.get("delegation_id"))
        for item in records
        if item.get("record_type") == "delegation_revoked"
    }
    result: list[Delegation] = []
    for item in records:
        if item.get("record_type") != "delegation_granted":
            continue
        try:
            delegation_id = str(item["id"])
            result.append(
                Delegation(
                    delegation_id,
                    str(item["delegator"]),
                    str(item["delegate"]),
                    int(item["rank"]),
                    int(item["from_ts"]),
                    int(item["to_ts"]),
                    str(item["actor"]),
                    delegation_id in revoked,
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("availability log contains a malformed delegation") from exc
    return result


def list_delegations(scope: ProjectScope, *, include_revoked: bool = False) -> list[Delegation]:
    return [item for item in _delegations(scope) if include_revoked or not item.revoked]


def _overlaps(item: Delegation, start: int, end: int) -> bool:
    return not item.revoked and item.from_ts < end and start < item.to_ts


def grant_delegation(
    scope: ProjectScope,
    *,
    delegator: str,
    delegate: str,
    delegator_rank: int,
    delegated_rank: int,
    from_ts: int,
    to_ts: int,
    actor: str,
    actor_kind: str = "human",
) -> Delegation:
    _human(actor_kind)
    owner = _identity(delegator, label="delegator")
    recipient = _identity(delegate, label="delegate")
    clean_actor = _identity(actor, label="actor")
    if clean_actor.casefold() != owner.casefold():
        raise PermissionError("only the delegator may grant their authority")
    if owner.casefold() == recipient.casefold():
        raise ValueError("delegator and delegate must differ")
    owner_rank = _rank(delegator_rank, label="delegator rank")
    rank = _rank(delegated_rank, label="delegated rank")
    if rank == 100 and owner_rank != 100:
        raise PermissionError("only the CEO may delegate CEO rank")
    if rank != owner_rank:
        raise PermissionError("a delegate acts at exactly the delegator's rank")
    start, end = _window(from_ts, to_ts)
    current = _delegations(scope)
    if any(
        item.delegate.casefold() == owner.casefold() and _overlaps(item, start, end)
        for item in current
    ):
        raise PermissionError("a delegate may not sub-delegate authority")
    if any(
        item.delegator.casefold() == owner.casefold() and _overlaps(item, start, end)
        for item in current
    ):
        raise ValueError("delegator already has an overlapping active delegation")
    item = Delegation(
        uuid.uuid4().hex, owner, recipient, rank, start, end, clean_actor
    )
    _append(
        scope,
        {"record_type": "delegation_granted", "recorded_at": int(time.time()), **asdict(item)},
    )
    return item


def revoke_delegation(
    scope: ProjectScope,
    *,
    delegation_id: str,
    actor: str,
    actor_kind: str = "human",
) -> Delegation:
    _human(actor_kind)
    clean_id = _identity(delegation_id, label="delegation id")
    clean_actor = _identity(actor, label="actor")
    item = next((entry for entry in _delegations(scope) if entry.id == clean_id), None)
    if item is None:
        raise ValueError(f"unknown delegation '{clean_id}'")
    if item.revoked:
        return item
    if clean_actor.casefold() != item.delegator.casefold():
        raise PermissionError("only the delegator may revoke their delegation")
    _append(
        scope,
        {
            "record_type": "delegation_revoked",
            "delegation_id": clean_id,
            "actor": clean_actor,
            "recorded_at": int(time.time()),
        },
    )
    return Delegation(**{**asdict(item), "revoked": True})


def active_delegation(
    scope: ProjectScope, *, delegate: str, at: int | None = None
) -> Delegation | None:
    recipient = _identity(delegate, label="delegate")
    timestamp = int(time.time()) if at is None else int(at)
    matches = [
        item
        for item in _delegations(scope)
        if not item.revoked
        and item.delegate.casefold() == recipient.casefold()
        and item.from_ts <= timestamp < item.to_ts
    ]
    if len(matches) > 1:
        raise RuntimeError(f"delegate '{recipient}' has ambiguous active authority")
    return matches[0] if matches else None


def decide_with_delegation(
    scope: ProjectScope,
    *,
    approval_id: str,
    decision: str,
    actor: str,
    note: str = "",
    at: int | None = None,
    actor_kind: str = "human",
) -> approval.ApprovalView:
    """Resolve current delegated authority, decide, and audit acting-as use."""

    _human(actor_kind)
    clean_actor = _identity(actor, label="actor")
    delegation = active_delegation(scope, delegate=clean_actor, at=at)
    if delegation is None:
        raise PermissionError(f"'{clean_actor}' has no active delegated authority")
    result = approval._decide_approval_as_delegate(
        board_slug=scope.board_slug,
        approval_id=approval_id,
        decision=decision,
        actor=clean_actor,
        delegated_rank=delegation.rank,
        note=note,
        acting_as=delegation.delegator,
    )
    _append(
        scope,
        {
            "record_type": "delegated_decision",
            "recorded_at": int(time.time()),
            "delegation_id": delegation.id,
            "approval_id": result.approval_id,
            "decision": result.status,
            "actor": clean_actor,
            "acting_as": delegation.delegator,
            "rank": delegation.rank,
        },
    )
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--board", required=True)
    parser.add_argument("--workspace")
    sub = parser.add_subparsers(dest="command", required=True)

    setting = sub.add_parser("set")
    setting.add_argument("user_id")
    setting.add_argument("status", choices=sorted(STATUSES))
    setting.add_argument("--from-ts", required=True, type=int)
    setting.add_argument("--to-ts", required=True, type=int)
    setting.add_argument("--actor", required=True)

    listing = sub.add_parser("list")
    listing.add_argument("--include-revoked", action="store_true")
    listing.add_argument("--at", type=int)

    grant = sub.add_parser("delegate")
    grant.add_argument("delegator")
    grant.add_argument("delegate")
    grant.add_argument("--delegator-rank", required=True, type=int)
    grant.add_argument("--delegated-rank", required=True, type=int)
    grant.add_argument("--from-ts", required=True, type=int)
    grant.add_argument("--to-ts", required=True, type=int)
    grant.add_argument("--actor", required=True)

    revoke = sub.add_parser("revoke")
    revoke.add_argument("delegation_id")
    revoke.add_argument("--actor", required=True)

    decide = sub.add_parser("decide")
    decide.add_argument("approval_id")
    decide.add_argument("decision", choices=("approved", "rejected"))
    decide.add_argument("--actor", required=True)
    decide.add_argument("--note", default="")
    decide.add_argument("--at", type=int)
    args = parser.parse_args(argv)
    try:
        scope = resolve(args.project, board_slug=args.board, workspace=args.workspace)
        if args.command == "set":
            result: object = set_availability(
                scope,
                user_id=args.user_id,
                status=args.status,
                from_ts=args.from_ts,
                to_ts=args.to_ts,
                actor=args.actor,
            )
        elif args.command == "list":
            result = {
                "availability": [asdict(item) for item in list_availability(scope, at=args.at)],
                "delegations": [
                    asdict(item)
                    for item in list_delegations(
                        scope, include_revoked=args.include_revoked
                    )
                ],
            }
        elif args.command == "delegate":
            result = grant_delegation(
                scope,
                delegator=args.delegator,
                delegate=args.delegate,
                delegator_rank=args.delegator_rank,
                delegated_rank=args.delegated_rank,
                from_ts=args.from_ts,
                to_ts=args.to_ts,
                actor=args.actor,
            )
        elif args.command == "revoke":
            result = revoke_delegation(
                scope, delegation_id=args.delegation_id, actor=args.actor
            )
        else:
            result = decide_with_delegation(
                scope,
                approval_id=args.approval_id,
                decision=args.decision,
                actor=args.actor,
                note=args.note,
                at=args.at,
            )
    except (OSError, PermissionError, RuntimeError, ValueError) as exc:
        print(f"pmo availability: {exc}", file=sys.stderr)
        return 2
    if isinstance(result, dict):
        payload = result
    elif isinstance(result, list):
        payload = [asdict(item) for item in result]
    else:
        payload = asdict(result)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
