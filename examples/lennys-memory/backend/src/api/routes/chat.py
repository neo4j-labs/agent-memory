"""Chat API endpoint with SSE streaming."""

import json
import logging
import time
import uuid
from collections.abc import AsyncGenerator
from typing import Any
from uuid import UUID

from fastapi import APIRouter
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from sse_starlette.sse import EventSourceResponse

from neo4j_agent_memory import MemoryClient
from neo4j_agent_memory.mcp._preference_detector import PreferenceDetector
from neo4j_agent_memory.memory.reasoning import ToolCallStatus
from neo4j_agent_memory.memory.short_term import MessageRole
from neo4j_agent_memory.schema.models import EntityRef, TraceOutcome
from src.agent.agent import get_podcast_agent
from src.agent.dependencies import AgentDeps
from src.agent.tools import PREFERENCE_CATEGORIES
from src.api.schemas import ChatRequest
from src.memory.client import get_memory_client

router = APIRouter()
logger = logging.getLogger(__name__)


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


# Preference detection is delegated to the library's pattern-based detector,
# which extracts the preference *clause* instead of storing the whole message.
_preference_detector = PreferenceDetector()

# Podcast-specific category keywords. The detector's own categories (food,
# music, ...) are not useful here, so we re-categorize into the four buckets
# ``get_user_preferences`` reads back (``tools.PREFERENCE_CATEGORIES``).
_FORMAT_WORDS = ("format", "summary", "summaries", "bullet", "concise", "brief", "detailed")
_CONTENT_WORDS = ("topic", "subject", "podcast", "episode", "guest")
_TOPIC_WORDS = ("product", "growth", "startup", "leadership", "career", "mental health")


async def get_conversation_history(
    memory: MemoryClient,
    session_id: str,
    limit: int = 20,
) -> list[ModelRequest | ModelResponse]:
    """Fetch the most recent conversation turns in PydanticAI message format.

    Args:
        memory: The memory client.
        session_id: The conversation session ID.
        limit: Maximum number of messages to retrieve.

    Returns:
        List of PydanticAI messages suitable for message_history parameter.
    """
    try:
        # ``get_conversation`` orders messages oldest-first and applies LIMIT in
        # Cypher, so passing ``limit=`` would pin the agent to the *opening*
        # turns of a long thread. Fetch the conversation and slice the tail.
        conversation = await memory.short_term.get_conversation(session_id=session_id)

        if not conversation or not conversation.messages:
            return []

        history: list[ModelRequest | ModelResponse] = []

        for msg in conversation.messages[-limit:]:
            if msg.role == MessageRole.USER:
                # User messages become ModelRequest with UserPromptPart
                history.append(ModelRequest(parts=[UserPromptPart(content=msg.content)]))
            elif msg.role == MessageRole.ASSISTANT:
                # Assistant messages become ModelResponse with TextPart
                history.append(ModelResponse(parts=[TextPart(content=msg.content)]))
            # Skip system messages as they're handled by the system prompt

        return history

    except Exception as e:
        logger.warning(f"Failed to fetch conversation history: {e}")
        return []


# POLE+O types (plus the podcast schema's custom types) that a tool result may
# report. Used to turn tool output into ``:TOUCHED`` audit edges.
_KNOWN_ENTITY_TYPES = {
    "PERSON",
    "OBJECT",
    "LOCATION",
    "EVENT",
    "ORGANIZATION",
    "CONCEPT",
    "PRODUCT",
    "TECHNOLOGY",
    "BOOK",
    "COMPANY",
}


def extract_touched_entities(result: Any) -> list[EntityRef]:
    """Pull ``{name, type}`` pairs out of a tool result.

    Entity-shaped rows become :class:`EntityRef`s, which ``record_tool_call``
    materializes as ``(:ReasoningStep)-[:TOUCHED]->(:Entity)`` edges so an audit
    query can reach "what did this step affect?" in one hop. See
    ``docs/.../how-to/audit-reasoning.adoc``.
    """
    if not isinstance(result, list):
        return []
    refs: dict[tuple[str, str], EntityRef] = {}
    for row in result[:25]:
        if not isinstance(row, dict):
            continue
        name = row.get("name")
        raw_type = row.get("type") or row.get("entity_type")
        if not isinstance(name, str) or not isinstance(raw_type, str):
            continue
        base_type = raw_type.split(":", 1)[0].upper()
        if base_type not in _KNOWN_ENTITY_TYPES:
            continue
        refs[(name, base_type)] = EntityRef(name=name, type=base_type)
    return list(refs.values())


def categorize_preference(text: str) -> str:
    """Map a detected preference clause onto one of this app's categories."""
    lowered = text.lower()
    if any(word in lowered for word in _FORMAT_WORDS):
        return "format"
    if any(word in lowered for word in _CONTENT_WORDS):
        return "content"
    if any(word in lowered for word in _TOPIC_WORDS):
        return "topics"
    return "general"


async def extract_and_store_preferences(
    memory: MemoryClient,
    message: str,
    session_id: str,
) -> None:
    """Detect preferences in a user message and store them in long-term memory.

    Uses the library's :class:`PreferenceDetector` (regex patterns, no LLM call)
    so only the matched clause is persisted -- not the whole user message.
    """
    detected = _preference_detector.detect(message)
    if not detected:
        return

    for pref in detected:
        category = categorize_preference(pref.source_text)
        if category not in PREFERENCE_CATEGORIES:  # pragma: no cover - defensive
            category = "general"
        try:
            await memory.long_term.add_preference(
                category=category,
                preference=pref.preference,
                context=pref.source_text,
                confidence=pref.confidence,
                metadata={
                    "source": "chat",
                    "session_id": session_id,
                    "sentiment": pref.sentiment,
                    "detector": "PreferenceDetector",
                },
            )
            logger.info("Stored preference: [%s] %s", category, pref.preference[:60])
        except Exception:
            logger.exception("Failed to store preference")


async def stream_chat_response(
    request: ChatRequest,
    memory: MemoryClient | None,
) -> AsyncGenerator[dict, None]:
    """Stream chat response as SSE events with full reasoning memory tracking."""
    message_id = str(uuid.uuid4())
    trace_id: UUID | None = None
    current_step_id: UUID | None = None
    user_message_id: UUID | None = None
    memory_enabled = request.memory_enabled and memory is not None
    task_success = True
    error_message: str | None = None
    full_response = ""

    # Track tool call timings (tool_call_id -> start_time)
    tool_call_start_times: dict[str, float] = {}
    # Arguments per tool_call_id, so a turn with several tool calls records the
    # right arguments for each one (a shared `args` variable records the last).
    args_by_call_id: dict[str, dict[str, Any]] = {}
    # Entities the turn touched, collected from tool results for the audit edges.
    touched: dict[tuple[str, str], EntityRef] = {}

    try:
        # Get conversation history before adding the new message
        message_history: list[ModelRequest | ModelResponse] = []
        if memory_enabled and memory:
            message_history = await get_conversation_history(
                memory=memory,
                session_id=request.thread_id,
                limit=20,  # Last 20 messages for context
            )
            logger.info(f"Loaded {len(message_history)} messages from conversation history")

        # Create agent dependencies with current query for similar trace lookup
        deps = AgentDeps.create(
            memory=memory,
            session_id=request.thread_id,
            memory_enabled=memory_enabled,
            current_query=request.message,
        )

        # Store user message in short-term memory
        if memory_enabled and memory:
            user_message = await memory.short_term.add_message(
                session_id=request.thread_id,
                role=MessageRole.USER,
                content=request.message,
            )
            user_message_id = user_message.id

            # Extract and store any preferences from the user message
            await extract_and_store_preferences(
                memory=memory,
                message=request.message,
                session_id=request.thread_id,
            )

        # Start reasoning trace if memory enabled
        if memory_enabled and memory:
            trace = await memory.reasoning.start_trace(
                session_id=request.thread_id,
                task=request.message,
                metadata={"message_id": message_id},
                # Creates (:ReasoningTrace)-[:INITIATED_BY]->(:Message)
                triggered_by_message_id=user_message_id,
            )
            trace_id = trace.id
            logger.info(f"Started reasoning trace: {trace_id}")

        # Run agent with streaming and conversation history
        agent = get_podcast_agent()

        async with agent.run_stream(
            request.message,
            deps=deps,
            message_history=message_history if message_history else None,
        ) as result:
            # Stream text tokens
            async for text in result.stream_text(delta=True):
                full_response += text
                yield {"data": json.dumps({"type": "token", "content": text})}

            # Process messages for tool calls and record to reasoning memory
            step_number = 0
            for msg in result.all_messages():
                if isinstance(msg, ModelResponse):
                    for part in msg.parts:
                        if isinstance(part, ToolCallPart):
                            # Get args safely using args_as_dict() method
                            args = {}
                            try:
                                args = safe_serialize(part.args_as_dict())
                            except Exception:
                                # Fallback: try direct access if args is already a dict
                                if isinstance(part.args, dict):
                                    args = safe_serialize(part.args)
                                elif isinstance(part.args, str):
                                    try:
                                        args = json.loads(part.args)
                                    except json.JSONDecodeError:
                                        args = {"raw": part.args}

                            tool_call_id = part.tool_call_id or str(uuid.uuid4())
                            args_by_call_id[tool_call_id] = args

                            # Record start time for duration calculation
                            tool_call_start_times[tool_call_id] = time.time()

                            # Create a reasoning step for this tool call if memory enabled
                            if trace_id and memory:
                                step_number += 1
                                step = await memory.reasoning.add_step(
                                    trace_id,
                                    thought=f"Need to use {part.tool_name} tool",
                                    action=f"Calling {part.tool_name} with args: {json.dumps(args)[:200]}",
                                    generate_embedding=False,  # Skip embedding for performance
                                    metadata={
                                        "tool_call_id": tool_call_id,
                                        "tool_name": part.tool_name,
                                    },
                                )
                                current_step_id = step.id
                                logger.debug(
                                    f"Created reasoning step {step_number} for tool {part.tool_name}"
                                )

                            # Emit tool call event
                            event = {
                                "type": "tool_call",
                                "id": tool_call_id,
                                "name": part.tool_name,
                                "args": args,
                            }
                            yield {"data": json.dumps(event)}

                if isinstance(msg, ModelRequest):
                    for part in msg.parts:
                        if isinstance(part, ToolReturnPart):
                            tool_call_id = part.tool_call_id or ""

                            # Calculate duration
                            duration_ms = 0
                            if tool_call_id in tool_call_start_times:
                                duration_ms = int(
                                    (time.time() - tool_call_start_times[tool_call_id]) * 1000
                                )
                                del tool_call_start_times[tool_call_id]

                            # Determine status based on result
                            result_content = safe_serialize(part.content)
                            is_error = False
                            if isinstance(result_content, list) and result_content:
                                # Check if result contains error
                                if (
                                    isinstance(result_content[0], dict)
                                    and "error" in result_content[0]
                                ):
                                    is_error = True

                            # Record tool call to reasoning memory
                            if current_step_id and memory:
                                call_entities = extract_touched_entities(result_content)
                                for ref in call_entities:
                                    touched[(ref.name or "", ref.type or "")] = ref
                                try:
                                    await memory.reasoning.record_tool_call(
                                        step_id=current_step_id,
                                        tool_name=part.tool_name,
                                        arguments=args_by_call_id.get(tool_call_id, {}),
                                        result=result_content,
                                        status=ToolCallStatus.ERROR
                                        if is_error
                                        else ToolCallStatus.SUCCESS,
                                        duration_ms=duration_ms,
                                        error=str(result_content[0].get("error"))
                                        if is_error and isinstance(result_content, list)
                                        else None,
                                        # (:ToolCall)-[:TRIGGERED_BY]->(:Message)
                                        message_id=user_message_id,
                                        # (:ReasoningStep)-[:TOUCHED]->(:Entity)
                                        touched_entities=call_entities or None,
                                    )
                                    logger.debug(
                                        f"Recorded tool call {part.tool_name} "
                                        f"(duration: {duration_ms}ms, status: {'error' if is_error else 'success'})"
                                    )
                                except Exception as e:
                                    logger.warning(f"Failed to record tool call: {e}")

                            # Emit tool result event
                            event = {
                                "type": "tool_result",
                                "id": tool_call_id,
                                "name": part.tool_name,
                                "result": result_content,
                                "duration_ms": duration_ms,
                            }
                            yield {"data": json.dumps(event)}

        # Store assistant response in short-term memory
        if memory_enabled and memory:
            await memory.short_term.add_message(
                session_id=request.thread_id,
                role=MessageRole.ASSISTANT,
                content=full_response,
            )

    except Exception as e:
        logger.exception("Error in chat stream")
        task_success = False
        error_message = str(e)
        event = {"type": "error", "message": str(e)}
        yield {"data": json.dumps(event)}

    finally:
        # Complete reasoning trace with a structured outcome: the error kind is
        # indexed for filtering, related entities make the trace retrievable by
        # subject, and the metrics land on the ReasoningTrace node.
        if trace_id and memory:
            try:
                await memory.reasoning.complete_trace(
                    trace_id,
                    outcome=TraceOutcome(
                        success=task_success,
                        summary=full_response[:500] if task_success else f"Error: {error_message}",
                        error_kind=None if task_success else "agent_error",
                        related_entities=list(touched.values()),
                        metrics={
                            "tools_called": float(len(args_by_call_id)),
                            "response_chars": float(len(full_response)),
                        },
                    ),
                )
                logger.info(f"Completed reasoning trace: {trace_id} (success: {task_success})")
            except Exception as e:
                logger.warning(f"Failed to complete reasoning trace: {e}")

        # Send done event (only if not already sent error)
        if task_success:
            event = {
                "type": "done",
                "message_id": message_id,
                "trace_id": str(trace_id) if trace_id else None,
            }
            yield {"data": json.dumps(event)}


@router.post("/chat")
async def chat(
    request: ChatRequest,
) -> EventSourceResponse:
    """Handle chat with SSE streaming response.

    Request body:
    - thread_id: The conversation thread ID
    - message: The user's message
    - memory_enabled: Whether to use memory (default True)

    Response: Server-Sent Events stream with:
    - {"type": "token", "content": "..."}
    - {"type": "tool_call", "id": "...", "name": "...", "args": {...}}
    - {"type": "tool_result", "id": "...", "name": "...", "result": {...}}
    - {"type": "done", "message_id": "...", "trace_id": "..."}
    - {"type": "error", "message": "..."}
    """
    memory = get_memory_client()
    return EventSourceResponse(stream_chat_response(request, memory))
