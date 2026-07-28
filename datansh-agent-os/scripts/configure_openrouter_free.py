from __future__ import annotations

from pathlib import Path

from datansh_common import get_env, is_free_model, is_placeholder


def main() -> int:
    model = get_env("DATANSH_DEFAULT_MODEL", "openrouter/free")
    aux_model = get_env("DATANSH_AUX_MODEL", "openrouter/free")
    if not is_free_model(model) or not is_free_model(aux_model):
        print("Refusing to configure non-free model. Use openrouter/free or explicit :free IDs.")
        return 1

    hermes_home = Path(get_env("HERMES_HOME") or Path.home() / ".hermes")
    hermes_home.mkdir(parents=True, exist_ok=True)
    env_path = hermes_home / ".env"
    config_path = hermes_home / "config.yaml"

    api_key = get_env("OPENROUTER_API_KEY")
    if not is_placeholder(api_key):
        existing = env_path.read_text(encoding="utf-8", errors="replace") if env_path.exists() else ""
        lines = [line for line in existing.splitlines() if not line.startswith("OPENROUTER_API_KEY=")]
        lines.append(f"OPENROUTER_API_KEY={api_key}")
        env_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        print(f"Wrote OPENROUTER_API_KEY to {env_path} without printing the key.")
    else:
        print("OPENROUTER_API_KEY not configured in Datansh .env; skipped writing Hermes secret.")

    if config_path.exists():
        backup = config_path.with_suffix(".yaml.datansh-backup")
        backup.write_text(config_path.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
        print(f"Backed up existing config to {backup}.")

    config = f"""# Datansh Agent OS free-only Hermes config.
model:
  provider: openrouter
  default: {model}
  base_url: ''
  api_mode: chat_completions

auxiliary:
  title_generation:
    provider: openrouter
    model: {aux_model}
    base_url: ''
    api_key: ''
    timeout: 120
    extra_body: {{}}
    download_timeout: 30
  compression:
    provider: openrouter
    model: {aux_model}
    base_url: ''
    api_key: ''
    timeout: 120
    extra_body: {{}}
    download_timeout: 30

dashboard:
  host: 127.0.0.1
  port: 9119
"""
    config_path.write_text(config, encoding="utf-8")
    print(f"Wrote free-only Hermes config to {config_path}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
