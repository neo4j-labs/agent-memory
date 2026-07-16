"""NAMS implementation of :class:`ReasoningProtocol`.

Endpoint mappings verified against the live NAMS OpenAPI spec.

NAMS reasoning model
====================

NAMS exposes a **flat** reasoning model — there is **no Trace entity**.
Steps belong directly to a conversation (``conversationId``), and tool
calls belong to a step (``stepId``). Endpoints:

* ``POST /v1/reasoning/steps`` — record a step
  ``{conversationId, reasoning, actionTaken, result?}``
* ``POST /v1/reasoning/tool-calls`` — record a tool call
  ``{toolName, input, stepId?, status?, output?, durationMs?}``
* ``GET /v1/reasoning/trace/{conversationId}`` — fetch all steps +
  tool calls for a conversation
* ``GET /v1/reasoning/provenance/{entityId}`` — entity reasoning
  provenance (handled by ``long_term.get_entity_provenance``)

The Protocol's :class:`ReasoningTrace` lifecycle (``start_trace``,
``add_step``, ``complete_trace``) is synthesized client-side via an
in-memory cache that maps the synthetic trace_id to the underlying
NAMS ``conversationId``.

Field name mapping (Protocol → NAMS):

* ``thought`` → ``reasoning``
* ``action`` → ``actionTaken``
* ``observation`` → ``result``
* ``tool_name`` → ``toolName``
* ``arguments`` (dict) → ``input`` (JSON-encoded string)
* ``result`` (any) → ``output`` (JSON-encoded string)
* ``duration_ms`` → ``durationMs``
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from neo4j_agent_memory.core.exceptions import NotSupportedError
from neo4j_agent_memory.memory.reasoning import (
    ReasoningStep,
    ReasoningTrace,
    ToolCall,
)
from neo4j_agent_memory.nams._serialization import payload_to_model, snakeize_keys
from neo4j_agent_memory.nams.endpoints import EndpointSpec

if TYPE_CHECKING:
    from neo4j_agent_memory.nams.transport import HttpTransport


_SPEC_RECORD_STEP = EndpointSpec(
    rest_method="POST",
    rest_path="/reasoning/steps",
    bridge_method="record_step",
)
_SPEC_RECORD_TOOL_CALL = EndpointSpec(
    rest_method="POST",
    rest_path="/reasoning/tool-calls",
    bridge_method="record_tool_call",
)
_SPEC_GET_REASONING_TRACE = EndpointSpec(
    rest_method="GET",
    rest_path="/reasoning/trace/{conversation_id}",
    bridge_method="get_reasoning_trace",
)


def _drop_none(d: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in d.items() if v is not None}


def _to_str(value: UUID | str) -> str:
    return str(value)


def _json_safe_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, default=str)
    except (TypeError, ValueError):
        return str(value)


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_step(payload: dict[str, Any], *, trace_id: UUID | str) -> dict[str, Any]:
    data = snakeize_keys(payload) if isinstance(payload, dict) else {}
    return {
        "id": data.get("id") or str(uuid4()),
        "trace_id": str(trace_id),
        "step_number": data.get("step_number") or 0,
        "thought": data.get("reasoning"),
        "action": data.get("action_taken"),
        "observation": data.get("result"),
        "tool_calls": [],
        "created_at": data.get("created_at") or _now_utc_iso(),
        "metadata": data.get("metadata") or {},
    }


def _normalize_tool_call(
    payload: dict[str, Any],
    *,
    step_id: UUID | str | None = None,
    fallback_tool_name: str = "",
    fallback_arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data = snakeize_keys(payload) if isinstance(payload, dict) else {}
    raw_input = data.get("input")
    if raw_input is None:
        arguments: Any = fallback_arguments or {}
    elif isinstance(raw_input, str):
        try:
            arguments = json.loads(raw_input)
        except (json.JSONDecodeError, TypeError):
            arguments = {"_raw": raw_input}
    else:
        arguments = raw_input

    raw_output = data.get("output")
    if raw_output is None:
        result: Any = None
    elif isinstance(raw_output, str):
        try:
            result = json.loads(raw_output)
        except (json.JSONDecodeError, TypeError):
            result = raw_output
    else:
        result = raw_output

    return {
        "id": data.get("id") or str(uuid4()),
        "tool_name": data.get("tool_name") or fallback_tool_name,
        "arguments": arguments if isinstance(arguments, dict) else {"value": arguments},
        "result": result,
        "status": data.get("status") or "success",
        "duration_ms": data.get("duration_ms"),
        "error": data.get("error"),
        "step_id": str(step_id)
        if step_id is not None
        else (str(data["step_id"]) if data.get("step_id") else None),
        "created_at": data.get("created_at") or _now_utc_iso(),
        "metadata": {},
    }


class NamsReasoningMemory:
    """Reasoning memory backed by NAMS's flat step + tool-call model."""

    def __init__(self, transport: HttpTransport) -> None:
        self._transport = transport
        self._traces: dict[str, dict[str, Any]] = {}

    async def start_trace(self, session_id: str, task: str, **kwargs: Any) -> ReasoningTrace:
        trace_id = uuid4()
        now = _now_utc_iso()
        self._traces[str(trace_id)] = {
            "session_id": session_id,
            "task": task,
            "outcome": None,
            "success": None,
            "started_at": now,
            "completed_at": None,
            "metadata": kwargs.get("metadata") or {},
        }
        return payload_to_model(
            {
                "id": str(trace_id),
                "session_id": session_id,
                "task": task,
                "steps": [],
                "started_at": now,
                "created_at": now,
                "metadata": kwargs.get("metadata") or {},
            },
            ReasoningTrace,
        )

    async def add_step(self, trace_id: UUID | str, **kwargs: Any) -> ReasoningStep:
        trace_key = str(trace_id)
        trace = self._traces.get(trace_key)
        if trace is None:
            raise ValueError(f"Unknown trace_id {trace_key!r}. Call start_trace() first.")
        body = _drop_none(
            {
                "conversationId": trace["session_id"],
                "reasoning": kwargs.get("thought") or " ",
                "actionTaken": kwargs.get("action") or " ",
                "result": kwargs.get("observation"),
            }
        )
        payload = await self._transport.request(_SPEC_RECORD_STEP, json=body)
        return payload_to_model(_normalize_step(payload or {}, trace_id=trace_id), ReasoningStep)

    async def record_tool_call(
        self,
        step_id: UUID | str,
        tool_name: str,
        arguments: dict[str, Any],
        **kwargs: Any,
    ) -> ToolCall:
        body = _drop_none(
            {
                "toolName": tool_name,
                "input": _json_safe_str(arguments),
                "stepId": _to_str(step_id),
                "status": kwargs.get("status"),
                "output": _json_safe_str(kwargs.get("result"))
                if kwargs.get("result") is not None
                else None,
                "durationMs": kwargs.get("duration_ms"),
            }
        )
        payload = await self._transport.request(_SPEC_RECORD_TOOL_CALL, json=body)
        return payload_to_model(
            _normalize_tool_call(
                payload or {},
                step_id=step_id,
                fallback_tool_name=tool_name,
                fallback_arguments=arguments,
            ),
            ToolCall,
        )

    async def complete_trace(self, trace_id: UUID | str, **kwargs: Any) -> None:
        trace_key = str(trace_id)
        trace = self._traces.get(trace_key)
        if trace is None:
            return
        trace["outcome"] = kwargs.get("outcome")
        trace["success"] = kwargs.get("success")
        trace["completed_at"] = _now_utc_iso()

    async def search_steps(self, query: str, **kwargs: Any) -> list[ReasoningStep]:
        raise NotSupportedError(
            backend="nams",
            method="ReasoningMemory.search_steps",
            message="NAMS does not expose a step-search endpoint.",
            workaround="Use client.query.cypher(...) over (:ReasoningStep) nodes.",
        )

    async def get_similar_traces(self, task: str, **kwargs: Any) -> list[ReasoningTrace]:
        raise NotSupportedError(
            backend="nams",
            method="ReasoningMemory.get_similar_traces",
            message="NAMS does not expose a similar-traces endpoint.",
        )

    async def get_trace(self, trace_id: UUID | str) -> ReasoningTrace | None:
        trace_key = str(trace_id)
        trace = self._traces.get(trace_key)
        if trace is None:
            return None
        return payload_to_model(
            {
                "id": trace_key,
                "session_id": trace["session_id"],
                "task": trace["task"],
                "steps": [],
                "outcome": trace.get("outcome"),
                "success": trace.get("success"),
                "started_at": trace["started_at"],
                "completed_at": trace.get("completed_at"),
                "created_at": trace["started_at"],
                "metadata": trace.get("metadata") or {},
            },
            ReasoningTrace,
        )

    async def get_trace_with_steps(self, trace_id: UUID | str) -> ReasoningTrace | None:
        trace_key = str(trace_id)
        trace = self._traces.get(trace_key)
        if trace is None:
            return None
        envelope = await self._transport.request(
            _SPEC_GET_REASONING_TRACE,
            path_params={"conversation_id": trace["session_id"]},
        )
        normalized_steps = self._assemble_steps(envelope, trace_id=trace_id)
        return payload_to_model(
            {
                "id": trace_key,
                "session_id": trace["session_id"],
                "task": trace["task"],
                "steps": normalized_steps,
                "outcome": trace.get("outcome"),
                "success": trace.get("success"),
                "started_at": trace["started_at"],
                "completed_at": trace.get("completed_at"),
                "created_at": trace["started_at"],
                "metadata": trace.get("metadata") or {},
            },
            ReasoningTrace,
        )

    async def get_session_traces(self, session_id: str, **kwargs: Any) -> list[ReasoningTrace]:
        envelope = await self._transport.request(
            _SPEC_GET_REASONING_TRACE,
            path_params={"conversation_id": session_id},
        )
        trace_id = uuid4()
        normalized_steps = self._assemble_steps(envelope, trace_id=trace_id)
        if not normalized_steps:
            return []
        return [
            payload_to_model(
                {
                    "id": str(trace_id),
                    "session_id": session_id,
                    "task": "Aggregated session reasoning",
                    "steps": normalized_steps,
                    "started_at": normalized_steps[0]["created_at"],
                    "created_at": normalized_steps[0]["created_at"],
                    "metadata": {},
                },
                ReasoningTrace,
            )
        ]

    async def list_traces(self, **kwargs: Any) -> list[ReasoningTrace]:
        raise NotSupportedError(
            backend="nams",
            method="ReasoningMemory.list_traces",
            message="NAMS has no Trace entity.",
            workaround="Use get_session_traces(session_id) for per-conversation reasoning.",
        )

    async def get_context(self, query: str, **kwargs: Any) -> str:
        return ""

    async def get_tool_stats(self, **kwargs: Any) -> Any:
        raise NotSupportedError(
            backend="nams",
            method="ReasoningMemory.get_tool_stats",
            message="NAMS does not aggregate tool-usage stats via API.",
            workaround="Use client.query.cypher() with a counting query over (:ToolCall) nodes.",
        )

    async def link_trace_to_message(self, trace_id: UUID | str, message_id: UUID | str) -> None:
        # No-op: NAMS steps are already conversation-scoped.
        return None

    def _assemble_steps(self, envelope: Any, *, trace_id: UUID | str) -> list[dict[str, Any]]:
        if not isinstance(envelope, dict):
            return []
        raw_steps = envelope.get("steps") or []
        raw_tool_calls = envelope.get("toolCalls") or envelope.get("tool_calls") or []
        by_step: dict[str, list[dict[str, Any]]] = {}
        for tc in raw_tool_calls:
            if not isinstance(tc, dict):
                continue
            sid = tc.get("stepId") or tc.get("step_id")
            if not sid:
                continue
            by_step.setdefault(str(sid), []).append(_normalize_tool_call(tc, step_id=sid))
        out: list[dict[str, Any]] = []
        for raw in raw_steps:
            if not isinstance(raw, dict):
                continue
            step = _normalize_step(raw, trace_id=trace_id)
            step["tool_calls"] = by_step.get(str(step["id"]), [])
            out.append(step)
        return out


__all__ = ["NamsReasoningMemory"]
