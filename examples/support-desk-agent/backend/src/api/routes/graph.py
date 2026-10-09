"""The Graph panel: a thread's slice of the memory graph, and node expansion.

Node ids are the nodes' ``id`` properties (conversations, messages and
entities all carry one), so the frontend can expand any node it was given.
Relationship captions are the typed relation name (``r.type``) for
``RELATED_TO`` and the relationship type otherwise.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query

from src.api import MemoryClientDep
from src.api.schemas import Graph, GraphNode, GraphRelationship
from src.memory import jsonable
from src.ontology import entity_labels

router = APIRouter()

#: Cap on the seeded overview (no ``thread_id``).
OVERVIEW_NODE_LIMIT = 300
#: Cap on one expansion.
NEIGHBOR_LIMIT = 50
#: Cap on the record context added to a thread's graph.
CONTEXT_LIMIT = 60

THREAD_NODES = """
MATCH (c:Conversation {session_id: $session_id})
OPTIONAL MATCH (c)-[:HAS_MESSAGE]->(m:Message)
OPTIONAL MATCH (m)-[:MENTIONS]->(e:Entity)
WHERE NOT 'merged_into' IN keys(e)
RETURN c AS conversation, labels(c) AS conversation_labels,
       collect(DISTINCT {node: m, labels: labels(m)}) AS messages,
       collect(DISTINCT {node: e, labels: labels(e)}) AS entities
"""

THREAD_EDGES = """
MATCH (c:Conversation {session_id: $session_id})-[h:HAS_MESSAGE]->(m:Message)
RETURN elementId(h) AS id, c.id AS from, m.id AS to, type(h) AS type, null AS relation,
       properties(h) AS properties
UNION
MATCH (:Conversation {session_id: $session_id})-[:HAS_MESSAGE]->(m:Message)-[r:MENTIONS]->(e:Entity)
WHERE NOT 'merged_into' IN keys(e)
RETURN elementId(r) AS id, m.id AS from, e.id AS to, type(r) AS type, null AS relation,
       properties(r) AS properties
"""

#: The records one typed edge away from what a thread mentions, following
#: ownership outwards (a customer's orders, an order's lines, a line's product,
#: a warranty's line) plus the customer who placed a mentioned order. Not the
#: other way round: a mentioned product would pull in every order line, from
#: every order, that contains it.
RECORD_CONTEXT = """
CALL (*) {
  MATCH (a:Entity)-[r:RELATED_TO]->(x:Entity)
  WHERE a.id IN $ids AND r.type IN ['PLACED', 'CONTAINS', 'OF_PRODUCT', 'COVERS', 'REPLACED_BY']
  RETURN x
  UNION
  MATCH (x:Entity)-[:RELATED_TO {type: 'PLACED'}]->(a:Entity)
  WHERE a.id IN $ids
  RETURN x
}
WITH DISTINCT x
WHERE NOT x.id IN $ids AND NOT 'merged_into' IN keys(x)
LIMIT $limit
RETURN x AS node, labels(x) AS labels
"""

#: Every record entity and every entity a seeded conversation mentions.
SEEDED_ENTITIES = """
MATCH (e:Entity)
WHERE NOT 'merged_into' IN keys(e)
  AND ('record_kind' IN keys(e)
       OR EXISTS {
         MATCH (c:Conversation)-[:HAS_MESSAGE]->(:Message)-[:MENTIONS]->(e)
         WHERE c.session_id STARTS WITH 'seed-'
       })
WITH e
ORDER BY e.type, e.name
LIMIT $limit
RETURN collect({node: e, labels: labels(e)}) AS entities
"""

EDGES_AMONG = """
MATCH (a:Entity)-[r:RELATED_TO|SAME_AS]->(b:Entity)
WHERE a.id IN $ids AND b.id IN $ids
RETURN elementId(r) AS id, a.id AS from, b.id AS to, type(r) AS type, r.type AS relation,
       properties(r) AS properties
"""

#: The node itself, under whichever of the three labels it carries.
NODE_BY_ID = """
MATCH (n:Entity {id: $id}) RETURN n AS node, labels(n) AS labels
UNION
MATCH (n:Message {id: $id}) RETURN n AS node, labels(n) AS labels
UNION
MATCH (n:Conversation {id: $id}) RETURN n AS node, labels(n) AS labels
"""

NEIGHBORS = """
MATCH (n {id: $id})-[r:RELATED_TO|SAME_AS|MENTIONS|HAS_MESSAGE]-(x)
WHERE (x:Entity OR x:Message OR x:Conversation) AND NOT 'merged_into' IN keys(x)
RETURN x AS node, labels(x) AS labels,
       elementId(r) AS id, startNode(r).id AS from, endNode(r).id AS to,
       type(r) AS type, CASE WHEN type(r) = 'RELATED_TO' THEN r.type END AS relation,
       properties(r) AS properties
LIMIT $limit
"""


NodeKind = Literal["entity", "message", "conversation"]


def _kind(labels: list[str]) -> NodeKind:
    if "Conversation" in labels:
        return "conversation"
    if "Message" in labels:
        return "message"
    return "entity"


def _caption(node: dict[str, Any], kind: str) -> str:
    if kind == "conversation":
        return str(node.get("title") or node.get("session_id") or "Conversation")
    if kind == "message":
        content = str(node.get("content") or "")
        snippet = content if len(content) <= 48 else content[:47].rstrip() + "…"
        return f"{node.get('role', 'message')}: {snippet}"
    return str(node.get("name") or node.get("id") or "")


def _node(node: dict[str, Any] | None, labels: list[str] | None) -> GraphNode | None:
    if not node or not node.get("id"):
        return None
    labels = list(labels or [])
    kind = _kind(labels)
    properties = jsonable(node)
    return GraphNode(
        id=str(node["id"]),
        caption=_caption(node, kind),
        labels=entity_labels(labels) if kind == "entity" else labels,
        kind=kind,
        properties=properties if isinstance(properties, dict) else {},
    )


def _relationship(row: dict[str, Any]) -> GraphRelationship | None:
    if not row.get("from") or not row.get("to"):
        return None
    properties = jsonable(row.get("properties") or {})
    return GraphRelationship(
        id=str(row["id"]),
        from_=str(row["from"]),
        to=str(row["to"]),
        type=str(row["type"]),
        caption=str(row.get("relation") or row["type"]),
        properties=properties if isinstance(properties, dict) else {},
    )


def _graph(nodes: list[GraphNode | None], rows: list[dict[str, Any]]) -> Graph:
    """De-duplicate, and keep only relationships whose endpoints are present."""
    by_id: dict[str, GraphNode] = {}
    for node in nodes:
        if node is not None:
            by_id.setdefault(node.id, node)
    relationships: dict[str, GraphRelationship] = {}
    for row in rows:
        relationship = _relationship(row)
        if relationship and relationship.from_ in by_id and relationship.to in by_id:
            relationships.setdefault(relationship.id, relationship)
    return Graph(nodes=list(by_id.values()), relationships=list(relationships.values()))


@router.get("/graph", response_model=Graph, response_model_by_alias=True)
async def thread_graph(
    client: MemoryClientDep, thread_id: str | None = Query(default=None)
) -> Graph:
    """The thread's conversation, messages, mentioned entities, their record context.

    The context is what is one typed relationship away from a mentioned entity
    (an order's lines, a line's product), with the typed and ``SAME_AS`` edges
    among all of it. Without ``thread_id``: every record entity and every
    entity the seeded conversations mention (at most 300), with the typed
    relationships among them.
    """
    if not thread_id:
        rows = await client.query.cypher(SEEDED_ENTITIES, {"limit": OVERVIEW_NODE_LIMIT})
        items = rows[0]["entities"] if rows else []
        nodes = [_node(item["node"], item["labels"]) for item in items]
        ids = [node.id for node in nodes if node is not None]
        return _graph(nodes, await client.query.cypher(EDGES_AMONG, {"ids": ids}))

    rows = await client.query.cypher(THREAD_NODES, {"session_id": thread_id})
    if not rows:
        raise HTTPException(status_code=404, detail=f"Thread {thread_id!r} not found")
    row = rows[0]
    nodes = [_node(row["conversation"], row["conversation_labels"])]
    nodes += [_node(item["node"], item["labels"]) for item in row["messages"]]
    mentioned = [_node(item["node"], item["labels"]) for item in row["entities"]]
    mentioned_ids = [node.id for node in mentioned if node is not None]
    context = [
        _node(item["node"], item["labels"])
        for item in await client.query.cypher(
            RECORD_CONTEXT, {"ids": mentioned_ids, "limit": CONTEXT_LIMIT}
        )
    ]
    entity_ids = mentioned_ids + [node.id for node in context if node is not None]
    edges = await client.query.cypher(THREAD_EDGES, {"session_id": thread_id})
    edges += await client.query.cypher(EDGES_AMONG, {"ids": entity_ids})
    return _graph(nodes + mentioned + context, edges)


@router.get("/graph/neighbors/{node_id}", response_model=Graph, response_model_by_alias=True)
async def neighbors(client: MemoryClientDep, node_id: str) -> Graph:
    """One node and up to 50 neighbours via RELATED_TO, SAME_AS, MENTIONS or HAS_MESSAGE."""
    found = await client.query.cypher(NODE_BY_ID, {"id": node_id})
    if not found:
        raise HTTPException(status_code=404, detail=f"Node {node_id!r} not found")
    rows = await client.query.cypher(NEIGHBORS, {"id": node_id, "limit": NEIGHBOR_LIMIT})
    nodes = [_node(found[0]["node"], found[0]["labels"])]
    nodes += [_node(row["node"], row["labels"]) for row in rows]
    return _graph(nodes, rows)
