from __future__ import annotations

import json
import time
import urllib.request
from datetime import datetime

from datansh_common import ROOT, call_openrouter, get_env, is_free_model, is_placeholder, write_text


NON_TEXT_HINTS = ("audio", "music", "image", "embedding", "rerank", "moderation", "tts", "stt")
QUALITY_HINTS = ("code", "coding", "reason", "agent", "tool", "program", "math", "instruct")


def price_zero(model: dict) -> bool:
    pricing = model.get("pricing") or {}
    return str(pricing.get("prompt", "")).strip() == "0" and str(pricing.get("completion", "")).strip() == "0"


def score(model: dict) -> int:
    text = " ".join(str(model.get(k, "")).lower() for k in ("id", "name", "description"))
    value = 0
    if model.get("id", "").endswith(":free"):
        value += 50
    if price_zero(model):
        value += 40
    context = int(model.get("context_length") or 0)
    if context >= 64000:
        value += 20
    if context >= 200000:
        value += 10
    value += sum(8 for hint in QUALITY_HINTS if hint in text)
    value -= sum(50 for hint in NON_TEXT_HINTS if hint in text)
    return value


def why(model: dict) -> str:
    notes = []
    model_id = model.get("id", "")
    context = int(model.get("context_length") or 0)
    text = f"{model_id} {model.get('name', '')} {model.get('description', '')}".lower()
    if model_id.endswith(":free"):
        notes.append("explicit :free endpoint")
    if context >= 64000:
        notes.append(f"{context:,} context")
    hits = [hint for hint in QUALITY_HINTS if hint in text]
    if hits:
        notes.append("matches " + ", ".join(sorted(set(hits))[:3]))
    return "; ".join(notes) or "zero-cost text candidate"


def main() -> int:
    with urllib.request.urlopen("https://openrouter.ai/api/v1/models", timeout=60) as response:
        payload = json.loads(response.read().decode("utf-8"))

    models = payload.get("data", [])
    free = []
    for model in models:
        model_id = model.get("id", "")
        text = f"{model_id} {model.get('name', '')} {model.get('description', '')}".lower()
        if any(hint in text for hint in NON_TEXT_HINTS):
            continue
        if model_id == "openrouter/free" or model_id.endswith(":free") or price_zero(model):
            row = dict(model)
            row["_datansh_score"] = score(row)
            row["_datansh_why"] = why(row)
            free.append(row)

    free.sort(key=lambda item: item["_datansh_score"], reverse=True)
    date = datetime.now().strftime("%Y-%m-%d")
    json_path = ROOT / "docs" / f"openrouter-free-models-{date}.json"
    json_path.write_text(json.dumps(free, indent=2, ensure_ascii=False), encoding="utf-8")

    top = [{"id": "openrouter/free", "context_length": "", "_datansh_why": "free router; easiest setup"}]
    top.extend([m for m in free if m.get("id") != "openrouter/free"][:5])

    smoke_rows = []
    key = get_env("OPENROUTER_API_KEY")
    if not is_placeholder(key):
        for model in [m["id"] for m in top[:3] if is_free_model(m["id"])]:
            started = time.perf_counter()
            content, meta = call_openrouter(
                "Reply with exactly: Datansh free model smoke test passed.",
                model,
                temperature=0,
            )
            smoke_rows.append(
                {
                    "model": model,
                    "passed": bool(content and "Datansh free model smoke test passed." in content),
                    "latency_ms": int((time.perf_counter() - started) * 1000),
                    "mode": meta.get("mode"),
                    "notes": meta.get("error") or (content or "")[:120],
                }
            )
    else:
        smoke_rows.append({"model": "openrouter/free", "passed": False, "mode": "skipped", "notes": "OPENROUTER_API_KEY not configured"})

    table = [
        "# OpenRouter Free Models Summary",
        "",
        f"Generated: {datetime.now().isoformat(timespec='seconds')}",
        "",
        f"Live API candidates saved to `{json_path.name}`.",
        "",
        "| Rank | Model ID | Context | Why Candidate | Risk |",
        "| --- | --- | ---: | --- | --- |",
    ]
    for index, model in enumerate(top[:6], start=1):
        model_id = model.get("id", "")
        context = model.get("context_length", "")
        risk = "Random free model choice" if model_id == "openrouter/free" else "Availability and tool-call quality unproven"
        table.append(f"| {index} | `{model_id}` | {context} | {model.get('_datansh_why', '')} | {risk} |")
    table.extend(["", "## Smoke Test Matrix", "", "| Model | Passed | Mode | Latency ms | Notes |", "| --- | --- | --- | ---: | --- |"])
    for row in smoke_rows:
        notes = str(row.get("notes", "")).replace("|", "\\|").replace("\n", " ")[:180]
        table.append(f"| `{row['model']}` | {row['passed']} | {row.get('mode', '')} | {row.get('latency_ms', '')} | {notes} |")
    write_text(ROOT / "docs" / "openrouter-free-models-summary.md", "\n".join(table))
    print(f"Discovered {len(free)} free candidates. Wrote {json_path}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
