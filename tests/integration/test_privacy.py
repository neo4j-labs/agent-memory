"""Integration tests for v0.5 P2.2 (privacy helpers)."""

from __future__ import annotations

import pytest


@pytest.mark.integration
@pytest.mark.asyncio
class TestRecordReadAudit:
    async def test_audit_node_recorded(self, clean_memory_client):
        client = clean_memory_client
        await client.users.upsert_user(identifier="sara@omg.com")

        audit_id = await client.consolidation.record_read_audit(
            "What does Sara prefer?",
            user_identifier="sara@omg.com",
            kind="preference.read",
            result_count=3,
            metadata={"endpoint": "/api/preferences"},
        )

        assert audit_id is not None

        rows = await client.graph.execute_read(
            """
            MATCH (a:MemoryReadAudit {id: $id})
            OPTIONAL MATCH (u:User)-[:PERFORMED_READ]->(a)
            RETURN a.kind AS kind, a.query AS query, a.result_count AS rc,
                   u.identifier AS user_identifier
            """,
            {"id": audit_id},
        )
        assert len(rows) == 1
        assert rows[0]["kind"] == "preference.read"
        assert rows[0]["query"] == "What does Sara prefer?"
        assert rows[0]["rc"] == 3
        assert rows[0]["user_identifier"] == "sara@omg.com"

    async def test_audit_without_user(self, clean_memory_client):
        client = clean_memory_client
        await client.consolidation.record_read_audit("anonymous read")

        rows = await client.graph.execute_read(
            "MATCH (a:MemoryReadAudit) "
            "WHERE a.query = 'anonymous read' "
            "RETURN a.user_identifier AS uid"
        )
        assert rows[0]["uid"] is None
