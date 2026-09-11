"""Chat API endpoint with SSE streaming.

The SSE producer is driven by PydanticAI 2.x's ``agent.run_stream_events()``,
so tool events are emitted *when they happen* rather than after the whole
answer has streamed. That is also what makes the reasoning trace real: one
``ReasoningStep`` + ``ToolCall`` is written per tool call as the events arrive,
so ``/api/memory/traces`` reports ``step_count > 0`` and
``/api/memory/tool-stats`` is non-empty.

Memory writes go through ``MemoryIntegration.store_message()``, which adds the
message *and* fires background entity extraction and preference detection.
"""

import json
import logging
import time
import uuid
from collections.abc import AsyncGenerator
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Request
from neo4j import AsyncDriver
from pydantic_ai import AgentRunResultEvent
from pydantic_ai.messages import (
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    PartDeltaEvent,
    PartStartEvent,
    RetryPromptPart,
    TextPart,
    TextPartDelta,
)
from sse_starlette.sse import EventSourceResponse

from neo4j_agent_memory import MemoryClient, MemoryIntegration
from neo4j_agent_memory.memory.reasoning import ToolCallStatus
from neo4j_agent_memory.schema.models import EntityRef, TraceOutcome
from src.agent.agent import get_news_agent
from src.agent.dependencies import AgentDeps
from src.api.schemas import ChatRequest
from src.config import get_settings
from src.memory.client import get_memory_client, get_memory_integration

router = APIRouter()
logger = logging.getLogger(__name__)

#: Which result keys become ``(:ReasoningStep)-[:TOUCHED]->(:Entity)`` edges,
#: and the POLE+O type each maps to.
TOUCHED_FIELDS: dict[str, str] = {
    "people": "PERSON",
    "person": "PERSON",
    "organizations": "ORGANIZATION",
    "organization": "ORGANIZATION",
    "location": "LOCATION",
    "locations": "LOCATION",
}

#: Cap on audit edges per tool call, so one broad search cannot flood the graph.
MAX_TOUCHED_PER_CALL = 8


def safe_serialize(obj: Any) -> Any:
    """Safely convert an object to JSON-serializable format."""
    if obj is None:
        return None
    if isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, uuid.UUID):
        return str(obj)
    if isinstance(obj, (list, tuple)):
        return [safe_serialize(item) for item in obj]
    if isinstance(obj, dict):
        return {str(k): safe_serialize(v) for k, v in obj.items()}
    if hasattr(obj, "model_dump"):
        return safe_serialize(obj.model_dump())
    if hasattr(obj, "__dict__"):
        return safe_serialize(obj.__dict__)
    # For non-serializable objects, convert to string
    return str(obj)


def tool_call_args(part: Any) -> dict[str, Any]:
    """Read a ``ToolCallPart``'s arguments as a dict.

    On PydanticAI 1.x+ ``part.args`` is ``str | dict | None`` and the accessors
    are methods on the *part* (``args_as_dict()``), not attributes of ``args``.
    """
    try:
        return dict(safe_serialize(part.args_as_dict())) if part.args is not None else {}
    except Exception:
        return {}


def extract_touched_entities(result: Any) -> list[EntityRef]:
    """Pull entity references out of a tool result for audit edges.

    Only the fields in :data:`TOUCHED_FIELDS` are considered, and the list is
    capped — these become real ``:Entity`` nodes in the memory graph, so the
    audit query ``MATCH (e:Entity)<-[:TOUCHED]-(s:ReasoningStep)`` can answer
    "which entities did this reasoning step touch?" in one hop.
    """
    rows: list[Any] = result if isinstance(result, list) else [result]
    refs: list[EntityRef] = []
    seen: set[tuple[str, str]] = set()

    for row in rows:
        if not isinstance(row, dict):
            continue
        for key, entity_type in TOUCHED_FIELDS.items():
            value = row.get(key)
            names = value if isinstance(value, list) else [value]
            for name in names:
                if not isinstance(name, str) or not name.strip():
                    continue
                key_pair = (name.strip(), entity_type)
                if key_pair in seen:
                    continue
                seen.add(key_pair)
                refs.append(EntityRef(name=name.strip(), type=entity_type))
                if len(refs) >= MAX_TOUCHED_PER_CALL:
                    return refs
    return refs


def parse_tool_result(content: Any) -> Any:
    """Tools return JSON strings; decode for the SSE payload and audit edges."""
    if isinstance(content, str):
        try:
            return json.loads(content)
        except (ValueError, TypeError):
            return content
    return safe_serialize(content)


async def record_tool_step(
    memory: MemoryClient,
    trace_id: UUID,
    *,
    tool_name: str,
    arguments: dict[str, Any],
    result: Any,
    duration_ms: int,
    failed: bool,
    message_id: str | None,
) -> None:
    """Write one ReasoningStep + ToolCall (+ TOUCHED edges) for a tool call.

    Memory bookkeeping must never break the chat stream, so failures are
    logged rather than raised.
    """
    try:
        step = await memory.reasoning.add_step(
            trace_id,
            thought=f"Query the news graph with {tool_name}",
            action=tool_name,
        )
        await memory.reasoning.record_tool_call(
            step.id,
            tool_name=tool_name,
            arguments=arguments,
            result=result,
            status=ToolCallStatus.FAILURE if failed else ToolCallStatus.SUCCESS,
            duration_ms=duration_ms,
            message_id=message_id,
            touched_entities=extract_touched_entities(result),
            auto_observation=True,
        )
    except Exception as e:
        logger.warning("Could not record reasoning step for %s: %s", tool_name, e)


async def stream_chat_response(
    request: ChatRequest,
    memory: MemoryClient | None,
    integration: MemoryIntegration | None,
    news_driver: AsyncDriver | None,
) -> AsyncGenerator[dict[str, str], None]:
    """Stream chat response as SSE events.

    Yields dicts that EventSourceResponse will serialize to JSON.
    """
    settings = get_settings()
    message_id = str(uuid.uuid4())
    trace_id: UUID | None = None
    user_message_id: str | None = None
    full_response = ""
    memory_enabled = request.memory_enabled and memory is not None and integration is not None

    try:
        deps = AgentDeps.create(
            memory=memory,
            session_id=request.thread_id,
            news_driver=news_driver,
            news_database=settings.news_graph_database,
            memory_enabled=memory_enabled,
        )

        if memory_enabled and integration is not None:
            # store_message() adds the message, extracts entities (when
            # EXTRACTION_MODE != none) and runs pattern-based preference
            # detection in the background — all on the shared client.
            stored = await integration.store_message(
                "user",
                request.message,
                session_id=request.thread_id,
            )
            user_message_id = stored.get("id")
            if "error" in stored:
                logger.warning("Could not store the user message: %s", stored["error"])

        if memory_enabled and memory is not None:
            trace = await memory.reasoning.start_trace(
                session_id=request.thread_id,
                task=request.message,
                # Cross-memory edge: (ReasoningTrace)-[:INITIATED_BY]->(Message)
                triggered_by_message_id=user_message_id,
            )
            trace_id = trace.id

        agent = get_news_agent()
        # tool_call_id -> (tool_name, args, monotonic start)
        in_flight: dict[str, tuple[str, dict[str, Any], float]] = {}
        tool_calls = 0

        async with agent.run_stream_events(request.message, deps=deps) as events:
            async for event in events:
                if isinstance(event, PartStartEvent):
                    if isinstance(event.part, TextPart) and event.part.content:
                        full_response += event.part.content
                        yield {"data": json.dumps({"type": "token", "content": event.part.content})}

                elif isinstance(event, PartDeltaEvent):
                    if isinstance(event.delta, TextPartDelta) and event.delta.content_delta:
                        full_response += event.delta.content_delta
                        yield {
                            "data": json.dumps(
                                {"type": "token", "content": event.delta.content_delta}
                            )
                        }

                elif isinstance(event, FunctionToolCallEvent):
                    args = tool_call_args(event.part)
                    call_id = event.part.tool_call_id or str(uuid.uuid4())
                    in_flight[call_id] = (event.part.tool_name, args, time.monotonic())
                    tool_calls += 1
                    yield {
                        "data": json.dumps(
                            {
                                "type": "tool_call",
                                "id": call_id,
                                "name": event.part.tool_name,
                                "args": args,
                            }
                        )
                    }

                elif isinstance(event, FunctionToolResultEvent):
                    call_id = event.tool_call_id
                    tool_name, args, started = in_flight.pop(
                        call_id,
                        # RetryPromptPart.tool_name is optional.
                        (event.part.tool_name or "unknown_tool", {}, time.monotonic()),
                    )
                    duration_ms = int((time.monotonic() - started) * 1000)
                    failed = isinstance(event.part, RetryPromptPart)
                    result = parse_tool_result(event.part.content)
                    yield {
                        "data": json.dumps(
                            {
                                "type": "tool_result",
                                "id": call_id,
                                "name": tool_name,
                                "result": result,
                                "duration_ms": duration_ms,
                            }
                        )
                    }
                    if trace_id is not None and memory is not None:
                        await record_tool_step(
                            memory,
                            trace_id,
                            tool_name=tool_name,
                            arguments=args,
                            result=result,
                            duration_ms=duration_ms,
                            failed=failed,
                            message_id=user_message_id,
                        )

                elif isinstance(event, AgentRunResultEvent):
                    # Authoritative final text, in case no text part streamed.
                    output = str(event.result.output or "")
                    if output and not full_response:
                        full_response = output
                        yield {"data": json.dumps({"type": "token", "content": output})}

        if memory_enabled and integration is not None:
            await integration.store_message(
                "assistant",
                full_response,
                session_id=request.thread_id,
            )

        if trace_id is not None and memory is not None:
            await memory.reasoning.complete_trace(
                trace_id,
                outcome=TraceOutcome(
                    success=True,
                    summary=full_response[:500],
                    metrics={"tool_calls": float(tool_calls)},
                ),
            )

        yield {
            "data": json.dumps(
                {
                    "type": "done",
                    "message_id": message_id,
                    "trace_id": str(trace_id) if trace_id else None,
                }
            )
        }

    except Exception as e:
        # Never leave half-written memory behind: complete the trace with a
        # failure outcome and persist whatever text did stream.
        logger.exception("Chat turn failed for thread %s", request.thread_id)
        if trace_id is not None and memory is not None:
            try:
                await memory.reasoning.complete_trace(
                    trace_id,
                    outcome=TraceOutcome(
                        success=False,
                        summary=str(e)[:500],
                        error_kind=type(e).__name__,
                    ),
                )
            except Exception:
                logger.warning("Could not complete the failed trace %s", trace_id)
        if full_response and memory_enabled and integration is not None:
            try:
                await integration.store_message(
                    "assistant",
                    full_response,
                    session_id=request.thread_id,
                )
            except Exception:
                logger.warning("Could not store the partial assistant message")
        yield {"data": json.dumps({"type": "error", "message": str(e)})}


@router.post("/chat")
async def chat(request: ChatRequest, http_request: Request) -> EventSourceResponse:
    """Handle chat with SSE streaming response.

    Request body:
    - thread_id: The conversation thread ID
    - message: The user's message
    - memory_enabled: Whether to use memory (default True)

    Response: Server-Sent Events stream with:
    - {"type": "token", "content": "..."}
    - {"type": "tool_call", "id": "...", "name": "...", "args": {...}}
    - {"type": "tool_result", "id": "...", "name": "...", "result": {...}, "duration_ms": 12}
    - {"type": "done", "message_id": "...", "trace_id": "..."}
    - {"type": "error", "message": "..."}
    """
    return EventSourceResponse(
        stream_chat_response(
            request,
            get_memory_client(),
            get_memory_integration(),
            # Application-scoped driver created (and verified) in the lifespan.
            getattr(http_request.app.state, "news_driver", None),
        )
    )
