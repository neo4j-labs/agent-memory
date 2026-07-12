"""Bolt (self-hosted) backend fully implements the memory Protocols.

Type-safety Phase 5 made the concrete bolt ``ShortTermMemory`` /
``LongTermMemory`` conform to ``ShortTermProtocol`` / ``LongTermProtocol``
so ``MemoryClient.short_term``/``.long_term`` no longer need
``attr-defined``/``arg-type`` suppressions at call sites:

* Gold-tier conversation methods (``create_conversation``,
  ``list_conversations``, ``bulk_add_messages``) are real, graph-backed.
* Platinum-tier NAMS features (``get_observations``, ``get_reflections``
  on short-term; ``set_entity_feedback``, ``get_entity_history`` on
  long-term) raise :class:`NotSupportedError` on bolt.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from neo4j_agent_memory.core.exceptions import NotSupportedError
from neo4j_agent_memory.memory.long_term import LongTermMemory
from neo4j_agent_memory.memory.short_term import (
    Conversation,
    Message,
    MessageRole,
    ShortTermMemory,
)


class _ClientStub:
    """Minimal stand-in for Neo4jClient recording the queries it is given."""

    def __init__(self, read_result: list[dict[str, Any]] | None = None) -> None:
        self._read_result = read_result or []
        self.reads: list[tuple[str, dict[str, Any]]] = []
        self.writes: list[tuple[str, dict[str, Any]]] = []

    async def execute_read(self, query: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        self.reads.append((query, params))
        return self._read_result

    async def execute_write(self, query: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        self.writes.append((query, params))
        return []


# _ClientStub duck-types Neo4jClient (a concrete class, not a Protocol), so
# the constructor's `client: Neo4jClient` param needs an arg-type override;
# these helpers localize that single suppression instead of repeating it.
def _long_term(client: _ClientStub | None = None) -> LongTermMemory:
    return LongTermMemory(client or _ClientStub())  # type: ignore[arg-type]


def _short_term(client: _ClientStub | None = None) -> ShortTermMemory:
    return ShortTermMemory(client or _ClientStub(), embedder=None, extractor=None)  # type: ignore[arg-type]


# ── Platinum-tier NAMS boundary: bolt raises NotSupportedError ──────────


@pytest.mark.asyncio
async def test_long_term_set_entity_feedback_not_supported_on_bolt():
    lt = _long_term()
    with pytest.raises(NotSupportedError) as exc:
        await lt.set_entity_feedback(uuid4(), "positive")
    assert exc.value.backend == "bolt"
    assert "set_entity_feedback" in exc.value.method


@pytest.mark.asyncio
async def test_long_term_get_entity_history_not_supported_on_bolt():
    lt = _long_term()
    with pytest.raises(NotSupportedError) as exc:
        await lt.get_entity_history(uuid4())
    assert exc.value.backend == "bolt"
    assert "get_entity_history" in exc.value.method


@pytest.mark.asyncio
async def test_short_term_get_observations_not_supported_on_bolt():
    st = _short_term()
    with pytest.raises(NotSupportedError) as exc:
        await st.get_observations("session-1")
    assert exc.value.backend == "bolt"
    assert "get_observations" in exc.value.method


@pytest.mark.asyncio
async def test_short_term_get_reflections_not_supported_on_bolt():
    st = _short_term()
    with pytest.raises(NotSupportedError) as exc:
        await st.get_reflections("session-1")
    assert exc.value.backend == "bolt"
    assert "get_reflections" in exc.value.method


# ── Gold-tier conversation methods: real, graph-backed on bolt ──────────


@pytest.mark.asyncio
async def test_bulk_add_messages_delegates_to_batch(monkeypatch):
    st = _short_term()
    captured: dict[str, Any] = {}

    async def fake_batch(session_id: str, messages: list[dict[str, Any]], **kwargs: Any):
        captured["session_id"] = session_id
        captured["messages"] = messages
        return [Message(role=MessageRole.USER, content=messages[0]["content"])]

    monkeypatch.setattr(st, "add_messages_batch", fake_batch)

    out = await st.bulk_add_messages("s1", [{"role": "user", "content": "hi"}])
    assert captured["session_id"] == "s1"
    assert captured["messages"] == [{"role": "user", "content": "hi"}]
    assert len(out) == 1
    assert out[0].content == "hi"


@pytest.mark.asyncio
async def test_create_conversation_returns_conversation(monkeypatch):
    st = _short_term()
    conv_id = uuid4()

    async def fake_ensure(session_id: str, *args: Any, **kwargs: Any):
        return conv_id

    async def fake_get(session_id: str, *args: Any, **kwargs: Any) -> Conversation:
        return Conversation(id=conv_id, session_id=session_id)

    monkeypatch.setattr(st, "_ensure_conversation", fake_ensure)
    monkeypatch.setattr(st, "get_conversation", fake_get)

    conv = await st.create_conversation("s1")
    assert isinstance(conv, Conversation)
    assert conv.session_id == "s1"
    assert conv.id == conv_id


@pytest.mark.asyncio
async def test_list_conversations_returns_conversations():
    client = _ClientStub(
        read_result=[
            {"c": {"id": str(uuid4()), "session_id": "s1", "title": "First"}},
            {"c": {"id": str(uuid4()), "session_id": "s2", "title": None}},
        ]
    )
    st = _short_term(client)
    convs = await st.list_conversations(limit=10)
    assert len(convs) == 2
    assert all(isinstance(c, Conversation) for c in convs)
    assert {c.session_id for c in convs} == {"s1", "s2"}


@pytest.mark.asyncio
async def test_list_conversations_coerces_none_limit():
    # An explicit limit=None must not reach Cypher (Neo4j rejects LIMIT null).
    client = _ClientStub(read_result=[])
    st = _short_term(client)
    await st.list_conversations(limit=None)
    assert client.reads, "expected a read query"
    _, params = client.reads[-1]
    assert params["limit"] == 100
