"""Comprehensive integration tests for short-term memory."""

import asyncio
import random
from datetime import datetime

import pytest

from neo4j_agent_memory.memory.short_term import MessageRole


@pytest.mark.integration
class TestShortTermMemoryBasicOperations:
    """Test basic short-term memory operations."""

    @pytest.mark.asyncio
    async def test_add_single_message(self, memory_client, session_id):
        """Test adding a single message to a conversation."""
        msg = await memory_client.short_term.add_message(
            session_id,
            MessageRole.USER,
            "Hello, this is a test message",
            extract_entities=False,
            generate_embedding=False,
        )

        assert msg is not None
        assert msg.content == "Hello, this is a test message"
        assert msg.role == MessageRole.USER
        assert msg.id is not None
        assert msg.created_at is not None

    @pytest.mark.asyncio
    async def test_add_message_with_all_roles(self, memory_client, session_id):
        """Test adding messages with all supported roles."""
        roles = [MessageRole.USER, MessageRole.ASSISTANT, MessageRole.SYSTEM]

        for role in roles:
            msg = await memory_client.short_term.add_message(
                session_id,
                role,
                f"Test message with role {role.value}",
                extract_entities=False,
                generate_embedding=False,
            )
            assert msg.role == role

    @pytest.mark.asyncio
    async def test_get_conversation(self, memory_client, session_id):
        """Test retrieving a full conversation."""
        # Add multiple messages
        messages = [
            (MessageRole.USER, "Hello"),
            (MessageRole.ASSISTANT, "Hi there! How can I help?"),
            (MessageRole.USER, "What's the weather?"),
            (MessageRole.ASSISTANT, "I don't have access to weather data."),
        ]

        for role, content in messages:
            await memory_client.short_term.add_message(
                session_id,
                role,
                content,
                extract_entities=False,
                generate_embedding=False,
            )

        # Retrieve conversation
        conv = await memory_client.short_term.get_conversation(session_id)

        assert conv is not None
        assert conv.session_id == session_id
        assert len(conv.messages) == 4

        # Verify message order (should be chronological)
        for i, (role, content) in enumerate(messages):
            assert conv.messages[i].role == role
            assert conv.messages[i].content == content

    @pytest.mark.asyncio
    async def test_get_conversation_with_limit(self, memory_client, session_id):
        """Test retrieving conversation with message limit."""
        # Add many messages
        for i in range(10):
            await memory_client.short_term.add_message(
                session_id,
                MessageRole.USER if i % 2 == 0 else MessageRole.ASSISTANT,
                f"Message {i}",
                extract_entities=False,
                generate_embedding=False,
            )

        # Retrieve with limit
        conv = await memory_client.short_term.get_conversation(session_id, limit=5)

        assert len(conv.messages) == 5

    @pytest.mark.asyncio
    async def test_get_nonexistent_conversation(self, memory_client):
        """Test retrieving a conversation that doesn't exist."""
        conv = await memory_client.short_term.get_conversation("nonexistent-session-id")

        # Should return an empty conversation, not None
        assert conv is not None
        assert len(conv.messages) == 0

    @pytest.mark.asyncio
    async def test_conversation_isolation(self, memory_client):
        """Test that conversations are isolated by session_id."""
        session1 = f"test-session-1-{datetime.now().timestamp()}"
        session2 = f"test-session-2-{datetime.now().timestamp()}"

        # Add messages to session 1
        await memory_client.short_term.add_message(
            session1,
            MessageRole.USER,
            "Session 1 message",
            extract_entities=False,
            generate_embedding=False,
        )

        # Add messages to session 2
        await memory_client.short_term.add_message(
            session2,
            MessageRole.USER,
            "Session 2 message",
            extract_entities=False,
            generate_embedding=False,
        )

        # Verify isolation
        conv1 = await memory_client.short_term.get_conversation(session1)
        conv2 = await memory_client.short_term.get_conversation(session2)

        assert len(conv1.messages) == 1
        assert conv1.messages[0].content == "Session 1 message"

        assert len(conv2.messages) == 1
        assert conv2.messages[0].content == "Session 2 message"


@pytest.mark.integration
class TestShortTermMemorySearch:
    """Test short-term memory search functionality."""

    @pytest.mark.asyncio
    async def test_search_messages_basic(self, memory_client, session_id):
        """Test basic message search."""
        # Add messages with specific content
        await memory_client.short_term.add_message(
            session_id,
            MessageRole.USER,
            "I love Italian food",
            extract_entities=False,
            generate_embedding=True,
        )
        await memory_client.short_term.add_message(
            session_id,
            MessageRole.USER,
            "The weather is nice today",
            extract_entities=False,
            generate_embedding=True,
        )
        await memory_client.short_term.add_message(
            session_id,
            MessageRole.USER,
            "Pizza and pasta are my favorites",
            extract_entities=False,
            generate_embedding=True,
        )

        # Search for food-related messages
        results = await memory_client.short_term.search_messages(
            "Italian cuisine restaurants",
            limit=10,
        )

        # Should find food-related messages (exact results depend on embedding similarity)
        assert isinstance(results, list)

    @pytest.mark.asyncio
    async def test_search_messages_empty_results(self, memory_client, session_id):
        """Test search with no matching results."""
        # Add unrelated message
        await memory_client.short_term.add_message(
            session_id,
            MessageRole.USER,
            "Hello world",
            extract_entities=False,
            generate_embedding=True,
        )

        # Search for something very different
        results = await memory_client.short_term.search_messages(
            "quantum physics equations",
            limit=10,
        )

        # Should return empty or low-relevance results
        assert isinstance(results, list)


@pytest.mark.integration
class TestShortTermMemoryWithEmbeddings:
    """Test short-term memory with embedding generation."""

    @pytest.mark.asyncio
    async def test_add_message_with_embedding(self, memory_client, session_id):
        """Test adding a message with embedding generation."""
        msg = await memory_client.short_term.add_message(
            session_id,
            MessageRole.USER,
            "This message should have an embedding",
            extract_entities=False,
            generate_embedding=True,
        )

        assert msg is not None
        assert msg.embedding is not None
        assert len(msg.embedding) > 0

    @pytest.mark.asyncio
    async def test_semantic_search_with_embeddings(self, memory_client, session_id):
        """Test semantic search using embeddings."""
        # Add messages about different topics
        topics = [
            "I want to learn Python programming",
            "The best restaurants in New York serve amazing food",
            "Machine learning models require lots of data",
            "Traveling to Japan is on my bucket list",
            "Software engineering best practices include testing",
        ]

        for topic in topics:
            await memory_client.short_term.add_message(
                session_id,
                MessageRole.USER,
                topic,
                extract_entities=False,
                generate_embedding=True,
            )

        # Search for programming-related content
        results = await memory_client.short_term.search_messages(
            "coding and software development",
            limit=3,
        )

        # Results should exist
        assert isinstance(results, list)


@pytest.mark.integration
class TestShortTermMemoryWithExtraction:
    """Test short-term memory with entity extraction."""

    @pytest.mark.asyncio
    async def test_add_message_with_entity_extraction(self, memory_client, session_id):
        """Test adding a message with entity extraction enabled."""
        msg = await memory_client.short_term.add_message(
            session_id,
            MessageRole.USER,
            "My name is John and I work at Google",
            extract_entities=True,
            generate_embedding=False,
        )

        assert msg is not None
        # Entity extraction results depend on the extractor implementation

    @pytest.mark.asyncio
    async def test_conversation_with_mentioned_entities(self, memory_client, session_id):
        """Test conversation tracking mentioned entities."""
        await memory_client.short_term.add_message(
            session_id,
            MessageRole.USER,
            "Hi, I'm Alice from Microsoft",
            extract_entities=True,
            generate_embedding=False,
        )
        await memory_client.short_term.add_message(
            session_id,
            MessageRole.USER,
            "I met Bob yesterday at the conference",
            extract_entities=True,
            generate_embedding=False,
        )

        # Get conversation and verify it has messages
        conv = await memory_client.short_term.get_conversation(session_id)
        assert len(conv.messages) == 2

    @pytest.mark.asyncio
    async def test_repeated_entity_is_mentioned_by_every_message(self, memory_client, session_id):
        """A second message naming an existing entity still gets its MENTIONS edge.

        The entity MERGE matches the existing node and does not overwrite its id,
        so linking must use the id the MERGE returned.
        """
        text = "Quillonberg reviewed the launch plan"
        first = await memory_client.short_term.add_message(
            session_id, MessageRole.USER, text, extract_entities=True, generate_embedding=False
        )
        second = await memory_client.short_term.add_message(
            session_id, MessageRole.USER, text, extract_entities=True, generate_embedding=False
        )
        later_session = f"{session_id}-later"
        third = await memory_client.short_term.add_message(
            later_session, MessageRole.USER, text, extract_entities=False, generate_embedding=False
        )
        await memory_client.short_term.extract_entities_from_session(
            later_session, skip_existing=False
        )

        rows = await memory_client._client.execute_read(
            """
            MATCH (e:Entity {name: 'Quillonberg'})
            OPTIONAL MATCH (m:Message)-[:MENTIONS]->(e)
            RETURN count(DISTINCT e) AS entities, collect(DISTINCT m.id) AS message_ids
            """
        )
        assert rows[0]["entities"] == 1
        assert set(rows[0]["message_ids"]) == {str(first.id), str(second.id), str(third.id)}


@pytest.mark.integration
class TestShortTermMemoryEdgeCases:
    """Test edge cases and error handling."""

    @pytest.mark.asyncio
    async def test_empty_message_content(self, memory_client, session_id):
        """Test handling of empty message content."""
        msg = await memory_client.short_term.add_message(
            session_id,
            MessageRole.USER,
            "",
            extract_entities=False,
            generate_embedding=False,
        )

        assert msg.content == ""

    @pytest.mark.asyncio
    async def test_very_long_message(self, memory_client, session_id):
        """Test handling of very long messages."""
        long_content = "This is a test message. " * 1000  # ~24KB of text

        msg = await memory_client.short_term.add_message(
            session_id,
            MessageRole.USER,
            long_content,
            extract_entities=False,
            generate_embedding=False,
        )

        assert msg.content == long_content

    @pytest.mark.asyncio
    async def test_special_characters_in_message(self, memory_client, session_id):
        """Test handling of special characters."""
        special_content = "Hello! @#$%^&*() 你好 مرحبا 🎉 <script>alert('test')</script>"

        msg = await memory_client.short_term.add_message(
            session_id,
            MessageRole.USER,
            special_content,
            extract_entities=False,
            generate_embedding=False,
        )

        assert msg.content == special_content

    @pytest.mark.asyncio
    async def test_concurrent_message_additions(self, memory_client, session_id):
        """Concurrent appends to one conversation keep the chain a chain.

        These used to be staggered by a sleep, blamed on "UUID collisions".
        The real failure was that appends did not serialize: two of them read
        the same tail, both linked to it, and the conversation was left with
        two tails. The next append then matched both, ran its CREATE once per
        row and breached the uniqueness constraint on Message.id -- after
        which every further append to that conversation failed too.

        The first message is added on its own so the conversation already
        exists. `_ensure_conversation` is a separate read-then-create race
        that this test is not about: concurrent first writes to one session
        can still mint several conversations, and session_id is deliberately
        not unique, so that one needs a data-model decision rather than a
        query fix.
        """
        import asyncio

        async def add_message(index):
            return await memory_client.short_term.add_message(
                session_id,
                MessageRole.USER,
                f"Concurrent message {index}",
                extract_entities=False,
                generate_embedding=False,
            )

        first = await add_message(0)
        results = await asyncio.gather(*(add_message(i) for i in range(1, 6)))
        assert len(results) == 5
        assert len({str(m.id) for m in [first, *results]}) == 6

        conv = await memory_client.short_term.get_conversation(session_id)
        assert len(conv.messages) == 6

        # One head, one tail, one unbroken chain -- not a fork.
        shape = await memory_client._client.execute_read(
            """
            MATCH (c:Conversation {session_id: $session_id})
            OPTIONAL MATCH (c)-[:FIRST_MESSAGE]->(head:Message)
            WITH c, count(DISTINCT head) AS heads
            MATCH (c)-[:HAS_MESSAGE]->(m:Message)
            WHERE NOT (m)-[:NEXT_MESSAGE]->()
            RETURN heads, count(DISTINCT m) AS tails
            """,
            {"session_id": session_id},
        )
        assert shape[0]["heads"] == 1, "conversation has more than one FIRST_MESSAGE"
        assert shape[0]["tails"] == 1, "conversation chain forked into several tails"

    @pytest.mark.asyncio
    async def test_message_timestamps_are_ordered(self, memory_client, session_id):
        """Test that message timestamps are properly ordered."""
        import asyncio

        for i in range(5):
            await memory_client.short_term.add_message(
                session_id,
                MessageRole.USER,
                f"Message {i}",
                extract_entities=False,
                generate_embedding=False,
            )
            await asyncio.sleep(0.01)  # Small delay to ensure timestamp ordering

        conv = await memory_client.short_term.get_conversation(session_id)

        # Verify timestamps are in ascending order
        timestamps = [msg.created_at for msg in conv.messages]
        assert timestamps == sorted(timestamps)


CHAIN_SHAPE = """
MATCH (c:Conversation {session_id: $session_id})-[:HAS_MESSAGE]->(m:Message)
OPTIONAL MATCH (m)-[out:NEXT_MESSAGE]->()
WITH m, count(out) AS outgoing
RETURN count(CASE WHEN outgoing = 0 THEN 1 END) AS tails,
       max(outgoing) AS max_out,
       count(m) AS messages
"""

CHAIN_CONTENTS = """
MATCH (c:Conversation {session_id: $session_id})-[:FIRST_MESSAGE]->(first:Message)
MATCH path = (first)-[:NEXT_MESSAGE*0..]->(m:Message)
WITH m, length(path) AS pos
ORDER BY pos
RETURN collect(m.content) AS contents
"""


async def _chain_shape(memory_client, session_id):
    rows = await memory_client._client.execute_read(CHAIN_SHAPE, {"session_id": session_id})
    return rows[0]


@pytest.mark.integration
class TestForkedChainRecovery:
    """A conversation with several tails must not wedge every later append.

    Reproduces the end state of the concurrency bug directly, so the check does
    not depend on winning a race: an append used to fan out over every tail and
    CREATE its message once per row, breaching the uniqueness constraint on
    Message.id. Any conversation written before the fix can still be in this
    shape, so appending to one has to keep working.
    """

    async def _fork(self, memory_client, session_id):
        """Fork the chain the way two racing appends did.

        Both appends read "First" as the tail and each ran
        ``CREATE (last)-[:NEXT_MESSAGE]->(m)``, so "First" ends up with two
        outgoing NEXT_MESSAGE edges and the conversation with two tails.
        """
        first = await memory_client.short_term.add_message(
            session_id,
            MessageRole.USER,
            "First",
            extract_entities=False,
            generate_embedding=False,
        )
        await memory_client.short_term.add_message(
            session_id,
            MessageRole.USER,
            "Racer A",
            extract_entities=False,
            generate_embedding=False,
        )
        await memory_client._client.execute_write(
            """
            MATCH (c:Conversation {session_id: $session_id})-[:HAS_MESSAGE]->(first:Message {id: $first_id})
            CREATE (c)-[:HAS_MESSAGE]->(m:Message {
                id: $id, role: 'user', content: 'Racer B',
                timestamp: datetime(), metadata: '{}'
            })
            CREATE (first)-[:NEXT_MESSAGE]->(m)
            """,
            {
                "session_id": session_id,
                "first_id": str(first.id),
                "id": "00000000-0000-0000-0000-00000000f00d",
            },
        )
        shape = await _chain_shape(memory_client, session_id)
        assert (shape["tails"], shape["max_out"]) == (2, 2), "fixture did not fork the chain"
        return first

    @pytest.mark.asyncio
    async def test_append_to_a_forked_chain_creates_exactly_one_message(
        self, memory_client, session_id
    ):
        await self._fork(memory_client, session_id)

        appended = await memory_client.short_term.add_message(
            session_id,
            MessageRole.USER,
            "After the fork",
            extract_entities=False,
            generate_embedding=False,
        )

        stored = await memory_client._client.execute_read(
            "MATCH (m:Message {id: $id}) RETURN count(m) AS count",
            {"id": str(appended.id)},
        )
        assert stored[0]["count"] == 1, "the append duplicated its own message node"

        conv = await memory_client.short_term.get_conversation(session_id)
        assert len(conv.messages) == 4

    @pytest.mark.asyncio
    async def test_forked_chain_is_repairable_and_then_appendable(self, memory_client, session_id):
        await self._fork(memory_client, session_id)
        await memory_client.short_term.migrate_message_links()

        await memory_client.short_term.add_message(
            session_id,
            MessageRole.USER,
            "After the repair",
            extract_entities=False,
            generate_embedding=False,
        )

        shape = await _chain_shape(memory_client, session_id)
        assert shape["tails"] == 1, "migrate_message_links left more than one tail"
        assert shape["max_out"] == 1, "migrate_message_links left a branch in the chain"

        contents = await memory_client._client.execute_read(
            CHAIN_CONTENTS, {"session_id": session_id}
        )
        assert contents[0]["contents"] == ["First", "Racer A", "Racer B", "After the repair"]

    @pytest.mark.asyncio
    async def test_migrate_keeps_a_tied_timestamp_chain_in_chain_order(
        self, memory_client, session_id
    ):
        """A 0.6.0 batch shares one timestamp and is chained in input order.

        When that order disagrees with message-id order, ordering the tie by id
        used to add the reverse link and a second FIRST_MESSAGE: a cycle with
        no tail, so the next append found nothing to link to.
        """
        conv_id = await memory_client.short_term._ensure_conversation(session_id, None)
        await memory_client._client.execute_write(
            """
            MATCH (c:Conversation {id: $conv_id})
            WITH c, datetime() AS t
            CREATE (c)-[:HAS_MESSAGE]->(u:Message {
                id: $user_id, role: 'user', content: 'Question', timestamp: t, metadata: '{}'
            })
            CREATE (c)-[:HAS_MESSAGE]->(a:Message {
                id: $assistant_id, role: 'assistant', content: 'Answer', timestamp: t,
                metadata: '{}'
            })
            CREATE (c)-[:FIRST_MESSAGE]->(u)
            CREATE (u)-[:NEXT_MESSAGE]->(a)
            """,
            {
                "conv_id": str(conv_id),
                # Chain order is the reverse of id order.
                "user_id": f"b-{session_id}",
                "assistant_id": f"a-{session_id}",
            },
        )

        await memory_client.short_term.migrate_message_links()
        await memory_client.short_term.add_message(
            session_id,
            MessageRole.USER,
            "Follow-up",
            extract_entities=False,
            generate_embedding=False,
        )

        shape = await _chain_shape(memory_client, session_id)
        assert shape["tails"] == 1, "migrate_message_links left no single tail"
        assert shape["max_out"] == 1, "migrate_message_links left a branch in the chain"
        heads = await memory_client._client.execute_read(
            """
            MATCH (:Conversation {session_id: $session_id})-[f:FIRST_MESSAGE]->()
            RETURN count(f) AS heads
            """,
            {"session_id": session_id},
        )
        assert heads[0]["heads"] == 1, "migrate_message_links added a second FIRST_MESSAGE"

        contents = await memory_client._client.execute_read(
            CHAIN_CONTENTS, {"session_id": session_id}
        )
        assert contents[0]["contents"] == ["Question", "Answer", "Follow-up"]

    @pytest.mark.asyncio
    async def test_migrate_repairs_the_cycle_the_060_migrate_left(self, memory_client, session_id):
        """Two tied 0.6.0 batches, overlaid with the links the 0.6.0 migrate added.

        Each batch shares one timestamp and is chained in input order. The
        0.6.0 migrate then ordered each tie by id, which disagrees with input
        order, and merged that chain and a second FIRST_MESSAGE on top: every
        batch becomes a cycle with no tail. The number of paths through such a
        group grows exponentially, so ranking ties by path count never
        finished on 30 messages.
        """
        batch_size = 30
        ids = [f"{session_id}-{k:03d}" for k in range(2 * batch_size)]
        random.Random(0).shuffle(ids)
        batches = [ids[:batch_size], ids[batch_size:]]
        conv_id = await memory_client.short_term._ensure_conversation(session_id, None)
        await memory_client._client.execute_write(
            """
            MATCH (c:Conversation {id: $conv_id})
            UNWIND range(0, size($batches) - 1) AS b
            WITH c, b, datetime() + duration({seconds: b}) AS t
            UNWIND range(0, size($batches[b]) - 1) AS i
            CREATE (c)-[:HAS_MESSAGE]->(:Message {
                id: $batches[b][i], role: 'user', content: $batches[b][i],
                timestamp: t, metadata: '{}'
            })
            """,
            {"conv_id": str(conv_id), "batches": batches},
        )
        link_chain = """
            MATCH (c:Conversation {id: $conv_id})
            MATCH (first:Message {id: $order[0]})
            MERGE (c)-[:FIRST_MESSAGE]->(first)
            WITH $order AS order
            UNWIND range(0, size(order) - 2) AS i
            MATCH (a:Message {id: order[i]}), (b:Message {id: order[i + 1]})
            MERGE (a)-[:NEXT_MESSAGE]->(b)
        """
        # The batches as add_messages_batch chained them, in input order.
        await memory_client._client.execute_write(
            link_chain, {"conv_id": str(conv_id), "order": ids}
        )
        # The chain the 0.6.0 migrate merged on top, each tie in id order.
        id_order = [message_id for batch in batches for message_id in sorted(batch)]
        await memory_client._client.execute_write(
            link_chain, {"conv_id": str(conv_id), "order": id_order}
        )
        shape = await _chain_shape(memory_client, session_id)
        assert (shape["tails"], shape["max_out"]) == (0, 2), "fixture did not close the cycle"

        await asyncio.wait_for(memory_client.short_term.migrate_message_links(), timeout=60)

        shape = await _chain_shape(memory_client, session_id)
        assert shape["tails"] == 1, "migrate_message_links left no single tail"
        assert shape["max_out"] == 1, "migrate_message_links left a branch in the chain"
        heads = await memory_client._client.execute_read(
            """
            MATCH (:Conversation {session_id: $session_id})-[f:FIRST_MESSAGE]->()
            RETURN count(f) AS heads
            """,
            {"session_id": session_id},
        )
        assert heads[0]["heads"] == 1, "migrate_message_links left a second FIRST_MESSAGE"

        # No message in either tied batch is a chain start (each has a tied
        # predecessor), so no head reaches them and they fall back to id order.
        contents = await memory_client._client.execute_read(
            CHAIN_CONTENTS, {"session_id": session_id}
        )
        assert contents[0]["contents"] == id_order

    @pytest.mark.asyncio
    async def test_migrate_is_linear_in_messages_sharing_one_timestamp(
        self, clean_memory_client, session_id
    ):
        """Many unlinked messages across conversations, all on one timestamp.

        A database written by 0.6.0's add_messages_batch has whole batches on
        one timestamp. The head test used to be planned as a Message(timestamp)
        index seek across the whole database, so the migration was quadratic in
        the number of messages sharing a timestamp: 40 x 250 took ~30s.
        """
        conversations, per_conversation = 40, 250
        client = clean_memory_client
        await client._client.execute_write(
            """
            WITH datetime() AS t
            UNWIND range(0, $conversations - 1) AS ci
            CREATE (c:Conversation {
                id: $prefix + '-conv-' + toString(ci),
                session_id: $prefix + '-' + toString(ci),
                created_at: t, updated_at: t
            })
            WITH c, ci, t
            UNWIND range(0, $per_conversation - 1) AS mi
            CREATE (c)-[:HAS_MESSAGE]->(:Message {
                id: $prefix + '-' + toString(ci) + '-' + toString(1000 + mi),
                role: 'user', content: toString(mi), timestamp: t, metadata: '{}'
            })
            """,
            {
                "prefix": session_id,
                "conversations": conversations,
                "per_conversation": per_conversation,
            },
        )

        migrated = await asyncio.wait_for(client.short_term.migrate_message_links(), timeout=15)

        assert len(migrated) == conversations
        assert set(migrated.values()) == {per_conversation}
        for ci in (0, conversations - 1):
            shape = await _chain_shape(client, f"{session_id}-{ci}")
            assert (shape["tails"], shape["max_out"], shape["messages"]) == (
                1,
                1,
                per_conversation,
            )
            # Unlinked ties fall back to id order.
            contents = await client._client.execute_read(
                CHAIN_CONTENTS, {"session_id": f"{session_id}-{ci}"}
            )
            assert contents[0]["contents"] == [str(mi) for mi in range(per_conversation)]


@pytest.mark.integration
class TestBatchMessageOrdering:
    """add_messages_batch reads back in input order and never forks the chain."""

    @pytest.mark.asyncio
    async def test_batch_readback_preserves_input_order(self, memory_client, session_id):
        # One statement used to stamp every message with the same datetime(),
        # so readback order was arbitrary. Repeat to make a tie visible.
        for round_number in range(5):
            batch = [
                {"role": "user" if i % 2 == 0 else "assistant", "content": f"R{round_number}-{i}"}
                for i in range(6)
            ]
            stored = await memory_client.short_term.add_messages_batch(
                session_id, batch, generate_embeddings=False, extract_entities=False
            )
            conversation = await memory_client.short_term.get_conversation(session_id)
            tail = conversation.messages[-len(stored) :]
            assert [m.id for m in tail] == [m.id for m in stored]

        conversation = await memory_client.short_term.get_conversation(session_id)
        timestamps = [m.created_at for m in conversation.messages]
        assert len(set(timestamps)) == len(timestamps), "batch messages share a timestamp"
        assert timestamps == sorted(timestamps)

        contents = await memory_client._client.execute_read(
            CHAIN_CONTENTS, {"session_id": session_id}
        )
        assert contents[0]["contents"] == [m.content for m in conversation.messages]

    @pytest.mark.asyncio
    async def test_explicit_timestamps_are_kept(self, memory_client, session_id):
        batch = [
            {"role": "user", "content": "early", "timestamp": "2024-01-01T10:00:00Z"},
            {"role": "assistant", "content": "late", "timestamp": "2024-01-01T10:05:00Z"},
        ]
        await memory_client.short_term.add_messages_batch(
            session_id, batch, generate_embeddings=False, extract_entities=False
        )

        conversation = await memory_client.short_term.get_conversation(session_id)
        assert [m.content for m in conversation.messages] == ["early", "late"]
        assert conversation.messages[0].created_at.minute == 0
        assert conversation.messages[1].created_at.minute == 5

    @pytest.mark.asyncio
    async def test_concurrent_batch_and_single_appends_keep_one_chain(
        self, memory_client, session_id
    ):
        # The batch path used to read the tail in one transaction and link in
        # another, so an append landing in between forked the chain.
        await memory_client.short_term.add_message(
            session_id, MessageRole.USER, "seed", extract_entities=False, generate_embedding=False
        )
        writes = []
        for i in range(8):
            writes.append(
                memory_client.short_term.add_messages_batch(
                    session_id,
                    [{"role": "user", "content": f"batch {i}-{j}"} for j in range(3)],
                    generate_embeddings=False,
                    extract_entities=False,
                )
            )
            writes.append(
                memory_client.short_term.add_message(
                    session_id,
                    MessageRole.ASSISTANT,
                    f"single {i}",
                    extract_entities=False,
                    generate_embedding=False,
                )
            )
        await asyncio.gather(*writes)

        shape = await _chain_shape(memory_client, session_id)
        assert shape["messages"] == 1 + 8 * 3 + 8
        assert (shape["tails"], shape["max_out"]) == (1, 1)

        conversation = await memory_client.short_term.get_conversation(session_id)
        contents = await memory_client._client.execute_read(
            CHAIN_CONTENTS, {"session_id": session_id}
        )
        assert contents[0]["contents"] == [m.content for m in conversation.messages]


@pytest.mark.integration
class TestAutoExtractedEntitySearch:
    """Entities written by auto-extraction are embedded, so vector search finds them."""

    @pytest.mark.asyncio
    async def test_search_entities_finds_auto_extracted_entity(self, memory_client, session_id):
        await memory_client.short_term.add_message(
            session_id,
            MessageRole.USER,
            "Zanzibarova visited the observatory yesterday",
            extract_entities=True,
        )

        stored = await memory_client._client.execute_read(
            "MATCH (e:Entity {name: 'Zanzibarova'}) RETURN e.embedding IS NOT NULL AS embedded"
        )
        assert stored and all(row["embedded"] for row in stored)

        found = await memory_client.long_term.search_entities("Zanzibarova", limit=5)
        assert "Zanzibarova" in [entity.name for entity in found]

    @pytest.mark.asyncio
    async def test_embeddings_off_leaves_extracted_entity_unembedded(
        self, memory_client, session_id
    ):
        """generate_embedding=False keeps the embedder out of auto-extraction too."""
        await memory_client.short_term.add_message(
            session_id,
            MessageRole.USER,
            "Zanzibarova visited the observatory yesterday",
            extract_entities=True,
            generate_embedding=False,
        )

        stored = await memory_client._client.execute_read(
            "MATCH (e:Entity {name: 'Zanzibarova'}) RETURN e.embedding IS NULL AS unembedded"
        )
        assert stored and all(row["unembedded"] for row in stored)


@pytest.mark.integration
class TestMessageLinking:
    """Test NEXT_MESSAGE and FIRST_MESSAGE relationships."""

    @pytest.mark.asyncio
    async def test_first_message_creates_first_message_rel(self, memory_client, session_id):
        """First message should create FIRST_MESSAGE relationship."""
        msg = await memory_client.short_term.add_message(
            session_id,
            MessageRole.USER,
            "First message",
            extract_entities=False,
            generate_embedding=False,
        )

        # Verify FIRST_MESSAGE relationship exists
        result = await memory_client._client.execute_read(
            """
            MATCH (c:Conversation {session_id: $session_id})-[:FIRST_MESSAGE]->(m:Message)
            RETURN m.id AS message_id
            """,
            {"session_id": session_id},
        )
        assert len(result) == 1
        assert result[0]["message_id"] == str(msg.id)

    @pytest.mark.asyncio
    async def test_sequential_messages_create_next_message_chain(self, memory_client, session_id):
        """Sequential messages should be linked with NEXT_MESSAGE."""
        msgs = []
        for i in range(5):
            msg = await memory_client.short_term.add_message(
                session_id,
                MessageRole.USER,
                f"Message {i}",
                extract_entities=False,
                generate_embedding=False,
            )
            msgs.append(msg)

        # Verify chain: each message links to the next
        for i in range(len(msgs) - 1):
            result = await memory_client._client.execute_read(
                """
                MATCH (m1:Message {id: $id1})-[:NEXT_MESSAGE]->(m2:Message {id: $id2})
                RETURN count(*) AS count
                """,
                {"id1": str(msgs[i].id), "id2": str(msgs[i + 1].id)},
            )
            assert result[0]["count"] == 1, f"Missing NEXT_MESSAGE from msg {i} to msg {i + 1}"

    @pytest.mark.asyncio
    async def test_last_message_has_no_next(self, memory_client, session_id):
        """Last message should have no outgoing NEXT_MESSAGE relationship."""
        for i in range(3):
            await memory_client.short_term.add_message(
                session_id,
                MessageRole.USER,
                f"Message {i}",
                extract_entities=False,
                generate_embedding=False,
            )

        # Find messages with no outgoing NEXT_MESSAGE (should be exactly 1)
        result = await memory_client._client.execute_read(
            """
            MATCH (c:Conversation {session_id: $session_id})-[:HAS_MESSAGE]->(m:Message)
            WHERE NOT (m)-[:NEXT_MESSAGE]->()
            RETURN count(m) AS count
            """,
            {"session_id": session_id},
        )
        assert result[0]["count"] == 1

    @pytest.mark.asyncio
    async def test_batch_messages_maintain_order(self, memory_client, session_id):
        """Batch-added messages should maintain NEXT_MESSAGE order."""
        messages = [{"role": "user", "content": f"Batch message {i}"} for i in range(10)]

        created = await memory_client.short_term.add_messages_batch(
            session_id,
            messages,
            generate_embeddings=False,
            extract_entities=False,
        )

        # Verify chain traversal matches creation order
        result = await memory_client._client.execute_read(
            """
            MATCH (c:Conversation {session_id: $session_id})-[:FIRST_MESSAGE]->(first:Message)
            MATCH path = (first)-[:NEXT_MESSAGE*0..]->(m:Message)
            WITH m, length(path) AS pos
            ORDER BY pos
            RETURN collect(m.content) AS contents
            """,
            {"session_id": session_id},
        )

        contents = result[0]["contents"]
        assert len(contents) == 10
        for i, content in enumerate(contents):
            assert content == f"Batch message {i}"

    @pytest.mark.asyncio
    async def test_mixed_single_and_batch_linking(self, memory_client, session_id):
        """Single and batch messages should link correctly."""
        # Add single message
        msg1 = await memory_client.short_term.add_message(
            session_id,
            MessageRole.USER,
            "Single 1",
            extract_entities=False,
            generate_embedding=False,
        )

        # Add batch
        batch = [{"role": "user", "content": f"Batch {i}"} for i in range(3)]
        await memory_client.short_term.add_messages_batch(
            session_id,
            batch,
            generate_embeddings=False,
            extract_entities=False,
        )

        # Add another single
        msg2 = await memory_client.short_term.add_message(
            session_id,
            MessageRole.USER,
            "Single 2",
            extract_entities=False,
            generate_embedding=False,
        )

        # Verify complete chain: Single1 -> Batch0 -> Batch1 -> Batch2 -> Single2
        result = await memory_client._client.execute_read(
            """
            MATCH (c:Conversation {session_id: $session_id})-[:FIRST_MESSAGE]->(first:Message)
            MATCH path = (first)-[:NEXT_MESSAGE*0..]->(last:Message)
            WHERE NOT (last)-[:NEXT_MESSAGE]->()
            RETURN length(path) + 1 AS chain_length
            """,
            {"session_id": session_id},
        )
        assert result[0]["chain_length"] == 5

    @pytest.mark.asyncio
    async def test_chain_traversal_matches_timestamp_order(self, memory_client, session_id):
        """NEXT_MESSAGE chain should match timestamp-based ordering."""
        for i in range(5):
            await memory_client.short_term.add_message(
                session_id,
                MessageRole.USER,
                f"Message {i}",
                extract_entities=False,
                generate_embedding=False,
            )

        # Get messages via timestamp ordering
        conv = await memory_client.short_term.get_conversation(session_id)
        timestamp_order = [msg.content for msg in conv.messages]

        # Get messages via chain traversal
        result = await memory_client._client.execute_read(
            """
            MATCH (c:Conversation {session_id: $session_id})-[:FIRST_MESSAGE]->(first:Message)
            MATCH path = (first)-[:NEXT_MESSAGE*0..]->(m:Message)
            WITH m, length(path) AS pos
            ORDER BY pos
            RETURN collect(m.content) AS contents
            """,
            {"session_id": session_id},
        )
        chain_order = result[0]["contents"]

        assert timestamp_order == chain_order

    @pytest.mark.asyncio
    async def test_migrate_message_links(self, clean_memory_client, session_id):
        """Migration should create links for pre-existing messages."""
        # Simulate old data by creating messages without links
        # First create the conversation
        conv_id = await clean_memory_client.short_term._ensure_conversation(session_id, None)

        # Insert messages directly without NEXT_MESSAGE links (simulating old behavior)
        for i in range(3):
            await clean_memory_client._client.execute_write(
                """
                MATCH (c:Conversation {id: $conv_id})
                CREATE (m:Message {id: $id, role: 'user', content: $content, timestamp: datetime()})
                CREATE (c)-[:HAS_MESSAGE]->(m)
                """,
                {
                    "conv_id": str(conv_id),
                    "id": f"old-msg-{i}-{session_id}",
                    "content": f"Old message {i}",
                },
            )

        # Verify no FIRST_MESSAGE exists yet
        result = await clean_memory_client._client.execute_read(
            """
            MATCH (c:Conversation {session_id: $session_id})-[:FIRST_MESSAGE]->()
            RETURN count(*) AS count
            """,
            {"session_id": session_id},
        )
        assert result[0]["count"] == 0

        # Run migration
        migrated = await clean_memory_client.short_term.migrate_message_links()

        # Verify links were created for this conversation
        result = await clean_memory_client._client.execute_read(
            """
            MATCH (c:Conversation {session_id: $session_id})-[:FIRST_MESSAGE]->()
            RETURN count(*) AS has_first
            """,
            {"session_id": session_id},
        )
        assert result[0]["has_first"] == 1

        # Verify NEXT_MESSAGE chain
        result = await clean_memory_client._client.execute_read(
            """
            MATCH (c:Conversation {session_id: $session_id})-[:HAS_MESSAGE]->(m:Message)
            WHERE NOT (m)-[:NEXT_MESSAGE]->()
            RETURN count(m) AS last_count
            """,
            {"session_id": session_id},
        )
        assert result[0]["last_count"] == 1  # Only one message should be the last
