"""Tests for the Cypher accessor — verified against live NAMS spec.

NAMS: ``POST /v1/query`` with body ``{"cypher": ..., "params": ...}``,
response ``{"columns": [...], "rows": [...], "stats": {...}}``.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
import respx

from neo4j_agent_memory.core.protocols import CypherQueryProtocol
from neo4j_agent_memory.core.query import BoltCypherQuery, is_read_only_query
from neo4j_agent_memory.nams import HttpTransport, NamsCypherQuery, StaticApiKeyAuth


class TestIsReadOnlyQuery:
    def test_simple_match(self):
        assert is_read_only_query("MATCH (n) RETURN n")

    def test_call_procedures_allowed(self):
        assert is_read_only_query("CALL db.index.vector.queryNodes('idx', 5, $emb)")

    def test_lowercase_match(self):
        assert is_read_only_query("match (n) return n")

    @pytest.mark.parametrize(
        "query",
        [
            "CREATE (n:Test)",
            "MERGE (n:Test {id: 1})",
            "MATCH (n) DELETE n",
            "MATCH (n) DETACH DELETE n",
            "MATCH (n) SET n.name = 'x'",
            "MATCH (n) REMOVE n.name",
            "DROP INDEX foo",
            "LOAD CSV FROM 'file' AS row",
            "MATCH (n) FOREACH (x IN n | SET x.a = 1)",
            "CALL { CREATE (m:X) }",
            "MATCH (n) CALL { WITH n DELETE n } IN TRANSACTIONS",
        ],
    )
    def test_writes_rejected(self, query: str):
        assert not is_read_only_query(query)

    def test_case_insensitive(self):
        assert not is_read_only_query("Match (n) Set n.foo = 1")


class TestBoltCypherQuery:
    @pytest.fixture
    def mock_client(self):
        client = MagicMock()
        client.execute_read = AsyncMock(return_value=[{"n": 1}])
        return client

    async def test_forwards_read_only_to_execute_read(self, mock_client):
        q = BoltCypherQuery(mock_client)
        result = await q.cypher("MATCH (n) RETURN n LIMIT 5")
        # User-written Cypher keeps the server's "does not exist" hints.
        mock_client.execute_read.assert_awaited_once_with(
            "MATCH (n) RETURN n LIMIT 5", parameters={}, report_unrecognized=True
        )
        assert result == [{"n": 1}]

    async def test_forwards_params(self, mock_client):
        q = BoltCypherQuery(mock_client)
        await q.cypher("MATCH (n {id: $id}) RETURN n", {"id": "abc"})
        mock_client.execute_read.assert_awaited_once_with(
            "MATCH (n {id: $id}) RETURN n", parameters={"id": "abc"}, report_unrecognized=True
        )

    async def test_rejects_write_query(self, mock_client):
        q = BoltCypherQuery(mock_client)
        with pytest.raises(ValueError, match="read-only"):
            await q.cypher("CREATE (n:Test)")
        mock_client.execute_read.assert_not_awaited()

    def test_satisfies_protocol(self, mock_client):
        q = BoltCypherQuery(mock_client)
        assert isinstance(q, CypherQueryProtocol)


@pytest.fixture
async def transport(nams_config):
    auth = StaticApiKeyAuth.from_config(nams_config)
    t = HttpTransport.from_config(nams_config, auth=auth)
    async with t:
        yield t


@pytest.fixture
def nams_query(transport) -> NamsCypherQuery:
    return NamsCypherQuery(transport)


class TestNamsCypherQuery:
    @respx.mock
    async def test_basic_returns_rows(self, nams_query):
        route = respx.post("https://memory.test/v1/query").respond(
            200,
            json={"columns": ["n"], "rows": [{"n": 1}, {"n": 2}], "stats": {}},
        )
        result = await nams_query.cypher("MATCH (n) RETURN n LIMIT 2")
        assert result == [{"n": 1}, {"n": 2}]
        body = json.loads(route.calls[0].request.content)
        assert body == {"cypher": "MATCH (n) RETURN n LIMIT 2", "params": {}}

    @respx.mock
    async def test_forwards_params(self, nams_query):
        respx.post("https://memory.test/v1/query").respond(
            200, json={"columns": [], "rows": [], "stats": {}}
        )
        await nams_query.cypher("MATCH (n {id: $id}) RETURN n", {"id": "abc"})

    @respx.mock
    async def test_bare_list_response_supported(self, nams_query):
        respx.post("https://memory.test/v1/query").respond(200, json=[{"n": 1}])
        result = await nams_query.cypher("MATCH (n) RETURN n")
        assert result == [{"n": 1}]

    @respx.mock
    async def test_empty_response(self, nams_query):
        respx.post("https://memory.test/v1/query").respond(
            200, json={"columns": [], "rows": [], "stats": {}}
        )
        result = await nams_query.cypher("MATCH (n) RETURN n LIMIT 0")
        assert result == []

    async def test_rejects_write_locally_no_request(self, nams_query):
        with pytest.raises(ValueError, match="read-only"):
            await nams_query.cypher("CREATE (n:Test)")

    @respx.mock
    async def test_bridge_protocol(self, bridge_config):
        auth = StaticApiKeyAuth.from_config(bridge_config)
        route = respx.post("https://memory.test/query").respond(200, json={"rows": []})
        async with HttpTransport.from_config(bridge_config, auth=auth) as t:
            q = NamsCypherQuery(t)
            await q.cypher("MATCH (n) RETURN n LIMIT 1")
        assert route.called

    def test_satisfies_protocol(self, nams_query):
        assert isinstance(nams_query, CypherQueryProtocol)


class TestNamsBackendQueryAccessor:
    @respx.mock
    async def test_query_accessor_exposed(self, nams_config):
        from neo4j_agent_memory.nams import NamsBackend

        respx.post("https://memory.test/v1/query").respond(200, json={"rows": [{"count": 42}]})
        async with NamsBackend.from_config(nams_config) as backend:
            assert isinstance(backend.query, NamsCypherQuery)
            result = await backend.query.cypher("MATCH (n) RETURN count(n) AS count")
        assert result == [{"count": 42}]

    @respx.mock
    async def test_query_shares_transport_with_memory_layers(self, nams_config):
        from neo4j_agent_memory.nams import NamsBackend

        backend = NamsBackend.from_config(nams_config)
        assert backend.query._transport is backend.transport
