"""Coexistence of Neo4jSessionManager and Neo4jMemoryStore on one agent.

Both constructs are first-class Agent parameters, so pairing them is normal.
Two overlaps are not: double extraction (raises) and double injection (warns).
"""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("strands", reason="strands-agents not installed")

from tests.unit.integrations.strands_fakes import FakeAgent, FakeMemoryClient


def _manager_with_store(**store_kwargs: Any) -> Any:
    from strands.memory import MemoryManager

    from neo4j_agent_memory.integrations.strands import Neo4jMemoryStore, Neo4jMemoryStoreConfig

    store = Neo4jMemoryStore(
        Neo4jMemoryStoreConfig(name="graph", client=FakeMemoryClient(), **store_kwargs)
    )
    return MemoryManager(stores=[store])


class TestDoubleExtraction:
    def test_raises_when_both_sides_extract(self) -> None:
        from neo4j_agent_memory.integrations.strands import Neo4jSessionManager

        manager = Neo4jSessionManager(
            "s1", memory_client=FakeMemoryClient(), extract_entities=True
        )
        agent = FakeAgent(memory_manager=_manager_with_store(extraction=True))

        with pytest.raises(ValueError) as excinfo:
            manager.initialize(agent)

        message = str(excinfo.value)
        assert "twice" in message
        assert "extraction=False" in message
        assert "extract_entities=False" in message

    def test_raises_on_nams_even_with_session_extraction_off(self) -> None:
        """NAMS extracts server-side regardless of the session manager's flag."""
        from neo4j_agent_memory.integrations.strands import Neo4jSessionManager

        manager = Neo4jSessionManager(
            "s1", memory_client=FakeMemoryClient(nams_mode=True), extract_entities=False
        )
        agent = FakeAgent(memory_manager=_manager_with_store(extraction=True))

        with pytest.raises(ValueError, match="twice"):
            manager.initialize(agent)

    def test_allows_the_recommended_pairing(self) -> None:
        from neo4j_agent_memory.integrations.strands import Neo4jSessionManager

        manager = Neo4jSessionManager(
            "s1", memory_client=FakeMemoryClient(), extract_entities=True
        )
        agent = FakeAgent(memory_manager=_manager_with_store())  # extraction off

        manager.initialize(agent)  # must not raise

    def test_allows_store_owned_extraction_when_session_manager_does_not(self) -> None:
        from neo4j_agent_memory.integrations.strands import Neo4jSessionManager

        manager = Neo4jSessionManager(
            "s1", memory_client=FakeMemoryClient(), extract_entities=False
        )
        agent = FakeAgent(memory_manager=_manager_with_store(extraction=True))

        manager.initialize(agent)  # must not raise

    def test_ignores_a_memory_manager_holding_only_foreign_stores(self) -> None:
        """extraction=True on the foreign store, deliberately: the point is that
        ``_our_stores`` filters by ``isinstance(store, Neo4jMemoryStore)``, not by
        the extraction flag. A foreign store with extraction off would pass this
        test even if the isinstance filter were replaced by duck-typing (e.g.
        ``getattr(s, "extraction", False)``) — that regression must still fail
        here, on a store that both extracts and isn't ours.
        """
        from strands.memory import MemoryManager
        from strands.vended_memory_stores.test_memory_store import TestMemoryStore

        from neo4j_agent_memory.integrations.strands import Neo4jSessionManager

        manager = Neo4jSessionManager(
            "s1", memory_client=FakeMemoryClient(), extract_entities=True
        )
        agent = FakeAgent(
            memory_manager=MemoryManager(stores=[TestMemoryStore(name="t", extraction=True)])
        )

        manager.initialize(agent)  # not our store, not our problem

    def test_no_memory_manager_is_fine(self) -> None:
        from neo4j_agent_memory.integrations.strands import Neo4jSessionManager

        manager = Neo4jSessionManager("s1", memory_client=FakeMemoryClient())

        manager.initialize(FakeAgent(memory_manager=None))  # must not raise


class TestDoubleInjection:
    def test_warns_once_when_both_inject(self, caplog: pytest.LogCaptureFixture) -> None:
        from neo4j_agent_memory.integrations.strands import (
            Neo4jRetrievalConfig,
            Neo4jSessionManager,
        )

        manager = Neo4jSessionManager(
            "s1",
            memory_client=FakeMemoryClient(),
            extract_entities=False,
            retrieval_config=Neo4jRetrievalConfig(),
        )
        agent = FakeAgent(memory_manager=_manager_with_store())

        manager.initialize(agent)
        manager.initialize(agent)

        assert caplog.text.lower().count("injected twice") == 1

    def test_silent_without_retrieval_config(self, caplog: pytest.LogCaptureFixture) -> None:
        from neo4j_agent_memory.integrations.strands import Neo4jSessionManager

        manager = Neo4jSessionManager(
            "s1", memory_client=FakeMemoryClient(), extract_entities=False
        )
        agent = FakeAgent(memory_manager=_manager_with_store())

        manager.initialize(agent)

        assert "injected twice" not in caplog.text.lower()

    def test_silent_when_manager_injection_is_disabled(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        from strands.memory import MemoryManager

        from neo4j_agent_memory.integrations.strands import (
            Neo4jMemoryStore,
            Neo4jMemoryStoreConfig,
            Neo4jRetrievalConfig,
            Neo4jSessionManager,
        )

        store = Neo4jMemoryStore(Neo4jMemoryStoreConfig(name="graph", client=FakeMemoryClient()))
        manager = Neo4jSessionManager(
            "s1",
            memory_client=FakeMemoryClient(),
            extract_entities=False,
            retrieval_config=Neo4jRetrievalConfig(),
        )
        agent = FakeAgent(memory_manager=MemoryManager(stores=[store], injection=False))

        manager.initialize(agent)

        assert "injected twice" not in caplog.text.lower()


class TestPrivateAttributeCoupling:
    def test_memory_manager_still_exposes_stores(self) -> None:
        """Fails loudly if a strands upgrade moves MemoryManager._stores."""
        from strands.memory import MemoryManager
        from strands.vended_memory_stores.test_memory_store import TestMemoryStore

        manager = MemoryManager(stores=[TestMemoryStore(name="t")])

        assert hasattr(manager, "_stores"), (
            "MemoryManager._stores is gone; the coexistence guards in "
            "session_manager.py need a new way to enumerate stores."
        )
        assert [s.name for s in manager._stores] == ["t"]
