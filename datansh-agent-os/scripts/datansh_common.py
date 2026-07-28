from __future__ import annotations

import json
import os
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
ENV_EXAMPLE_PATH = ROOT / ".env.example"
EVENTS_PATH = ROOT / os.environ.get("DATANSH_EVENTS_PATH", "monitor/events.jsonl")
RUNS_DIR = ROOT / os.environ.get("DATANSH_RUNS_DIR", "demo/runs")


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def run_id() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H-%M-%S")


def load_env() -> dict[str, str]:
    values: dict[str, str] = {}
    for path in (ENV_EXAMPLE_PATH, ENV_PATH):
        if not path.exists():
            continue
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            value = value.strip().strip('"').strip("'")
            values[key.strip()] = value
    for key, value in values.items():
        if key not in os.environ and value and not is_placeholder(value):
            os.environ[key] = value
    return values


def get_env(name: str, default: str = "") -> str:
    load_env()
    return os.environ.get(name, default)


def is_placeholder(value: str) -> bool:
    normalized = value.strip().lower()
    return (
        not normalized
        or normalized.startswith("<")
        or "your-key" in normalized
        or "change-me" in normalized
    )


def is_free_model(model: str) -> bool:
    model = model.strip()
    return model == "openrouter/free" or model.endswith(":free")


def redact(text: str) -> str:
    openrouter_prefix = "sk" + "-or-"
    text = re.sub(re.escape(openrouter_prefix) + r"[A-Za-z0-9_\-]+", openrouter_prefix + "REDACTED", text)
    text = re.sub(r"(?i)(OPENROUTER_API_KEY=)[^\s]+", r"\1REDACTED", text)
    return text


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def append_event(
    run: str,
    agent: str,
    event_type: str,
    status: str,
    task: str,
    message: str,
    output_path: str | None = None,
    model: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    EVENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "run_id": run,
        "event_id": str(uuid.uuid4()),
        "timestamp": now_iso(),
        "agent": agent,
        "status": status,
        "event_type": event_type,
        "task": task,
        "model": model or get_env("DATANSH_DEFAULT_MODEL", "openrouter/free"),
        "message": redact(message),
        "output_path": output_path,
        "metadata": metadata or {},
    }
    with EVENTS_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def read_text(path: Path, fallback: str = "") -> str:
    if not path.exists():
        return fallback
    return path.read_text(encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(redact(text).rstrip() + "\n", encoding="utf-8")


def append_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(redact(text))


def brain_context(max_chars: int = 16000) -> str:
    brain_dir = ROOT / "datansh-brain"
    parts = []
    for path in sorted(brain_dir.glob("*.md")):
        parts.append(f"\n\n--- {path.name} ---\n{read_text(path)}")
    return "".join(parts)[-max_chars:]


def role_prompt(role_file: str) -> str:
    return read_text(ROOT / "datansh-agents" / role_file)


def call_openrouter(prompt: str, model: str, temperature: float = 0.2) -> tuple[str | None, dict[str, Any]]:
    import urllib.error
    import urllib.request

    if not is_free_model(model):
        raise ValueError(f"Refusing non-free model for POC: {model}")
    api_key = get_env("OPENROUTER_API_KEY")
    if is_placeholder(api_key):
        return None, {"mode": "offline", "reason": "OPENROUTER_API_KEY not configured"}

    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
    }
    data = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=data,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": get_env("OPENROUTER_SITE_URL", "http://localhost:8501"),
            "X-Title": get_env("OPENROUTER_APP_NAME", "Datansh Agent OS"),
        },
        method="POST",
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")[:1200]
        return None, {"mode": "live_failed", "status": exc.code, "error": redact(details)}
    except Exception as exc:
        return None, {"mode": "live_failed", "error": redact(str(exc))}
    latency_ms = int((time.perf_counter() - started) * 1000)
    content = payload.get("choices", [{}])[0].get("message", {}).get("content", "")
    usage = payload.get("usage", {})
    return content, {
        "mode": "live",
        "latency_ms": latency_ms,
        "tokens_in": usage.get("prompt_tokens"),
        "tokens_out": usage.get("completion_tokens"),
    }
