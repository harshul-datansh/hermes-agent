"""Plugin-owned ``hermes pmo`` command multiplexer."""

from __future__ import annotations

import argparse
import importlib


COMMAND_MODULES = {
    "agents": "plugins.pmo.agents",
    "approval": "plugins.pmo.approval",
    "auth": "plugins.pmo.provider_auth",
    "availability": "plugins.pmo.availability",
    "bootstrap": "plugins.pmo.bootstrap",
    "comment": "plugins.pmo.comment",
    "context": "plugins.pmo.project_context",
    "demo": "plugins.pmo.demo",
    "doctor": "plugins.pmo.doctor",
    "export": "plugins.pmo.lifecycle",
    "git": "plugins.pmo.git_flow",
    "portfolio": "plugins.pmo.portfolio",
    "project": "plugins.pmo.lifecycle",
    "report": "plugins.pmo.report",
    "repo": "plugins.pmo.repositories",
    "retention": "plugins.pmo.lifecycle",
    "setup": "plugins.pmo.setup",
    "sla": "plugins.pmo.approval_sla",
    "ticket": "plugins.pmo.ticket",
    "timeline": "plugins.pmo.timeline",
    "workflow": "plugins.pmo.workflow",
}


def register_cli(parser: argparse.ArgumentParser) -> None:
    parser.description = (
        "Operate Datansh PM-OS through additive project, Kanban, gateway, and "
        "profile seams. Run `hermes pmo <command> --help` for command options."
    )
    parser.add_argument("pmo_action", choices=tuple(COMMAND_MODULES))
    parser.add_argument("pmo_args", nargs=argparse.REMAINDER)
    parser.set_defaults(func=pmo_command)


def pmo_command(args: argparse.Namespace) -> int:
    action = str(getattr(args, "pmo_action", "") or "")
    module_name = COMMAND_MODULES.get(action)
    if module_name is None:
        print("Usage: hermes pmo {" + "|".join(COMMAND_MODULES) + "}")
        return 2
    module = importlib.import_module(module_name)
    action_main = getattr(module, "main_for_action", None)
    if callable(action_main):
        return int(action_main(action, list(getattr(args, "pmo_args", ()) or ())) or 0)
    main = getattr(module, "main", None)
    if not callable(main):
        raise RuntimeError(f"PM-OS command module has no main(): {module_name}")
    return int(main(list(getattr(args, "pmo_args", ()) or ())) or 0)
