"""Contract tests for the additive Datansh PM-OS profile distribution."""

from pathlib import Path

import pytest

from hermes_cli.profile_distribution import (
    install_distribution,
    plan_install,
    read_manifest,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
DIST_ROOT = REPO_ROOT / "distributions" / "datansh-pm-os"


@pytest.fixture()
def isolated_profile_home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    return hermes_home


def test_manifest_is_an_additive_profile_payload(tmp_path):
    manifest = read_manifest(DIST_ROOT)

    assert manifest is not None
    assert manifest.name == "datansh-pm-os"
    assert manifest.hermes_requires == ">=0.20.0"
    assert manifest.env_requires == []
    assert manifest.distribution_owned == [
        "SOUL.md",
        "skills/datansh-pm-os",
    ]

    plan = plan_install(str(DIST_ROOT), tmp_path)
    assert plan.manifest.name == "datansh-pm-os"
    assert plan.has_skills is True
    assert plan.has_cron is False


def test_install_uses_existing_profile_distribution_boundary(isolated_profile_home):
    plan = install_distribution(str(DIST_ROOT))
    target = plan.target_dir

    assert target == isolated_profile_home / "profiles" / "datansh-pm-os"
    assert (target / "SOUL.md").read_text(encoding="utf-8") == (
        DIST_ROOT / "SOUL.md"
    ).read_text(encoding="utf-8")
    assert (
        target / "skills" / "datansh-pm-os" / "SKILL.md"
    ).read_text(encoding="utf-8") == (
        DIST_ROOT / "skills" / "datansh-pm-os" / "SKILL.md"
    ).read_text(encoding="utf-8")

    installed_manifest = read_manifest(target)
    assert installed_manifest is not None
    assert installed_manifest.source == str(DIST_ROOT.resolve())

    # The distribution does not ship an alternate runtime configuration,
    # tool allowlist, MCP set, credentials, cron jobs, or parallel database.
    assert not (target / "config.yaml").exists()
    assert not (target / "mcp.json").exists()
    assert not (target / "pmo.db").exists()
    assert list((target / "cron").iterdir()) == []

    # Standard profile-owned state is still bootstrapped by Hermes itself.
    for dirname in ("memories", "sessions", "logs", "plans", "workspace", "home"):
        assert (target / dirname).is_dir()


def test_role_text_explicitly_preserves_hermes_capabilities():
    role_text = "\n".join(
        [
            (DIST_ROOT / "SOUL.md").read_text(encoding="utf-8"),
            (DIST_ROOT / "skills" / "datansh-pm-os" / "SKILL.md").read_text(
                encoding="utf-8"
            ),
        ]
    ).lower()

    for capability in (
        "memory",
        "skills",
        "self-learning",
        "background review",
        "delegation",
        "messaging",
        "terminal",
        "browser",
        "mcp",
        "plugins",
        "approval",
    ):
        assert capability in role_text
