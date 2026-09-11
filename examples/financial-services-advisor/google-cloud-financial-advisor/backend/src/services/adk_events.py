"""One place that knows the shape of a Google ADK ``Event``.

``/api/chat``, ``/api/chat/stream`` and ``/api/investigations/{id}/start`` all
need the same four facts out of a run: which agent is speaking, what it
delegated, which tools it called with which arguments, and what it finally
said. Previously each route re-derived that, and two of the three guessed at
attributes ADK does not have (``event.tool_calls``, ``event.agent_name``) so
they silently reported nothing.

ADK 2.x exposes exactly this surface on ``Event``:

================================  ==========================================
``event.author``                  the agent (or ``"user"``) that produced it
``event.actions.transfer_to_agent``  delegation target, when delegating
``event.get_function_calls()``    ``types.FunctionCall`` list
``event.get_function_responses()``  ``types.FunctionResponse`` list
``event.content.parts[*].text``   text chunks
``event.is_final_response()``     whether the text is the final answer
================================  ==========================================

``consume_run`` normalises those into :class:`RunEvent` records;
``collect_run`` drains them into a :class:`RunSummary` for the non-streaming
callers.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

#: ADK-internal functions used for agent delegation. They are surfaced as
#: ``agent_delegate`` events instead of being reported as tool calls.
INTERNAL_FUNCTIONS = frozenset({"transfer_to_agent", "transfer"})


def truncate_result(result: Any, max_len: int = 500) -> str | None:
    """Render a tool result as a string, truncated for transport.

    Always returns a string (or ``None``): dicts and lists go through
    ``json.dumps`` so a frontend never renders ``[object Object]``.
    """
    if result is None:
        return None
    if isinstance(result, (dict, list)):
        rendered = json.dumps(result, default=str)
    else:
        rendered = str(result)
    if len(rendered) > max_len:
        return rendered[:max_len] + "..."
    return rendered


@dataclass
class ToolInvocation:
    """A completed tool call observed during a run."""

    tool: str
    args: dict[str, Any]
    result: Any = None
    agent: str | None = None


@dataclass
class RunEvent:
    """A normalised event from an ADK run."""

    kind: str
    timestamp: float
    agent: str | None = None
    target: str | None = None
    tool: str | None = None
    args: dict[str, Any] = field(default_factory=dict)
    result: Any = None
    text: str | None = None


@dataclass
class RunSummary:
    """Everything the non-streaming callers need from a run."""

    response_text: str = ""
    agents_consulted: list[str] = field(default_factory=list)
    tool_calls: list[ToolInvocation] = field(default_factory=list)
    step_count: int = 0
    events: list[RunEvent] = field(default_factory=list)


async def consume_run(
    runner: Any,
    *,
    user_id: str,
    session_id: str,
    new_message: Any,
) -> AsyncIterator[RunEvent]:
    """Drive ``runner.run_async`` and yield normalised :class:`RunEvent`s."""
    current_agent: str | None = None
    pending_calls: dict[str, dict[str, Any]] = {}

    async for event in runner.run_async(
        user_id=user_id,
        session_id=session_id,
        new_message=new_message,
    ):
        now = time.time()

        # --- Agent transitions come from event.author ---
        author = event.author if event.author != "user" else None
        if author and author != current_agent:
            if current_agent:
                yield RunEvent("agent_complete", now, agent=current_agent)
                yield RunEvent("agent_delegate", now, agent=current_agent, target=author)
            current_agent = author
            yield RunEvent("agent_start", now, agent=current_agent)

        # --- Explicit delegation recorded in the event actions ---
        if event.actions and event.actions.transfer_to_agent:
            yield RunEvent(
                "agent_delegate",
                now,
                agent=current_agent or "supervisor",
                target=event.actions.transfer_to_agent,
            )

        # --- Tool calls ---
        for call in event.get_function_calls():
            if call.name in INTERNAL_FUNCTIONS:
                continue
            args = dict(call.args) if call.args else {}
            pending_calls[call.name] = args
            yield RunEvent("tool_call", now, agent=current_agent, tool=call.name, args=args)

        # --- Tool results ---
        for response in event.get_function_responses():
            if response.name in INTERNAL_FUNCTIONS:
                continue
            args = pending_calls.pop(response.name, {})
            yield RunEvent(
                "tool_result",
                now,
                agent=current_agent,
                tool=response.name,
                args=args,
                result=response.response,
            )

        # --- Text ---
        if event.content and event.content.parts:
            is_final = event.is_final_response()
            for part in event.content.parts:
                if not part.text:
                    continue
                yield RunEvent(
                    "response" if is_final else "thinking",
                    now,
                    agent=current_agent,
                    text=part.text,
                )

    if current_agent:
        yield RunEvent("agent_complete", time.time(), agent=current_agent)


async def collect_run(
    runner: Any,
    *,
    user_id: str,
    session_id: str,
    new_message: Any,
) -> RunSummary:
    """Run to completion and return a :class:`RunSummary`."""
    summary = RunSummary()
    seen_agents: list[str] = []

    async for run_event in consume_run(
        runner,
        user_id=user_id,
        session_id=session_id,
        new_message=new_message,
    ):
        summary.events.append(run_event)
        if run_event.kind == "agent_start":
            summary.step_count += 1
            if run_event.agent and run_event.agent not in seen_agents:
                seen_agents.append(run_event.agent)
        elif run_event.kind == "tool_result":
            summary.tool_calls.append(
                ToolInvocation(
                    tool=run_event.tool or "unknown",
                    args=run_event.args,
                    result=run_event.result,
                    agent=run_event.agent,
                )
            )
        elif run_event.kind in ("response", "thinking") and run_event.text:
            summary.response_text += run_event.text

    summary.agents_consulted = seen_agents
    return summary
