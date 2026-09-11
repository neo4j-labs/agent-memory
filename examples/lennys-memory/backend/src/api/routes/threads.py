"""Thread management API endpoints.

Threads are stored where everything else in this demo is stored: as
``(:Conversation)`` nodes in the memory graph, under a ``chat-`` session-id
prefix so they can be told apart from the ``lenny-podcast-*`` sessions created
by ``scripts/load_transcripts.py``. That means threads survive a restart and
behave correctly behind more than one uvicorn worker -- neither of which was
true of the module-level dict this router used to keep.

Known gap (see the README "Known limitations"): threads are not scoped per
visitor. Every caller sees every chat thread. The library's multi-tenant mode
(``MemorySettings.memory.multi_tenant=True`` plus ``user_identifier=``) is the
fix; it needs a visitor identity the frontend does not yet send.
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

# Session-id prefix for user-created chat threads. Podcast transcripts use
# "lenny-podcast-<guest-slug>" and are data sources, not user threads.
THREAD_PREFIX = "chat-"

DEFAULT_TITLE = "New Conversation"


def _require_memory() -> MemoryClient:
    memory = get_memory_client()
    if memory is None:
        raise HTTPException(status_code=503, detail="Memory service unavailable")
    return memory


async def _load_thread(memory: MemoryClient, thread_id: str) -> dict | None:
    """Return stored thread metadata, or None when the conversation is absent."""
    rows = await memory.query.cypher(
        """
        MATCH (c:Conversation {session_id: $session_id})
        OPTIONAL MATCH (c)-[:HAS_MESSAGE]->(m:Message)
        RETURN c.title AS title,
               c.created_at AS created_at,
               c.updated_at AS updated_at,
               count(m) AS message_count
        """,
        {"session_id": thread_id},
    )
    if not rows:
        return None
    row = rows[0]
    created = row.get("created_at")
    updated = row.get("updated_at")
    return {
        "id": thread_id,
        "title": row.get("title") or DEFAULT_TITLE,
        "created_at": _to_datetime(created),
        "updated_at": _to_datetime(updated) or _to_datetime(created),
        "message_count": row.get("message_count") or 0,
    }


def _to_datetime(value: object) -> datetime | None:
    """Convert a neo4j DateTime (or datetime) to an aware Python datetime."""
    if value is None:
        return None
    to_native = getattr(value, "to_native", None)
    if callable(to_native):
        value = to_native()
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return None


@router.get("/threads", response_model=list[ThreadSummary])
async def list_threads(
    limit: int = 100,
    offset: int = 0,
) -> list[ThreadSummary]:
    """List user-created conversation threads.

    Backed by ``short_term.list_sessions(prefix="chat-")``, so podcast sessions
    are excluded without loading them.
    """
    memory = get_memory_client()
    if memory is None:
        return []

    sessions = await memory.short_term.list_sessions(
        prefix=THREAD_PREFIX,
        limit=limit,
        offset=offset,
        order_by="updated_at",
        order_dir="desc",
    )
    return [
        ThreadSummary(
            id=session.session_id,
            title=session.title or session.first_message_preview or DEFAULT_TITLE,
            created_at=session.created_at,
            updated_at=session.updated_at or session.created_at,
            message_count=session.message_count,
        )
        for session in sessions
    ]


@router.post("/threads", response_model=ThreadSummary)
async def create_thread(
    request: CreateThreadRequest,
) -> ThreadSummary:
    """Create a new conversation thread."""
    memory = _require_memory()

    thread_id = f"{THREAD_PREFIX}{uuid.uuid4()}"
    title = request.title or DEFAULT_TITLE

    conversation = await memory.short_term.create_conversation(thread_id)
    # ``create_conversation`` does not take a title; set it on the node so the
    # sidebar can show something other than the session id.
    await memory.graph.execute_write(
        "MATCH (c:Conversation {session_id: $session_id}) SET c.title = $title",
        {"session_id": thread_id, "title": title},
    )

    created = conversation.created_at or datetime.now(timezone.utc)
    return ThreadSummary(
        id=thread_id,
        title=title,
        created_at=created,
        updated_at=created,
        message_count=0,
    )


@router.get("/threads/{thread_id}", response_model=Thread)
async def get_thread(
    thread_id: str,
) -> Thread:
    """Get a thread with its messages.

    Works for chat threads and for loaded podcast sessions alike -- both are
    ``(:Conversation)`` nodes. Returns 404 when no such conversation exists.
    """
    memory = _require_memory()

    thread_data = await _load_thread(memory, thread_id)
    if thread_data is None:
        raise HTTPException(status_code=404, detail="Thread not found")

    conversation = await memory.short_term.get_conversation(thread_id)
    messages = [
        ChatMessage(
            id=str(msg.id),
            role=msg.role.value,
            content=msg.content,
            timestamp=msg.created_at,
            tool_calls=[],
        )
        for msg in conversation.messages
    ]

    return Thread(
        id=thread_id,
        title=thread_data["title"],
        created_at=thread_data["created_at"] or datetime.now(timezone.utc),
        updated_at=thread_data["updated_at"] or datetime.now(timezone.utc),
        messages=messages,
    )


@router.delete("/threads/{thread_id}")
async def delete_thread(
    thread_id: str,
) -> dict:
    """Delete a thread and its messages."""
    memory = _require_memory()

    if await _load_thread(memory, thread_id) is None:
        raise HTTPException(status_code=404, detail="Thread not found")

    await memory.short_term.clear_session(thread_id)
    return {"status": "deleted", "thread_id": thread_id}


@router.patch("/threads/{thread_id}")
async def update_thread(
    thread_id: str,
    title: str | None = None,
) -> ThreadSummary:
    """Update a thread's title."""
    memory = _require_memory()

    thread_data = await _load_thread(memory, thread_id)
    if thread_data is None:
        raise HTTPException(status_code=404, detail="Thread not found")

    if title is not None:
        await memory.graph.execute_write(
            "MATCH (c:Conversation {session_id: $session_id}) "
            "SET c.title = $title, c.updated_at = datetime()",
            {"session_id": thread_id, "title": title},
        )
        thread_data["title"] = title

    return ThreadSummary(
        id=thread_id,
        title=thread_data["title"],
        created_at=thread_data["created_at"] or datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        message_count=thread_data["message_count"],
    )
