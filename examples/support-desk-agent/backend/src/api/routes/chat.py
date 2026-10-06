"""``POST /api/chat`` — one agent turn, streamed as server-sent events.

The order of a turn, and the memory each step writes:

1. Store the user message (short-term memory). GLiNER2.5 extracts its entities
   against the active ontology and resolution merges them onto existing nodes;
   the stream reports them as ``message_stored``.
2. ``reasoning.start_trace(task=message, triggered_by_message_id=...)`` — the
   trace is linked ``(:ReasoningTrace)-[:INITIATED_BY]->(:Message)``.
3. Run the agent with ``run_stream_events()``. Every tool result becomes a
   ``ReasoningStep`` plus a ``ToolCall`` (``TRIGGERED_BY`` the user message),
   with ``(:ReasoningStep)-[:TOUCHED]->(:Entity)`` edges for the entities the
   tool named, written as the events arrive.
4. Store the assistant message and complete the trace with a ``TraceOutcome``.

Memory bookkeeping never breaks the stream: a failed write is logged and the
turn goes on. A failed turn completes its trace with ``success=False``.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Request
from pydantic_ai import AgentRunResultEvent
from pydantic_ai.messages import (
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    PartDeltaEvent,
    PartStartEvent,
    RetryPromptPart,
    TextPart,
    TextPartDelta,
    UserPromptPart,
)
from sse_starlette.sse import EventSourceResponse

from neo4j_agent_memory import BoltMemoryClient
from neo4j_agent_memory.memory.reasoning import ToolCallStatus
from neo4j_agent_memory.schema.models import EntityRef, TraceOutcome
from src.agent.agent import get_agent
from src.agent.deps import SupportDeskDeps
from src.agent.thoughts import thought_for
from src.agent.tools import entity_refs
from src.api import memory_service
from src.api.routes.threads import DEFAULT_TITLE, set_title, title_from
from src.api.schemas import ChatRequest
from src.config import get_settings
from src.memory import jsonable
from src.ontology import entity_labels
from src.statebench import world

router = APIRouter()
logger = logging.getLogger(__name__)

#: Prior messages replayed to the model, so a follow-up question has context.
HISTORY_LIMIT = 20

MESSAGE_ENTITIES = """
MATCH (m:Message {id: $id})-[:MENTIONS]->(e:Entity)
RETURN DISTINCT e.name AS name, e.type AS type, e.subtype AS subtype, labels(e) AS labels
ORDER BY name
"""

CONVERSATION_TITLE = """
MATCH (c:Conversation {session_id: $session_id})
RETURN c.title AS title
"""


def _event(payload: dict[str, Any]) -> dict[str, str]:
    """One SSE frame: ``data: <json>``."""
    return {"data": json.dumps(payload, default=str)}


def _tool_args(part: Any) -> dict[str, Any]:
    try:
        args = part.args_as_dict() if part.args is not None else {}
    except Exception:
        return {}
    converted = jsonable(args)
    return converted if isinstance(converted, dict) else {}


def _tool_result(content: Any) -> Any:
    """Tool results are dicts; retry prompts are strings (maybe JSON)."""
    if isinstance(content, str):
        try:
            return json.loads(content)
        except ValueError:
            return content
    return jsonable(content)


def _touched(result: Any) -> list[dict[str, Any]]:
    touched = result.get("touched") if isinstance(result, dict) else None
    return [item for item in touched if isinstance(item, dict)] if isinstance(touched, list) else []


async def _touched_for(
    client: BoltMemoryClient, tool_name: str, arguments: dict[str, Any], result: Any
) -> list[dict[str, Any]]:
    """The entities a call named: STATE-Bench calls by the record ids they mention."""
    if tool_name in world.TOOL_NAMES:
        return world.touched(await world.load_world(client), tool_name, arguments, result)
    return _touched(result)


async def _touched_refs(
    client: BoltMemoryClient, touched: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """The touched entities with their labels, for the stream. Never raises."""
    try:
        return await entity_refs(client, touched)
    except Exception:
        logger.warning("Could not look up the labels of touched entities", exc_info=True)
        return [
            {
                "id": str(t.get("id") or ""),
                "name": str(t["name"]),
                "type": str(t.get("type") or ""),
                "labels": [],
            }
            for t in touched
            if t.get("name")
        ]


async def _history(client: BoltMemoryClient, session_id: str) -> list[ModelMessage]:
    """The thread's earlier messages as PydanticAI message history."""
    conversation = await client.short_term.get_conversation(session_id)
    history: list[ModelMessage] = []
    for message in conversation.messages[-HISTORY_LIMIT:]:
        if message.role.value == "user":
            history.append(ModelRequest(parts=[UserPromptPart(content=message.content)]))
        elif message.role.value == "assistant":
            history.append(ModelResponse(parts=[TextPart(content=message.content)]))
    return history


async def _message_entities(client: BoltMemoryClient, message_id: str) -> list[dict[str, Any]]:
    rows = await client.query.cypher(MESSAGE_ENTITIES, {"id": message_id})
    return [
        {
            "name": row["name"],
            "type": row.get("type"),
            "subtype": row.get("subtype"),
            "labels": entity_labels(row.get("labels") or []),
        }
        for row in rows
    ]


async def _ensure_title(client: BoltMemoryClient, session_id: str, message: str) -> None:
    """Title an untitled thread after its first message, so the sidebar can tell chats apart."""
    rows = await client.query.cypher(CONVERSATION_TITLE, {"session_id": session_id})
    if rows and rows[0].get("title") in (None, "", DEFAULT_TITLE):
        await set_title(client, session_id, title_from(message))


async def _record_step(
    client: BoltMemoryClient,
    trace_id: str,
    *,
    tool_name: str,
    arguments: dict[str, Any],
    result: Any,
    duration_ms: int,
    failed: bool,
    message_id: str | None,
    touched_entities: list[dict[str, Any]],
) -> None:
    """One ReasoningStep + ToolCall + TOUCHED edges. Logged, never raised."""
    try:
        step = await client.reasoning.add_step(
            trace_id,
            thought=thought_for(tool_name, arguments),
            action=tool_name,
        )
        touched = [
            EntityRef(id=item["id"], name=item.get("name"), type=item.get("type"))
            for item in touched_entities
            if item.get("id")
        ]
        await client.reasoning.record_tool_call(
            step.id,
            tool_name=tool_name,
            arguments=arguments,
            result=result,
            status=ToolCallStatus.ERROR if failed else ToolCallStatus.SUCCESS,
            duration_ms=duration_ms,
            error=str(result)[:500] if failed else None,
            message_id=message_id,
            touched_entities=touched,
            auto_observation=True,
        )
    except Exception:
        logger.warning("Could not record the reasoning step for %s", tool_name, exc_info=True)


async def stream_turn(request: ChatRequest, http_request: Request) -> AsyncIterator[dict[str, str]]:
    """The SSE producer for one chat turn."""
    settings = get_settings()
    service = memory_service(http_request)
    try:
        client = await service.get_client()
    except Exception as exc:
        yield _event({"type": "error", "message": f"Neo4j is unavailable: {exc}"})
        return

    session_id = request.thread_id
    user_message_id: str | None = None
    trace_id: str | None = None
    response_text = ""
    tool_calls = 0

    try:
        # TestModel only calls tools on a turn with no earlier model response in
        # its history, so the keyless smoke-test model runs every turn fresh.
        history = [] if settings.uses_test_model else await _history(client, session_id)

        # 1. Store the user message; extraction and resolution run here.
        try:
            stored = await client.short_term.add_message(session_id, "user", request.message)
            user_message_id = str(stored.id)
            await world.repair_id_mentions(client, [user_message_id])
            await _ensure_title(client, session_id, request.message)
            entities = await _message_entities(client, user_message_id)
            yield _event(
                {"type": "message_stored", "message_id": user_message_id, "entities": entities}
            )
        except Exception:
            logger.warning("Could not store the user message", exc_info=True)

        # 2. Open the reasoning trace, linked to the message that started it.
        try:
            trace = await client.reasoning.start_trace(
                session_id, request.message, triggered_by_message_id=user_message_id
            )
            trace_id = str(trace.id)
            yield _event({"type": "trace_started", "trace_id": trace_id})
        except Exception:
            logger.warning("Could not start the reasoning trace", exc_info=True)

        # 3. Run the agent, recording one step per tool call as it happens.
        deps = SupportDeskDeps(
            client=client,
            session_id=session_id,
            user_message_id=user_message_id,
            trace_id=trace_id,
        )
        in_flight: dict[str, tuple[str, dict[str, Any], float]] = {}
        async with get_agent().run_stream_events(
            request.message, deps=deps, message_history=history
        ) as events:
            async for event in events:
                if isinstance(event, PartStartEvent):
                    if isinstance(event.part, TextPart) and event.part.content:
                        response_text += event.part.content
                        yield _event({"type": "token", "content": event.part.content})
                elif isinstance(event, PartDeltaEvent):
                    if isinstance(event.delta, TextPartDelta) and event.delta.content_delta:
                        response_text += event.delta.content_delta
                        yield _event({"type": "token", "content": event.delta.content_delta})
                elif isinstance(event, FunctionToolCallEvent):
                    call_id = event.part.tool_call_id or uuid.uuid4().hex
                    args = _tool_args(event.part)
                    in_flight[call_id] = (event.part.tool_name, args, time.monotonic())
                    tool_calls += 1
                    yield _event(
                        {
                            "type": "tool_call",
                            "id": call_id,
                            "name": event.part.tool_name,
                            "args": args,
                        }
                    )
                elif isinstance(event, FunctionToolResultEvent):
                    call_id = event.tool_call_id
                    tool_name, args, started = in_flight.pop(
                        call_id, (event.part.tool_name or "unknown_tool", {}, time.monotonic())
                    )
                    duration_ms = int((time.monotonic() - started) * 1000)
                    failed = isinstance(event.part, RetryPromptPart)
                    result = _tool_result(event.part.content)
                    try:
                        touched = await _touched_for(client, tool_name, args, result)
                    except Exception:
                        logger.warning(
                            "Could not work out what %s touched", tool_name, exc_info=True
                        )
                        touched = []
                    yield _event(
                        {
                            "type": "tool_result",
                            "id": call_id,
                            "name": tool_name,
                            "result": result,
                            "duration_ms": duration_ms,
                            "touched": await _touched_refs(client, touched),
                        }
                    )
                    if trace_id is not None:
                        await _record_step(
                            client,
                            trace_id,
                            tool_name=tool_name,
                            arguments=args,
                            result=result,
                            duration_ms=duration_ms,
                            failed=failed,
                            message_id=user_message_id,
                            touched_entities=touched,
                        )
                elif isinstance(event, AgentRunResultEvent):
                    output = str(event.result.output or "")
                    if output and not response_text:
                        response_text = output
                        yield _event({"type": "token", "content": output})

        # 4. Store the answer and close the trace.
        assistant_message_id: str | None = None
        try:
            answer = await client.short_term.add_message(
                session_id,
                "assistant",
                response_text,
                # TestModel answers with a JSON dump of tool results, not prose.
                extract_entities=not settings.uses_test_model,
            )
            assistant_message_id = str(answer.id)
            await world.repair_id_mentions(client, [assistant_message_id])
        except Exception:
            logger.warning("Could not store the assistant message", exc_info=True)
        if trace_id is not None:
            try:
                await client.reasoning.complete_trace(
                    trace_id,
                    outcome=TraceOutcome(
                        success=True,
                        summary=response_text[:500],
                        metrics={"tool_calls": float(tool_calls)},
                    ),
                )
            except Exception:
                logger.warning("Could not complete trace %s", trace_id, exc_info=True)
        yield _event({"type": "done", "message_id": assistant_message_id, "trace_id": trace_id})

    except Exception as exc:
        logger.exception("Chat turn failed for thread %s", session_id)
        if trace_id is not None:
            try:
                await client.reasoning.complete_trace(
                    trace_id,
                    outcome=TraceOutcome(
                        success=False,
                        summary=str(exc)[:500] or type(exc).__name__,
                        error_kind=type(exc).__name__,
                        metrics={"tool_calls": float(tool_calls)},
                    ),
                )
            except Exception:
                logger.warning("Could not complete the failed trace %s", trace_id)
        if response_text:
            try:
                await client.short_term.add_message(
                    session_id, "assistant", response_text, extract_entities=False
                )
            except Exception:
                logger.warning("Could not store the partial answer")
        yield _event({"type": "error", "message": str(exc) or type(exc).__name__})


@router.post("/chat")
async def chat(request: ChatRequest, http_request: Request) -> EventSourceResponse:
    """Stream one agent turn. Each event is ``data: <json>`` with a ``type``:

    ``message_stored``, ``trace_started``, ``token``, ``tool_call``,
    ``tool_result``, ``done`` or ``error``.
    """
    return EventSourceResponse(stream_turn(request, http_request))
