from __future__ import annotations

import subprocess

import yaml

from plugins.pmo import setup


def _repo(path):
    path.mkdir()
    subprocess.run(["git", "init", "--quiet", str(path)], check=True)
    return path


def test_every_step_has_a_default(tmp_path):
    repo = _repo(tmp_path / "first-project")
    result = setup.setup_project(workspace=repo)
    assert result.slug == "first-project"
    assert result.name == "First Project"
    assert result.board_slug == "first-project"


def test_reuses_existing_provider_config(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(
        yaml.safe_dump({"provider": "openrouter", "model": "existing-model"}),
        encoding="utf-8",
    )
    summary = setup.detect_provider_state(config)
    assert "openrouter/existing-model" in summary
    assert "secret" not in summary.casefold()


def test_ends_with_doctor_output(tmp_path, capsys):
    repo = _repo(tmp_path / "doctor-project")
    assert setup.main(["--workspace", str(repo)]) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert lines
    assert lines[-1].startswith("Doctor: Hermes provider state:")
    assert all(line.startswith("Doctor:") for line in lines)
