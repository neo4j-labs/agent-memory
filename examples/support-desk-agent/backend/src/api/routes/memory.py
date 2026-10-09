"""The Memory panel: a thread's entities and the resolution review queue.

Ingest-time resolution merges a mention onto an existing node when it is sure
(exact name, alias) and writes a pending ``SAME_AS`` pair when it is not — a
first name next to a full name lands there. The panel confirms (merge) or
rejects each pair through ``long_term.review_duplicate()``.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query

from src.api import MemoryClientDep
from src.api.schemas import (
    EntityStub,
    MemoryContext,
    MemoryEntity,
    Ok,
    PendingDuplicate,
    ReviewRequest,
)
from src.ontology import entity_labels

router = APIRouter()

#: Entities the thread's messages mention. Merged-away duplicates are left out:
#: after a confirmed merge the survivor carries their mentions.
THREAD_ENTITIES = """
MATCH (:Conversation {session_id: $session_id})-[:HAS_MESSAGE]->(m:Message)-[:MENTIONS]->(e:Entity)
WHERE NOT 'merged_into' IN keys(e)
WITH e, count(DISTINCT m) AS mentions
RETURN e.id AS id, e.name AS name, e.type AS type, e.subtype AS subtype,
       labels(e) AS labels, coalesce(properties(e).aliases, []) AS aliases, mentions
ORDER BY e.type, e.name
"""

#: Pending SAME_AS pairs; with a thread, those with an endpoint the thread mentions.
PENDING_PAIRS = """
MATCH (a:Entity)-[r:SAME_AS]->(b:Entity)
WHERE r.status = 'pending'
  AND ($session_id IS NULL OR EXISTS {
        MATCH (:Conversation {session_id: $session_id})-[:HAS_MESSAGE]->(:Message)
              -[:MENTIONS]->(x:Entity)
        WHERE x = a OR x = b
      })
RETURN a.id AS source_id, a.name AS source_name, labels(a) AS source_labels,
       b.id AS target_id, b.name AS target_name, labels(b) AS target_labels,
       r.confidence AS confidence, r.match_type AS match_type
ORDER BY confidence DESC
LIMIT 100
"""


@router.get("/memory/context", response_model=MemoryContext)
async def memory_context(
    client: MemoryClientDep, thread_id: str | None = Query(default=None)
) -> MemoryContext:
    """Entities mentioned in ``thread_id`` plus the pending review pairs touching them.

    Without ``thread_id``: no entities, and every pending pair.
    """
    entities: list[MemoryEntity] = []
    if thread_id:
        rows = await client.query.cypher(THREAD_ENTITIES, {"session_id": thread_id})
        entities = [
            MemoryEntity(
                id=row["id"],
                name=row["name"],
                type=row.get("type"),
                subtype=row.get("subtype"),
                labels=entity_labels(row.get("labels") or []),
                aliases=list(row.get("aliases") or []),
                mentions=int(row["mentions"]),
            )
            for row in rows
        ]
    pairs = await client.query.cypher(PENDING_PAIRS, {"session_id": thread_id or None})
    return MemoryContext(
        entities=entities,
        pending_duplicates=[
            PendingDuplicate(
                source=EntityStub(
                    id=row["source_id"],
                    name=row["source_name"],
                    labels=entity_labels(row.get("source_labels") or []),
                ),
                target=EntityStub(
                    id=row["target_id"],
                    name=row["target_name"],
                    labels=entity_labels(row.get("target_labels") or []),
                ),
                confidence=float(row.get("confidence") or 0.0),
                match_type=row.get("match_type"),
            )
            for row in pairs
        ],
    )


@router.post("/memory/duplicates/review", response_model=Ok)
async def review_duplicate(client: MemoryClientDep, request: ReviewRequest) -> Ok:
    """Confirm (merge ``source`` into ``target``) or reject a pending pair."""
    try:
        source_id, target_id = UUID(request.source_id), UUID(request.target_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Entity ids must be UUIDs") from exc
    done = await client.long_term.review_duplicate(source_id, target_id, confirm=request.confirm)
    if not done:
        raise HTTPException(status_code=404, detail="No such pair to merge")
    return Ok()
