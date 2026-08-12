"""Structural authorization coverage for the PM-OS HTTP surface.

Plan 002 §2e calls this "the load-bearing test of the whole authorization
story": without it, a route added on a busy afternoon ships unguarded and
nothing says so. Every other authorization test proves that a *policy
decision* is correct; this one proves the decision is actually *reached*.

It is deliberately structural rather than behavioural. Behavioural tests need
a session, a project and a board, so in practice they get written for the
handful of routes someone remembered. This walks the router.
"""
from __future__ import annotations

import pytest

from plugins.pmo.dashboard.plugin_api import router


# Routes that may legitimately run before an identity is established. Keep this
# empty unless there is a hard reason: every entry is a hole someone has to
# re-audit later. A health probe belongs on the host, not on a project-scoped
# plugin router.
PUBLIC_PATHS: frozenset[str] = frozenset()

# Dependency callables that establish or enforce identity. `require(action)`
# returns a closure, so match on the qualified name rather than identity.
_GUARD_MARKERS = ("require", "principal")


def _guard_names(route) -> list[str]:
    dependant = getattr(route, "dependant", None)
    if dependant is None:
        return []
    names = []
    for dependency in dependant.dependencies:
        call = getattr(dependency, "call", None)
        names.append(
            getattr(call, "__qualname__", "") or getattr(call, "__name__", "")
        )
    return names


def _api_routes():
    return [r for r in router.routes if getattr(r, "dependant", None) is not None]


def test_router_exposes_routes():
    """Guard against the sweep silently passing because it found nothing."""
    assert len(_api_routes()) > 20


def _route_id(route) -> str:
    """Readable test id. Must never raise — WebSocket routes have no ``methods``."""
    try:
        methods = "|".join(sorted(getattr(route, "methods", None) or [])) or "WS"
        return f"{methods} {getattr(route, 'path', '?')}"
    except Exception:  # noqa: BLE001 - an id helper must not break collection
        return repr(route)[:60]


@pytest.mark.parametrize("route", _api_routes(), ids=_route_id)
def test_every_pmo_route_is_guarded(route):
    if route.path in PUBLIC_PATHS:
        pytest.skip("explicitly public")
    blob = " ".join(_guard_names(route)).lower()
    assert any(marker in blob for marker in _GUARD_MARKERS), (
        f"PM-OS route {sorted(route.methods or [])} {route.path} has no "
        f"authorization dependency. Add Depends(require(<action>)) — see "
        f"plugins/pmo/access_policy.py. Dependencies found: {_guard_names(route)}"
    )


def test_state_changing_routes_are_not_read_only_guarded():
    """A POST/PATCH/DELETE guarded only by ``project.read`` is a privilege bug.

    Catches the copy-paste failure where a write route inherits the read guard
    from the handler above it — which passes the sweep above while allowing a
    viewer to mutate the project.
    """
    offenders = []
    for route in _api_routes():
        # WebSocket routes carry no ``methods``; they are covered by the sweep
        # above and by test_websocket_route_is_guarded below.
        methods = set(getattr(route, "methods", None) or [])
        if not methods & {"POST", "PUT", "PATCH", "DELETE"}:
            continue
        blob = " ".join(_guard_names(route))
        if "project.read" in blob or "task.read" in blob:
            offenders.append(f"{sorted(methods)} {route.path} -> {blob}")
    assert offenders == [], (
        "state-changing PM-OS routes guarded by a read-only action:\n  "
        + "\n  ".join(offenders)
    )


def test_websocket_route_is_guarded():
    """Plan 006 §4's most consequential trap.

    The live-updates socket streams board events and Founder's Office messages.
    An unscoped subscribe leaks one project's spend and scope discussions to a
    member of another — a confidentiality failure, not just a scope bug.
    """
    sockets = [
        r for r in _api_routes() if not getattr(r, "methods", None)
    ]
    for route in sockets:
        blob = " ".join(_guard_names(route)).lower()
        assert any(marker in blob for marker in _GUARD_MARKERS), (
            f"PM-OS WebSocket {route.path} has no authorization dependency: "
            f"{_guard_names(route)}"
        )
