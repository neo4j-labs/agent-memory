"""Thread management API endpoints."""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from src.api.schemas import (
    ChatMessage,
    CreateThreadRequest,
    Thread,
    ThreadSummary,
)
from src.memory.client import get_memory_client

router = APIRouter()

# In-memory thread storage (replace with database in production)
_threads: dict[str, dict] = {}


def _get_thread_or_404(thread_id: str) -> dict:
    """Get thread by ID or raise 404."""
    if thread_id not in _threads:
        raise HTTPException(status_code=404, detail="Thread not found")
    return _threads[thread_id]


def update_thread_activity(thread_id: str, increment_messages: int = 0) -> None:
    """Update thread's updated_at timestamp and optionally increment message count.

    Called by the chat endpoint when messages are added to a thread.

    Args:
        thread_id: The thread to update
        increment_messages: Number of messages to add to the count (default 0)
    """
    if thread_id in _threads:
        _threads[thread_id]["updated_at"] = datetime.now(timezone.utc)
        if increment_messages > 0:
            current_count = _threads[thread_id].get("message_count", 0)
            _threads[thread_id]["message_count"] = current_count + increment_messages


@router.get("/threads", response_model=list[ThreadSummary])
async def list_threads(
    limit: int = 100,
    offset: int = 0,
) -> list[ThreadSummary]:
    """List user-created conversation threads.

    Only returns threads created via the API (stored in-memory).
    Podcast transcripts stored in Neo4j are data sources, not user threads.
    This avoids the slow list_sessions() query that loads all podcast data.
    """
    summaries = []

    # Only return user-created threads (in-memory)
    # Podcast sessions in Neo4j are data, not user threads
    for thread_id, thread_data in _threads.items():
        summaries.append(
            ThreadSummary(
                id=thread_id,
                title=thread_data.get("title", "Untitled"),
                created_at=thread_data.get("created_at", datetime.now(timezone.utc)),
                updated_at=thread_data.get("updated_at", datetime.now(timezone.utc)),
                message_count=thread_data.get("message_count", 0),
            )
        )

    # Sort by updated_at descending
    summaries.sort(key=lambda x: x.updated_at, reverse=True)
    return summaries[offset : offset + limit]


@router.post("/threads", response_model=ThreadSummary)
async def create_thread(
    request: CreateThreadRequest,
) -> ThreadSummary:
    """Create a new conversation thread."""
    thread_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    thread_data = {
        "id": thread_id,
        "title": request.title or "New Conversation",
        "created_at": now,
        "updated_at": now,
    }

    _threads[thread_id] = thread_data

    return ThreadSummary(
        id=thread_id,
        title=thread_data["title"],
        created_at=now,
        updated_at=now,
        message_count=0,
    )


@router.get("/threads/{thread_id}", response_model=Thread)
async def get_thread(
    thread_id: str,
) -> Thread:
    """Get a thread with its messages.

    First checks in-memory storage, then falls back to Neo4j sessions
    (e.g., loaded podcast transcripts).
    """
    memory = get_memory_client()
    thread_data = _threads.get(thread_id)

    # If not in local storage, try to get from Neo4j
    if thread_data is None and memory:
        try:
            conversation = await memory.short_term.get_conversation(thread_id)
            if conversation:
                # Create thread_data from Neo4j session
                thread_data = {
                    "id": thread_id,
                    "title": conversation.title or thread_id,
                    "created_at": conversation.created_at or datetime.now(timezone.utc),
                    "updated_at": conversation.updated_at
                    or conversation.created_at
                    or datetime.now(timezone.utc),
                }
        except Exception:
            pass

    if thread_data is None:
        raise HTTPException(status_code=404, detail="Thread not found")

    # Get messages from short-term memory
    messages = []
    if memory:
        try:
            conversation = await memory.short_term.get_conversation(thread_id)
            if conversation and conversation.messages:
                for msg in conversation.messages:
                    messages.append(
                        ChatMessage(
                            id=str(msg.id),
                            role=msg.role.value,
                            content=msg.content,
                            timestamp=msg.created_at,
                            tool_calls=[],
                        )
                    )
        except Exception:
            pass

    return Thread(
        id=thread_id,
        title=thread_data.get("title", "Untitled"),
        created_at=thread_data.get("created_at", datetime.now(timezone.utc)),
        updated_at=thread_data.get("updated_at", datetime.now(timezone.utc)),
        messages=messages,
    )


@router.delete("/threads/{thread_id}")
async def delete_thread(
    thread_id: str,
) -> dict:
    """Delete a thread and its messages."""
    memory = get_memory_client()

    # Check if thread exists in local storage or Neo4j
    exists_locally = thread_id in _threads
    exists_in_neo4j = False

    if memory:
        try:
            conversation = await memory.short_term.get_conversation(thread_id)
            exists_in_neo4j = conversation is not None
        except Exception:
            pass

    if not exists_locally and not exists_in_neo4j:
        raise HTTPException(status_code=404, detail="Thread not found")

    # Delete from local storage if present
    if exists_locally:
        del _threads[thread_id]

    # Delete from Neo4j if present
    if exists_in_neo4j and memory:
        try:
            await memory.short_term.clear_session(thread_id)
        except Exception:
            pass

    return {"status": "deleted", "thread_id": thread_id}


@router.patch("/threads/{thread_id}")
async def update_thread(
    thread_id: str,
    title: str | None = None,
) -> ThreadSummary:
    """Update a thread's title."""
    memory = get_memory_client()
    thread_data = _threads.get(thread_id)

    # If not in local storage, try to get from Neo4j
    if thread_data is None and memory:
        try:
            conversation = await memory.short_term.get_conversation(thread_id)
            if conversation:
                # Create thread_data from Neo4j session and store locally
                thread_data = {
                    "id": thread_id,
                    "title": conversation.title or thread_id,
                    "created_at": conversation.created_at or datetime.now(timezone.utc),
                    "updated_at": conversation.updated_at
                    or conversation.created_at
                    or datetime.now(timezone.utc),
                }
                _threads[thread_id] = thread_data
        except Exception:
            pass

    if thread_data is None:
        raise HTTPException(status_code=404, detail="Thread not found")

    if title is not None:
        thread_data["title"] = title

    thread_data["updated_at"] = datetime.now(timezone.utc)

    return ThreadSummary(
        id=thread_id,
        title=thread_data["title"],
        created_at=thread_data["created_at"],
        updated_at=thread_data["updated_at"],
        message_count=0,
    )
