"""Integration tests for ``client.consolidation`` (v0.5 P1.5)."""

from __future__ import annotations

import pytest


@pytest.mark.integration
@pytest.mark.asyncio
class TestDedupeEntities:
    async def test_dry_run_reports_candidates_without_mutation(self, clean_memory_client):
        client = clean_memory_client
        # Seed two near-identical entities of the same type with embeddings
        # close enough to fall above the 0.95 threshold.
        embedding = [0.1] * 1536
        # Slightly perturbed second embedding (similarity ≈ 1).
        embedding_b = [0.1] * 1536
        await client.graph.execute_write(
            """
            CREATE (a:Entity:Person {
                id: 'a-1', name: 'Jon Smith', type: 'PERSON',
                embedding: $emb
            })
            CREATE (b:Entity:Person {
                id: 'b-1', name: 'John Smith', type: 'PERSON',
                embedding: $emb_b
            })
            """,
            {"emb": embedding, "emb_b": embedding_b},
        )

        report = await client.consolidation.dedupe_entities(similarity_threshold=0.9, dry_run=True)

        assert report.dry_run is True
        assert report.kind == "dedupe_entities"
        assert report.run_id is None
        assert any(
            "Jon Smith" in c.description or "John Smith" in c.description for c in report.candidates
        )
        # No actions taken in a dry run.
        assert report.actions_taken == 0

        # Confirm no SAME_AS edge was written.
        rows = await client.graph.execute_read("MATCH ()-[r:SAME_AS]->() RETURN count(r) AS cnt")
        assert rows[0]["cnt"] == 0

    async def test_real_run_writes_same_as_and_audit_node(self, clean_memory_client):
        client = clean_memory_client
        embedding = [0.2] * 1536
        await client.graph.execute_write(
            """
            CREATE (a:Entity:Org {
                id: 'a-2', name: 'Acme Corp', type: 'ORGANIZATION',
                embedding: $emb
            })
            CREATE (b:Entity:Org {
                id: 'b-2', name: 'Acme Corp Inc', type: 'ORGANIZATION',
                embedding: $emb
            })
            """,
            {"emb": embedding},
        )

        report = await client.consolidation.dedupe_entities(similarity_threshold=0.9, dry_run=False)

        assert report.dry_run is False
        assert report.run_id is not None
        assert report.actions_taken >= 1

        # SAME_AS edge written.
        rows = await client.graph.execute_read(
            """
            MATCH (a:Entity {id: 'a-2'})-[r:SAME_AS]-(b:Entity {id: 'b-2'})
            RETURN r.status AS status, r.match_type AS match_type
            """
        )
        assert len(rows) >= 1
        assert rows[0]["status"] == "auto_consolidated"
        assert rows[0]["match_type"] == "embedding"

        # Audit node written.
        rows = await client.graph.execute_read(
            "MATCH (cr:ConsolidationRun {kind: 'dedupe_entities'}) RETURN cr.id AS id"
        )
        assert any(r["id"] == report.run_id for r in rows)


@pytest.mark.integration
@pytest.mark.asyncio
class TestSummarizeLongTraces:
    async def test_flags_long_traces(self, clean_memory_client, session_id):
        client = clean_memory_client
        trace = await client.reasoning.start_trace(session_id, "Long task")
        # Seed enough steps to exceed min_steps=3.
        for i in range(5):
            await client.reasoning.add_step(trace.id, thought=f"Step {i}", generate_embedding=False)

        report = await client.consolidation.summarize_long_traces(min_steps=3, dry_run=False)

        assert report.kind == "summarize_long_traces"
        assert report.dry_run is False
        assert report.actions_taken >= 1
        assert report.run_id is not None

        rows = await client.graph.execute_read(
            "MATCH (rt:ReasoningTrace {id: $id}) RETURN rt.summarization_pending AS pending",
            {"id": str(trace.id)},
        )
        assert rows[0]["pending"] is True

    async def test_skips_already_pending_traces(self, clean_memory_client, session_id):
        client = clean_memory_client
        trace = await client.reasoning.start_trace(session_id, "Already-pending")
        for i in range(4):
            await client.reasoning.add_step(trace.id, thought=f"Step {i}", generate_embedding=False)

        # First run flags it.
        await client.consolidation.summarize_long_traces(min_steps=3, dry_run=False)
        # Second run should find no candidates — idempotent.
        report = await client.consolidation.summarize_long_traces(min_steps=3, dry_run=False)
        assert report.candidate_count == 0


@pytest.mark.integration
@pytest.mark.asyncio
class TestArchiveExpiredConversations:
    async def test_archives_old_conversations(self, clean_memory_client):
        client = clean_memory_client
        # Seed an old conversation by direct insert.
        await client.graph.execute_write(
            """
            CREATE (c:Conversation {
                id: 'old-1', session_id: 'old-session',
                created_at: datetime() - duration({days: 90}),
                updated_at: datetime() - duration({days: 90})
            })
            """,
        )
        # And a recent one that should NOT be archived.
        await client.graph.execute_write(
            """
            CREATE (c:Conversation {
                id: 'recent-1', session_id: 'recent-session',
                created_at: datetime(),
                updated_at: datetime()
            })
            """
        )

        report = await client.consolidation.archive_expired_conversations(
            ttl_days=30, dry_run=False
        )

        assert report.actions_taken == 1
        assert report.candidates[0].payload["id"] == "old-1"

        rows = await client.graph.execute_read(
            "MATCH (c:Conversation {id: 'old-1'}) RETURN c.archived AS archived"
        )
        assert rows[0]["archived"] is True

        rows = await client.graph.execute_read(
            "MATCH (c:Conversation {id: 'recent-1'}) RETURN c.archived AS archived"
        )
        # Recent conversation should NOT be archived.
        assert rows[0]["archived"] is None or rows[0]["archived"] is False

    async def test_requires_explicit_ttl(self, clean_memory_client):
        client = clean_memory_client
        with pytest.raises(ValueError, match="ttl_days"):
            await client.consolidation.archive_expired_conversations()
