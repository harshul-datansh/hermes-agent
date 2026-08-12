"""Role charters, and the profile-home project lookup they depend on.

Two defects are pinned here, both found by watching real agents rather than
by reading code:

* every PM-OS profile shipped with the stock Hermes SOUL, so no agent had any
  reason to use the collaboration tools it had been given; and
* a dispatched worker could not resolve its own project, because
  ``projects.db`` is per-profile and a worker's profile has never had a
  project registered in it.
"""

from __future__ import annotations

import pytest

from plugins.pmo import agent_soul


class TestCharterContent:
    def test_every_role_template_has_a_charter(self):
        """A role that provisions but renders no charter would silently ship
        an agent back to the stock SOUL — the exact bug this fixes."""
        from plugins.pmo.agents import ROLE_TEMPLATES

        for role in ROLE_TEMPLATES:
            text = agent_soul.render(
                role=role, handle=role, project_name="X", teammates=["pm"]
            )
            assert text.strip()

    def test_unknown_role_is_refused_rather_than_rendered_generic(self):
        with pytest.raises(KeyError):
            agent_soul.render(role="wizard", handle="w", project_name="X")

    @pytest.mark.parametrize("role", ["dev", "qa", "ops", "pm"])
    def test_charter_names_the_collaboration_tools(self, role):
        """The whole point: the tools must be reachable *and* mentioned."""
        text = agent_soul.render(role=role, handle=role, project_name="X")
        for tool in ("pmo_ask", "pmo_transfer", "pmo_escalate", "pmo_handles"):
            assert tool in text

    def test_charter_states_the_four_terminal_states(self):
        text = agent_soul.render(role="dev", handle="dev", project_name="X")
        for state in ("finished", "asked", "transferred", "escalated"):
            assert state in text

    def test_charter_identifies_the_agent_and_project(self):
        text = agent_soul.render(
            role="qa", handle="qa-2", project_name="Hedgi App", teammates=["dev-1"]
        )
        assert "@qa-2" in text
        assert "Hedgi App" in text
        assert "@dev-1" in text

    def test_teammate_list_defers_to_pmo_handles(self):
        """A baked roster goes stale; the charter must say so or agents will
        mention handles that no longer exist."""
        text = agent_soul.render(role="dev", handle="dev", project_name="X")
        assert "pmo_handles" in text
        assert "stale" in text.lower()


class TestWriteSoul:
    def _profile(self, tmp_path, monkeypatch, name="dev-x"):
        home = tmp_path / "home"
        (home / "profiles" / name).mkdir(parents=True)
        monkeypatch.setenv("HERMES_HOME", str(home))
        return home / "profiles" / name

    def test_writes_then_reports_unchanged(self, tmp_path, monkeypatch):
        path = self._profile(tmp_path, monkeypatch)
        text = agent_soul.render(role="dev", handle="dev", project_name="X")
        assert agent_soul.write_soul("dev-x", text) == "written"
        assert (path / "SOUL.md").read_text(encoding="utf-8") == text
        assert agent_soul.write_soul("dev-x", text) == "unchanged"

    def test_stock_hermes_soul_is_replaced(self, tmp_path, monkeypatch):
        """Every PM-OS profile starts holding it, so overwriting is the fix."""
        path = self._profile(tmp_path, monkeypatch)
        (path / "SOUL.md").write_text(
            "You are Hermes Agent, an intelligent AI assistant created by "
            "Nous Research. You are helpful.",
            encoding="utf-8",
        )
        text = agent_soul.render(role="dev", handle="dev", project_name="X")
        assert agent_soul.write_soul("dev-x", text) == "updated"
        assert agent_soul.is_managed((path / "SOUL.md").read_text(encoding="utf-8"))

    def test_hand_edited_soul_is_left_alone(self, tmp_path, monkeypatch):
        """Profiles are meant to be customised; silently reverting an
        operator's edit on every re-provision would make that pointless."""
        path = self._profile(tmp_path, monkeypatch)
        custom = "# My own carefully tuned agent\nDo it my way.\n"
        (path / "SOUL.md").write_text(custom, encoding="utf-8")
        text = agent_soul.render(role="dev", handle="dev", project_name="X")
        assert agent_soul.write_soul("dev-x", text) == "skipped"
        assert (path / "SOUL.md").read_text(encoding="utf-8") == custom

    def test_force_overrides_a_hand_edit(self, tmp_path, monkeypatch):
        path = self._profile(tmp_path, monkeypatch)
        (path / "SOUL.md").write_text("# mine\n", encoding="utf-8")
        text = agent_soul.render(role="dev", handle="dev", project_name="X")
        assert agent_soul.write_soul("dev-x", text, force=True) == "updated"
        assert (path / "SOUL.md").read_text(encoding="utf-8") == text

    def test_our_own_charter_is_rewritable_without_force(self, tmp_path, monkeypatch):
        """Re-provisioning must be able to roll a charter change forward."""
        self._profile(tmp_path, monkeypatch)
        first = agent_soul.render(role="dev", handle="dev", project_name="X")
        agent_soul.write_soul("dev-x", first)
        second = agent_soul.render(role="dev", handle="dev", project_name="Y")
        assert agent_soul.write_soul("dev-x", second) == "updated"

    def test_missing_profile_is_skipped_not_raised(self, tmp_path, monkeypatch):
        self._profile(tmp_path, monkeypatch)
        text = agent_soul.render(role="dev", handle="dev", project_name="X")
        assert agent_soul.write_soul("no-such-profile", text) == "skipped"


class TestProfileHomeProjectLookup:
    """The worker-side failure: ``unknown project: p_...`` from inside a
    dispatched agent, because ``projects.db`` is per-profile."""

    def test_root_db_is_found_from_inside_a_profile_home(self, tmp_path, monkeypatch):
        from plugins.pmo import project_scope

        root = tmp_path / "hermes"
        (root / "profiles" / "dev-x").mkdir(parents=True)
        (root / "projects.db").write_bytes(b"")
        monkeypatch.setenv("HERMES_HOME", str(root / "profiles" / "dev-x"))

        found = project_scope._root_projects_db()
        assert found is not None
        assert found == root / "projects.db"

    def test_no_fallback_when_already_at_the_root(self, tmp_path, monkeypatch):
        """At the root there is no second place to look, and returning the
        same path would just repeat the query that already failed."""
        from plugins.pmo import project_scope

        root = tmp_path / "hermes"
        root.mkdir(parents=True)
        (root / "projects.db").write_bytes(b"")
        monkeypatch.setenv("HERMES_HOME", str(root))

        assert project_scope._root_projects_db() is None

    def test_absent_root_db_is_not_an_error(self, tmp_path, monkeypatch):
        from plugins.pmo import project_scope

        root = tmp_path / "hermes"
        (root / "profiles" / "dev-x").mkdir(parents=True)
        monkeypatch.setenv("HERMES_HOME", str(root / "profiles" / "dev-x"))

        assert project_scope._root_projects_db() is None
