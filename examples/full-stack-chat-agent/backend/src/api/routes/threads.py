"""Thread management API endpoints.

A "thread" is a neo4j-agent-memory *session*: one ``(:Conversation)`` node and
its ``(:Message)`` chain. The library's session APIs do the work —
``create_conversation`` / ``get_conversation`` / ``list_sessions`` /
``clear_session`` — with one raw write for the title, which the library does
not yet expose (see the TODO on :func:`set_conversation_title`).
"""

import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from neo4j_agent_memory import MemoryClient
from src.api.schemas import (
    ChatMessage,
    CreateThreadRequest,
    Thread,
    ThreadSummary,
)
from src.memory.client import get_memory_client

router = APIRouter()
logger = logging.getLogger(__name__)

DEFAULT_TITLE = "New Conversation"

# Title lives on the Conversation node so `list_sessions()` can read it back.
# TODO(neo4j-agent-memory): replace with short_term.set_conversation_title()
# once the library ships it — tracked as a follow-up from the examples review.
SET_CONVERSATION_TITLE = """
MATCH (c:Conversation {session_id: $session_id})
SET c.title = $title, c.updated_at = datetime()
RETURN c.session_id AS session_id
"""


async def set_conversation_title(memory: MemoryClient, session_id: str, title: str) -> bool:
    """Persist a thread title onto its Conversation node."""
    rows = await memory.graph.execute_write(
        SET_CONVERSATION_TITLE,
        {"session_id": session_id, "title": title},
    )
    return bool(rows)


async def thread_exists(memory: MemoryClient, session_id: str) -> bool:
    """Whether a Conversation node exists for ``session_id``."""
    sessions = await memory.short_term.list_sessions(prefix=session_id, limit=5)
    return any(session.session_id == session_id for session in sessions)


# `clear_session()` only reaches traces linked with (:Conversation)-[:HAS_TRACE]->,
# which `start_trace(session_id=...)` does not create — so a deleted thread would
# leave its ReasoningTrace/ReasoningStep/ToolCall nodes behind.
# TODO(neo4j-agent-memory): have clear_session() also match traces by session_id.
DELETE_SESSION_TRACES = """
MATCH (rt:ReasoningTrace {session_id: $session_id})
OPTIONAL MATCH (rt)-[:HAS_STEP]->(rs:ReasoningStep)
OPTIONAL MATCH (rs)-[:USES_TOOL]->(tc:ToolCall)
DETACH DELETE rt, rs, tc
"""


@router.get("/threads", response_model=list[ThreadSummary])
async def list_threads() -> list[ThreadSummary]:
    """List all conversation threads using neo4j-agent-memory's list_sessions()."""
    memory = get_memory_client()
    if memory is None:
        return []

    try:
        sessions = await memory.short_term.list_sessions(
            limit=100,
            order_by="updated_at",
            order_dir="desc",
        )
    except Exception as e:
        logger.warning("Failed to list sessions: %s", e)
        return []

    return [
        ThreadSummary(
            id=session.session_id,
            title=session.title or DEFAULT_TITLE,
            created_at=session.created_at,
            updated_at=session.updated_at or session.created_at,
            message_count=session.message_count,
        )
        for session in sessions
    ]


@router.post("/threads", response_model=ThreadSummary)
async def create_thread(request: CreateThreadRequest) -> ThreadSummary:
    """Create a new conversation thread.

    Creates the Conversation node explicitly (no placeholder system message)
    and persists the title on it so it survives a page reload.
    """
    memory = get_memory_client()
    thread_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    title = request.title or DEFAULT_TITLE

    if memory is not None:
        try:
            conversation = await memory.short_term.create_conversation(thread_id)
            await set_conversation_title(memory, thread_id, title)
            now = conversation.created_at or now
        except Exception as e:
            logger.warning("Failed to create thread %s in memory: %s", thread_id, e)

    return ThreadSummary(
        id=thread_id,
        title=title,
        created_at=now,
        updated_at=now,
        message_count=0,
    )


@router.get("/threads/{thread_id}", response_model=Thread)
async def get_thread(thread_id: str) -> Thread:
    """Get a thread with its messages."""
    memory = get_memory_client()
    now = datetime.now(timezone.utc)

    if memory is None:
        raise HTTPException(status_code=503, detail="Memory service unavailable")

    # Let a real failure surface as a 500: only a genuinely missing thread is
    # a 404. (A broad `except` here used to turn every error into "not found",
    # which is how an AttributeError on Message.timestamp hid as a 404.)
    conversation = await memory.short_term.get_conversation(thread_id)

    # get_conversation() returns an empty Conversation for an unknown session,
    # which is indistinguishable from a real thread with no messages yet —
    # so confirm existence through the session listing.
    if not conversation.messages and not await thread_exists(memory, thread_id):
        raise HTTPException(status_code=404, detail="Thread not found")

    messages = [
        ChatMessage(
            id=str(msg.id),
            role=msg.role.value,
            content=msg.content,
            # Message inherits MemoryEntry: created_at / updated_at, no `timestamp`.
            timestamp=msg.created_at or now,
            tool_calls=[],
        )
        for msg in conversation.messages
        if msg.role.value != "system"
    ]

    created_at = conversation.created_at or now
    return Thread(
        id=thread_id,
        title=conversation.title or DEFAULT_TITLE,
        created_at=created_at,
        updated_at=conversation.updated_at or created_at,
        messages=messages,
    )


@router.delete("/threads/{thread_id}")
async def delete_thread(thread_id: str) -> dict[str, object]:
    """Delete a thread: its messages, the Conversation node, and its traces.

    ``clear_session()`` is one Cypher statement; the old per-message
    ``delete_message()`` loop left the Conversation node behind, so the thread
    kept showing up in ``list_sessions()``.
    """
    memory = get_memory_client()
    if memory is None:
        raise HTTPException(status_code=503, detail="Memory service unavailable")

    try:
        await memory.short_term.clear_session(thread_id)
        await memory.graph.execute_write(DELETE_SESSION_TRACES, {"session_id": thread_id})
    except Exception as e:
        logger.warning("Failed to delete thread %s: %s", thread_id, e)
        raise HTTPException(status_code=500, detail=str(e)) from e

    return {"status": "deleted", "thread_id": thread_id}


@router.patch("/threads/{thread_id}", response_model=ThreadSummary)
async def update_thread(thread_id: str, title: str) -> ThreadSummary:
    """Rename a thread. Persists the title and returns the stored values."""
    memory = get_memory_client()
    if memory is None:
        raise HTTPException(status_code=503, detail="Memory service unavailable")

    try:
        updated = await set_conversation_title(memory, thread_id, title)
    except Exception as e:
        logger.warning("Failed to rename thread %s: %s", thread_id, e)
        raise HTTPException(status_code=500, detail=str(e)) from e

    if not updated:
        raise HTTPException(status_code=404, detail="Thread not found")

    # Read back rather than synthesising created_at / message_count.
    sessions = [
        s
        for s in await memory.short_term.list_sessions(prefix=thread_id, limit=5)
        if s.session_id == thread_id
    ]
    if not sessions:
        raise HTTPException(status_code=404, detail="Thread not found")

    session = sessions[0]
    return ThreadSummary(
        id=session.session_id,
        title=session.title or title,
        created_at=session.created_at,
        updated_at=session.updated_at or session.created_at,
        message_count=session.message_count,
    )


@router.get("/threads/{thread_id}/summary")
async def get_thread_summary(thread_id: str) -> dict[str, object]:
    """Get a summary of the conversation thread.

    Uses get_conversation_summary() for AI-powered summarization (falls back to
    a basic summary when no LLM is configured).
    """
    memory = get_memory_client()

    if memory is None:
        raise HTTPException(status_code=503, detail="Memory service unavailable")

    try:
        summary = await memory.short_term.get_conversation_summary(
            session_id=thread_id,
            max_tokens=500,
            include_entities=True,
        )
    except Exception as e:
        logger.warning("Failed to get summary for %s: %s", thread_id, e)
        raise HTTPException(status_code=500, detail=str(e)) from e

    return {
        "session_id": summary.session_id,
        "summary": summary.summary,
        "message_count": summary.message_count,
        "time_range": (
            [summary.time_range[0].isoformat(), summary.time_range[1].isoformat()]
            if summary.time_range
            else None
        ),
        "key_entities": summary.key_entities,
        "key_topics": summary.key_topics,
        "generated_at": summary.generated_at.isoformat(),
    }
