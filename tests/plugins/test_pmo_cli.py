"""Contract tests for the plugin-owned ``hermes pmo`` CLI seam."""

from __future__ import annotations

import argparse
from types import SimpleNamespace

from plugins.platforms.pmo import cli


def test_cli_registers_all_operator_edges_without_core_command_edit():
    parser = argparse.ArgumentParser()
    cli.register_cli(parser)
    args = parser.parse_args(["report", "--board", "delivery", "--json"])
    assert args.pmo_action == "report"
    assert args.pmo_args == ["--board", "delivery", "--json"]
    assert args.func is cli.pmo_command
    assert cli.COMMAND_MODULES["doctor"] == "plugins.pmo.doctor"
    assert cli.COMMAND_MODULES["git"] == "plugins.pmo.git_flow"
    assert cli.COMMAND_MODULES["repo"] == "plugins.pmo.repositories"


def test_cli_dispatches_to_existing_edge_main(monkeypatch):
    called = []
    fake = SimpleNamespace(main=lambda argv: called.append(argv) or 7)
    monkeypatch.setattr(cli.importlib, "import_module", lambda name: fake)
    result = cli.pmo_command(
        SimpleNamespace(pmo_action="timeline", pmo_args=["--board", "x", "--task", "t"])
    )
    assert result == 7
    assert called == [["--board", "x", "--task", "t"]]
