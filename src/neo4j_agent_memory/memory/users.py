"""User memory — first-class :User concept for multi-tenant deployments.

Exposed as ``client.users`` on :class:`MemoryClient`. Pair with the
``user_identifier=`` kwarg threaded through short-term, long-term, and
reasoning APIs to scope reads and writes by user.

Schema:

* ``(:User {id, identifier, attributes_json, created_at})``
* Unique constraint on ``User.identifier``.
* ``(:User)-[:HAS_CONVERSATION]->(:Conversation)`` is written by
  ``ShortTermMemory.create_conversation(user_identifier=...)``.
* ``(:User)-[:HAS_TRACE]->(:ReasoningTrace)`` is written by
  ``ReasoningMemory.start_trace(user_identifier=...)``.
* ``(:User)-[:HAS_PREFERENCE]->(:Preference)`` is written by
  ``LongTermMemory.add_preference(user_identifier=...)``.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from neo4j_agent_memory.schema.models import User

if TYPE_CHECKING:
    from neo4j_agent_memory.graph.client import Neo4jClient


_UPSERT_USER = """
MERGE (u:User {identifier: $identifier})
ON CREATE SET u.id = $id,
              u.attributes_json = $attributes_json,
              u.created_at = datetime()
ON MATCH SET u.attributes_json = coalesce($attributes_json, u.attributes_json)
RETURN u.id AS id, u.identifier AS identifier,
       u.attributes_json AS attributes_json
"""

_GET_USER = """
MATCH (u:User {identifier: $identifier})
RETURN u.id AS id, u.identifier AS identifier,
       u.attributes_json AS attributes_json
LIMIT 1
"""

_LIST_USERS = """
MATCH (u:User)
RETURN u.id AS id, u.identifier AS identifier,
       u.attributes_json AS attributes_json
ORDER BY u.created_at DESC
LIMIT $limit
"""


def _row_to_user(row: dict[str, Any]) -> User:
    return User(
        id=row["id"],
        identifier=row["identifier"],
        attributes=json.loads(row["attributes_json"]) if row.get("attributes_json") else {},
    )


class UserMemory:
    """User upsert / lookup / listing for multi-tenant deployments."""

    def __init__(self, client: Neo4jClient):
        self._client = client

    async def upsert_user(
        self,
        *,
        identifier: str,
        attributes: dict[str, Any] | None = None,
    ) -> User:
        """Create the user if missing; otherwise update attributes.

        Idempotent — safe to call on every request.
        """
        rows = await self._client.execute_write(
            _UPSERT_USER,
            {
                "identifier": identifier,
                "id": str(uuid4()),
                "attributes_json": json.dumps(attributes) if attributes else None,
            },
        )
        return _row_to_user(rows[0])

    async def get_user(self, identifier: str) -> User | None:
        """Return the user matching ``identifier`` or ``None``."""
        rows = await self._client.execute_read(_GET_USER, {"identifier": identifier})
        return _row_to_user(rows[0]) if rows else None

    async def list_users(self, *, limit: int = 100) -> list[User]:
        """List all users, newest first."""
        rows = await self._client.execute_read(_LIST_USERS, {"limit": limit})
        return [_row_to_user(r) for r in rows]
