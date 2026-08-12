"""Cost-trend behavior for the opt-in PMO live lane."""

from __future__ import annotations

import json

from scripts.pmo_live_trend import markdown, update_trend


def _record(cost: float, model: str = "test/model") -> dict:
    return {
        "schema": "datansh-pmo-live-cost.v1",
        "provider": "openrouter",
        "model": model,
        "estimated_cost_usd": cost,
        "ticket_count": 3,
    }


def test_live_cost_history_is_bounded_and_rendered(tmp_path):
    current = tmp_path / "current.json"
    history = tmp_path / "history.json"
    history.write_text(
        json.dumps([_record(float(value)) for value in range(35)]),
        encoding="utf-8",
    )
    current.write_text(json.dumps(_record(0.25)), encoding="utf-8")

    records = update_trend(current, history, keep=30)

    assert len(records) == 30
    assert records[-1]["estimated_cost_usd"] == 0.25
    assert len(json.loads(history.read_text(encoding="utf-8"))) == 30
    summary = markdown(records)
    assert "PM-OS live acceptance cost" in summary
    assert "test/model" in summary
