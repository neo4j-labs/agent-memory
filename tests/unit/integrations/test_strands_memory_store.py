"""Neo4jMemoryStore — construction, attributes, lifecycle."""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("strands", reason="strands-agents not installed")

from tests.unit.integrations.strands_fakes import FakeMemoryClient


def _store(**kw: Any) -> Any:
    """Build a Neo4jMemoryStore from loose kwargs via its real config dataclass.

    Keeps the individual tests readable while still exercising the actual
    ``Neo4jMemoryStoreConfig`` field names — ``Neo4jMemoryStoreConfig(**kw)``
    is the real dataclass constructor, so a typo in a field name here is a
    ``TypeError``, not a silently-ignored key.
    """
    from neo4j_agent_memory.integrations.strands import Neo4jMemoryStore, Neo4jMemoryStoreConfig

    return Neo4jMemoryStore(Neo4jMemoryStoreConfig(**kw))


class TestConstruction:
    def test_requires_a_name(self) -> None:
        """``name`` must be non-empty; checked eagerly in
        ``Neo4jMemoryStoreConfig.__post_init__``, before the store ever sees it."""

        with pytest.raises(ValueError, match="name"):
            _store(name="", client=FakeMemoryClient())

    def test_requires_exactly_one_of_client_or_settings(self) -> None:
        from pydantic import SecretStr

        from neo4j_agent_memory import MemorySettings
        from neo4j_agent_memory.config.settings import Neo4jConfig

        with pytest.raises(ValueError, match="exactly one"):
            _store(name="s")
        with pytest.raises(ValueError, match="exactly one"):
            _store(
                name="s",
                client=FakeMemoryClient(),
                settings=MemorySettings(neo4j=Neo4jConfig(password=SecretStr("p"))),
            )

    def test_protocol_attribute_defaults(self) -> None:
        store = _store(name="graph", client=FakeMemoryClient())

        assert store.name == "graph"
        assert store.writable is True
        assert store.extraction is False
        assert store.max_search_results is None
        assert store.description is not None and "graph" in store.description.lower()
        assert store.graph_tools is True

    def test_protocol_fields_are_all_assigned_onto_the_store(self) -> None:
        """``MemoryStore``'s protocol requires name/description/max_search_results/
        writable/extraction as instance attributes. A future rename of one of
        these on ``Neo4jMemoryStoreConfig`` that forgot the matching
        ``self.x = config.x`` line in ``__init__`` would silently drop a
        protocol attribute — this pins all five down against the config."""
        from neo4j_agent_memory.integrations.strands import Neo4jMemoryStore, Neo4jMemoryStoreConfig

        config = Neo4jMemoryStoreConfig(
            name="graph",
            client=FakeMemoryClient(),
            description="a custom description",
            max_search_results=9,
            writable=False,
            extraction=True,
        )
        store = Neo4jMemoryStore(config)

        assert store.name == config.name
        assert store.description == config.description
        assert store.max_search_results == config.max_search_results
        assert store.writable == config.writable
        assert store.extraction == config.extraction

    def test_settings_construction_owns_the_client(self) -> None:
        """Settings-constructed stores build and own a real MemoryClient.

        MemoryClient.__init__ is lazy (no connection, no embedder until
        .connect()), so this is cheap and does not touch the network.
        """
        from pydantic import SecretStr

        from neo4j_agent_memory import MemoryClient, MemorySettings
        from neo4j_agent_memory.config.settings import Neo4jConfig

        store = _store(
            name="graph", settings=MemorySettings(neo4j=Neo4jConfig(password=SecretStr("p")))
        )

        assert store._owns_client is True
        assert isinstance(store._client, MemoryClient)


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_initialize_connects_an_owned_client_only(self) -> None:
        client = FakeMemoryClient()
        store = _store(name="graph", client=client)
        await store.initialize()

        assert client.connect_calls == 1
        await store.aclose()
        assert client.close_calls == 0  # borrowed client stays open

    @pytest.mark.asyncio
    async def test_initialize_is_idempotent(self) -> None:
        client = FakeMemoryClient()
        store = _store(name="graph", client=client)
        await store.initialize()
        await store.initialize()

        assert client.connect_calls == 1

    @pytest.mark.asyncio
    async def test_context_manager_closes_an_owned_client(self) -> None:
        """Ownership must come from the constructor (settings=), not a monkey-patched flag."""
        from unittest.mock import AsyncMock

        from pydantic import SecretStr

        from neo4j_agent_memory import MemorySettings
        from neo4j_agent_memory.config.settings import Neo4jConfig

        store = _store(
            name="graph", settings=MemorySettings(neo4j=Neo4jConfig(password=SecretStr("p")))
        )
        store._client.connect = AsyncMock()  # type: ignore[method-assign]
        store._client.close = AsyncMock()  # type: ignore[method-assign]

        async with store:
            pass

        store._client.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_aclose_resets_initialization_so_reentry_reconnects(self) -> None:
        """Re-entering the context manager must reconnect, not skip on a stale flag.

        Ownership comes from the constructor (``settings=``) as it must; only
        the transport underneath is swapped for a call-counting fake, so the
        store still closes on exit and has to reconnect on re-entry.
        """
        from pydantic import SecretStr

        from neo4j_agent_memory import MemorySettings
        from neo4j_agent_memory.config.settings import Neo4jConfig

        store = _store(
            name="graph", settings=MemorySettings(neo4j=Neo4jConfig(password=SecretStr("p")))
        )
        client = FakeMemoryClient()
        store._client = client  # type: ignore[assignment]

        async with store:
            pass
        async with store:
            pass

        assert client.connect_calls == 2
        assert client.close_calls == 2


class TestEventLoopRebinding:
    """Strands' synchronous entry points drive each call on a fresh loop.

    ``Agent.__init__`` runs ``initialize()`` through
    ``strands._async.run_async`` (``asyncio.run`` in a throwaway thread) and
    every ``Agent.__call__`` uses a *different* loop. The neo4j driver and
    the NAMS transport bind to the loop that opened them, so the store has
    to notice the change. See ``tests/integration/`` for the same scenario
    end-to-end against a real Neo4j.
    """

    def test_a_borrowed_client_raises_a_named_error_on_a_new_loop(self) -> None:
        """A client we were handed is not ours to close or reconnect — so: raise.

        The error has to name the problem and both remedies; the alternative
        is an opaque ``RuntimeError`` from inside the neo4j driver.
        """
        from strands._async import run_async

        client = FakeMemoryClient()
        store = _store(name="graph", client=client)

        run_async(store.initialize)

        with pytest.raises(RuntimeError, match="different event loop") as raised:
            run_async(store.initialize)

        message = str(raised.value)
        assert "settings=" in message and "client=" in message
        # Untouched: neither closed nor reconnected behind the owner's back.
        assert client.close_calls == 0
        assert client.connect_calls == 1

    def test_the_same_loop_stays_idempotent(self) -> None:
        """Only a *changed* loop triggers the rebind path."""
        from strands._async import run_async

        client = FakeMemoryClient()
        store = _store(name="graph", client=client)

        async def twice() -> None:
            await store.initialize()
            await store.initialize()

        run_async(twice)

        assert client.connect_calls == 1


class TestForNams:
    def test_builds_nams_settings_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from neo4j_agent_memory.integrations.strands import (
            Neo4jMemoryStore,
            Neo4jMemoryStoreConfig,
        )

        monkeypatch.setenv("MEMORY_API_KEY", "test-key")

        store = Neo4jMemoryStore.for_nams(Neo4jMemoryStoreConfig(name="graph"))

        settings = store._client._settings
        assert settings.backend == "nams"
        assert settings.nams.validate_on_connect is False

    def test_raises_without_api_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from neo4j_agent_memory.integrations.strands import (
            Neo4jMemoryStore,
            Neo4jMemoryStoreConfig,
        )

        monkeypatch.delenv("MEMORY_API_KEY", raising=False)

        with pytest.raises(ValueError, match="api_key is required"):
            Neo4jMemoryStore.for_nams(Neo4jMemoryStoreConfig(name="graph"))

    def test_does_not_mutate_the_callers_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``for_nams`` uses ``dataclasses.replace`` to inject ``settings`` into
        a copy, not the caller's own config instance."""
        from neo4j_agent_memory.integrations.strands import (
            Neo4jMemoryStore,
            Neo4jMemoryStoreConfig,
        )

        monkeypatch.setenv("MEMORY_API_KEY", "test-key")
        original_config = Neo4jMemoryStoreConfig(name="graph")

        Neo4jMemoryStore.for_nams(original_config)

        assert original_config.settings is None


class TestSinkResolution:
    @pytest.mark.asyncio
    async def test_creates_a_deterministically_named_sink(self) -> None:
        """Bolt keys conversations by session_id; the deterministic name is the whole contract."""

        client = FakeMemoryClient()
        store = _store(name="graph", client=client, user_id="alice")
        await store.initialize()
        key = await store._resolve_sink()

        assert key == "strands-memory-store/alice/graph"
        assert client.short_term.conversations == {}  # nothing minted
        assert client.short_term.list_conversations_calls == []

    @pytest.mark.asyncio
    async def test_reuses_an_existing_sink_across_instances(self) -> None:
        """Bolt needs no round-trip for reuse: same name, same key, every time."""

        client = FakeMemoryClient()
        first = _store(name="graph", client=client)
        await first.initialize()
        key_one = await first._resolve_sink()

        second = _store(name="graph", client=client)
        await second.initialize()
        key_two = await second._resolve_sink()

        assert key_one == key_two
        assert client.short_term.conversations == {}
        assert client.short_term.list_conversations_calls == []

    @pytest.mark.asyncio
    async def test_reuses_the_nams_server_minted_id_by_metadata(self) -> None:
        """NAMS mints conversation ids, so reuse matches on metadata, not id."""

        client = FakeMemoryClient(nams_mode=True)
        first = _store(name="graph", client=client)
        await first.initialize()
        key_one = await first._resolve_sink()

        second = _store(name="graph", client=client)
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

        client = FakeMemoryClient(nams_mode=True)
        store = _store(name="graph", client=client)
        await store.initialize()
        await store._resolve_sink()

        assert len(client.short_term.list_conversations_calls) == 1

    @pytest.mark.asyncio
    async def test_explicit_conversation_id_is_used_verbatim(self) -> None:
        client = FakeMemoryClient()
        store = _store(name="graph", client=client, conversation_id="chat-42")
        await store.initialize()

        assert await store._resolve_sink() == "chat-42"
        assert client.short_term.conversations == {}  # nothing minted

    @pytest.mark.asyncio
    async def test_two_stores_with_different_names_get_different_sinks(self) -> None:
        client = FakeMemoryClient()
        personal = _store(name="personal", client=client)
        team = _store(name="team", client=client)
        await personal.initialize()
        await team.initialize()

        assert await personal._resolve_sink() != await team._resolve_sink()

    @pytest.mark.asyncio
    async def test_bolt_does_not_scan_conversations(self) -> None:
        """On bolt the deterministic name is the key; a list scan would be wasted work."""

        client = FakeMemoryClient()
        store = _store(name="graph", client=client)
        await store.initialize()
        await store._resolve_sink()

        assert client.short_term.list_conversations_calls == []


class TestSearch:
    @pytest.mark.asyncio
    async def test_returns_memory_entries_with_metadata(self) -> None:
        from neo4j_agent_memory.memory.long_term import Entity, Preference

        client = FakeMemoryClient()
        entity = Entity(name="Acme Corp", type="ORGANIZATION")
        entity.metadata["similarity"] = 0.9
        client.long_term.entities = [entity]
        client.long_term.preferences = [Preference(category="ui", preference="dark mode")]

        store = _store(name="graph", client=client)
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
        client = FakeMemoryClient()
        store = _store(name="graph", client=client, include_preferences=False, include_facts=False)
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
        from neo4j_agent_memory.memory.long_term import Entity, Fact, Preference

        client = FakeMemoryClient()
        client.long_term.entities = [Entity(name="Acme Corp", type="ORGANIZATION")]
        client.long_term.preferences = [Preference(category="ui", preference="dark mode")]
        client.long_term.facts = [Fact(subject="Acme", predicate="located_in", object="NYC")]

        store = _store(
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
        from neo4j_agent_memory.memory.long_term import Entity, Preference

        client = FakeMemoryClient(nams_mode=True)
        client.long_term.entities = [Entity(name="Acme Corp", type="ORGANIZATION")]
        client.long_term.preferences = [Preference(category="ui", preference="dark mode")]

        store = _store(name="graph", client=client)
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

        client = FakeMemoryClient(nams_mode=True)
        store = _store(name="graph", client=client)
        await store.initialize()
        await store.search("q")

        assert client.short_term.list_conversations_calls == []
        assert client.short_term.conversations == {}

    @pytest.mark.asyncio
    async def test_search_initializes_without_a_prior_initialize_call(self) -> None:
        """Standalone use (no MemoryManager.init_agent) must still connect."""

        client = FakeMemoryClient()
        store = _store(name="graph", client=client)
        await store.search("q")

        assert client.connect_calls == 1


class TestAdd:
    @pytest.mark.asyncio
    async def test_default_writes_a_message_into_the_sink_with_extraction(self) -> None:
        client = FakeMemoryClient()
        store = _store(name="graph", client=client)
        await store.initialize()
        result = await store.add("The user prefers dark mode")

        call = client.short_term.add_message_calls[-1]
        assert call["content"] == "The user prefers dark mode"
        assert call["role"] == "user"
        assert call["extract_entities"] is True
        assert result["kind"] == "message"

    @pytest.mark.asyncio
    async def test_kind_preference_routes_to_add_preference(self) -> None:
        client = FakeMemoryClient()
        store = _store(name="graph", client=client)
        await store.initialize()
        result = await store.add("dark mode", {"kind": "preference", "category": "ui"})

        assert client.long_term.added_preferences == [("ui", "dark mode")]
        assert result["kind"] == "preference"
        assert client.short_term.add_message_calls == []

    @pytest.mark.asyncio
    async def test_kind_preference_without_category_defaults_to_memory(self) -> None:
        client = FakeMemoryClient()
        store = _store(name="graph", client=client)
        await store.initialize()
        await store.add("dark mode", {"kind": "preference"})

        assert client.long_term.added_preferences == [("memory", "dark mode")]

    @pytest.mark.asyncio
    async def test_kind_fact_requires_a_triple(self) -> None:
        client = FakeMemoryClient()
        store = _store(name="graph", client=client)
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
        client = FakeMemoryClient()
        store = _store(name="graph", client=client)
        await store.initialize()
        await store.add("Acme Corp", {"kind": "entity", "type": "ORGANIZATION"})

        assert client.long_term.added_entities == [("Acme Corp", "ORGANIZATION")]
        assert client.short_term.add_message_calls == []

    @pytest.mark.asyncio
    async def test_kind_entity_defaults_name_and_type(self) -> None:
        client = FakeMemoryClient()
        store = _store(name="graph", client=client)
        await store.initialize()
        await store.add("Acme Corp", {"kind": "entity"})

        assert client.long_term.added_entities == [("Acme Corp", "OBJECT")]

    @pytest.mark.asyncio
    async def test_kind_entity_on_nams_returns_bare_entity_id(self) -> None:
        """NAMS add_entity returns a bare Entity, not a (Entity, Dedup) tuple.

        Without the isinstance narrowing in `_add_typed`, this raises trying
        to subscript a bare Entity as a tuple.
        """

        client = FakeMemoryClient(nams_mode=True)
        store = _store(name="graph", client=client)
        await store.initialize()
        result = await store.add("Acme Corp", {"kind": "entity", "type": "ORGANIZATION"})

        assert client.long_term.added_entities == [("Acme Corp", "ORGANIZATION")]
        assert result["kind"] == "entity"
        assert result["id"]

    @pytest.mark.asyncio
    async def test_unsupported_kind_on_nams_falls_back_to_the_sink(self, caplog) -> None:
        client = FakeMemoryClient(nams_mode=True)
        store = _store(name="graph", client=client)
        await store.initialize()
        result = await store.add("dark mode", {"kind": "preference", "category": "ui"})

        assert result["kind"] == "message"
        assert client.short_term.add_message_calls[-1]["content"] == "dark mode"
        assert "falling back" in caplog.text.lower()

    @pytest.mark.asyncio
    async def test_unsupported_kind_warning_is_logged_once_per_store(self, caplog) -> None:
        client = FakeMemoryClient(nams_mode=True)
        store = _store(name="graph", client=client)
        await store.initialize()

        with caplog.at_level("WARNING"):
            await store.add("dark mode", {"kind": "preference", "category": "ui"})
            await store.add("another one", {"kind": "preference", "category": "ui"})

        warnings = [r for r in caplog.records if "unsupported on this backend" in r.message]
        assert len(warnings) == 1
        assert len(client.short_term.add_message_calls) == 2

    @pytest.mark.asyncio
    async def test_rejects_writes_when_not_writable(self) -> None:
        store = _store(name="graph", client=FakeMemoryClient(), writable=False)
        await store.initialize()

        with pytest.raises(ValueError, match="not writable"):
            await store.add("anything")

    @pytest.mark.asyncio
    async def test_rejects_empty_content(self) -> None:
        store = _store(name="graph", client=FakeMemoryClient())
        await store.initialize()

        with pytest.raises(ValueError, match="empty"):
            await store.add("   ")


class TestAddMessages:
    @pytest.mark.asyncio
    async def test_writes_the_batch_to_the_sink_with_extraction(self) -> None:
        from strands.memory import AddMessagesContext

        client = FakeMemoryClient()
        store = _store(name="graph", client=client)
        await store.initialize()

        result = await store.add_messages(
            [
                {"role": "user", "content": [{"text": "I prefer dark mode"}]},
                {"role": "assistant", "content": [{"text": "Noted"}]},
            ],
            AddMessagesContext(sequence_numbers=[0, 1]),
        )

        call = client.short_term.bulk_calls[-1]
        assert call["kwargs"]["extract_entities"] is True
        assert [m["content"] for m in call["messages"]] == ["I prefer dark mode", "Noted"]
        assert [m["role"] for m in call["messages"]] == ["user", "assistant"]
        assert result == {"written": 2, "skipped": 0}

    @pytest.mark.asyncio
    async def test_a_retried_batch_is_not_written_twice(self) -> None:
        """Extraction writes are at-least-once; the same sequence numbers repeat."""
        from strands.memory import AddMessagesContext

        client = FakeMemoryClient()
        store = _store(name="graph", client=client)
        await store.initialize()
        batch = [{"role": "user", "content": [{"text": "ok"}]}]

        first = await store.add_messages(batch, AddMessagesContext(sequence_numbers=[0]))
        second = await store.add_messages(batch, AddMessagesContext(sequence_numbers=[0]))

        assert first == {"written": 1, "skipped": 0}
        assert second == {"written": 0, "skipped": 1}
        assert len(client.short_term.bulk_calls) == 1

    @pytest.mark.asyncio
    async def test_identical_text_with_distinct_sequence_numbers_is_kept(self) -> None:
        """Dedupe keys on sequence number, not content — two 'ok's are two messages."""
        from strands.memory import AddMessagesContext

        client = FakeMemoryClient()
        store = _store(name="graph", client=client)
        await store.initialize()

        result = await store.add_messages(
            [
                {"role": "user", "content": [{"text": "ok"}]},
                {"role": "user", "content": [{"text": "ok"}]},
            ],
            AddMessagesContext(sequence_numbers=[3, 4]),
        )

        assert result == {"written": 2, "skipped": 0}

    @pytest.mark.asyncio
    async def test_without_sequence_numbers_everything_is_written(self) -> None:
        client = FakeMemoryClient()
        store = _store(name="graph", client=client)
        await store.initialize()
        batch = [{"role": "user", "content": [{"text": "ok"}]}]

        assert await store.add_messages(batch, None) == {"written": 1, "skipped": 0}
        assert await store.add_messages(batch, None) == {"written": 1, "skipped": 0}

    @pytest.mark.asyncio
    async def test_messages_with_no_text_blocks_are_dropped(self) -> None:
        client = FakeMemoryClient()
        store = _store(name="graph", client=client)
        await store.initialize()

        result = await store.add_messages(
            [{"role": "assistant", "content": [{"toolUse": {"name": "x", "input": {}}}]}],
            None,
        )

        assert result == {"written": 0, "skipped": 1}
        assert client.short_term.bulk_calls == []

    @pytest.mark.asyncio
    async def test_batches_larger_than_100_are_chunked(self) -> None:
        client = FakeMemoryClient()
        store = _store(name="graph", client=client)
        await store.initialize()

        messages = [{"role": "user", "content": [{"text": f"m{i}"}]} for i in range(250)]
        result = await store.add_messages(messages, None)

        assert result == {"written": 250, "skipped": 0}
        assert [len(c["messages"]) for c in client.short_term.bulk_calls] == [100, 100, 50]

    @pytest.mark.asyncio
    async def test_rejects_writes_when_not_writable(self) -> None:
        store = _store(name="graph", client=FakeMemoryClient(), writable=False)
        await store.initialize()

        with pytest.raises(ValueError, match="not writable"):
            await store.add_messages([{"role": "user", "content": [{"text": "x"}]}], None)

    @pytest.mark.asyncio
    async def test_bulk_kwargs_bind_against_the_real_bolt_signature(self) -> None:
        """Regression guard for the ``user_identifier`` crash-on-bolt bug.

        The bug: the store passed ``user_identifier`` to ``bulk_add_messages``
        while the real ``ShortTermMemory.add_messages_batch`` had no such
        parameter and no ``**kwargs`` catch-all, so bolt raised
        ``TypeError: got an unexpected keyword argument 'user_identifier'``.
        Fixed by adding the parameter to ``add_messages_batch`` itself (it now
        enforces multi-tenancy and links the conversation, matching
        ``add_message``), so the store keeps passing it.

        ``FakeShortTerm.bulk_add_messages`` now mirrors the real, explicit
        parameter list (no ``**kwargs`` catch-all either), so it can no longer
        swallow a keyword the real backend would reject — this suite would
        have failed with the bug in place, and stays a live tripwire against
        any future kwarg the store sends that the real signature doesn't
        accept. Binding against the actual method's ``inspect.signature`` is
        the belt-and-suspenders check on top of that fidelity fix.
        """
        import inspect

        from neo4j_agent_memory.memory.short_term import ShortTermMemory

        client = FakeMemoryClient()
        store = _store(name="graph", client=client, user_id="alice")
        await store.initialize()
        await store.add_messages([{"role": "user", "content": [{"text": "ok"}]}], None)

        call = client.short_term.bulk_calls[-1]
        real_signature = inspect.signature(ShortTermMemory.add_messages_batch)
        # `self` is positional-only for bind() purposes here; its value is
        # never inspected, only its presence in the parameter list matters.
        real_signature.bind(None, call["session_id"], call["messages"], **call["kwargs"])


class TestWriteSinks:
    def test_declares_both_write_sinks(self) -> None:
        """Both sinks on one class: server-side extraction, `add` still available."""
        from strands.memory.types import _has_method, _has_write_sink

        store = _store(name="graph", client=FakeMemoryClient())

        assert _has_method(store, "add") is True
        assert _has_method(store, "add_messages") is True
        assert _has_write_sink(store) is True

    def test_server_side_extraction_is_the_resolved_default(self) -> None:
        """`add_messages` present -> no ModelExtractor, so no extra model call.

        Non-vacuous only here: with `add` alone (task 7) this would resolve to a
        ModelExtractor, and with neither sink it resolves to None trivially.
        """
        from strands.memory.extraction.resolve_extraction_config import (
            _resolve_extraction_config,
        )

        store = _store(name="graph", client=FakeMemoryClient(), extraction=True)
        resolved = _resolve_extraction_config(store.extraction, store)

        assert resolved is not None
        assert resolved.extractor is None


class TestGetTools:
    def test_bolt_exposes_both_graph_tools(self) -> None:
        store = _store(name="graph", client=FakeMemoryClient(), user_id="alice")
        names = {t.tool_name for t in store.get_tools()}

        assert names == {"get_entity_graph", "get_user_preferences"}

    def test_bolt_omits_preferences_tool_without_a_user_id(self) -> None:
        """get_preferences_for requires a user identifier; with none, no tool."""
        store = _store(name="graph", client=FakeMemoryClient())
        names = {t.tool_name for t in store.get_tools()}

        assert names == {"get_entity_graph"}

    def test_nams_omits_the_preferences_tool(self) -> None:
        """NAMS exposes no preferences endpoint; expand_graph covers traversal."""
        store = _store(name="graph", client=FakeMemoryClient(nams_mode=True), user_id="alice")
        names = {t.tool_name for t in store.get_tools()}

        assert names == {"get_entity_graph"}

    def test_graph_tools_false_exposes_nothing(self) -> None:
        store = _store(name="graph", client=FakeMemoryClient(), graph_tools=False)

        assert store.get_tools() == []

    @pytest.mark.asyncio
    async def test_entity_graph_traverses_with_depth_on_bolt(self) -> None:
        from neo4j_agent_memory.integrations.strands._store_tools import _entity_graph
        from neo4j_agent_memory.memory.long_term import Entity

        client = FakeMemoryClient()
        centre = Entity(name="Acme Corp", type="ORGANIZATION")
        client.long_term.entities = [centre]
        client.long_term.related = [(Entity(name="Ada", type="PERSON"), "WORKS_AT")]

        result = await _entity_graph(client, "Acme Corp", depth=2, nams=False)

        assert result["center"] == "Acme Corp"
        assert {"name": "Ada", "type": "PERSON", "is_center": False} in result["nodes"]
        assert {"from": "Ada", "relationship": "WORKS_AT", "to": "Acme Corp"} in result["edges"]
        assert client.long_term.related_kwargs[-1]["depth"] == 2

    @pytest.mark.asyncio
    async def test_entity_graph_depth_is_clamped_to_three_by_the_tool(self) -> None:
        """The @tool wrapper clamps depth to [1, 3]; _entity_graph itself trusts its caller."""
        from neo4j_agent_memory.memory.long_term import Entity

        client = FakeMemoryClient()
        centre = Entity(name="Acme Corp", type="ORGANIZATION")
        client.long_term.entities = [centre]

        store = _store(name="graph", client=client, user_id="alice")
        tools = {t.tool_name: t for t in store.get_tools()}

        await tools["get_entity_graph"](entity_name="Acme Corp", depth=99)

        assert client.long_term.related_kwargs[-1]["depth"] == 3

    @pytest.mark.asyncio
    async def test_entity_graph_caps_edges_at_max_edges_on_bolt(self) -> None:
        from neo4j_agent_memory.integrations.strands._store_tools import (
            _MAX_EDGES,
            _entity_graph,
        )
        from neo4j_agent_memory.memory.long_term import Entity

        client = FakeMemoryClient()
        centre = Entity(name="Acme Corp", type="ORGANIZATION")
        client.long_term.entities = [centre]
        client.long_term.related = [
            (Entity(name=f"Person {i}", type="PERSON"), "WORKS_AT")
            for i in range(_MAX_EDGES + 10)
        ]

        result = await _entity_graph(client, "Acme Corp", depth=1, nams=False)

        assert len(result["edges"]) == _MAX_EDGES

    @pytest.mark.asyncio
    async def test_entity_graph_uses_expand_graph_on_nams(self) -> None:
        """NAMS: name resolved via search, then a 1-hop expansion by node id."""
        from neo4j_agent_memory.integrations.strands._store_tools import _entity_graph
        from neo4j_agent_memory.memory.long_term import Entity

        client = FakeMemoryClient(nams_mode=True)
        centre = Entity(name="Acme Corp", type="ORGANIZATION")
        client.long_term.entities = [centre]
        client.long_term.expansion = {
            "nodes": [{"id": "n2", "name": "Ada", "type": "PERSON"}],
            "edges": [{"from": "n2", "to": str(centre.id), "type": "WORKS_AT"}],
        }

        result = await _entity_graph(client, "Acme Corp", depth=3, nams=True)

        assert client.long_term.expand_calls == [str(centre.id)]
        assert result["depth"] == 1  # 1 hop is all NAMS offers
        assert result["nodes"] == client.long_term.expansion["nodes"]
        assert result["edges"] == client.long_term.expansion["edges"]

    @pytest.mark.asyncio
    async def test_entity_graph_reports_an_unknown_entity(self) -> None:
        from neo4j_agent_memory.integrations.strands._store_tools import _entity_graph

        client = FakeMemoryClient()
        client.long_term.entities = []

        result = await _entity_graph(client, "Nobody", depth=1, nams=False)

        assert result["error"] == "entity not found: Nobody"

    @pytest.mark.asyncio
    async def test_get_user_preferences_forwards_the_stores_user_id(self) -> None:
        """Regression guard: a hard-coded or dropped user id must fail this."""
        client = FakeMemoryClient()
        store = _store(name="graph", client=client, user_id="alice")
        tools = {t.tool_name: t for t in store.get_tools()}

        await tools["get_user_preferences"]()

        assert client.long_term.preferences_for_calls[-1]["user_identifier"] == "alice"

    @pytest.mark.asyncio
    async def test_get_user_preferences_category_filter_narrows_results(self) -> None:
        from neo4j_agent_memory.memory.long_term import Preference

        client = FakeMemoryClient()
        client.long_term.preferences_for = [
            Preference(category="food", preference="loves sushi"),
            Preference(category="ui", preference="dark mode"),
        ]
        store = _store(name="graph", client=client, user_id="alice")
        tools = {t.tool_name: t for t in store.get_tools()}

        result = await tools["get_user_preferences"](category="food")

        assert result == [{"category": "food", "preference": "loves sushi", "context": None}]
