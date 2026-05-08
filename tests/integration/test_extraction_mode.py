"""Integration tests for v0.3 P1.4 (extraction_mode) and P1.2 (search_steps)."""

from __future__ import annotations

import pytest

from neo4j_agent_memory.schema.models import EntityRef


@pytest.mark.integration
@pytest.mark.asyncio
class TestExtractionMode:
    async def test_skip_mode_writes_no_mentions(self, clean_memory_client, session_id):
        client = clean_memory_client
        message = await client.short_term.add_message(
            session_id,
            "user",
            "John works at Acme.",
            extraction_mode="skip",
        )

        rows = await client.graph.execute_read(
            """
            MATCH (m:Message {id: $id})-[:MENTIONS]->(e:Entity)
            RETURN count(e) AS cnt
            """,
            {"id": str(message.id)},
        )
        assert rows[0]["cnt"] == 0

    async def test_explicit_mode_writes_only_supplied_mentions(
        self, clean_memory_client, session_id
    ):
        client = clean_memory_client
        message = await client.short_term.add_message(
            session_id,
            "user",
            # Mock extractor would normally pull John, Acme, etc. — but
            # explicit mode bypasses that entirely.
            "John works at Acme and lives in NYC.",
            extraction_mode="explicit",
            explicit_mentions=[
                EntityRef(name="John Smith", type="PERSON"),
                EntityRef(name="Acme Corp", type="ORGANIZATION"),
            ],
        )

        rows = await client.graph.execute_read(
            """
            MATCH (m:Message {id: $id})-[:MENTIONS]->(e:Entity)
            RETURN e.name AS name ORDER BY name
            """,
            {"id": str(message.id)},
        )
        names = [r["name"] for r in rows]
        assert names == ["Acme Corp", "John Smith"]

    async def test_auto_mode_runs_extractor(self, clean_memory_client, session_id):
        """``extraction_mode='auto'`` (default) is back-compat: the
        configured extractor runs and produces MENTIONS edges. The
        ``extracted_by`` attribution is plumbed via metadata when the
        extractor sets ``extractor=`` on its results.
        """
        client = clean_memory_client
        await client.short_term.add_message(session_id, "user", "I had lunch with Alice yesterday")

        rows = await client.graph.execute_read(
            """
            MATCH (m:Message)-[:MENTIONS]->(e:Entity)
            RETURN e.name AS name
            """,
        )
        names = [r["name"] for r in rows]
        # MockExtractor produces capitalized words as PERSON entities;
        # exact list isn't important — we just want to confirm extraction
        # ran and produced at least one MENTIONS edge.
        assert len(names) >= 1


@pytest.mark.integration
@pytest.mark.asyncio
class TestSearchSteps:
    async def test_search_steps_returns_expected_results(self, clean_memory_client, session_id):
        client = clean_memory_client
        # Seed two traces with distinguishable steps.
        trace1 = await client.reasoning.start_trace(session_id, "Staff a healthcare team")
        await client.reasoning.add_step(
            trace1.id,
            thought="Query the schema before joining Skill to Technology",
            generate_embedding=True,
        )
        await client.reasoning.complete_trace(trace1.id, outcome="Done", success=True)

        trace2 = await client.reasoning.start_trace(session_id, "Order a coffee")
        await client.reasoning.add_step(
            trace2.id,
            thought="Walk to the cafe",
            generate_embedding=True,
        )
        await client.reasoning.complete_trace(trace2.id, outcome="Done", success=True)

        results = await client.reasoning.search_steps(
            "query schema before joining",
            limit=5,
            success_only=True,
            threshold=0.0,
        )

        # We should get at least one result and the most-similar step
        # should be the schema-querying one.
        assert len(results) >= 1
        top = results[0]
        assert top.parent_task == "Staff a healthcare team"
        assert "schema" in top.step.thought.lower()
