"""Neo4jMemoryStore — construction, attributes, lifecycle."""

from __future__ import annotations

import pytest

pytest.importorskip("strands", reason="strands-agents not installed")

from tests.unit.integrations.strands_fakes import FakeMemoryClient


class TestConstruction:
    def test_requires_a_name(self) -> None:
        from neo4j_agent_memory.integrations.strands import Neo4jMemoryStore

        with pytest.raises(ValueError, match="name"):
            Neo4jMemoryStore(client=FakeMemoryClient())  # type: ignore[call-arg]

    def test_requires_exactly_one_of_client_or_settings(self) -> None:
        from neo4j_agent_memory import MemorySettings
        from neo4j_agent_memory.integrations.strands import Neo4jMemoryStore

        with pytest.raises(ValueError, match="exactly one"):
            Neo4jMemoryStore(name="s")
        with pytest.raises(ValueError, match="exactly one"):
            Neo4jMemoryStore(
                name="s",
                client=FakeMemoryClient(),
                settings=MemorySettings(neo4j={"password": "p"}),
            )

    def test_protocol_attribute_defaults(self) -> None:
        from neo4j_agent_memory.integrations.strands import Neo4jMemoryStore

        store = Neo4jMemoryStore(name="graph", client=FakeMemoryClient())

        assert store.name == "graph"
        assert store.writable is True
        assert store.extraction is False
        assert store.max_search_results is None
        assert store.description is not None and "graph" in store.description.lower()
        assert store.graph_tools is True

    def test_settings_construction_owns_the_client(self) -> None:
        """Settings-constructed stores build and own a real MemoryClient.

        MemoryClient.__init__ is lazy (no connection, no embedder until
        .connect()), so this is cheap and does not touch the network.
        """
        from neo4j_agent_memory import MemoryClient, MemorySettings
        from neo4j_agent_memory.integrations.strands import Neo4jMemoryStore

        store = Neo4jMemoryStore(name="graph", settings=MemorySettings(neo4j={"password": "p"}))

        assert store._owns_client is True
        assert isinstance(store._client, MemoryClient)

    def test_add_messages_is_not_declared_yet(self) -> None:
        """Scope guard: `add` landed in task 7, `add_messages` lands in task 8.

        `_has_method` compares `getattr(type(store), name)` against the Protocol's
        own stub by identity, so an undefined method reads as absent. `add` is now
        real, so `_has_write_sink` is already True; the both-sinks assertion lives
        in task 8.
        """
        from strands.memory.types import _has_method, _has_write_sink

        from neo4j_agent_memory.integrations.strands import Neo4jMemoryStore

        store = Neo4jMemoryStore(name="graph", client=FakeMemoryClient())

        assert _has_method(store, "initialize") is True
        assert _has_method(store, "add") is True
        assert _has_method(store, "add_messages") is False
        assert _has_write_sink(store) is True


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_initialize_connects_an_owned_client_only(self) -> None:
        from neo4j_agent_memory.integrations.strands import Neo4jMemoryStore

        client = FakeMemoryClient()
        store = Neo4jMemoryStore(name="graph", client=client)
        await store.initialize()

        assert client.connect_calls == 1
        await store.aclose()
        assert client.close_calls == 0  # borrowed client stays open

    @pytest.mark.asyncio
    async def test_initialize_is_idempotent(self) -> None:
        from neo4j_agent_memory.integrations.strands import Neo4jMemoryStore

        client = FakeMemoryClient()
        store = Neo4jMemoryStore(name="graph", client=client)
        await store.initialize()
        await store.initialize()

        assert client.connect_calls == 1

    @pytest.mark.asyncio
    async def test_context_manager_closes_an_owned_client(self) -> None:
        """Ownership must come from the constructor (settings=), not a monkey-patched flag."""
        from unittest.mock import AsyncMock

        from neo4j_agent_memory import MemorySettings
        from neo4j_agent_memory.integrations.strands import Neo4jMemoryStore

        store = Neo4jMemoryStore(name="graph", settings=MemorySettings(neo4j={"password": "p"}))
        store._client.connect = AsyncMock()  # type: ignore[method-assign]
        store._client.close = AsyncMock()  # type: ignore[method-assign]

        async with store:
            pass

        store._client.close.assert_awaited_once()


class TestForNams:
    def test_builds_nams_settings_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from neo4j_agent_memory.integrations.strands import Neo4jMemoryStore

        monkeypatch.setenv("MEMORY_API_KEY", "test-key")

        store = Neo4jMemoryStore.for_nams(name="graph")

        settings = store._client._settings
        assert settings.backend == "nams"
        assert settings.nams.validate_on_connect is False

    def test_raises_without_api_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from neo4j_agent_memory.integrations.strands import Neo4jMemoryStore

        monkeypatch.delenv("MEMORY_API_KEY", raising=False)

        with pytest.raises(ValueError, match="api_key is required"):
            Neo4jMemoryStore.for_nams(name="graph")


class TestSinkResolution:
    @pytest.mark.asyncio
    async def test_creates_a_deterministically_named_sink(self) -> None:
        """Bolt keys conversations by session_id; the deterministic name is the whole contract."""
        from neo4j_agent_memory.integrations.strands import Neo4jMemoryStore

        client = FakeMemoryClient()
        store = Neo4jMemoryStore(name="graph", client=client, user_id="alice")
        await store.initialize()
        key = await store._resolve_sink()

        assert key == "strands-memory-store/alice/graph"
        assert client.short_term.conversations == {}  # nothing minted
        assert client.short_term.list_conversations_calls == []

    @pytest.mark.asyncio
    async def test_reuses_an_existing_sink_across_instances(self) -> None:
        """Bolt needs no round-trip for reuse: same name, same key, every time."""
        from neo4j_agent_memory.integrations.strands import Neo4jMemoryStore

        client = FakeMemoryClient()
        first = Neo4jMemoryStore(name="graph", client=client)
        await first.initialize()
        key_one = await first._resolve_sink()

        second = Neo4jMemoryStore(name="graph", client=client)
        await second.initialize()
        key_two = await second._resolve_sink()

        assert key_one == key_two
        assert client.short_term.conversations == {}
        assert client.short_term.list_conversations_calls == []

    @pytest.mark.asyncio
    async def test_reuses_the_nams_server_minted_id_by_metadata(self) -> None:
        """NAMS mints conversation ids, so reuse matches on metadata, not id."""
        from neo4j_agent_memory.integrations.strands import Neo4jMemoryStore

        client = FakeMemoryClient(nams_mode=True)
        first = Neo4jMemoryStore(name="graph", client=client)
        await first.initialize()
        key_one = await first._resolve_sink()

        second = Neo4jMemoryStore(name="graph", client=client)
        await second.initialize()
        key_two = await second._resolve_sink()

        assert key_one == key_two
        assert key_one != "strands-memory-store/_/graph"  # the cached key is the minted uuid
        only_conv = next(iter(client.short_term.conversations.values()))
        assert key_one == str(only_conv.id)
        assert len(client.short_term.conversations) == 1

    @pytest.mark.asyncio
    async def test_nams_scans_conversations_once(self) -> None:
        """Symmetric to the bolt no-scan case: NAMS needs exactly one list round-trip."""
        from neo4j_agent_memory.integrations.strands import Neo4jMemoryStore

        client = FakeMemoryClient(nams_mode=True)
        store = Neo4jMemoryStore(name="graph", client=client)
        await store.initialize()
        await store._resolve_sink()

        assert len(client.short_term.list_conversations_calls) == 1

    @pytest.mark.asyncio
    async def test_explicit_conversation_id_is_used_verbatim(self) -> None:
        from neo4j_agent_memory.integrations.strands import Neo4jMemoryStore

        client = FakeMemoryClient()
        store = Neo4jMemoryStore(name="graph", client=client, conversation_id="chat-42")
        await store.initialize()

        assert await store._resolve_sink() == "chat-42"
        assert client.short_term.conversations == {}  # nothing minted

    @pytest.mark.asyncio
    async def test_two_stores_with_different_names_get_different_sinks(self) -> None:
        from neo4j_agent_memory.integrations.strands import Neo4jMemoryStore

        client = FakeMemoryClient()
        personal = Neo4jMemoryStore(name="personal", client=client)
        team = Neo4jMemoryStore(name="team", client=client)
        await personal.initialize()
        await team.initialize()

        assert await personal._resolve_sink() != await team._resolve_sink()

    @pytest.mark.asyncio
    async def test_bolt_does_not_scan_conversations(self) -> None:
        """On bolt the deterministic name is the key; a list scan would be wasted work."""
        from neo4j_agent_memory.integrations.strands import Neo4jMemoryStore

        client = FakeMemoryClient()
        store = Neo4jMemoryStore(name="graph", client=client)
        await store.initialize()
        await store._resolve_sink()

        assert client.short_term.list_conversations_calls == []


class TestSearch:
    @pytest.mark.asyncio
    async def test_returns_memory_entries_with_metadata(self) -> None:
        from neo4j_agent_memory.integrations.strands import Neo4jMemoryStore
        from neo4j_agent_memory.memory.long_term import Entity, Preference

        client = FakeMemoryClient()
        entity = Entity(name="Acme Corp", type="ORGANIZATION")
        entity.metadata["similarity"] = 0.9
        client.long_term.entities = [entity]
        client.long_term.preferences = [Preference(category="ui", preference="dark mode")]

        store = Neo4jMemoryStore(name="graph", client=client)
        await store.initialize()
        entries = await store.search("acme")

        assert [e.content for e in entries] == [
            "[entity] Acme Corp (ORGANIZATION)",
            "[preference] ui: dark mode",
        ]
        assert entries[0].metadata is not None
        assert entries[0].metadata["kind"] == "entity"
        assert entries[0].metadata["score"] == 0.9

    @pytest.mark.asyncio
    async def test_limit_precedence_call_then_store_then_default(self) -> None:
        from neo4j_agent_memory.integrations.strands import Neo4jMemoryStore

        client = FakeMemoryClient()
        store = Neo4jMemoryStore(
            name="graph", client=client, include_preferences=False, include_facts=False
        )
        await store.initialize()

        await store.search("q")
        assert client.long_term.search_kwargs[-1]["limit"] == 3  # protocol default

        store.max_search_results = 7
        await store.search("q")
        assert client.long_term.search_kwargs[-1]["limit"] == 7

        await store.search("q", {"max_search_results": 2})
        assert client.long_term.search_kwargs[-1]["limit"] == 2

        # Explicit 0 must stay 0, not fall through to a truthiness check.
        await store.search("q", {"max_search_results": 0})
        assert client.long_term.search_kwargs[-1]["limit"] == 0

    @pytest.mark.asyncio
    async def test_kind_flags_are_honoured(self) -> None:
        """All three kinds have data; only the enabled one should ever be searched."""
        from neo4j_agent_memory.integrations.strands import Neo4jMemoryStore
        from neo4j_agent_memory.memory.long_term import Entity, Fact, Preference

        client = FakeMemoryClient()
        client.long_term.entities = [Entity(name="Acme Corp", type="ORGANIZATION")]
        client.long_term.preferences = [Preference(category="ui", preference="dark mode")]
        client.long_term.facts = [Fact(subject="Acme", predicate="located_in", object="NYC")]

        store = Neo4jMemoryStore(
            name="graph",
            client=client,
            include_entities=False,
            include_preferences=True,
            include_facts=False,
        )
        await store.initialize()
        entries = await store.search("q")

        assert len(entries) == 1
        assert entries[0].metadata is not None
        assert entries[0].metadata["kind"] == "preference"
        # Proves entities/facts were never searched, not merely that they
        # returned nothing (a dropped include_* kwarg would sail through
        # on len(entries) == 1 alone).
        assert client.long_term.search_calls == 1

    @pytest.mark.asyncio
    async def test_nams_returns_entities_only(self) -> None:
        from neo4j_agent_memory.integrations.strands import Neo4jMemoryStore
        from neo4j_agent_memory.memory.long_term import Entity, Preference

        client = FakeMemoryClient(nams_mode=True)
        client.long_term.entities = [Entity(name="Acme Corp", type="ORGANIZATION")]
        client.long_term.preferences = [Preference(category="ui", preference="dark mode")]

        store = Neo4jMemoryStore(name="graph", client=client)
        await store.initialize()
        entries = await store.search("q")

        assert len(entries) == 1
        assert entries[0].metadata is not None
        assert entries[0].metadata["kind"] == "entity"

    @pytest.mark.asyncio
    async def test_search_does_not_mint_a_sink(self) -> None:
        """Reads are conversation-independent; only writes need the sink.

        Must use nams_mode=True: on bolt, _resolve_sink() makes no backend
        call at all (it just sets self._sink_key locally), so an accidental
        _resolve_sink() call from search() would be invisible there. NAMS
        genuinely calls list_conversations (and possibly create_conversation),
        so this is the only client mode that can catch that regression.
        """
        from neo4j_agent_memory.integrations.strands import Neo4jMemoryStore

        client = FakeMemoryClient(nams_mode=True)
        store = Neo4jMemoryStore(name="graph", client=client)
        await store.initialize()
        await store.search("q")

        assert client.short_term.list_conversations_calls == []
        assert client.short_term.conversations == {}

    @pytest.mark.asyncio
    async def test_search_initializes_without_a_prior_initialize_call(self) -> None:
        """Standalone use (no MemoryManager.init_agent) must still connect."""
        from neo4j_agent_memory.integrations.strands import Neo4jMemoryStore

        client = FakeMemoryClient()
        store = Neo4jMemoryStore(name="graph", client=client)
        await store.search("q")

        assert client.connect_calls == 1


class TestAdd:
    @pytest.mark.asyncio
    async def test_default_writes_a_message_into_the_sink_with_extraction(self) -> None:
        from neo4j_agent_memory.integrations.strands import Neo4jMemoryStore

        client = FakeMemoryClient()
        store = Neo4jMemoryStore(name="graph", client=client)
        await store.initialize()
        result = await store.add("The user prefers dark mode")

        call = client.short_term.add_message_calls[-1]
        assert call["content"] == "The user prefers dark mode"
        assert call["role"] == "user"
        assert call["extract_entities"] is True
        assert result["kind"] == "message"

    @pytest.mark.asyncio
    async def test_kind_preference_routes_to_add_preference(self) -> None:
        from neo4j_agent_memory.integrations.strands import Neo4jMemoryStore

        client = FakeMemoryClient()
        store = Neo4jMemoryStore(name="graph", client=client)
        await store.initialize()
        result = await store.add("dark mode", {"kind": "preference", "category": "ui"})

        assert client.long_term.added_preferences == [("ui", "dark mode")]
        assert result["kind"] == "preference"
        assert client.short_term.add_message_calls == []

    @pytest.mark.asyncio
    async def test_kind_preference_without_category_defaults_to_memory(self) -> None:
        from neo4j_agent_memory.integrations.strands import Neo4jMemoryStore

        client = FakeMemoryClient()
        store = Neo4jMemoryStore(name="graph", client=client)
        await store.initialize()
        await store.add("dark mode", {"kind": "preference"})

        assert client.long_term.added_preferences == [("memory", "dark mode")]

    @pytest.mark.asyncio
    async def test_kind_fact_requires_a_triple(self) -> None:
        from neo4j_agent_memory.integrations.strands import Neo4jMemoryStore

        client = FakeMemoryClient()
        store = Neo4jMemoryStore(name="graph", client=client)
        await store.initialize()

        await store.add(
            "Ada works at Acme",
            {"kind": "fact", "subject": "Ada", "predicate": "works_at", "object": "Acme"},
        )
        assert client.long_term.added_facts == [("Ada", "works_at", "Acme")]

        with pytest.raises(ValueError, match="subject.*predicate.*object"):
            await store.add("Ada works at Acme", {"kind": "fact"})

    @pytest.mark.asyncio
    async def test_kind_entity_routes_to_add_entity(self) -> None:
        from neo4j_agent_memory.integrations.strands import Neo4jMemoryStore

        client = FakeMemoryClient()
        store = Neo4jMemoryStore(name="graph", client=client)
        await store.initialize()
        await store.add("Acme Corp", {"kind": "entity", "type": "ORGANIZATION"})

        assert client.long_term.added_entities == [("Acme Corp", "ORGANIZATION")]
        assert client.short_term.add_message_calls == []

    @pytest.mark.asyncio
    async def test_kind_entity_defaults_name_and_type(self) -> None:
        from neo4j_agent_memory.integrations.strands import Neo4jMemoryStore

        client = FakeMemoryClient()
        store = Neo4jMemoryStore(name="graph", client=client)
        await store.initialize()
        await store.add("Acme Corp", {"kind": "entity"})

        assert client.long_term.added_entities == [("Acme Corp", "OBJECT")]

    @pytest.mark.asyncio
    async def test_kind_entity_on_nams_returns_bare_entity_id(self) -> None:
        """NAMS add_entity returns a bare Entity, not a (Entity, Dedup) tuple.

        Without the isinstance narrowing in `_add_typed`, this raises trying
        to subscript a bare Entity as a tuple.
        """
        from neo4j_agent_memory.integrations.strands import Neo4jMemoryStore

        client = FakeMemoryClient(nams_mode=True)
        store = Neo4jMemoryStore(name="graph", client=client)
        await store.initialize()
        result = await store.add("Acme Corp", {"kind": "entity", "type": "ORGANIZATION"})

        assert client.long_term.added_entities == [("Acme Corp", "ORGANIZATION")]
        assert result["kind"] == "entity"
        assert result["id"]

    @pytest.mark.asyncio
    async def test_unsupported_kind_on_nams_falls_back_to_the_sink(self, caplog) -> None:
        from neo4j_agent_memory.integrations.strands import Neo4jMemoryStore

        client = FakeMemoryClient(nams_mode=True)
        store = Neo4jMemoryStore(name="graph", client=client)
        await store.initialize()
        result = await store.add("dark mode", {"kind": "preference", "category": "ui"})

        assert result["kind"] == "message"
        assert client.short_term.add_message_calls[-1]["content"] == "dark mode"
        assert "falling back" in caplog.text.lower()

    @pytest.mark.asyncio
    async def test_unsupported_kind_warning_is_logged_once_per_store(self, caplog) -> None:
        from neo4j_agent_memory.integrations.strands import Neo4jMemoryStore

        client = FakeMemoryClient(nams_mode=True)
        store = Neo4jMemoryStore(name="graph", client=client)
        await store.initialize()

        with caplog.at_level("WARNING"):
            await store.add("dark mode", {"kind": "preference", "category": "ui"})
            await store.add("another one", {"kind": "preference", "category": "ui"})

        warnings = [r for r in caplog.records if "unsupported on this backend" in r.message]
        assert len(warnings) == 1
        assert len(client.short_term.add_message_calls) == 2

    @pytest.mark.asyncio
    async def test_rejects_writes_when_not_writable(self) -> None:
        from neo4j_agent_memory.integrations.strands import Neo4jMemoryStore

        store = Neo4jMemoryStore(name="graph", client=FakeMemoryClient(), writable=False)
        await store.initialize()

        with pytest.raises(ValueError, match="not writable"):
            await store.add("anything")

    @pytest.mark.asyncio
    async def test_rejects_empty_content(self) -> None:
        from neo4j_agent_memory.integrations.strands import Neo4jMemoryStore

        store = Neo4jMemoryStore(name="graph", client=FakeMemoryClient())
        await store.initialize()

        with pytest.raises(ValueError, match="empty"):
            await store.add("   ")
