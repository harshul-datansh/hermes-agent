"""Maintain a bounded cost trend for the opt-in PM-OS live acceptance lane."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def update_trend(current: Path, history: Path, *, keep: int = 30) -> list[dict]:
    record = json.loads(current.read_text(encoding="utf-8"))
    if record.get("schema") != "datansh-pmo-live-cost.v1":
        raise ValueError("current result is not a PM-OS live cost record")
    records: list[dict] = []
    if history.is_file():
        loaded = json.loads(history.read_text(encoding="utf-8"))
        if isinstance(loaded, list):
            records = [item for item in loaded if isinstance(item, dict)]
    records.append(record)
    records = records[-max(1, keep) :]
    history.write_text(json.dumps(records, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return records


def markdown(records: list[dict]) -> str:
    current = records[-1]
    costs = [float(item.get("estimated_cost_usd") or 0.0) for item in records]
    baseline = sum(costs[:-1]) / len(costs[:-1]) if len(costs) > 1 else costs[-1]
    ratio = costs[-1] / baseline if baseline else 1.0
    return (
        "## PM-OS live acceptance cost\n\n"
        f"Current: **${costs[-1]:.4f}** · prior mean: **${baseline:.4f}** · "
        f"ratio: **{ratio:.2f}×** · samples: **{len(records)}**\n\n"
        f"Model: `{current.get('model', 'unknown')}` · tickets: "
        f"**{int(current.get('ticket_count') or 0)}**\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--current", type=Path, required=True)
    parser.add_argument("--history", type=Path, required=True)
    parser.add_argument("--summary", type=Path)
    parser.add_argument("--keep", type=int, default=30)
    args = parser.parse_args()
    records = update_trend(args.current, args.history, keep=args.keep)
    rendered = markdown(records)
    print(rendered)
    if args.summary:
        with args.summary.open("a", encoding="utf-8") as handle:
            handle.write(rendered + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

