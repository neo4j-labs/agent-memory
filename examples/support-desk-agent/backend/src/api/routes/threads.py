"""Threads: one ``(:Conversation)`` per session, seeded (``seed-*``) or chat (``chat-*``)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException

from neo4j_agent_memory import BoltMemoryClient
from src.api import MemoryClientDep
from src.api.routes.traces import tool_calls_by_trace
from src.api.schemas import CreateThreadRequest, Thread, ThreadMessage, ThreadSummary
from src.memory import iso

router = APIRouter()

SEED_PREFIX = "seed-"
CHAT_PREFIX = "chat-"
DEFAULT_TITLE = "New chat"

# The library has no title setter yet, so the title is one property write.
SET_CONVERSATION_TITLE = """
MATCH (c:Conversation {session_id: $session_id})
SET c.title = $title
RETURN c.session_id AS session_id
"""

CONVERSATION = """
MATCH (c:Conversation {session_id: $session_id})
RETURN c.session_id AS session_id, c.title AS title, c.updated_at AS updated_at
"""

#: The newest trace each message initiated: (ReasoningTrace)-[:INITIATED_BY]->(Message).
INITIATED_TRACES = """
MATCH (rt:ReasoningTrace)-[:INITIATED_BY]->(m:Message)
WHERE m.id IN $ids
RETURN m.id AS message_id, rt.id AS trace_id
ORDER BY rt.started_at
"""


#: Longest title derived from a first message.
TITLE_LENGTH = 60


def is_seeded(session_id: str) -> bool:
    return session_id.startswith(SEED_PREFIX)


def title_from(message: str) -> str:
    """A thread title from its first message: one line, cut at a word boundary."""
    text = " ".join(message.split())
    if not text:
        return DEFAULT_TITLE
    if len(text) <= TITLE_LENGTH:
        return text
    cut = text[: TITLE_LENGTH - 1]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip(" ,.;:-") + "…"


async def set_title(client: BoltMemoryClient, session_id: str, title: str) -> None:
    await client.graph.execute_write(
        SET_CONVERSATION_TITLE, {"session_id": session_id, "title": title}
    )


@router.get("/threads", response_model=list[ThreadSummary])
async def list_threads(client: MemoryClientDep) -> list[ThreadSummary]:
    """Every thread, newest activity first. Seeded threads are included and flagged."""
    sessions = await client.short_term.list_sessions(
        limit=200, order_by="updated_at", order_dir="desc"
    )
    return [
        ThreadSummary(
            id=session.session_id,
            title=session.title or DEFAULT_TITLE,
            message_count=session.message_count,
            updated_at=iso(session.updated_at or session.created_at),
            seeded=is_seeded(session.session_id),
        )
        for session in sessions
    ]


@router.post("/threads", response_model=ThreadSummary)
async def create_thread(client: MemoryClientDep, request: CreateThreadRequest) -> ThreadSummary:
    """Create an empty chat thread (``chat-<8 hex>``).

    Without a ``title`` the thread stays untitled, and the first chat turn
    titles it after the message (``chat._ensure_title``).
    """
    session_id = f"{CHAT_PREFIX}{uuid.uuid4().hex[:8]}"
    title = (request.title or "").strip()
    conversation = await client.short_term.create_conversation(session_id)
    if title:
        await set_title(client, session_id, title)
    return ThreadSummary(
        id=session_id,
        title=title or DEFAULT_TITLE,
        message_count=0,
        updated_at=iso(conversation.updated_at or conversation.created_at),
        seeded=False,
    )


@router.get("/threads/{thread_id}", response_model=Thread)
async def get_thread(client: MemoryClientDep, thread_id: str) -> Thread:
    """A thread's messages, oldest first. User messages carry the trace they initiated."""
    rows = await client.query.cypher(CONVERSATION, {"session_id": thread_id})
    if not rows:
        raise HTTPException(status_code=404, detail=f"Thread {thread_id!r} not found")
    conversation = await client.short_term.get_conversation(thread_id)
    ids = [str(message.id) for message in conversation.messages]
    traces = {
        row["message_id"]: row["trace_id"]
        for row in await client.query.cypher(INITIATED_TRACES, {"ids": ids})
    }
    tool_calls = await tool_calls_by_trace(client, sorted(set(traces.values())))
    return Thread(
        id=thread_id,
        title=rows[0].get("title") or DEFAULT_TITLE,
        seeded=is_seeded(thread_id),
        messages=[
            ThreadMessage(
                id=str(message.id),
                role=message.role.value,
                content=message.content,
                created_at=iso(message.created_at),
                trace_id=traces.get(str(message.id)),
                tool_calls=tool_calls.get(traces.get(str(message.id), ""), []),
            )
            for message in conversation.messages
            if message.role.value != "system"
        ],
    )
