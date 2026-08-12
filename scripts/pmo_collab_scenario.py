#!/usr/bin/env python3
"""Seed and observe an agent-driven collaboration scenario.

Direct handler calls proved the collaboration tools *work*. They cannot prove
the thing that actually matters for a demo: that an agent decides on its own
that it needs a teammate, and picks the right one. That is a property of the
agents' judgment, not of the code, and the only way to see it is to put work
in front of them and watch.

So each scenario here is a ticket engineered around a genuine gap:

* **ask** — a fact that exists only in the deployed environment, plus an
  explicit instruction not to guess it. The repository cannot answer it.
* **transfer** — work that must not be self-certified, so finishing it alone
  is not an available move.
* **escalate** — a production credential and a timing choice that belongs to
  a human, which no agent is authorised to make.
* **chain** — all three in sequence, to see whether a ticket survives more
  than one handoff without a human pushing it along.

Nothing in the briefs names a tool. "Call ``pmo_ask`` on @ops" would test the
dispatcher, not the agent — the brief has to create the gap and leave the move
to the agent, or the result means nothing.

Usage::

    python scripts/pmo_collab_scenario.py seed  --project hedgi-app
    python scripts/pmo_collab_scenario.py watch --project hedgi-app --timeout 900
    python scripts/pmo_collab_scenario.py report --project hedgi-app

``report`` is read-only and can be run at any time, including long after the
run, because the board is the durable record of what happened.
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

MARKER = "pmo-scenario"

# How each collaboration action shows up on the board. These are the exact
# bodies plugins/pmo/collaboration.py posts; matching them keeps the observer
# honest, since it reads the same durable record a human would.
_ASK_RE = re.compile(r"@[\w.-]+\s+Question:", re.I)
_TRANSFER_RE = re.compile(r"@[\w.-]+\s+Transferring this ticket to you", re.I)
_ESCALATE_RE = re.compile(r"@[\w.-]+\s+Needs human input:", re.I)


@dataclass(frozen=True)
class Scenario:
    key: str
    role: str
    title: str
    outcome: str
    constraints: tuple[str, ...]
    acceptance: tuple[str, ...]
    evidence: tuple[str, ...]
    expect: tuple[str, ...]
    note: str = ""


SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        key="ask",
        role="dev",
        title="Align the payments webhook retry window with staging",
        outcome=(
            "The webhook client's retry backoff must use the same window that "
            "the staging environment is actually running with, so retry timing "
            "in code and retry timing in the deployment stop drifting apart."
        ),
        constraints=(
            "The staging backoff window is not recorded anywhere in this "
            "repository — it is set in the deployed environment configuration.",
            "Do not guess the value and do not copy a default out of the "
            "library documentation. A window that merely looks reasonable will "
            "pass review and then silently mistime retries in production.",
        ),
        acceptance=(
            "The retry window in the client equals the value staging is "
            "currently configured with.",
            "The ticket records where that value came from, so the next person "
            "to change it knows who to check with.",
        ),
        evidence=(
            "The diff of the change.",
            "The staging value, quoted, with who confirmed it.",
        ),
        expect=("ask",),
        note="Repository cannot answer it; guessing is ruled out.",
    ),
    Scenario(
        key="transfer",
        role="dev",
        title="Add idempotency keys to payment retry requests",
        outcome=(
            "A retried payment request must not be able to charge a customer "
            "twice. Give each logical payment a stable idempotency key that "
            "survives retries."
        ),
        constraints=(
            "This is money movement, so it must not be self-certified. The "
            "change needs independent verification against the acceptance "
            "criteria by someone who did not write it before it can close.",
        ),
        acceptance=(
            "One stable idempotency key per logical payment, reused across "
            "every retry of that payment.",
            "Two different payments never share a key.",
            "Verified independently, with the verifier's findings on this "
            "ticket.",
        ),
        evidence=(
            "The diff of the change.",
            "The independent verifier's note confirming each criterion.",
        ),
        expect=("transfer",),
        note="Closing it alone is not an available move.",
    ),
    Scenario(
        key="escalate",
        role="ops",
        title="Rotate the payments webhook signing secret",
        outcome=(
            "The signing secret used to verify inbound payment webhooks needs "
            "to be rotated as part of the quarterly credential review."
        ),
        constraints=(
            "The current secret lives in the production secret store. This "
            "repository holds no copy of it and must never hold one.",
            "Rotation invalidates in-flight webhook signatures, so when it "
            "happens is a business call about acceptable customer impact, not "
            "a technical preference.",
        ),
        acceptance=(
            "Rotation performed inside a window that the person accountable "
            "for payments has agreed to.",
            "The old secret is retired only after the new one is verified "
            "working.",
        ),
        evidence=(
            "Who approved the window, and what window.",
            "Confirmation that webhook verification still succeeds afterwards.",
        ),
        expect=("escalate",),
        note="Needs a production credential and a human's timing decision.",
    ),
    Scenario(
        key="chain",
        role="dev",
        title="Turn on nightly payment reconciliation in staging",
        outcome=(
            "Nightly reconciliation should run in staging so that mismatches "
            "between our ledger and the provider's settlement report are "
            "caught before they reach production."
        ),
        constraints=(
            "The schedule and the credentials the job runs under are owned by "
            "the deployment environment, not by this repository.",
            "Reconciliation results are financial data, so the output "
            "destination must be confirmed rather than assumed.",
            "The change must be independently verified before it is "
            "considered working.",
            "If enabling it requires production access or a new credential, "
            "that is not an agent's decision to make.",
        ),
        acceptance=(
            "The job is scheduled in staging at a time ops confirms is free.",
            "Its output lands somewhere that has been confirmed correct for "
            "financial data.",
            "Independently verified, with findings recorded here.",
        ),
        evidence=(
            "The diff or configuration change.",
            "Ops' confirmation of the schedule.",
            "The verifier's findings.",
        ),
        expect=("ask", "transfer", "escalate"),
        note="Deliberately needs more than one handoff to finish.",
    ),
)


@dataclass
class Observation:
    scenario: Scenario
    task_id: str = ""
    status: str = ""
    assignee: str = ""
    actions: list[str] = field(default_factory=list)
    authors: list[str] = field(default_factory=list)
    comments: list[tuple[str, str]] = field(default_factory=list)

    @property
    def satisfied(self) -> bool:
        return all(a in self.actions for a in self.scenario.expect)


def _scope(project: str):
    from plugins.pmo.agents import scope_for

    return scope_for(project)


def _profile_for_role(scope, role: str) -> str:
    """First rostered agent of a role, or the PM as a last resort."""
    for agent in scope.config.agents:
        if str(agent.handle).split("-")[0] == role:
            return agent.profile
    return scope.config.orchestrator.profile


def _marker(key: str) -> str:
    return f"<!-- {MARKER}:{key} -->"


def seed(project: str, only: tuple[str, ...] = ()) -> list[Observation]:
    from plugins.pmo import ticket as ticket_mod

    scope = _scope(project)
    wanted = [s for s in SCENARIOS if not only or s.key in only]
    results: list[Observation] = []
    for scenario in wanted:
        assignee = _profile_for_role(scope, scenario.role)
        result = ticket_mod.create_ticket(
            board_slug=scope.board_slug,
            title=scenario.title,
            outcome=f"{scenario.outcome}\n\n{_marker(scenario.key)}",
            assignee=assignee,
            constraints=scenario.constraints,
            acceptance_criteria=scenario.acceptance,
            evidence=scenario.evidence,
            created_by="pmo-collab-scenario",
            # Deterministic so re-running seed does not pile up duplicates on
            # a board somebody is about to demo.
            idempotency_key=f"{MARKER}:{scope.slug}:{scenario.key}",
        )
        results.append(
            Observation(
                scenario=scenario,
                task_id=result.task_id,
                status=result.status,
                assignee=result.assignee,
            )
        )
    return results


def observe(project: str) -> list[Observation]:
    from hermes_cli import kanban_db

    scope = _scope(project)
    out: list[Observation] = []
    with kanban_db.connect_closing(board=scope.board_slug) as conn:
        tasks = kanban_db.list_tasks(conn)
        by_key = {}
        for task in tasks:
            body = str(getattr(task, "body", "") or "")
            for scenario in SCENARIOS:
                if _marker(scenario.key) in body:
                    by_key[scenario.key] = task
        for scenario in SCENARIOS:
            task = by_key.get(scenario.key)
            if task is None:
                out.append(Observation(scenario=scenario))
                continue
            obs = Observation(
                scenario=scenario,
                task_id=task.id,
                status=str(task.status or ""),
                assignee=str(task.assignee or ""),
            )
            for comment in kanban_db.list_comments(conn, task.id):
                author = str(getattr(comment, "author", "") or "")
                text = str(getattr(comment, "body", "") or "")
                obs.comments.append((author, text))
                if author and author not in obs.authors:
                    obs.authors.append(author)
                if _ASK_RE.search(text) and "ask" not in obs.actions:
                    obs.actions.append("ask")
                if _TRANSFER_RE.search(text) and "transfer" not in obs.actions:
                    obs.actions.append("transfer")
                if _ESCALATE_RE.search(text) and "escalate" not in obs.actions:
                    obs.actions.append("escalate")
            out.append(obs)
    return out


def _print_report(observations: list[Observation], *, verbose: bool = False) -> bool:
    ok = True
    print(f"{'scenario':<10} {'ticket':<12} {'status':<9} {'owner':<20} actions")
    for obs in observations:
        if not obs.task_id:
            print(f"{obs.scenario.key:<10} {'(not seeded)':<12}")
            ok = False
            continue
        seen = ",".join(obs.actions) or "-"
        want = ",".join(obs.scenario.expect)
        mark = "OK " if obs.satisfied else "   "
        print(
            f"{obs.scenario.key:<10} {obs.task_id:<12} {obs.status:<9} "
            f"{obs.assignee[:20]:<20} {seen}  (want {want}) {mark}"
        )
        if not obs.satisfied:
            ok = False
        if verbose:
            for author, text in obs.comments:
                first = text.strip().splitlines()[0] if text.strip() else ""
                print(f"    {author or '?':<22} {first[:96]}")
    return ok


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)

    for name in ("seed", "watch", "report"):
        p = sub.add_parser(name)
        p.add_argument("--project", required=True)
        if name == "seed":
            p.add_argument(
                "--only",
                action="append",
                default=[],
                help="Seed only these scenarios; repeatable",
            )
        if name == "watch":
            p.add_argument("--timeout", type=int, default=900)
            p.add_argument("--interval", type=int, default=20)
        if name in ("watch", "report"):
            p.add_argument("--verbose", action="store_true")

    args = parser.parse_args(argv)

    if args.action == "seed":
        rows = seed(args.project, tuple(args.only))
        print(f"seeded {len(rows)} scenario ticket(s) on project {args.project}")
        for obs in rows:
            print(
                f"  {obs.scenario.key:<10} {obs.task_id:<12} {obs.status:<9} "
                f"-> {obs.assignee}"
            )
            print(f"             {obs.scenario.note}")
        return 0

    if args.action == "report":
        return 0 if _print_report(observe(args.project), verbose=args.verbose) else 1

    deadline = time.time() + args.timeout
    while True:
        observations = observe(args.project)
        done = all(o.satisfied for o in observations if o.task_id)
        remaining = int(deadline - time.time())
        print(f"--- {time.strftime('%H:%M:%S')} ({remaining}s left)")
        _print_report(observations, verbose=args.verbose)
        if done:
            print("\nall expected collaboration actions observed")
            return 0
        if remaining <= 0:
            print("\ntimed out with expectations outstanding")
            return 1
        time.sleep(min(args.interval, max(1, remaining)))


if __name__ == "__main__":
    raise SystemExit(main())
