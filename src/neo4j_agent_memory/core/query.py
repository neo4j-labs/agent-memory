"""Shared Cypher accessor — bolt impl + read-only query validator.

The :class:`CypherQueryProtocol` has two implementations: the bolt one
lives here (:class:`BoltCypherQuery` wraps :class:`Neo4jClient`), and
the NAMS one in :mod:`neo4j_agent_memory.nams.query` (wraps the HTTP
transport). Both enforce read-only via :func:`is_read_only_query`.

The validator was previously private to :mod:`mcp._tools`; relocating it
here makes it the single source of truth so the MCP ``graph_query``
tool, the bolt :meth:`Neo4jClient.execute_read` wrapper, and the NAMS
``POST /v1/query`` wrapper all agree on what "read-only" means.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from neo4j_agent_memory.graph.client import Neo4jClient


# Pattern list mirrors the historic MCP guard — keep additions case-insensitive
# (we uppercase the query before matching). When extending this list, run the
# tests in tests/unit/mcp/test_fastmcp_tools.py::TestIsReadOnlyQuery first.
_WRITE_PATTERNS: list[str] = [
    r"\bCREATE\b",
    r"\bMERGE\b",
    r"\bDELETE\b",
    r"\bDETACH\s+DELETE\b",
    r"\bSET\b",
    r"\bREMOVE\b",
    r"\bDROP\b",
    r"\bLOAD\s+CSV\b",
    r"\bFOREACH\b",
    r"\bCALL\s+\{",  # subquery write candidate; CALL apoc.x() etc. are fine.
    r"\bIN\s+TRANSACTIONS\b",
]


def is_read_only_query(query: str) -> bool:
    """Return True if ``query`` looks read-only.

    Conservative heuristic — uppercases the query then rejects on any of
    :data:`_WRITE_PATTERNS`. Read-only ``CALL`` invocations
    (``CALL apoc.x.y(...)``, ``CALL db.index.vector.queryNodes(...)``)
    are accepted because they don't match ``CALL {`` (subquery form).
    """
    query_upper = query.upper()
    return all(not re.search(pattern, query_upper) for pattern in _WRITE_PATTERNS)


class BoltCypherQuery:
    """Bolt implementation of :class:`CypherQueryProtocol`.

    Forwards to :meth:`Neo4jClient.execute_read` after read-only
    validation. Same client-side guard as the MCP ``graph_query`` tool
    historically used, so behavior is unchanged for existing users.
    """

    __slots__ = ("_client",)

    def __init__(self, client: Neo4jClient) -> None:
        self._client = client

    async def cypher(
        self,
        query: str,
        params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Execute a read-only Cypher query against Neo4j."""
        if not is_read_only_query(query):
            raise ValueError(
                "Only read-only Cypher queries are allowed. "
                "Detected write keywords (CREATE/MERGE/DELETE/SET/...). "
                "Use the appropriate memory-layer method for writes."
            )
        # User-written Cypher keeps the server's "label / property does not
        # exist" hints; the library's own queries turn them off.
        return await self._client.execute_read(
            query, parameters=params or {}, report_unrecognized=True
        )


__all__ = [
    "BoltCypherQuery",
    "is_read_only_query",
]
