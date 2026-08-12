"""Behavioral performance contracts for PMO paging and idle polling."""

from __future__ import annotations

from dataclasses import dataclass

from plugins.pmo.performance import IdleBackoff, page_columns, thread_page


@dataclass(frozen=True)
class Message:
    id: int


def test_idle_backoff_resets_on_event():
    backoff = IdleBackoff()
    assert [backoff.next_delay(had_events=False) for _ in range(8)] == [
        0.3,
        0.3,
        0.5,
        1.0,
        2.0,
        3.0,
        3.0,
        3.0,
    ]
    assert backoff.next_delay(had_events=True) == 0.3
    assert backoff.next_delay(had_events=False) == 0.3


def test_board_columns_paginate_at_fifty_without_reordering():
    columns = {"todo": list(range(75)), "blocked": list(range(3))}
    pages = page_columns(columns, limit=50)

    assert pages["todo"].items == tuple(range(50))
    assert pages["todo"].total == 75
    assert pages["todo"].has_more is True
    assert pages["blocked"].items == (0, 1, 2)
    assert pages["blocked"].has_more is False

    next_page = page_columns(columns, offsets={"todo": 50}, limit=50)
    assert next_page["todo"].items == tuple(range(50, 75))
    assert next_page["todo"].has_more is False


def test_thread_load_returns_newest_hundred_and_scrolls_back():
    messages = [Message(value) for value in range(1, 5_001)]
    newest = thread_page(messages)
    older = thread_page(messages, before=newest.items[-1].id)

    assert len(newest.items) == 100
    assert newest.items[0].id == 5_000
    assert newest.items[-1].id == 4_901
    assert older.items[0].id == 4_900
    assert older.items[-1].id == 4_801
