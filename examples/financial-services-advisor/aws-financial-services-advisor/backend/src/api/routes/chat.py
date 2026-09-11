"""Chat API routes: synchronous and SSE-streaming agent interaction.

Both routes drive the agent with Strands' async API — ``invoke_async`` and
``stream_async``. ``Agent.__call__`` submits to a thread pool and blocks on
``future.result()``, which stalls the event loop and makes SSE pointless: the
whole investigation would finish before the first byte reached the browser.

Each turn writes:

* **two messages** (one user, one assistant) — the single write path, with
  entity extraction on the user turn;
* **one reasoning trace**, opened before the run with
  ``triggered_by_message_id`` and closed with a structured ``TraceOutcome``;
* **one step per tool invocation**, with a ``ToolCall`` and
  ``(:ReasoningStep)-[:TOUCHED]->(:Entity)`` audit edges.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any, cast

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from neo4j_agent_memory.schema import EntityRef
from pydantic import BaseModel, Field

from ...agents import get_supervisor_agent
from ...services.memory_service import FinancialMemoryService, get_memory_service
from ...services.neo4j_service import Neo4jDomainService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/chat", tags=["chat"])


class ChatRequest(BaseModel):
    """Request model for chat endpoint."""

    message: str = Field(..., description="User message to the agent")
    session_id: str | None = Field(
        default=None, description="Session ID for conversation continuity"
    )
    customer_id: str | None = Field(
        default=None, description="Customer context for the conversation"
    )
    include_context: bool = Field(default=True, description="Whether to include graph context")


class ChatResponse(BaseModel):
    """Response model for chat endpoint."""

    response: str = Field(..., description="Agent response")
    session_id: str = Field(..., description="Session ID for this conversation")
    agent: str = Field(default="supervisor", description="Agent that handled the request")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Response metadata")


class ConversationMessage(BaseModel):
    """Single message in conversation history."""

    role: str = Field(..., description="Message role (user, assistant, system)")
    content: str = Field(..., description="Message content")
    timestamp: str | None = Field(default=None, description="Message timestamp")


class SearchRequest(BaseModel):
    """Request model for conversation search."""

    query: str = Field(..., description="Search query")
    session_id: str | None = Field(default=None, description="Limit search to session")
    limit: int = Field(default=10, ge=1, le=100, description="Maximum results")


def _sse_event(event_type: str, data: dict[str, Any]) -> str:
    """Format a Server-Sent Event."""
    return f"event: {event_type}\ndata: {json.dumps(data, default=str)}\n\n"


def _truncate_result(value: Any, max_len: int = 500) -> str:
    """Always return a string representation, truncated."""
    text = json.dumps(value, default=str) if isinstance(value, (dict, list)) else str(value)
    return text[:max_len] + "..." if len(text) > max_len else text


def _require_neo4j(req: Request) -> Neo4jDomainService:
    service = getattr(req.app.state, "neo4j_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="Neo4j service not available")
    # app.state is untyped; the lifespan is the only writer.
    return cast(Neo4jDomainService, service)


def _build_prompt(request: ChatRequest) -> str:
    if request.customer_id and request.include_context:
        return (
            f"Customer Context: {request.customer_id}\n\n"
            f"User Request: {request.message}\n\n"
            "Please analyze and coordinate with specialized agents."
        )
    return request.message


def _touched_entities(
    tool_name: str, tool_input: dict[str, Any], customer_id: str | None
) -> list[EntityRef]:
    """Entities a tool call acted on, for the ``:TOUCHED`` audit edges.

    The tools all identify their subject through one of a handful of argument
    names, so a small map covers every one of them. ``EntityRef`` resolves by
    ``id`` when the argument is a compliance id and by ``name`` otherwise.
    """
    refs: list[EntityRef] = []
    for key in ("customer_id", "entity_id", "transaction_id"):
        value = tool_input.get(key)
        if isinstance(value, str) and value:
            refs.append(EntityRef(id=value))
    for key in ("entity_name", "person_name"):
        value = tool_input.get(key)
        if isinstance(value, str) and value:
            refs.append(EntityRef(name=value))
    if not refs and customer_id:
        refs.append(EntityRef(id=customer_id))
    return refs


class _TurnRecorder:
    """Records one chat turn into short-term and reasoning memory.

    Kept as a small object rather than inline code because the streaming and
    synchronous routes record exactly the same thing and must not drift.
    """

    def __init__(
        self,
        memory: FinancialMemoryService,
        session_id: str,
        request: ChatRequest,
    ) -> None:
        self._memory = memory
        self._session_id = session_id
        self._request = request
        self.user_message_id: Any = None
        self.trace_id: str | None = None
        self.step_count = 0
        self.tool_call_count = 0
        self.touched: dict[str, EntityRef] = {}

    async def begin(self) -> None:
        """Store the user turn and open the trace linked to it."""
        message = await self._memory.add_conversation_message(
            session_id=self._session_id,
            role="user",
            content=self._request.message,
            metadata={"customer_id": self._request.customer_id},
            # One write per turn; extraction runs on this same write and is
            # what populates long-term entities.
            extract_entities=True,
        )
        self.user_message_id = getattr(message, "id", None)
        self.trace_id = await self._memory.start_investigation_trace(
            session_id=self._session_id,
            task=self._request.message,
            triggered_by_message_id=self.user_message_id,
        )

    async def record_tool_use(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        result: Any,
        duration_ms: int,
    ) -> None:
        """One step per tool invocation, with its ToolCall and audit edges."""
        if self.trace_id is None:
            return
        refs = _touched_entities(tool_name, tool_input, self._request.customer_id)
        for ref in refs:
            self.touched[ref.id or ref.name or ""] = ref
        step_id = await self._memory.add_reasoning_step(
            trace_id=self.trace_id,
            agent=tool_name.removeprefix("delegate_to_").removesuffix("_agent"),
            action=tool_name,
            reasoning=f"Called {tool_name} with {_truncate_result(tool_input, 300)}",
            result={"preview": _truncate_result(result, 300)},
        )
        self.step_count += 1
        await self._memory.record_tool_call(
            step_id,
            tool_name=tool_name,
            arguments=tool_input,
            result=_truncate_result(result, 2000),
            duration_ms=duration_ms,
            message_id=self.user_message_id,
            touched_entities=refs,
        )
        self.tool_call_count += 1

    async def finish(
        self, response_text: str, *, success: bool = True, error_kind: str | None = None
    ) -> None:
        """Store the assistant turn and close the trace."""
        await self._memory.add_conversation_message(
            session_id=self._session_id,
            role="assistant",
            content=response_text,
            metadata={"agent": "supervisor"},
        )
        if self.trace_id is None:
            return
        await self._memory.complete_investigation_trace(
            self.trace_id,
            conclusion=response_text[:500],
            success=success,
            error_kind=error_kind,
            related_entities=list(self.touched.values()),
            metrics={
                "steps": float(self.step_count),
                "tool_calls": float(self.tool_call_count),
            },
        )


@router.post("/stream")
async def chat_stream(request: ChatRequest, req: Request) -> StreamingResponse:
    """Chat with the financial advisor via SSE streaming.

    Events, emitted *while* the investigation runs:
    ``agent_start``, ``thinking``, ``tool_call``, ``tool_result``,
    ``agent_complete``, ``response``, ``trace_saved``, ``done``, ``error``.
    """
    session_id = request.session_id or str(uuid.uuid4())
    start_time = time.time()
    neo4j_service = _require_neo4j(req)

    async def event_generator() -> AsyncIterator[str]:
        memory = get_memory_service()
        recorder = _TurnRecorder(memory, session_id, request)
        await recorder.begin()

        supervisor = get_supervisor_agent(neo4j_service, session_id)
        yield _sse_event("agent_start", {"agent": "supervisor", "timestamp": time.time()})

        response_text = ""
        # Strands emits one `current_tool_use` event per tool-use delta; the id
        # lets us fire `tool_call` once per call instead of once per delta.
        seen_tool_uses: dict[str, tuple[str, dict[str, Any], float]] = {}
        try:
            async for event in supervisor.stream_async(_build_prompt(request)):
                if "data" in event:
                    yield _sse_event("thinking", {"content": str(event["data"])})

                tool_use = event.get("current_tool_use") or {}
                tool_use_id = tool_use.get("toolUseId")
                if tool_use_id and tool_use_id not in seen_tool_uses:
                    tool_name = tool_use.get("name", "unknown")
                    seen_tool_uses[tool_use_id] = (tool_name, {}, time.time())
                    yield _sse_event(
                        "tool_call",
                        {"tool": tool_name, "agent": "supervisor", "timestamp": time.time()},
                    )
                if tool_use_id and tool_use_id in seen_tool_uses:
                    name, _, started = seen_tool_uses[tool_use_id]
                    seen_tool_uses[tool_use_id] = (name, _tool_input(tool_use), started)

                message = event.get("message") or {}
                for block in message.get("content", []) or []:
                    tool_result = block.get("toolResult")
                    if not tool_result:
                        continue
                    use_id = tool_result.get("toolUseId", "")
                    name, tool_input, started = seen_tool_uses.get(
                        use_id, ("unknown", {}, time.time())
                    )
                    duration_ms = int((time.time() - started) * 1000)
                    yield _sse_event(
                        "tool_result",
                        {
                            "tool": name,
                            "result": _truncate_result(tool_result.get("content")),
                            "duration_ms": duration_ms,
                        },
                    )
                    await recorder.record_tool_use(
                        name, tool_input, tool_result.get("content"), duration_ms
                    )

                if "result" in event:
                    response_text = str(event["result"])
        except Exception as exc:
            logger.error("Agent error: %s", exc)
            await recorder.finish(
                f"Investigation failed: {exc}", success=False, error_kind="agent_error"
            )
            yield _sse_event("error", {"message": str(exc)})
            return

        yield _sse_event("agent_complete", {"agent": "supervisor", "timestamp": time.time()})
        yield _sse_event("response", {"content": response_text, "session_id": session_id})

        await recorder.finish(response_text)
        yield _sse_event(
            "trace_saved",
            {
                "trace_id": recorder.trace_id,
                "step_count": recorder.step_count,
                "tool_call_count": recorder.tool_call_count,
            },
        )
        yield _sse_event(
            "done",
            {
                "session_id": session_id,
                "agents_consulted": ["supervisor"],
                "tool_call_count": recorder.tool_call_count,
                "total_duration_ms": int((time.time() - start_time) * 1000),
                "trace_id": recorder.trace_id,
            },
        )

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _tool_input(tool_use: dict[str, Any]) -> dict[str, Any]:
    """Parse a ``current_tool_use`` payload's input, which may still be a string."""
    raw = tool_use.get("input")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


@router.post("", response_model=ChatResponse)
async def chat_with_agent(request: ChatRequest, req: Request) -> ChatResponse:
    """Chat with the financial advisor agent (non-streaming)."""
    session_id = request.session_id or str(uuid.uuid4())
    neo4j_service = _require_neo4j(req)
    memory = get_memory_service()
    recorder = _TurnRecorder(memory, session_id, request)

    try:
        await recorder.begin()
        supervisor = get_supervisor_agent(neo4j_service, session_id)
        logger.info("Processing chat request for session %s", session_id)
        result = await supervisor.invoke_async(_build_prompt(request))
        response_text = str(result)
        await recorder.finish(response_text)

        return ChatResponse(
            response=response_text,
            session_id=session_id,
            agent="supervisor",
            metadata={
                "customer_id": request.customer_id,
                "context_included": request.include_context,
                "trace_id": recorder.trace_id,
            },
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Chat error: %s", exc)
        await recorder.finish(
            f"Investigation failed: {exc}", success=False, error_kind="agent_error"
        )
        raise HTTPException(status_code=500, detail=f"Agent error: {exc}") from exc


@router.get("/history/{session_id}", response_model=list[ConversationMessage])
async def get_conversation_history(
    session_id: str,
    limit: int = 50,
) -> list[ConversationMessage]:
    """Get conversation history for a session."""
    try:
        messages = await get_memory_service().get_conversation_history(
            session_id=session_id, limit=limit
        )
        return [
            ConversationMessage(role=m["role"], content=m["content"], timestamp=m.get("timestamp"))
            for m in messages
        ]
    except Exception as exc:
        logger.error("Error fetching history: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/search")
async def search_conversations(request: SearchRequest) -> list[dict[str, Any]]:
    """Search conversation history semantically."""
    try:
        return await get_memory_service().search_conversations(
            query=request.query, session_id=request.session_id, limit=request.limit
        )
    except Exception as exc:
        logger.error("Search error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
