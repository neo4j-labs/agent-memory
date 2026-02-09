"""Short-term memory endpoints: sessions and messages."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from neo4j_agent_memory import MemoryClient
from neo4j_agent_memory.server.dependencies import get_memory_client
from neo4j_agent_memory.server.models import (
    AddMessageRequest,
    ConversationResponse,
    ConversationSummaryResponse,
    MessageResponse,
    SearchMessagesRequest,
    SessionResponse,
)

router = APIRouter(tags=["short-term"])


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


@router.get("/sessions", response_model=list[SessionResponse])
async def list_sessions(
    prefix: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    order_by: str = Query(default="updated_at"),
    order_dir: str = Query(default="desc"),
    memory: MemoryClient = Depends(get_memory_client),
) -> list[SessionResponse]:
    """List conversation sessions."""
    sessions = await memory.short_term.list_sessions(
        prefix=prefix,
        limit=limit,
        offset=offset,
        order_by=order_by,
        order_dir=order_dir,
    )
    return [SessionResponse.from_domain(s) for s in sessions]


@router.get("/sessions/{session_id}", response_model=ConversationResponse)
async def get_session(
    session_id: str,
    limit: int | None = None,
    memory: MemoryClient = Depends(get_memory_client),
) -> ConversationResponse:
    """Get a conversation session with its messages."""
    conversation = await memory.short_term.get_conversation(session_id=session_id, limit=limit)
    return ConversationResponse.from_domain(conversation)


@router.delete("/sessions/{session_id}")
async def delete_session(
    session_id: str,
    memory: MemoryClient = Depends(get_memory_client),
) -> dict[str, str]:
    """Clear all data for a session."""
    await memory.short_term.clear_session(session_id)
    return {"status": "deleted", "session_id": session_id}


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------


@router.post("/sessions/{session_id}/messages", response_model=MessageResponse, status_code=201)
async def add_message(
    session_id: str,
    request: AddMessageRequest,
    memory: MemoryClient = Depends(get_memory_client),
) -> MessageResponse:
    """Add a message to a session."""
    conversation_id = UUID(request.conversation_id) if request.conversation_id else None
    message = await memory.short_term.add_message(
        session_id=session_id,
        role=request.role,
        content=request.content,
        conversation_id=conversation_id,
        extract_entities=request.extract_entities,
        generate_embedding=request.generate_embedding,
        metadata=request.metadata,
    )
    return MessageResponse.from_domain(message)


@router.get("/sessions/{session_id}/messages", response_model=list[MessageResponse])
async def get_messages(
    session_id: str,
    limit: int | None = None,
    memory: MemoryClient = Depends(get_memory_client),
) -> list[MessageResponse]:
    """Get messages for a session."""
    conversation = await memory.short_term.get_conversation(session_id=session_id, limit=limit)
    return [MessageResponse.from_domain(m) for m in conversation.messages]


@router.get("/sessions/{session_id}/summary", response_model=ConversationSummaryResponse)
async def get_session_summary(
    session_id: str,
    memory: MemoryClient = Depends(get_memory_client),
) -> ConversationSummaryResponse:
    """Get a summary of a conversation session."""
    summary = await memory.short_term.get_conversation_summary(session_id)
    return ConversationSummaryResponse.from_domain(summary)


@router.post("/messages/search", response_model=list[MessageResponse])
async def search_messages(
    request: SearchMessagesRequest,
    memory: MemoryClient = Depends(get_memory_client),
) -> list[MessageResponse]:
    """Search messages using semantic similarity."""
    messages = await memory.short_term.search_messages(
        query=request.query,
        session_id=request.session_id,
        limit=request.limit,
        threshold=request.threshold,
    )
    return [MessageResponse.from_domain(m) for m in messages]


@router.delete("/messages/{message_id}")
async def delete_message(
    message_id: str,
    cascade: bool = Query(default=True),
    memory: MemoryClient = Depends(get_memory_client),
) -> dict[str, str]:
    """Delete a specific message."""
    deleted = await memory.short_term.delete_message(message_id, cascade=cascade)
    if not deleted:
        raise HTTPException(status_code=404, detail="Message not found")
    return {"status": "deleted", "message_id": message_id}
