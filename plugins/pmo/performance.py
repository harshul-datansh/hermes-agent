"""Bounded performance helpers for the copied PMO dashboard.

The helpers contain no database/runtime replacement.  They shape values
returned by the existing Kanban APIs and provide the idle WebSocket schedule
used by the copied dashboard route.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from time import perf_counter
from typing import Callable, Iterable, Mapping, Sequence, TypeVar

from hermes_cli import kanban_db


T = TypeVar("T")
IDLE_BACKOFF_SECONDS: tuple[float, ...] = (0.3, 0.3, 0.5, 1.0, 2.0, 3.0)
DEFAULT_COLUMN_PAGE_SIZE = 50
MAX_COLUMN_PAGE_SIZE = 100
DEFAULT_THREAD_PAGE_SIZE = 100
MAX_THREAD_PAGE_SIZE = 200


@dataclass
class IdleBackoff:
    """Per-connection idle polling state; any event returns to 300 ms."""

    schedule: tuple[float, ...] = IDLE_BACKOFF_SECONDS
    _index: int = 0

    def next_delay(self, *, had_events: bool) -> float:
        if not self.schedule:
            raise ValueError("idle backoff schedule must not be empty")
        if had_events:
            self._index = 0
            return self.schedule[0]
        delay = self.schedule[min(self._index, len(self.schedule) - 1)]
        self._index = min(self._index + 1, len(self.schedule) - 1)
        return delay


@dataclass(frozen=True)
class Page:
    items: tuple[T, ...]
    offset: int
    limit: int
    total: int
    has_more: bool


def page(
    values: Sequence[T] | Iterable[T],
    *,
    offset: int = 0,
    limit: int = DEFAULT_COLUMN_PAGE_SIZE,
    max_limit: int = MAX_COLUMN_PAGE_SIZE,
) -> Page[T]:
    """Return a deterministic bounded page without changing item ordering."""

    start = max(0, int(offset))
    size = min(max(1, int(limit)), max_limit)
    materialized = values if isinstance(values, Sequence) else tuple(values)
    total = len(materialized)
    selected = tuple(materialized[start : start + size])
    return Page(
        items=selected,
        offset=start,
        limit=size,
        total=total,
        has_more=start + len(selected) < total,
    )


def page_columns(
    columns: Mapping[str, Sequence[T]],
    *,
    offsets: Mapping[str, int] | None = None,
    limit: int = DEFAULT_COLUMN_PAGE_SIZE,
) -> dict[str, Page[T]]:
    offsets = offsets or {}
    return {
        name: page(values, offset=offsets.get(name, 0), limit=limit)
        for name, values in columns.items()
    }


def thread_page(
    values: Sequence[T] | Iterable[T],
    *,
    before: int | None = None,
    limit: int = DEFAULT_THREAD_PAGE_SIZE,
    id_of: Callable[[T], int] = lambda item: int(getattr(item, "id")),
) -> Page[T]:
    """Return the newest bounded messages, with ``before`` for scroll-back."""

    size = min(max(1, int(limit)), MAX_THREAD_PAGE_SIZE)
    ordered = sorted(values, key=id_of, reverse=True)
    if before is not None:
        ordered = [item for item in ordered if id_of(item) < int(before)]
    return page(ordered, offset=0, limit=size, max_limit=MAX_THREAD_PAGE_SIZE)


@dataclass(frozen=True)
class PerfSnapshot:
    board_query_p50_ms: float
    board_query_p95_ms: float
    thread_load_p50_ms: float
    ticket_count: int
    largest_thread_messages: int


def _percentile(samples: Sequence[float], percentile: float) -> float:
    if not samples:
        return 0.0
    ordered = sorted(samples)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[min(index, len(ordered) - 1)]


def _timed(operation: Callable[[], object], repeats: int) -> list[float]:
    samples: list[float] = []
    for _ in range(max(1, repeats)):
        started = perf_counter()
        operation()
        samples.append((perf_counter() - started) * 1_000)
    return samples


def measure_board(
    board_slug: str,
    *,
    thread_task_id: str | None = None,
    repeats: int = 5,
) -> PerfSnapshot:
    """Measure public Kanban reads; no write transaction spans measurement."""

    with kanban_db.connect_closing(board=board_slug) as conn:
        board_samples = _timed(
            lambda: kanban_db.list_tasks(conn, include_archived=True), repeats
        )
        tasks = kanban_db.list_tasks(conn, include_archived=True)
        selected_thread = thread_task_id or (tasks[0].id if tasks else None)
        if selected_thread:
            thread_samples = _timed(
                lambda: kanban_db.list_comments(conn, selected_thread), repeats
            )
            message_count = len(kanban_db.list_comments(conn, selected_thread))
        else:
            thread_samples = [0.0]
            message_count = 0
    return PerfSnapshot(
        board_query_p50_ms=_percentile(board_samples, 0.50),
        board_query_p95_ms=_percentile(board_samples, 0.95),
        thread_load_p50_ms=_percentile(thread_samples, 0.50),
        ticket_count=len(tasks),
        largest_thread_messages=message_count,
    )

