"""Deterministic scripted-model harness for PM-OS acceptance tests.

The harness deliberately sits in ``tests``.  It exercises production tool
handlers and Hermes' real session store, but it never installs another model
provider or changes the agent runtime.  Every scripted turn emits the ordered
trace required by plan 017 and script exhaustion is always an error.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from hermes_state import SessionDB


class ScriptExhausted(AssertionError):
    """The agent made an unplanned provider request."""


@dataclass(frozen=True)
class Text:
    content: str


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: Mapping[str, Any]
    call_id: str | None = None


@dataclass(frozen=True)
class AssistantTurn:
    """One normalized assistant turn, optionally containing several calls."""

    content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()


ScriptTurn = Text | ToolCall | AssistantTurn


@dataclass(frozen=True)
class ProviderRequest:
    messages: tuple[dict[str, Any], ...]
    offered_tools: tuple[str, ...]
    metadata: Mapping[str, Any]


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    reason: str


@dataclass(frozen=True)
class ToolResult:
    call_id: str
    name: str
    ok: bool
    value: Any = None
    error: str | None = None


@dataclass(frozen=True)
class TraceEvent:
    stage: str
    value: Any


class FakeModel:
    """Strict provider double whose turns are consumed exactly once."""

    def __init__(self, script: Sequence[ScriptTurn]):
        self._script = list(script)
        self._calls: list[ProviderRequest] = []

    def complete(self, request: ProviderRequest) -> ScriptTurn:
        self._calls.append(request)
        if not self._script:
            raise ScriptExhausted(
                f"unexpected provider request #{len(self._calls)}; scripted turns exhausted"
            )
        return self._script.pop(0)

    def calls(self) -> tuple[ProviderRequest, ...]:
        return tuple(self._calls)

    def assert_script_exhausted(self) -> None:
        if self._script:
            raise AssertionError(f"{len(self._script)} scripted provider turn(s) were not consumed")


class HermesHistory:
    """Tiny adapter over the production SQLite session transcript store."""

    def __init__(self, db_path: Path, session_id: str):
        self.db = SessionDB(db_path=db_path)
        self.session_id = session_id
        self.db.create_session(session_id, source="pmo-scripted-test")

    def append_user(self, content: str) -> None:
        self.db.append_message(self.session_id, "user", content=content)

    def append_assistant(self, turn: AssistantTurn) -> None:
        tool_calls = [
            {
                "id": call.call_id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(dict(call.arguments), sort_keys=True, default=str),
                },
            }
            for call in turn.tool_calls
        ]
        self.db.append_message(
            self.session_id,
            "assistant",
            content=turn.content,
            tool_calls=tool_calls or None,
            finish_reason="tool_calls" if tool_calls else "stop",
        )

    def append_tool(self, result: ToolResult) -> None:
        self.db.append_message(
            self.session_id,
            "tool",
            content=_json_value(
                result.value if result.ok else {"error": result.error, "ok": False}
            ),
            tool_name=result.name,
            tool_call_id=result.call_id,
        )

    def snapshot(self) -> tuple[dict[str, Any], ...]:
        return tuple(self.db.get_messages_as_conversation(self.session_id))


Policy = Callable[[ToolCall, ProviderRequest], PolicyDecision]
ToolHandler = Callable[..., Any]


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return repr(value)


def _json_value(value: Any) -> str:
    return json.dumps(_jsonable(value), ensure_ascii=False, sort_keys=True)


def _normalize(turn: ScriptTurn, index: int) -> AssistantTurn:
    if isinstance(turn, Text):
        return AssistantTurn(content=turn.content)
    if isinstance(turn, ToolCall):
        call_id = turn.call_id or f"call-{index}-1"
        return AssistantTurn(
            tool_calls=(ToolCall(turn.name, dict(turn.arguments), call_id=call_id),)
        )
    normalized: list[ToolCall] = []
    for call_index, call in enumerate(turn.tool_calls, start=1):
        normalized.append(
            ToolCall(
                call.name,
                dict(call.arguments),
                call_id=call.call_id or f"call-{index}-{call_index}",
            )
        )
    return AssistantTurn(content=turn.content, tool_calls=tuple(normalized))


def _lookup_reference(value: str, outputs: Sequence[Any]) -> Any:
    if not value.startswith("$"):
        return value
    head, dot, tail = value[1:].partition(".")
    if not head.isdigit() or int(head) < 1 or int(head) > len(outputs):
        raise AssertionError(f"unresolved scripted result reference: {value}")
    result = outputs[int(head) - 1]
    if not dot:
        if hasattr(result, "task_id"):
            return getattr(result, "task_id")
        if isinstance(result, Mapping) and "task_id" in result:
            return result["task_id"]
        return result
    current = result
    for key in tail.split("."):
        current = current[key] if isinstance(current, Mapping) else getattr(current, key)
    return current


def _resolve_references(value: Any, outputs: Sequence[Any]) -> Any:
    if isinstance(value, str):
        return _lookup_reference(value, outputs)
    if isinstance(value, Mapping):
        return {key: _resolve_references(item, outputs) for key, item in value.items()}
    if isinstance(value, tuple):
        return tuple(_resolve_references(item, outputs) for item in value)
    if isinstance(value, list):
        return [_resolve_references(item, outputs) for item in value]
    return value


class ScriptedAgent:
    """Run a strict model script through policy, tools and durable history."""

    def __init__(
        self,
        *,
        model: FakeModel,
        tools: Mapping[str, ToolHandler],
        offered_tools: Iterable[str],
        history: HermesHistory,
        policy: Policy | None = None,
        execution_lanes: Mapping[str, str] | None = None,
        max_turns: int = 50,
    ):
        self.model = model
        self.tools = dict(tools)
        self.offered_tools = tuple(dict.fromkeys(offered_tools))
        self.history = history
        self.policy = policy or self._default_policy
        self.execution_lanes = dict(execution_lanes or {})
        self.max_turns = int(max_turns)
        self.trace: list[TraceEvent] = []
        self.outputs: list[Any] = []

    def _default_policy(self, call: ToolCall, _request: ProviderRequest) -> PolicyDecision:
        if call.name not in self.offered_tools:
            return PolicyDecision(False, "tool_not_offered")
        if call.name not in self.tools:
            return PolicyDecision(False, "unknown_tool")
        return PolicyDecision(True, "allowed")

    def run(
        self,
        user_message: str,
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> str:
        self.history.append_user(user_message)
        for turn_number in range(1, self.max_turns + 1):
            request = ProviderRequest(
                messages=self.history.snapshot(),
                offered_tools=self.offered_tools,
                metadata=dict(metadata or {}),
            )
            self.trace.append(TraceEvent("provider_request", request))
            normalized = _normalize(self.model.complete(request), turn_number)
            self.trace.append(TraceEvent("normalized_assistant_message", normalized))
            self.history.append_assistant(normalized)

            for call in normalized.tool_calls:
                decision = self.policy(call, request)
                self.trace.append(TraceEvent("policy_decision", decision))
                lane = (
                    self.execution_lanes.get(call.name, "registry")
                    if decision.allowed
                    else "refused"
                )
                self.trace.append(TraceEvent("execution_lane", lane))
                if decision.allowed:
                    try:
                        arguments = _resolve_references(dict(call.arguments), self.outputs)
                        value = self.tools[call.name](**arguments)
                        result = ToolResult(call.call_id or "", call.name, True, value=value)
                        self.outputs.append(value)
                    except Exception as exc:  # surfaced to the next scripted turn
                        result = ToolResult(
                            call.call_id or "", call.name, False, error=f"{type(exc).__name__}: {exc}"
                        )
                        self.outputs.append(result)
                else:
                    result = ToolResult(
                        call.call_id or "", call.name, False, error=decision.reason
                    )
                    self.outputs.append(result)
                self.trace.append(TraceEvent("tool_result", result))
                self.history.append_tool(result)

            persisted = self.history.snapshot()
            self.trace.append(TraceEvent("persisted_history", persisted))
            if normalized.tool_calls:
                self.trace.append(
                    TraceEvent(
                        "next_provider_request",
                        {"message_count": len(persisted), "turn": turn_number + 1},
                    )
                )
                continue
            if normalized.content:
                self.trace.append(
                    TraceEvent(
                        "finalizer_outcome",
                        {"status": "completed", "response": normalized.content},
                    )
                )
                self.model.assert_script_exhausted()
                return normalized.content
            self.trace.append(
                TraceEvent("finalizer_outcome", {"status": "incomplete", "response": ""})
            )
        raise AssertionError(f"scripted agent exceeded max_turns={self.max_turns}")


def trace_stages(agent: ScriptedAgent) -> tuple[str, ...]:
    return tuple(event.stage for event in agent.trace)
