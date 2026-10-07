"""The library's own queries do not ask the server for "does not exist" hints.

A fresh database has no ``aliases`` property, no ``:SchemaMigration`` label and
no ``HAS_VERSION`` relationship yet, so the library's (correct) queries made
Neo4j log one UNRECOGNIZED warning each. User-written Cypher keeps them.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import SecretStr

from neo4j_agent_memory.config.settings import Neo4jConfig
from neo4j_agent_memory.graph.client import _UNRECOGNIZED_FILTER, Neo4jClient


def _client() -> tuple[Neo4jClient, MagicMock]:
    client = Neo4jClient(Neo4jConfig(password=SecretStr("x")))
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    session.execute_read = AsyncMock(return_value=[])
    session.execute_write = AsyncMock(return_value=[])
    driver = MagicMock()
    driver.session = MagicMock(return_value=session)
    client._driver = driver
    return client, driver


def test_the_filter_disables_the_unrecognized_classification():
    ((key, values),) = _UNRECOGNIZED_FILTER.items()
    assert key in {"notifications_disabled_classifications", "notifications_disabled_categories"}
    assert [str(getattr(value, "value", value)) for value in values] == ["UNRECOGNIZED"]


@pytest.mark.asyncio
async def test_library_reads_and_writes_filter_unrecognized_notifications():
    client, driver = _client()

    await client.execute_read("MATCH (e:Entity) RETURN e.aliases")
    await client.execute_write("MERGE (m:SchemaMigration {name: 'x'})")

    for call in driver.session.call_args_list:
        assert call.kwargs == {"database": "neo4j", **_UNRECOGNIZED_FILTER}


@pytest.mark.asyncio
async def test_user_cypher_and_raw_sessions_keep_the_hints():
    client, driver = _client()

    await client.execute_read("MATCH (n:Typo) RETURN n", report_unrecognized=True)
    client.session()

    for call in driver.session.call_args_list:
        assert call.kwargs == {"database": "neo4j"}
