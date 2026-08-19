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

    def test_write_sinks_are_not_declared_yet(self) -> None:
        """Scope guard: `add` lands in task 7, `add_messages` in task 8.

        `_has_method` compares `getattr(type(store), name)` against the Protocol's
        own stub by identity, so an undefined method reads as absent. Stubbing
        either sink here would flip write-sink detection before the methods do
        anything — hence this asserts the intermediate state rather than the
        final one. The both-sinks assertions live in task 8.
        """
        from strands.memory.types import _has_method, _has_write_sink

        from neo4j_agent_memory.integrations.strands import Neo4jMemoryStore

        store = Neo4jMemoryStore(name="graph", client=FakeMemoryClient())

        assert _has_method(store, "initialize") is True
        assert _has_method(store, "add") is False
        assert _has_method(store, "add_messages") is False
        assert _has_write_sink(store) is False


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
        from neo4j_agent_memory.integrations.strands import Neo4jMemoryStore

        client = FakeMemoryClient()
        store = Neo4jMemoryStore(name="graph", client=client)
        store._owns_client = True  # simulate settings-constructed
        async with store:
            pass

        assert client.close_calls == 1
