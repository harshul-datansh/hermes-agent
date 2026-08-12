"""Project-scoped decisions and shared knowledge for Datansh PM-OS.

The logs live in an explicit workspace's ``.datansh`` directory so they can
be reviewed and version-controlled with the project.  They complement, and do
not replace, Hermes profile memory or its self-learning/curator lifecycle.

Decisions and knowledge events are append-only JSONL.  ``context.md`` is a
bounded, derived projection intended for PM and worker skill intake.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import unicodedata
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable, Sequence

from agent.redact import redact_sensitive_text


STATE_DIR = ".datansh"
DECISIONS_FILE = "decisions.jsonl"
KNOWLEDGE_FILE = "knowledge.jsonl"
CONTEXT_FILE = "context.md"

MAX_TITLE_CHARS = 200
MAX_DECISION_TEXT_CHARS = 4_000
MAX_KNOWLEDGE_BODY_CHARS = 280
MAX_KNOWLEDGE_ENTRIES = 200
MAX_LIST_LIMIT = 100
DEFAULT_DECISION_CONTEXT_COUNT = 5
DEFAULT_KNOWLEDGE_CONTEXT_COUNT = 20
DEFAULT_CONTEXT_CHARS = 8_000
MAX_CONTEXT_CHARS = 16_000
DEDUPE_SIMILARITY = 0.92
KNOWLEDGE_KINDS = frozenset({"convention", "gotcha", "contact", "env", "risk"})
CONFIDENCE_LEVELS = frozenset({"observed", "confirmed"})


@dataclass(frozen=True)
class Decision:
    """One immutable decision with its status derived from later records."""

    id: str
    title: str
    context: str
    decision: str
    rationale: str
    alternatives: str | None
    decided_by: str
    thread_id: str | None
    task_id: str | None
    approval_id: str | None
    created_at: str
    supersedes: str | None
    status: str
    superseded_by: str | None


@dataclass(frozen=True)
class Knowledge:
    """One project fact with usage derived from later append-only events."""

    id: str
    kind: str
    body: str
    source: str | None
    confidence: str
    created_by: str
    created_at: str
    uses: int


@dataclass(frozen=True)
class KnowledgeResult:
    """Result of adding a new fact or deduplicating against an existing one."""

    knowledge: Knowledge
    deduplicated: bool


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _required_text(value: str, *, label: str, maximum: int) -> str:
    cleaned = str(value).strip()
    if not cleaned:
        raise ValueError(f"{label} must not be empty")
    if len(cleaned) > maximum:
        raise ValueError(f"{label} must be at most {maximum} characters")
    return redact_sensitive_text(cleaned, force=True)


def _optional_text(value: str | None, *, label: str, maximum: int) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    if not cleaned:
        return None
    if len(cleaned) > maximum:
        raise ValueError(f"{label} must be at most {maximum} characters")
    return redact_sensitive_text(cleaned, force=True)


def _bounded_limit(limit: int) -> int:
    value = int(limit)
    if not 1 <= value <= MAX_LIST_LIMIT:
        raise ValueError(f"limit must be between 1 and {MAX_LIST_LIMIT}")
    return value


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def resolve_workspace(workspace: str | Path) -> Path:
    """Resolve one explicit, existing project workspace."""

    raw = Path(workspace).expanduser()
    if not raw.exists():
        raise ValueError(f"workspace must be an existing directory: {raw}")
    resolved = raw.resolve(strict=True)
    if not resolved.is_dir():
        raise ValueError(f"workspace must be an existing directory: {resolved}")
    return resolved


def state_directory(workspace: str | Path, *, create: bool = True) -> Path:
    """Return a contained, non-symlink ``.datansh`` directory.

    Resolving the workspace first and rejecting a symlink at the state
    boundary prevents ``.datansh`` from redirecting reads or writes outside
    the project.  The check is repeated after creation to narrow races.
    """

    root = resolve_workspace(workspace)
    state = root / STATE_DIR
    if state.is_symlink():
        raise ValueError(f"refusing symlinked project state directory: {state}")
    if state.exists():
        if not state.is_dir():
            raise ValueError(f"project state path is not a directory: {state}")
    elif create:
        state.mkdir(mode=0o750)
    else:
        return state

    if state.is_symlink():
        raise ValueError(f"refusing symlinked project state directory: {state}")
    resolved = state.resolve(strict=True)
    if not _is_relative_to(resolved, root) or resolved.parent != root:
        raise ValueError(f"project state escapes workspace: {resolved}")
    return resolved


def _state_file(workspace: str | Path, filename: str, *, create_dir: bool = True) -> Path:
    state = state_directory(workspace, create=create_dir)
    path = state / filename
    if path.is_symlink():
        raise ValueError(f"refusing symlinked project state file: {path}")
    if path.exists():
        if not path.is_file():
            raise ValueError(f"project state path is not a file: {path}")
        resolved = path.resolve(strict=True)
        if resolved.parent != state:
            raise ValueError(f"project state file escapes workspace: {resolved}")
    return path


def _append_event(workspace: str | Path, filename: str, event: dict[str, Any]) -> None:
    path = _state_file(workspace, filename)
    payload = (json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
    flags = os.O_APPEND | os.O_CREAT | os.O_WRONLY | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o640)
    except OSError as exc:
        raise ValueError(f"cannot safely append project state file {path}: {exc}") from exc
    try:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short write while appending project state")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _read_events(workspace: str | Path, filename: str) -> list[dict[str, Any]]:
    path = _state_file(workspace, filename)
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSON in {path}:{line_number}: {exc.msg}") from exc
            if not isinstance(event, dict):
                raise ValueError(f"invalid non-object event in {path}:{line_number}")
            events.append(event)
    return events


def _decision_views(workspace: str | Path) -> list[Decision]:
    records = _read_events(workspace, DECISIONS_FILE)
    raw: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    superseded_by: dict[str, str] = {}
    for event in records:
        if event.get("record_type") != "decision":
            raise ValueError("decisions log contains an unsupported record type")
        decision_id = str(event.get("id", ""))
        if not decision_id or decision_id in by_id:
            raise ValueError(f"duplicate or missing decision id: {decision_id!r}")
        predecessor = event.get("supersedes")
        if predecessor is not None:
            predecessor = str(predecessor)
            if predecessor not in by_id:
                raise ValueError(f"decision {decision_id} supersedes unknown decision {predecessor}")
            if predecessor in superseded_by:
                raise ValueError(f"decision {predecessor} is superseded more than once")
            superseded_by[predecessor] = decision_id
        raw.append(event)
        by_id[decision_id] = event

    return [
        Decision(
            id=str(event["id"]),
            title=str(event["title"]),
            context=str(event["context"]),
            decision=str(event["decision"]),
            rationale=str(event["rationale"]),
            alternatives=event.get("alternatives"),
            decided_by=str(event["decided_by"]),
            thread_id=event.get("thread_id"),
            task_id=event.get("task_id"),
            approval_id=event.get("approval_id"),
            created_at=str(event["created_at"]),
            supersedes=event.get("supersedes"),
            status="superseded" if str(event["id"]) in superseded_by else "active",
            superseded_by=superseded_by.get(str(event["id"])),
        )
        for event in raw
    ]


def list_decisions(
    workspace: str | Path,
    *,
    limit: int = DEFAULT_DECISION_CONTEXT_COUNT,
    active_only: bool = False,
) -> list[Decision]:
    """List the newest project decisions with an enforced upper bound."""

    count = _bounded_limit(limit)
    decisions = _decision_views(workspace)
    if active_only:
        decisions = [item for item in decisions if item.status == "active"]
    return list(reversed(decisions))[:count]


def record_decision(
    workspace: str | Path,
    *,
    title: str,
    context: str,
    decision: str,
    rationale: str,
    decided_by: str,
    alternatives: str | None = None,
    task_id: str | None = None,
    thread_id: str | None = None,
    approval_id: str | None = None,
    supersedes: str | None = None,
) -> Decision:
    """Append one decision and refresh the bounded context projection."""

    predecessor = _optional_text(supersedes, label="supersedes", maximum=100)
    if predecessor is not None:
        existing = {item.id: item for item in _decision_views(workspace)}
        prior = existing.get(predecessor)
        if prior is None:
            raise ValueError(f"cannot supersede unknown decision '{predecessor}'")
        if prior.status != "active":
            raise ValueError(f"decision '{predecessor}' is already superseded")

    event = {
        "record_type": "decision",
        "id": f"d_{uuid.uuid4().hex[:12]}",
        "title": _required_text(title, label="title", maximum=MAX_TITLE_CHARS),
        "context": _required_text(
            context, label="context", maximum=MAX_DECISION_TEXT_CHARS
        ),
        "decision": _required_text(
            decision, label="decision", maximum=MAX_DECISION_TEXT_CHARS
        ),
        "rationale": _required_text(
            rationale, label="rationale", maximum=MAX_DECISION_TEXT_CHARS
        ),
        "alternatives": _optional_text(
            alternatives, label="alternatives", maximum=MAX_DECISION_TEXT_CHARS
        ),
        "decided_by": _required_text(decided_by, label="decided-by", maximum=200),
        "thread_id": _optional_text(thread_id, label="thread-id", maximum=200),
        "task_id": _optional_text(task_id, label="task-id", maximum=200),
        "approval_id": _optional_text(approval_id, label="approval-id", maximum=200),
        "supersedes": predecessor,
        "created_at": _now(),
    }
    _append_event(workspace, DECISIONS_FILE, event)
    refresh_context(workspace)
    return next(item for item in _decision_views(workspace) if item.id == event["id"])


def supersede_decision(
    workspace: str | Path,
    decision_id: str,
    **replacement: str | None,
) -> Decision:
    """Supersede a decision by appending its replacement, never editing it."""

    return record_decision(workspace, supersedes=decision_id, **replacement)  # type: ignore[arg-type]


def _normalized_fact(body: str) -> str:
    normalized = unicodedata.normalize("NFKC", body).casefold()
    return " ".join(re.findall(r"\w+", normalized, flags=re.UNICODE))


def _numeric_tokens(normalized: str) -> tuple[str, ...]:
    """Keep version/count changes from being mistaken for near duplicates."""

    return tuple(re.findall(r"\b\d+(?:[._-]\d+)*\b", normalized))


def _knowledge_views(workspace: str | Path) -> list[Knowledge]:
    records = _read_events(workspace, KNOWLEDGE_FILE)
    facts: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    uses: dict[str, int] = {}
    for event in records:
        record_type = event.get("record_type")
        if record_type == "knowledge":
            knowledge_id = str(event.get("id", ""))
            if not knowledge_id or knowledge_id in by_id:
                raise ValueError(f"duplicate or missing knowledge id: {knowledge_id!r}")
            facts.append(event)
            by_id[knowledge_id] = event
            uses[knowledge_id] = 0
        elif record_type == "knowledge_use":
            knowledge_id = str(event.get("knowledge_id", ""))
            if knowledge_id not in by_id:
                raise ValueError(f"knowledge use references unknown id: {knowledge_id!r}")
            uses[knowledge_id] += 1
        else:
            raise ValueError("knowledge log contains an unsupported record type")

    return [
        Knowledge(
            id=str(event["id"]),
            kind=str(event["kind"]),
            body=str(event["body"]),
            source=event.get("source"),
            confidence=str(event["confidence"]),
            created_by=str(event["created_by"]),
            created_at=str(event["created_at"]),
            uses=uses[str(event["id"])],
        )
        for event in facts
    ]


def list_knowledge(
    workspace: str | Path,
    *,
    limit: int = DEFAULT_KNOWLEDGE_CONTEXT_COUNT,
) -> list[Knowledge]:
    """List bounded project facts ordered by usefulness, then recency."""

    count = _bounded_limit(limit)
    facts = _knowledge_views(workspace)
    # Usage remains the primary signal, but make confirmed facts win ties with
    # observed facts.  This keeps the bounded projection from dropping the
    # only explicitly verified item when several facts were recorded in the
    # same second (the event timestamp intentionally has second precision).
    facts.sort(
        key=lambda item: (
            item.uses,
            item.confidence == "confirmed",
            item.created_at,
        ),
        reverse=True,
    )
    return facts[:count]


def add_knowledge(
    workspace: str | Path,
    *,
    kind: str,
    body: str,
    created_by: str,
    source: str | None = None,
    confidence: str = "observed",
) -> KnowledgeResult:
    """Append a fact or a usage event when it duplicates existing knowledge."""

    clean_kind = _required_text(kind, label="kind", maximum=40).casefold()
    if clean_kind not in KNOWLEDGE_KINDS:
        raise ValueError(f"kind must be one of: {', '.join(sorted(KNOWLEDGE_KINDS))}")
    clean_confidence = _required_text(
        confidence, label="confidence", maximum=20
    ).casefold()
    if clean_confidence not in CONFIDENCE_LEVELS:
        raise ValueError(
            f"confidence must be one of: {', '.join(sorted(CONFIDENCE_LEVELS))}"
        )
    clean_body = _required_text(
        body, label="body", maximum=MAX_KNOWLEDGE_BODY_CHARS
    )
    normalized = _normalized_fact(clean_body)
    if not normalized:
        raise ValueError("body must contain letters or numbers")

    facts = _knowledge_views(workspace)
    for fact in facts:
        if fact.kind != clean_kind:
            continue
        candidate = _normalized_fact(fact.body)
        is_near_duplicate = (
            _numeric_tokens(candidate) == _numeric_tokens(normalized)
            and SequenceMatcher(None, candidate, normalized).ratio() >= DEDUPE_SIMILARITY
        )
        if candidate == normalized or is_near_duplicate:
            _append_event(
                workspace,
                KNOWLEDGE_FILE,
                {
                    "record_type": "knowledge_use",
                    "knowledge_id": fact.id,
                    "source": _optional_text(source, label="source", maximum=200),
                    "created_by": _required_text(
                        created_by, label="created-by", maximum=200
                    ),
                    "created_at": _now(),
                },
            )
            refresh_context(workspace)
            updated = next(item for item in _knowledge_views(workspace) if item.id == fact.id)
            return KnowledgeResult(knowledge=updated, deduplicated=True)

    if len(facts) >= MAX_KNOWLEDGE_ENTRIES:
        raise ValueError(
            f"project knowledge is capped at {MAX_KNOWLEDGE_ENTRIES} unique facts"
        )
    event = {
        "record_type": "knowledge",
        "id": f"k_{uuid.uuid4().hex[:12]}",
        "kind": clean_kind,
        "body": clean_body,
        "source": _optional_text(source, label="source", maximum=200),
        "confidence": clean_confidence,
        "created_by": _required_text(created_by, label="created-by", maximum=200),
        "created_at": _now(),
    }
    _append_event(workspace, KNOWLEDGE_FILE, event)
    refresh_context(workspace)
    created = next(item for item in _knowledge_views(workspace) if item.id == event["id"])
    return KnowledgeResult(knowledge=created, deduplicated=False)


def render_context(
    workspace: str | Path,
    *,
    decision_limit: int = DEFAULT_DECISION_CONTEXT_COUNT,
    knowledge_limit: int = DEFAULT_KNOWLEDGE_CONTEXT_COUNT,
    max_chars: int = DEFAULT_CONTEXT_CHARS,
) -> str:
    """Render a deterministic, bounded block for PM and worker skill context."""

    decision_count = _bounded_limit(decision_limit)
    knowledge_count = _bounded_limit(knowledge_limit)
    char_limit = int(max_chars)
    if not 256 <= char_limit <= MAX_CONTEXT_CHARS:
        raise ValueError(f"max-chars must be between 256 and {MAX_CONTEXT_CHARS}")

    decisions = list_decisions(workspace, limit=decision_count, active_only=True)
    knowledge = list_knowledge(workspace, limit=knowledge_count)
    lines = [
        "<!-- Generated by plugins/pmo/project_context.py; do not hand-edit. -->",
        "# Datansh project context",
        "",
        "This project context complements Hermes agent memory and skills; it does not replace them.",
        "",
        "## Active decisions",
    ]
    if decisions:
        for item in decisions:
            lines.append(f"- [{item.id}] {item.title}: {item.decision} (by {item.decided_by})")
    else:
        lines.append("- None recorded.")
    lines.extend(("", "## Shared project knowledge"))
    if knowledge:
        for item in knowledge:
            lines.append(
                f"- [{item.confidence}] [{item.kind}] {item.body} "
                f"(id={item.id}, uses={item.uses})"
            )
    else:
        lines.append("- None recorded.")
    rendered = "\n".join(lines).rstrip() + "\n"
    if len(rendered) > char_limit:
        suffix = "\n<!-- Context truncated to configured character bound. -->\n"
        rendered = rendered[: char_limit - len(suffix)].rstrip() + suffix
    return rendered


def refresh_context(
    workspace: str | Path,
    *,
    decision_limit: int = DEFAULT_DECISION_CONTEXT_COUNT,
    knowledge_limit: int = DEFAULT_KNOWLEDGE_CONTEXT_COUNT,
    max_chars: int = DEFAULT_CONTEXT_CHARS,
) -> Path:
    """Atomically refresh ``.datansh/context.md`` from append-only logs."""

    target = _state_file(workspace, CONTEXT_FILE)
    content = render_context(
        workspace,
        decision_limit=decision_limit,
        knowledge_limit=knowledge_limit,
        max_chars=max_chars,
    )
    state = target.parent
    descriptor, temporary_name = tempfile.mkstemp(prefix=".context-", suffix=".tmp", dir=state)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if target.is_symlink():
            raise ValueError(f"refusing symlinked project state file: {target}")
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()
    return target


def _print_json(value: Any) -> None:
    if hasattr(value, "__dataclass_fields__"):
        value = asdict(value)
    print(json.dumps(value, indent=2, sort_keys=True))


def _decision_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--title", required=True)
    parser.add_argument("--context", required=True)
    parser.add_argument("--decision", required=True)
    parser.add_argument("--rationale", required=True)
    parser.add_argument("--decided-by", required=True)
    parser.add_argument("--alternatives")
    parser.add_argument("--task-id")
    parser.add_argument("--thread-id")
    parser.add_argument("--approval-id")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, help="Explicit existing project directory")
    subparsers = parser.add_subparsers(dest="command", required=True)

    decide = subparsers.add_parser("decide", help="Append an immutable decision")
    _decision_arguments(decide)
    supersede = subparsers.add_parser("supersede", help="Append a replacement decision")
    supersede.add_argument("decision_id")
    _decision_arguments(supersede)
    decisions = subparsers.add_parser("decisions", help="List recent decisions")
    decisions.add_argument("--limit", type=int, default=DEFAULT_DECISION_CONTEXT_COUNT)
    decisions.add_argument("--active-only", action="store_true")

    learn = subparsers.add_parser("learn", help="Add or deduplicate project knowledge")
    learn.add_argument("--kind", required=True, choices=sorted(KNOWLEDGE_KINDS))
    learn.add_argument("--body", required=True)
    learn.add_argument("--created-by", required=True)
    learn.add_argument("--source")
    learn.add_argument("--confidence", choices=sorted(CONFIDENCE_LEVELS), default="observed")
    knowledge = subparsers.add_parser("knowledge", help="List bounded project knowledge")
    knowledge.add_argument("--limit", type=int, default=DEFAULT_KNOWLEDGE_CONTEXT_COUNT)

    context = subparsers.add_parser("context", help="Refresh and print bounded context")
    context.add_argument("--decision-limit", type=int, default=DEFAULT_DECISION_CONTEXT_COUNT)
    context.add_argument("--knowledge-limit", type=int, default=DEFAULT_KNOWLEDGE_CONTEXT_COUNT)
    context.add_argument("--max-chars", type=int, default=DEFAULT_CONTEXT_CHARS)
    args = parser.parse_args(argv)

    try:
        if args.command in {"decide", "supersede"}:
            payload = {
                "title": args.title,
                "context": args.context,
                "decision": args.decision,
                "rationale": args.rationale,
                "decided_by": args.decided_by,
                "alternatives": args.alternatives,
                "task_id": args.task_id,
                "thread_id": args.thread_id,
                "approval_id": args.approval_id,
            }
            result = (
                record_decision(args.workspace, **payload)
                if args.command == "decide"
                else supersede_decision(args.workspace, args.decision_id, **payload)
            )
            _print_json(result)
        elif args.command == "decisions":
            _print_json(
                [asdict(item) for item in list_decisions(
                    args.workspace, limit=args.limit, active_only=args.active_only
                )]
            )
        elif args.command == "learn":
            _print_json(
                add_knowledge(
                    args.workspace,
                    kind=args.kind,
                    body=args.body,
                    created_by=args.created_by,
                    source=args.source,
                    confidence=args.confidence,
                )
            )
        elif args.command == "knowledge":
            _print_json([asdict(item) for item in list_knowledge(args.workspace, limit=args.limit)])
        else:
            path = refresh_context(
                args.workspace,
                decision_limit=args.decision_limit,
                knowledge_limit=args.knowledge_limit,
                max_chars=args.max_chars,
            )
            print(path.read_text(encoding="utf-8"), end="")
    except (OSError, ValueError) as exc:
        print(f"pmo project-context: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
