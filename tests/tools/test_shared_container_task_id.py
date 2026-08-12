"""
Regression tests for the shared-container task_id mapping.

The top-level agent and all delegate_task subagents share a single
terminal sandbox keyed by ``"default"``.  ``_resolve_container_task_id``
is the sole gatekeeper for which tool-call task_ids go to the shared
container vs. get their own isolated sandbox.  RL / benchmark
environments opt in to isolation by calling
``register_task_env_overrides(task_id, {...})`` before the agent loop;
every other task_id collapses back to ``"default"``.

If you change the collapse logic, update both the helper and these
tests -- see `hermes-agent-dev` skill, "Why do subagents get their own
containers?" section, and the Container lifecycle paragraph under
Docker Backend in ``website/docs/user-guide/configuration.md``.
"""

import pytest

from tools import terminal_tool


@pytest.fixture(autouse=True)
def _clean_overrides():
    """Ensure no stray overrides from other tests leak in."""
    before = dict(terminal_tool._task_env_overrides)
    terminal_tool._task_env_overrides.clear()
    yield
    terminal_tool._task_env_overrides.clear()
    terminal_tool._task_env_overrides.update(before)


def test_none_task_id_maps_to_default():
    assert terminal_tool._resolve_container_task_id(None) == "default"


def test_empty_task_id_maps_to_default():
    assert terminal_tool._resolve_container_task_id("") == "default"


def test_cwd_only_override_collapses_to_default():
    """CWD-only overrides (ACP adapter workspace tracking) must NOT trigger
    container isolation — they should collapse to the shared 'default'
    container so all surfaces (TUI, gateway, dashboard) share one sandbox.
    Regression for #37361."""
    terminal_tool.register_task_env_overrides(
        "acp-session-abc", {"cwd": "/home/user/project"}
    )
    try:
        assert (
            terminal_tool._resolve_container_task_id("acp-session-abc")
            == "default"
        )
    finally:
        terminal_tool.clear_task_env_overrides("acp-session-abc")


def test_env_type_override_keeps_own_id():
    """env_type is an isolation key — must trigger per-task container."""
    terminal_tool.register_task_env_overrides(
        "bench-env", {"env_type": "sandbox", "cwd": "/work"}
    )
    try:
        assert (
            terminal_tool._resolve_container_task_id("bench-env")
            == "bench-env"
        )
    finally:
        terminal_tool.clear_task_env_overrides("bench-env")


def test_explicit_sandbox_key_groups_only_that_project():
    terminal_tool.register_task_env_overrides(
        "pm-session", {"env_type": "docker", "sandbox_key": "pmo-project-a"}
    )
    try:
        assert terminal_tool._resolve_container_task_id("pm-session") == "pmo-project-a"
    finally:
        terminal_tool.clear_task_env_overrides("pm-session")


def test_sandbox_registration_does_not_mutate_stale_raw_environment(monkeypatch):
    class RawEnvironment:
        cwd = "/host/project"

    raw = RawEnvironment()
    monkeypatch.setattr(
        terminal_tool, "_active_environments", {"pm-session": raw}
    )

    terminal_tool.register_task_env_overrides(
        "pm-session",
        {
            "env_type": "docker",
            "sandbox_key": "pmo-project-a",
            "cwd": "/workspace",
        },
    )

    assert raw.cwd == "/host/project"
    assert terminal_tool.get_active_env("pm-session") is None


def test_repeated_sandbox_registration_preserves_live_cwd(monkeypatch):
    class SandboxEnvironment:
        cwd = "/workspace/src"

    sandbox = SandboxEnvironment()
    overrides = {
        "env_type": "docker",
        "sandbox_key": "pmo-project-a",
        "cwd": "/workspace",
    }
    monkeypatch.setattr(
        terminal_tool, "_active_environments", {"pmo-project-a": sandbox}
    )
    monkeypatch.setattr(
        terminal_tool, "_session_cwd", {"pm-session": "/workspace/src"}
    )

    terminal_tool.register_task_env_overrides("pm-session", overrides)
    sandbox.cwd = "/workspace/src"
    terminal_tool.record_session_cwd("pm-session", "/workspace/src")
    terminal_tool.register_task_env_overrides("pm-session", dict(overrides))

    assert sandbox.cwd == "/workspace/src"
    assert terminal_tool.get_session_cwd("pm-session") == "/workspace/src"


def test_shared_sandbox_cleanup_requires_force_remove(monkeypatch):
    cleaned = []

    class SandboxEnvironment:
        def cleanup(self, *, force_remove=False):
            cleaned.append(force_remove)

    sandbox = SandboxEnvironment()
    monkeypatch.setattr(
        terminal_tool, "_active_environments", {"pmo-project-a": sandbox}
    )
    monkeypatch.setattr(
        terminal_tool, "_last_activity", {"pmo-project-a": 123.0}
    )
    monkeypatch.setattr(terminal_tool, "_creation_locks", {})
    terminal_tool.register_task_env_overrides(
        "pm-session",
        {"env_type": "docker", "sandbox_key": "pmo-project-a"},
    )

    terminal_tool.cleanup_vm("pm-session")

    assert cleaned == []
    assert terminal_tool._active_environments["pmo-project-a"] is sandbox

    terminal_tool.cleanup_vm("pm-session", force_remove=True)

    assert cleaned == [True]
    assert "pmo-project-a" not in terminal_tool._active_environments
    assert "pmo-project-a" not in terminal_tool._last_activity


def test_task_backend_override_is_applied_without_mutating_global_config():
    base = {
        "env_type": "local",
        "cwd": "/host",
        "docker_volumes": ["/global:/workspace"],
    }
    overrides = {
        "env_type": "docker",
        "cwd": "/workspace",
        "docker_volumes": ["/project:/workspace"],
        "docker_strict_mounts": True,
        "untrusted_metadata": "ignored",
    }

    merged = terminal_tool._apply_task_config_overrides(base, overrides)

    assert merged["env_type"] == "docker"
    assert merged["cwd"] == "/workspace"
    assert merged["docker_volumes"] == ["/project:/workspace"]
    assert merged["docker_strict_mounts"] is True
    assert "untrusted_metadata" not in merged
    assert base == {
        "env_type": "local",
        "cwd": "/host",
        "docker_volumes": ["/global:/workspace"],
    }


def test_task_workdir_maps_host_subdirectory_into_project_mount(tmp_path):
    project = tmp_path / "project"
    nested = project / "src" / "api"
    nested.mkdir(parents=True)
    overrides = {"workdir_mappings": [(str(project), "/workspace")]}

    assert terminal_tool._map_task_workdir(str(project), overrides) == "/workspace"
    assert terminal_tool._map_task_workdir(str(nested), overrides) == "/workspace/src/api"
    assert terminal_tool._map_task_workdir(str(tmp_path / "other"), overrides) == str(
        tmp_path / "other"
    )
