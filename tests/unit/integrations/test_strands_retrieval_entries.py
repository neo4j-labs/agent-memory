"""_retrieve_entries: concurrent long-term fan-out returning entry rows."""

from __future__ import annotations

import pytest

pytest.importorskip("strands", reason="strands-agents not installed")

from neo4j_agent_memory.memory.long_term import Entity, Fact, Preference


def _entity() -> Entity:
    e = Entity(name="Acme Corp", type="ORGANIZATION")
    e.metadata["similarity"] = 0.83
    return e


class TestRetrieveEntries:
    @pytest.mark.asyncio
    async def test_maps_each_kind_to_a_row_with_metadata(self) -> None:
        from neo4j_agent_memory.integrations.strands._retrieval import _retrieve_entries
        from tests.unit.integrations.strands_fakes import FakeLongTerm

        long_term = FakeLongTerm()
        long_term.entities = [_entity()]
        long_term.preferences = [Preference(category="ui", preference="dark mode")]
        long_term.facts = [Fact(subject="Ada", predicate="works_at", object="Acme")]

        rows = await _retrieve_entries(
            long_term,
            "acme",
            limit=10,
            min_score=0.2,
            include_entities=True,
            include_preferences=True,
            include_facts=True,
            nams=False,
        )

        kinds = [r.metadata["kind"] for r in rows]
        assert kinds == ["entity", "preference", "fact"]
        assert rows[0].content == "[entity] Acme Corp (ORGANIZATION)"
        assert rows[0].metadata["score"] == 0.83
        assert rows[0].metadata["type"] == "ORGANIZATION"
        assert rows[1].metadata["type"] == "ui"
        assert "id" in rows[0].metadata

    @pytest.mark.asyncio
    async def test_nams_gates_preferences_and_facts_off(self) -> None:
        from neo4j_agent_memory.integrations.strands._retrieval import _retrieve_entries
        from tests.unit.integrations.strands_fakes import FakeLongTerm

        long_term = FakeLongTerm()
        long_term.entities = [_entity()]
        long_term.preferences = [Preference(category="ui", preference="dark mode")]

        rows = await _retrieve_entries(
            long_term, "q", limit=10, min_score=0.2,
            include_entities=True, include_preferences=True, include_facts=True,
            nams=True,
        )

        assert [r.metadata["kind"] for r in rows] == ["entity"]
        assert long_term.search_calls == 1  # preferences/facts never called

    @pytest.mark.asyncio
    async def test_one_failing_kind_does_not_lose_the_others(self, caplog) -> None:
        from neo4j_agent_memory.integrations.strands._retrieval import _retrieve_entries
        from tests.unit.integrations.strands_fakes import FakeLongTerm

        long_term = FakeLongTerm()
        long_term.entities = [_entity()]
        long_term.fail_preferences = True

        rows = await _retrieve_entries(
            long_term, "q", limit=10, min_score=0.2,
            include_entities=True, include_preferences=True, include_facts=False,
            nams=False,
        )

        assert [r.metadata["kind"] for r in rows] == ["entity"]
        assert "preference search failed" in caplog.text.lower()

    @pytest.mark.asyncio
    async def test_missing_score_is_omitted_not_zero(self) -> None:
        from neo4j_agent_memory.integrations.strands._retrieval import _retrieve_entries
        from tests.unit.integrations.strands_fakes import FakeLongTerm

        long_term = FakeLongTerm()
        long_term.entities = [Entity(name="Acme Corp", type="ORGANIZATION")]  # no similarity

        rows = await _retrieve_entries(
            long_term, "q", limit=10, min_score=0.2,
            include_entities=True, include_preferences=False, include_facts=False,
            nams=True,
        )

        assert "score" not in rows[0].metadata
