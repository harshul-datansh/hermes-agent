"""Project-specific agent profiles, model routing, and credential isolation.

Covers the three properties that make one Hermes install able to serve
several client projects at once:

* agent profiles are named ``<role>-<slug>`` and are per project;
* a model route can be pinned per agent, not just per role;
* a project's provider credentials land in that project's profile, never in
  another project's or unconditionally in the global store.
"""

from __future__ import annotations

import pytest

from plugins.pmo import agents as agents_mod


class TestProfileNaming:
    def test_profile_is_role_and_slug(self):
        assert agents_mod.profile_for("dev", "hedgi") == "dev-hedgi"
        assert agents_mod.profile_for("qa", "hedgi") == "qa-hedgi"
        assert agents_mod.profile_for("pm", "acme-portal") == "pm-acme-portal"

    def test_role_is_normalised(self):
        assert agents_mod.profile_for("DEV", "hedgi") == "dev-hedgi"

    def test_unknown_role_is_refused_with_the_known_set(self):
        with pytest.raises(agents_mod.AgentProvisionError) as excinfo:
            agents_mod.profile_for("wizard", "hedgi")
        message = str(excinfo.value)
        assert "wizard" in message
        # Reject-with-valid-list: the error names what *is* allowed.
        assert "dev" in message and "qa" in message

    @pytest.mark.parametrize("slug", ["", "Has Space", "-leading", "a" * 80, "sl/ash"])
    def test_unsafe_slug_is_refused(self, slug):
        with pytest.raises(agents_mod.AgentProvisionError):
            agents_mod.profile_for("dev", slug)

    def test_slug_case_is_normalised_not_refused(self):
        """Matches ``projects_db.normalize_slug`` so the profile name a project
        gets is the same whichever casing the operator typed."""
        assert agents_mod.profile_for("dev", "Hedgi") == "dev-hedgi"

    def test_two_projects_never_share_a_profile(self):
        assert agents_mod.profile_for("dev", "hedgi") != agents_mod.profile_for(
            "dev", "acme"
        )


class TestHandles:
    def test_single_agent_keeps_the_bare_handle(self):
        assert agents_mod.handle_for("dev", 1, 1) == "dev"

    def test_several_agents_are_numbered(self):
        assert agents_mod.handle_for("dev", 1, 3) == "dev-1"
        assert agents_mod.handle_for("dev", 3, 3) == "dev-3"

    def test_handles_stay_project_local(self):
        """Handles carry no slug: they are already scoped to one project.

        The *profile* carries the project name because profiles share a global
        namespace; handles do not, and keeping them short matters because they
        are typed on every comment.
        """
        assert "hedgi" not in agents_mod.handle_for("dev", 1, 2)


class TestModelRouting:
    """`agent.model` must actually reach the router, not just the file."""

    def _scope(self, monkeypatch, agents, models=None, orchestrator="pm-x"):
        from plugins.pmo import project_scope

        cfg = project_scope.ProjectConfig.model_validate(
            {
                "version": 1,
                "project": {"name": "X", "slug": "x"},
                "orchestrator": {"profile": orchestrator},
                "agents": agents,
                "models": models or {},
            }
        )
        return cfg

    def test_per_agent_model_overrides_role_default(self):
        from plugins.pmo import cost, project_scope

        cfg = self._scope(
            None,
            agents=[
                {
                    "handle": "dev-1",
                    "profile": "dev-x-1",
                    "role": "Engineer",
                    "model": "openai-codex-x",
                },
                {"handle": "dev-2", "profile": "dev-x-2", "role": "Engineer"},
            ],
            models={"worker": "sonnet"},
        )
        scope = type("S", (), {"config": cfg})()
        assert cost.model_route_for_assignee(scope, "dev-x-1") == "openai-codex-x"
        # The unpinned sibling still follows the role default.
        assert cost.model_route_for_assignee(scope, "dev-x-2") == "sonnet"

    def test_reviewer_role_default_still_applies_without_a_pin(self):
        from plugins.pmo import cost

        cfg = self._scope(
            None,
            agents=[{"handle": "qa", "profile": "qa-x", "role": "Reviewer"}],
            models={"worker": "sonnet", "reviewer": "opus"},
        )
        scope = type("S", (), {"config": cfg})()
        assert cost.model_route_for_assignee(scope, "qa-x") == "opus"

    def test_escalation_override_beats_a_per_agent_pin(self):
        """A repeatedly-blocked ticket is a safety case, not a routing case."""
        from plugins.pmo import cost

        cfg = self._scope(
            None,
            agents=[
                {
                    "handle": "dev",
                    "profile": "dev-x",
                    "role": "Engineer",
                    "model": "openai-codex-x",
                }
            ],
            models={"worker": "sonnet", "escalation_override": "opus"},
        )
        scope = type("S", (), {"config": cfg})()
        assert (
            cost.model_route_for_assignee(scope, "dev-x", blocked_recurrences=2)
            == "opus"
        )

    def test_agent_model_cannot_select_moa(self):
        """Same rule as ModelsConfig: no unattended route may pick MoA."""
        from plugins.pmo import project_scope

        with pytest.raises(Exception):
            project_scope.AgentConfig.model_validate(
                {
                    "handle": "dev",
                    "profile": "dev-x",
                    "role": "Engineer",
                    "model": "moa",
                }
            )


class TestCredentialIsolation:
    def test_project_override_wins_and_disconnect_restores_global_fallback(
        self, tmp_path, monkeypatch
    ):
        import json
        from plugins.pmo import provider_auth

        home = tmp_path / "home"
        profile = home / "profiles" / "dev-hedgi"
        profile.mkdir(parents=True)
        monkeypatch.setenv("HERMES_HOME", str(home))
        (home / "auth.json").write_text(json.dumps({
            "version": 1,
            "providers": {"openai-codex": {"tokens": {"access_token": "global"}}},
        }))
        (profile / "auth.json").write_text(json.dumps({
            "version": 1,
            "active_provider": "openai-codex",
            "providers": {"openai-codex": {"tokens": {"access_token": "project"}}},
        }))
        scope = type("Scope", (), {
            "slug": "hedgi",
            "config": type("Config", (), {
                "orchestrator": type("Orchestrator", (), {"profile": "dev-hedgi"})(),
                "agents": (),
            })(),
        })()

        row = provider_auth.status(scope)[0]
        assert row["openai_codex_source"] == "project"
        assert row["effective_providers"] == ["openai-codex"]

        assert provider_auth.disconnect(
            scope, provider="openai-codex", handle="pm"
        ) is True
        row = provider_auth.status(scope)[0]
        assert row["openai_codex_source"] == "global"
        assert row["own_providers"] == []
        assert "openai-codex" in row["inherited_providers"]
        global_store = json.loads((home / "auth.json").read_text())
        assert global_store["providers"]["openai-codex"]["tokens"]["access_token"] == "global"

    def test_profile_home_redirects_the_auth_store(self, tmp_path, monkeypatch):
        """A login inside a profile must write to that profile's auth.json."""
        from hermes_cli import auth as auth_mod
        from plugins.pmo import provider_auth

        home = tmp_path / "home"
        (home / "profiles" / "dev-hedgi").mkdir(parents=True)
        monkeypatch.setenv("HERMES_HOME", str(home))

        outside = auth_mod._auth_file_path()
        with provider_auth.profile_home("dev-hedgi"):
            inside = auth_mod._auth_file_path()

        assert inside != outside
        assert inside.parent.name == "dev-hedgi"
        # And the override is released, so the next caller is unaffected.
        assert auth_mod._auth_file_path() == outside

    def test_two_projects_get_distinct_stores(self, tmp_path, monkeypatch):
        from hermes_cli import auth as auth_mod
        from plugins.pmo import provider_auth

        home = tmp_path / "home"
        for name in ("pm-hedgi", "pm-acme"):
            (home / "profiles" / name).mkdir(parents=True)
        monkeypatch.setenv("HERMES_HOME", str(home))

        seen = {}
        for name in ("pm-hedgi", "pm-acme"):
            with provider_auth.profile_home(name):
                seen[name] = auth_mod._auth_file_path()
        assert seen["pm-hedgi"] != seen["pm-acme"]

    def test_missing_profile_is_refused_before_any_login(self, tmp_path, monkeypatch):
        from plugins.pmo import provider_auth

        monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
        with pytest.raises(provider_auth.ProviderAuthError) as excinfo:
            with provider_auth.profile_home("does-not-exist"):
                pass
        assert "agents provision" in str(excinfo.value)
