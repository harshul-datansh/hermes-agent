"""Regression tests for task/session cwd propagation in terminal_tool."""

import json
from types import SimpleNamespace

import tools.terminal_tool as terminal_tool


def _minimal_terminal_config(cwd="/default"):
    return {
        "env_type": "local",
        "cwd": cwd,
        "timeout": 60,
        "lifetime_seconds": 3600,
    }


def test_foreground_command_uses_registered_task_cwd_for_existing_environment(monkeypatch):
    """ACP can update task cwd after the local env exists; foreground must honor it."""
    calls = []

    class FakeEnv:
        env = {}

        def execute(self, command, **kwargs):
            calls.append((command, kwargs))
            return {"output": "ok", "returncode": 0}

    task_id = "acp-session-1"
    monkeypatch.setattr(terminal_tool, "_active_environments", {task_id: FakeEnv()})
    monkeypatch.setattr(terminal_tool, "_last_activity", {})
    monkeypatch.setattr(terminal_tool, "_task_env_overrides", {task_id: {"cwd": "/workspace/acp"}})
    monkeypatch.setattr(terminal_tool, "_get_env_config", lambda: _minimal_terminal_config())
    monkeypatch.setattr(
        terminal_tool,
        "_check_all_guards",
        lambda command, env_type, **kwargs: {"approved": True},
    )

    result = json.loads(terminal_tool.terminal_tool(command="pwd", task_id=task_id))

    assert result["exit_code"] == 0
    assert calls == [("pwd", {"timeout": 60, "cwd": "/workspace/acp", "bounded_capture": True})]


def test_session_id_only_resolves_registered_project_sandbox(monkeypatch):
    """Gateway session identity must select the PMO sandbox without task_id."""

    calls = []

    class FakeEnv:
        env = {}

        def execute(self, command, **kwargs):
            calls.append((command, kwargs))
            return {"output": "/workspace", "returncode": 0}

    session_id = "pmo-session-only"
    sandbox_key = "pmo-project-sandbox"
    monkeypatch.setattr(
        terminal_tool,
        "_task_env_overrides",
        {
            session_id: {
                "env_type": "docker",
                "sandbox_key": sandbox_key,
                "docker_image": "datansh-pm-os:sandbox",
                "cwd": "/workspace",
            }
        },
    )
    monkeypatch.setattr(terminal_tool, "_active_environments", {sandbox_key: FakeEnv()})
    monkeypatch.setattr(terminal_tool, "_last_activity", {})
    monkeypatch.setattr(terminal_tool, "_get_env_config", lambda: _minimal_terminal_config())
    monkeypatch.setattr(
        terminal_tool,
        "_check_all_guards",
        lambda command, env_type, **kwargs: {"approved": True},
    )

    result = json.loads(
        terminal_tool.terminal_tool(command="pwd", session_id=session_id)
    )

    assert result["exit_code"] == 0
    assert calls == [("pwd", {"timeout": 60, "cwd": "/workspace", "bounded_capture": True})]


def test_existing_host_session_cwd_maps_into_project_sandbox(monkeypatch, tmp_path):
    calls = []

    class FakeEnv:
        env = {}
        cwd = "/workspace"

        def execute(self, command, **kwargs):
            calls.append(kwargs)
            return {"output": "/workspace", "returncode": 0}

    project = tmp_path / "project"
    project.mkdir()
    session_id = "pmo-existing-chat"
    sandbox_key = "pmo-existing-chat-sandbox"
    monkeypatch.setattr(
        terminal_tool,
        "_task_env_overrides",
        {
            session_id: {
                "env_type": "docker",
                "sandbox_key": sandbox_key,
                "docker_image": "datansh-pm-os:sandbox",
                "cwd": "/workspace",
                "workdir_mappings": [(str(project), "/workspace")],
            }
        },
    )
    monkeypatch.setattr(terminal_tool, "_active_environments", {sandbox_key: FakeEnv()})
    monkeypatch.setattr(terminal_tool, "_last_activity", {})
    monkeypatch.setattr(
        terminal_tool, "_session_cwd", {session_id: str(project)}
    )
    monkeypatch.setattr(terminal_tool, "_get_env_config", lambda: _minimal_terminal_config())
    monkeypatch.setattr(
        terminal_tool,
        "_check_all_guards",
        lambda command, env_type, **kwargs: {"approved": True},
    )

    result = json.loads(
        terminal_tool.terminal_tool(command="pwd", session_id=session_id)
    )

    assert result["exit_code"] == 0
    assert calls == [{"timeout": 60, "cwd": "/workspace", "bounded_capture": True}]
    assert terminal_tool.get_session_cwd(session_id) == "/workspace"


def test_project_sandbox_never_reuses_raw_session_environment(monkeypatch):
    """A stale local/raw environment must not bypass a new sandbox key."""

    calls = []

    class UnsafeRawEnv:
        env = {}
        cwd = "/host/project"

        def execute(self, *_args, **_kwargs):
            raise AssertionError("raw session environment bypassed sandbox key")

    class ProjectSandboxEnv:
        env = {}
        cwd = "/workspace"

        def execute(self, command, **kwargs):
            calls.append((command, kwargs))
            return {"output": "/workspace", "returncode": 0}

    session_id = "reused-session"
    sandbox_key = "pmo-isolated-project"
    monkeypatch.setattr(
        terminal_tool,
        "_task_env_overrides",
        {
            session_id: {
                "env_type": "docker",
                "sandbox_key": sandbox_key,
                "docker_image": "datansh-pm-os:sandbox",
                "cwd": "/workspace",
            }
        },
    )
    monkeypatch.setattr(
        terminal_tool, "_active_environments", {session_id: UnsafeRawEnv()}
    )
    monkeypatch.setattr(terminal_tool, "_last_activity", {})
    monkeypatch.setattr(terminal_tool, "_creation_locks", {})
    monkeypatch.setattr(terminal_tool, "_get_env_config", lambda: _minimal_terminal_config())
    monkeypatch.setattr(terminal_tool, "_start_cleanup_thread", lambda: None)
    monkeypatch.setattr(
        terminal_tool,
        "_check_all_guards",
        lambda command, env_type, **kwargs: {"approved": True},
    )
    monkeypatch.setattr(
        terminal_tool,
        "_create_environment",
        lambda **kwargs: ProjectSandboxEnv(),
    )

    result = json.loads(
        terminal_tool.terminal_tool(command="pwd", session_id=session_id)
    )

    assert result["exit_code"] == 0
    assert calls == [
        ("pwd", {"timeout": 60, "cwd": "/workspace", "bounded_capture": True})
    ]
    assert sandbox_key in terminal_tool._active_environments


def test_explicit_workdir_still_wins_over_registered_task_cwd(monkeypatch):
    calls = []

    class FakeEnv:
        env = {}

        def execute(self, command, **kwargs):
            calls.append(kwargs)
            return {"output": "ok", "returncode": 0}

    task_id = "acp-session-1"
    monkeypatch.setattr(terminal_tool, "_active_environments", {task_id: FakeEnv()})
    monkeypatch.setattr(terminal_tool, "_last_activity", {})
    monkeypatch.setattr(terminal_tool, "_task_env_overrides", {task_id: {"cwd": "/workspace/acp"}})
    monkeypatch.setattr(terminal_tool, "_get_env_config", lambda: _minimal_terminal_config())
    monkeypatch.setattr(
        terminal_tool,
        "_check_all_guards",
        lambda command, env_type, **kwargs: {"approved": True},
    )

    result = json.loads(
        terminal_tool.terminal_tool(
            command="pwd",
            task_id=task_id,
            workdir="/explicit/workdir",
        )
    )

    assert result["exit_code"] == 0
    assert calls == [{"timeout": 60, "cwd": "/explicit/workdir", "bounded_capture": True}]


def test_explicit_workdir_does_not_persist_into_session_cwd(monkeypatch):
    """A per-command ``workdir`` must not hijack the durable session cwd.

    Regression: the post-command dual-write recorded ``env.cwd`` (stamped to
    the transient ``workdir``) into the session-cwd store, so every later
    command that omitted ``workdir`` inherited the one-off directory.
    """
    recorded = []

    class FakeEnv:
        env = {}
        cwd = "/workspace/acp"

        def execute(self, command, **kwargs):
            # Marker parse stamps env.cwd to where the command ran.
            self.cwd = kwargs.get("cwd", self.cwd)
            return {"output": "ok", "returncode": 0}

    task_id = "acp-session-2"
    monkeypatch.setattr(terminal_tool, "_active_environments", {task_id: FakeEnv()})
    monkeypatch.setattr(terminal_tool, "_last_activity", {})
    monkeypatch.setattr(terminal_tool, "_task_env_overrides", {task_id: {"cwd": "/workspace/acp"}})
    monkeypatch.setattr(terminal_tool, "_get_env_config", lambda: _minimal_terminal_config())
    monkeypatch.setattr(
        terminal_tool,
        "_check_all_guards",
        lambda command, env_type, **kwargs: {"approved": True},
    )
    monkeypatch.setattr(
        terminal_tool,
        "record_session_cwd",
        lambda session_key, cwd: recorded.append((session_key, cwd)),
    )

    terminal_tool.terminal_tool(command="pwd", task_id=task_id, workdir="/one/off/dir")

    # The transient workdir must NOT have been recorded as the session cwd.
    assert all(cwd != "/one/off/dir" for _, cwd in recorded), recorded


def test_background_command_prefers_recorded_session_cwd_over_init_time_cwd(monkeypatch):
    """Background process launches must also use the recorded session cwd."""

    class FakeEnv:
        env = {}
        cwd = "/workspace/live"

    class FakeRegistry:
        def __init__(self):
            self.calls = []
            self.pending_watchers = []

        def spawn_local(self, **kwargs):
            self.calls.append(kwargs)
            return SimpleNamespace(id="proc_test", pid=1234)

    import tools.process_registry as process_registry_mod

    registry = FakeRegistry()
    task_id = "session-live-cwd-bg"
    monkeypatch.setattr(terminal_tool, "_active_environments", {task_id: FakeEnv()})
    monkeypatch.setattr(terminal_tool, "_last_activity", {})
    monkeypatch.setattr(terminal_tool, "_session_cwd", {})
    monkeypatch.setattr(terminal_tool, "_task_env_overrides", {task_id: {"cwd": "/workspace/init"}})
    monkeypatch.setattr(terminal_tool, "_get_env_config", lambda: _minimal_terminal_config(cwd="/workspace/init"))
    monkeypatch.setattr(terminal_tool, "_start_cleanup_thread", lambda: None)
    monkeypatch.setattr(terminal_tool, "_resolve_container_task_id", lambda value: value or "default")
    monkeypatch.setattr(
        terminal_tool,
        "_check_all_guards",
        lambda command, env_type, **kwargs: {"approved": True},
    )
    monkeypatch.setattr(process_registry_mod, "process_registry", registry)
    terminal_tool.record_session_cwd(task_id, "/workspace/live")

    result = json.loads(
        terminal_tool.terminal_tool(
            command="sleep 1",
            task_id=task_id,
            background=True,
        )
    )

    assert result["exit_code"] == 0
    # session_key falls back to the raw task_id when no gateway contextvar is set
    # (it doesn't propagate to tool-worker threads), so process.kill / stop can
    # still find and terminate this background process.
    assert registry.calls == [{
        "command": "sleep 1",
        "cwd": "/workspace/live",
        "task_id": task_id,
        "session_key": task_id,
        "env_vars": {},
        "use_pty": False,
    }]


def test_safe_getcwd_falls_back_to_home_when_no_terminal_cwd(monkeypatch):
    def _boom():
        raise FileNotFoundError()

    monkeypatch.setattr(terminal_tool.os, "getcwd", _boom)
    monkeypatch.delenv("TERMINAL_CWD", raising=False)
    monkeypatch.setattr(terminal_tool.os.path, "expanduser", lambda p: "/home/me")
    assert terminal_tool._safe_getcwd() == "/home/me"
