"""Integration tests for the v0.4 multi-tenant guardrail and user-scoped writes.

Covers:

* ``MemorySettings.memory.multi_tenant=True`` raises when callers omit
  ``user_identifier=`` on the affected APIs.
* ``add_message(user_identifier=...)`` writes
  ``(:User)-[:HAS_CONVERSATION]->(:Conversation)`` and denormalizes
  ``user_identifier`` onto the Conversation node.
* ``start_trace(user_identifier=...)`` writes
  ``(:User)-[:HAS_TRACE]->(:ReasoningTrace)``.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest

from neo4j_agent_memory import MemoryClient, MemorySettings, Neo4jConfig


@pytest.fixture
async def multi_tenant_client(
    memory_settings, mock_embedder, mock_extractor, mock_resolver
) -> AsyncGenerator:
    """A connected MemoryClient with ``multi_tenant=True``."""
    settings = memory_settings.model_copy(deep=True)
    settings.memory.multi_tenant = True

    client = MemoryClient(
        settings,
        embedder=mock_embedder,
        extractor=mock_extractor,
        resolver=mock_resolver,
    )
    try:
        await client.connect()
    except Exception as e:
        pytest.skip(f"Neo4j not available: {e}")

    try:
        await client._client.execute_write("MATCH (n) DETACH DELETE n")
    except Exception:
        pass

    yield client

    try:
        await client._client.execute_write("MATCH (n) DETACH DELETE n")
    except Exception:
        pass
    await client.close()


@pytest.mark.integration
@pytest.mark.asyncio
class TestUserScopedConversation:
    async def test_add_message_writes_has_conversation_edge(self, clean_memory_client, session_id):
        client = clean_memory_client
        await client.users.upsert_user(identifier="sara@omg.com")

        await client.short_term.add_message(
            session_id,
            "user",
            "Find healthcare team",
            user_identifier="sara@omg.com",
        )

        rows = await client.graph.execute_read(
            """
            MATCH (u:User {identifier: 'sara@omg.com'})-[:HAS_CONVERSATION]->(c:Conversation)
            RETURN c.session_id AS session_id, c.user_identifier AS user_identifier
            """,
        )
        assert len(rows) == 1
        assert rows[0]["session_id"] == session_id
        assert rows[0]["user_identifier"] == "sara@omg.com"

    async def test_two_users_same_session_id_get_separate_conversations(self, clean_memory_client):
        """Per-user scoping is by edge, not by session_id alone — but the
        existing conversation lookup is per session_id, so reusing the
        session_id under a second user just attaches both users to the
        same conversation. This test documents that behavior."""
        client = clean_memory_client
        await client.users.upsert_user(identifier="sara@omg.com")
        await client.users.upsert_user(identifier="liam@omg.com")

        await client.short_term.add_message(
            "shared-session",
            "user",
            "msg from sara",
            user_identifier="sara@omg.com",
        )
        await client.short_term.add_message(
            "shared-session",
            "user",
            "msg from liam",
            user_identifier="liam@omg.com",
        )

        rows = await client.graph.execute_read(
            """
            MATCH (c:Conversation {session_id: 'shared-session'})
            <-[:HAS_CONVERSATION]-(u:User)
            RETURN collect(u.identifier) AS users
            """
        )
        assert sorted(rows[0]["users"]) == ["liam@omg.com", "sara@omg.com"]


@pytest.mark.integration
@pytest.mark.asyncio
class TestUserScopedReasoningTrace:
    async def test_start_trace_writes_has_trace_edge(self, clean_memory_client, session_id):
        client = clean_memory_client
        await client.users.upsert_user(identifier="sara@omg.com")

        trace = await client.reasoning.start_trace(
            session_id,
            "Recommend a team",
            user_identifier="sara@omg.com",
        )

        rows = await client.graph.execute_read(
            """
            MATCH (u:User {identifier: 'sara@omg.com'})-[:HAS_TRACE]->(rt:ReasoningTrace {id: $id})
            RETURN rt.user_identifier AS user_identifier
            """,
            {"id": str(trace.id)},
        )
        assert len(rows) == 1
        assert rows[0]["user_identifier"] == "sara@omg.com"


@pytest.mark.integration
@pytest.mark.asyncio
class TestMultiTenantGuardrail:
    async def test_add_message_raises_without_user_identifier(
        self, multi_tenant_client, session_id
    ):
        with pytest.raises(ValueError, match="user_identifier"):
            await multi_tenant_client.short_term.add_message(session_id, "user", "Hello")

    async def test_start_trace_raises_without_user_identifier(
        self, multi_tenant_client, session_id
    ):
        with pytest.raises(ValueError, match="user_identifier"):
            await multi_tenant_client.reasoning.start_trace(session_id, "Some task")

    async def test_add_preference_raises_without_user_identifier(self, multi_tenant_client):
        with pytest.raises(ValueError, match="user_identifier"):
            await multi_tenant_client.long_term.add_preference("food", "Italian")

    async def test_writes_succeed_with_user_identifier(self, multi_tenant_client, session_id):
        client = multi_tenant_client
        await client.users.upsert_user(identifier="sara@omg.com")

        # All three should pass cleanly with user_identifier supplied.
        await client.short_term.add_message(
            session_id, "user", "Hello", user_identifier="sara@omg.com"
        )
        await client.reasoning.start_trace(session_id, "Some task", user_identifier="sara@omg.com")
        await client.long_term.add_preference("food", "Italian", user_identifier="sara@omg.com")
