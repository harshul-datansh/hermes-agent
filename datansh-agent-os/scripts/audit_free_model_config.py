from __future__ import annotations

import re
from pathlib import Path

from datansh_common import ROOT, get_env, is_free_model, load_env

FORBIDDEN_PATTERNS = [
    "openrouter/auto",
    "anthropic/",
    "claude",
    "openai/gpt",
    "gpt-5",
    "gpt-4",
    "gemini",
    "mistral-large",
]


def candidates() -> list[Path]:
    paths = [ROOT / ".env.example", ROOT / ".env"]
    home = Path.home()
    hermes_home = Path(get_env("HERMES_HOME") or home / ".hermes")
    paths.extend([hermes_home / "config.yaml", hermes_home / ".env"])
    return [path for path in paths if path.exists()]


def extract_models(text: str) -> list[str]:
    models: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if re.search(r"(?i)(model|default|fallback|DATANSH_DEFAULT_MODEL|DATANSH_AUX_MODEL)", stripped):
            for match in re.findall(r"([A-Za-z0-9_.-]+/[A-Za-z0-9_.:-]+)", stripped):
                models.append(match)
    return models


def main() -> int:
    load_env()
    failures: list[str] = []
    inspected: list[str] = []
    for path in candidates():
        text = path.read_text(encoding="utf-8", errors="replace")
        inspected.append(str(path))
        lowered = text.lower()
        for pattern in FORBIDDEN_PATTERNS:
            if pattern in lowered:
                if pattern == "gemini" and ":free" in lowered:
                    continue
                failures.append(f"{path}: forbidden model/fallback marker `{pattern}` found")
        for model in extract_models(text):
            if "openrouter" in model or model.endswith(":free"):
                if not is_free_model(model):
                    failures.append(f"{path}: `{model}` is not allowed for the free-only POC")

    if failures:
        print("Free-model audit failed:")
        for item in failures:
            print(f"- {item}")
        return 1

    print("Free-model audit passed.")
    print("Inspected:")
    for path in inspected:
        print(f"- {path}")
    print("Allowed configured models: openrouter/free or explicit :free model IDs only.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

