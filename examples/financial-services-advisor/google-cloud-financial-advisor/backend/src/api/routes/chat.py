"""Chat API routes for interacting with the financial advisor agents.

Two entry points share one ADK event consumer (``services.adk_events``):

* ``POST /api/chat/stream`` — Server-Sent Events, used by the React frontend;
* ``POST /api/chat`` — the same run, collected into a single JSON response.

Both persist an audit-grade reasoning trace: the user message is stored first so
the trace can carry ``triggered_by_message_id`` (an ``INITIATED_BY`` edge), each
agent activation becomes a ``ReasoningStep``, each tool call records the
entities it touched (``(:ReasoningStep)-[:TOUCHED]->(:Entity)``), and the trace
is completed with a structured :class:`TraceOutcome`. Steps are written *as the
run proceeds*, so a crashed run still leaves a partial trace behind.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from ...agents.supervisor import get_supervisor_agent
from ...models.chat import (
    ChatMessage,
    ChatRequest,
    ChatResponse,
    ConversationHistory,
    MessageRole,
    SearchRequest,
    SearchResponse,
    SearchResult,
    ToolCall,
)
from ...services.adk_events import (
    collect_run,
    consume_run,
    truncate_result,
)
from ...services.memory_service import (
    FinancialMemoryService,
    get_initialized_memory_service,
)
from ...services.trace_writer import TraceWriter

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/chat", tags=["chat"])

APP_NAME = "financial_advisor"

# Demo-only: in-memory session service. Use a persistent store for production.
session_service = InMemorySessionService()


def _sse_event(event_type: str, data: dict[str, Any]) -> str:
    """Format a Server-Sent Event."""
    return f"event: {event_type}\ndata: {json.dumps(data, default=str)}\n\n"


def _truncate_result(result: Any, max_len: int = 500) -> str | None:
    """Truncate tool results for SSE transmission, always returning a string."""
    return truncate_result(result, max_len)


def _memory_operation(tool_name: str) -> str | None:
    """Classify a tool name as a memory read or write, for the UI indicator."""
    if any(kw in tool_name for kw in ("search", "context", "history", "load_memory")):
        return "search"
    if any(kw in tool_name for kw in ("store", "finding", "record")):
        return "store"
    return None


def _build_runner(
    memory_service: FinancialMemoryService,
    neo4j_service: Any,
) -> Runner:
    """Build an ADK Runner wired to both session and memory services.

    Passing ``memory_service=`` is what gives the agents ADK's native
    ``load_memory`` tool (and ``preload_memory``) against Neo4j — no bespoke
    search tool per agent.
    """
    supervisor = get_supervisor_agent(memory_service, neo4j_service=neo4j_service)
    return Runner(
        agent=supervisor,
        app_name=APP_NAME,
        session_service=session_service,
        memory_service=memory_service.adk_memory_service,
    )


async def _ensure_session(session_id: str, user_id: str) -> None:
    """Create the ADK session if it does not exist yet."""
    session = await session_service.get_session(
        app_name=APP_NAME,
        user_id=user_id,
        session_id=session_id,
    )
    if session is None:
        await session_service.create_session(
            app_name=APP_NAME,
            user_id=user_id,
            session_id=session_id,
        )


def _contextualise(request: ChatRequest) -> str:
    """Prefix the user message with any customer/investigation context."""
    context_parts = []
    if request.customer_id:
        context_parts.append(f"Customer context: {request.customer_id}")
    if request.investigation_id:
        context_parts.append(f"Investigation context: {request.investigation_id}")
    if not context_parts:
        return request.message
    return f"{' | '.join(context_parts)}\n\n{request.message}"


@router.post("/stream")
async def chat_stream(
    request: ChatRequest,
    raw_request: Request,
    memory_service: FinancialMemoryService = Depends(get_initialized_memory_service),
):
    """Stream agent events via Server-Sent Events.

    Event types: ``agent_start``/``agent_complete``, ``agent_delegate``,
    ``tool_call``/``tool_result``, ``memory_access``, ``thinking``,
    ``response``, ``trace_saved``, ``done``, ``error``.
    """
    start_time = time.time()
    session_id = request.session_id or str(uuid.uuid4())
    user_id = "user"

    neo4j_service = getattr(raw_request.app.state, "neo4j_service", None)
    await _ensure_session(session_id, user_id)
    runner = _build_runner(memory_service, neo4j_service)
    user_message = _contextualise(request)

    async def event_generator():
        response_text = ""
        agents_consulted: list[str] = []
        trace = TraceWriter(memory_service, session_id)

        try:
            # Store the user turn first: its id becomes the trace's
            # INITIATED_BY target.
            user_message_id = None
            try:
                stored = await memory_service.store_message(session_id, "user", request.message)
                user_message_id = stored.id
            except Exception as exc:
                logger.error("Failed to store user message: %s", exc, exc_info=True)

            await trace.start(request.message, triggered_by_message_id=user_message_id)

            async for run_event in consume_run(
                runner,
                user_id=user_id,
                session_id=session_id,
                new_message=types.Content(
                    role="user",
                    parts=[types.Part(text=user_message)],
                ),
            ):
                await trace.handle(run_event)
                payload: dict[str, Any] = {
                    "agent": run_event.agent,
                    "timestamp": run_event.timestamp,
                }

                if run_event.kind == "agent_start":
                    if run_event.agent and run_event.agent not in agents_consulted:
                        agents_consulted.append(run_event.agent)
                    yield _sse_event("agent_start", payload)
                elif run_event.kind == "agent_complete":
                    yield _sse_event("agent_complete", payload)
                elif run_event.kind == "agent_delegate":
                    yield _sse_event(
                        "agent_delegate",
                        {
                            "from": run_event.agent,
                            "to": run_event.target,
                            "timestamp": run_event.timestamp,
                        },
                    )
                elif run_event.kind == "tool_call":
                    operation = _memory_operation(run_event.tool or "")
                    if operation:
                        yield _sse_event(
                            "memory_access",
                            {
                                **payload,
                                "operation": operation,
                                "tool": run_event.tool,
                                "query": run_event.args.get("query", ""),
                            },
                        )
                    yield _sse_event(
                        "tool_call",
                        {**payload, "tool": run_event.tool, "args": run_event.args},
                    )
                elif run_event.kind == "tool_result":
                    yield _sse_event(
                        "tool_result",
                        {
                            **payload,
                            "tool": run_event.tool,
                            "result": _truncate_result(run_event.result),
                        },
                    )
                elif run_event.kind == "thinking":
                    yield _sse_event(
                        "thinking",
                        {**payload, "thought": (run_event.text or "")[:300]},
                    )
                    response_text += run_event.text or ""
                elif run_event.kind == "response":
                    response_text += run_event.text or ""

            if not response_text:
                response_text = "Investigation complete."

            yield _sse_event("response", {"content": response_text, "session_id": session_id})

            # Store the assistant turn (entity extraction runs here too).
            try:
                await memory_service.store_message(session_id, "assistant", response_text)
            except Exception as exc:
                logger.error("Failed to store assistant message: %s", exc, exc_info=True)

            total_duration = int((time.time() - start_time) * 1000)
            await trace.complete(
                summary=response_text,
                success=True,
                duration_ms=total_duration,
            )

            if trace.trace_id is not None:
                yield _sse_event(
                    "trace_saved",
                    {
                        "trace_id": str(trace.trace_id),
                        "step_count": trace.step_count,
                        "tool_call_count": trace.tool_call_count,
                        "touched_entities": [r.name for r in trace.touched],
                    },
                )

            yield _sse_event(
                "done",
                {
                    "session_id": session_id,
                    "agents_consulted": agents_consulted,
                    "tool_call_count": trace.tool_call_count,
                    "total_duration_ms": total_duration,
                    "trace_id": str(trace.trace_id) if trace.trace_id else None,
                },
            )

        except Exception as exc:
            logger.error("Stream error: %s", exc, exc_info=True)
            await trace.complete(
                summary=str(exc),
                success=False,
                duration_ms=int((time.time() - start_time) * 1000),
                error_kind=type(exc).__name__,
            )
            yield _sse_event("error", {"message": str(exc)})
        finally:
            await runner.close()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    raw_request: Request,
    memory_service: FinancialMemoryService = Depends(get_initialized_memory_service),
) -> ChatResponse:
    """Send a message to the financial advisor and get a single JSON response.

    Same run as ``/stream``, collected instead of streamed.
    """
    start_time = time.time()
    session_id = request.session_id or str(uuid.uuid4())
    user_id = "user"

    try:
        neo4j_service = getattr(raw_request.app.state, "neo4j_service", None)
        await _ensure_session(session_id, user_id)
        runner = _build_runner(memory_service, neo4j_service)

        stored = await memory_service.store_message(session_id, "user", request.message)
        trace = TraceWriter(memory_service, session_id)
        await trace.start(request.message, triggered_by_message_id=stored.id)

        try:
            summary = await collect_run(
                runner,
                user_id=user_id,
                session_id=session_id,
                new_message=types.Content(
                    role="user",
                    parts=[types.Part(text=_contextualise(request))],
                ),
            )
        finally:
            await runner.close()

        for run_event in summary.events:
            await trace.handle(run_event)

        response_text = summary.response_text or "Investigation complete."
        await memory_service.store_message(session_id, "assistant", response_text)

        response_time = int((time.time() - start_time) * 1000)
        await trace.complete(
            summary=response_text,
            success=True,
            duration_ms=response_time,
        )

        return ChatResponse(
            session_id=session_id,
            message=ChatMessage(
                role=MessageRole.ASSISTANT,
                content=response_text,
            ),
            agents_consulted=summary.agents_consulted,
            tool_calls=[
                ToolCall(
                    tool_name=call.tool,
                    arguments=call.args,
                    agent=call.agent,
                )
                for call in summary.tool_calls
            ],
            customer_id=request.customer_id,
            investigation_id=request.investigation_id,
            response_time_ms=response_time,
        )

    except Exception as exc:
        logger.error("Chat error: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/history/{session_id}", response_model=ConversationHistory)
async def get_conversation_history(
    session_id: str,
    limit: int = 50,
    memory_service: FinancialMemoryService = Depends(get_initialized_memory_service),
) -> ConversationHistory:
    """Get conversation history for a session."""
    try:
        entries = await memory_service.get_conversation_history(session_id, limit)

        messages = [
            ChatMessage(
                role=MessageRole(entry.metadata.get("role", "assistant"))
                if entry.metadata
                else MessageRole.ASSISTANT,
                content=entry.content,
            )
            for entry in entries
        ]

        return ConversationHistory(
            session_id=session_id,
            messages=messages,
        )

    except Exception as exc:
        logger.error("Error getting history: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/search", response_model=SearchResponse)
async def search_memory(
    request: SearchRequest,
    memory_service: FinancialMemoryService = Depends(get_initialized_memory_service),
) -> SearchResponse:
    """Search the context graph for relevant information."""
    try:
        results = await memory_service.search_context(
            query=request.query,
            limit=request.limit,
            threshold=request.threshold,
        )

        return SearchResponse(
            query=request.query,
            results=[
                SearchResult(
                    content=r["content"],
                    type=r["type"],
                    score=r.get("score"),
                    metadata=r.get("metadata") or {},
                )
                for r in results
            ],
            total=len(results),
        )

    except Exception as exc:
        logger.error("Search error: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.delete("/session/{session_id}")
async def clear_session(
    session_id: str,
    memory_service: FinancialMemoryService = Depends(get_initialized_memory_service),
) -> dict[str, str]:
    """Clear a conversation session."""
    try:
        await memory_service.clear_session(session_id)
        return {"status": "cleared", "session_id": session_id}
    except Exception as exc:
        logger.error("Error clearing session: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
