"""The agent's memory tools, as plain async functions over a connected client.

The STATE-Bench tools (``get_order``, ``process_return`` ...) run the vendored
environment against the records in the graph (:mod:`src.statebench.world`).
These are the tools that read *memory* instead:

- ``recall_similar_tasks``: reasoning memory, earlier traces for a similar task;
- ``find_customer``: a customer by name, email or id, with their orders;
- ``search_support_history``: earlier conversations, by meaning;
- ``get_ontology``: the active ontology revision.

Every read goes through ``client.query.cypher()``, the library's read-only
accessor. Labels are never hard-coded: each lookup asks the client's ontology
which label a role (``PERSON:CUSTOMER`` and so on) has, so the tools work
before and after the ``Warranty`` -> ``WarrantyCoverage`` rename.

Every result that names entities carries ``touched`` — ``[{"id", "name",
"type"}]`` — which the chat route records as
``(:ReasoningStep)-[:TOUCHED]->(:Entity)`` audit edges.
"""

from __future__ import annotations

import json
from typing import Any

from neo4j_agent_memory import BoltMemoryClient
from neo4j_agent_memory.core.exceptions import NotSupportedError
from neo4j_agent_memory.ontology import OntologyDocument
from src.memory import iso, parse_json_map, resolved_binding
from src.ontology import CUSTOMER, ORDER, ORDER_LINE, PRODUCT, entity_labels, role_label

#: Cap on audit edges per tool call, so one broad listing cannot flood the graph.
MAX_TOUCHED = 12

#: Similarity floor for ``recall_similar_tasks`` (MiniLM cosine).
RECALL_THRESHOLD = 0.6

#: Messages ``search_support_history`` returns, and how many extra it fetches
#: so that leaving the current conversation out still leaves that many.
MESSAGE_HITS = 8
MESSAGE_HITS_SLACK = 6

FIND_CUSTOMER = """
MATCH (e:Entity)
WHERE $label IN labels(e) AND NOT 'merged_into' IN keys(e)
  AND (toLower(e.name) = toLower($name)
       OR toLower(e.name) STARTS WITH toLower($name) + ' '
       OR any(alias IN coalesce(properties(e).aliases, []) WHERE toLower(alias) = toLower($name)))
RETURN e.id AS id, e.name AS name, e.type AS type, properties(e).record AS record,
       coalesce(properties(e).aliases, []) AS aliases,
       CASE WHEN toLower(e.name) = toLower($name) THEN 0
            WHEN any(alias IN coalesce(properties(e).aliases, [])
                     WHERE toLower(alias) = toLower($name)) THEN 1
            ELSE 2 END AS rank
ORDER BY rank, e.name
LIMIT 5
"""

CUSTOMER_ORDERS = """
MATCH (c:Entity {id: $id})-[r:RELATED_TO {type: 'PLACED'}]->(o:Entity)
WHERE NOT 'merged_into' IN keys(o)
OPTIONAL MATCH (o)-[:RELATED_TO {type: 'CONTAINS'}]->(i:Entity)
               -[:RELATED_TO {type: 'OF_PRODUCT'}]->(p:Entity)
RETURN o.id AS id, o.name AS name, o.type AS type, properties(o).record AS record,
       collect(DISTINCT p.name) AS products
ORDER BY o.name
"""

CONVERSATIONS_MENTIONING = """
MATCH (c:Conversation)-[:HAS_MESSAGE]->(m:Message)-[:MENTIONS]->(e:Entity {id: $id})
WITH c, count(m) AS mentions, max(m.timestamp) AS last_mentioned
RETURN c.session_id AS session_id, c.title AS title, mentions, last_mentioned
ORDER BY last_mentioned DESC
LIMIT 10
"""

PENDING_SAME_AS = """
MATCH (e:Entity {id: $id})-[r:SAME_AS]-(other:Entity)
WHERE r.status = 'pending'
RETURN other.id AS id, other.name AS name, r.confidence AS confidence,
       r.match_type AS match_type
ORDER BY confidence DESC
"""

GET_BY_IDS = """
MATCH (e:Entity) WHERE e.id IN $ids
RETURN e.id AS id, labels(e) AS labels, properties(e).merged_into AS merged_into
"""

MESSAGE_SESSIONS = """
MATCH (c:Conversation)-[:HAS_MESSAGE]->(m:Message)
WHERE m.id IN $ids
RETURN m.id AS id, c.session_id AS session_id, c.title AS title
"""

#: The orders and products each conversation mentions anywhere. A search hit is
#: one message, and the order it is about is often named in another one.
CONVERSATION_RECORDS = """
MATCH (c:Conversation)-[:HAS_MESSAGE]->(:Message)-[:MENTIONS]->(e:Entity)
WHERE c.session_id IN $sessions AND NOT 'merged_into' IN keys(e)
  AND any(label IN labels(e) WHERE label IN $labels)
RETURN c.session_id AS session_id, e.id AS id, e.name AS name, e.type AS type,
       labels(e) AS labels
ORDER BY e.name
"""

ENTITY_LABELS = """
MATCH (e:Entity) WHERE e.id IN $ids
RETURN e.id AS id, labels(e) AS labels
"""

TRACE_METADATA = """
MATCH (rt:ReasoningTrace) WHERE rt.id IN $ids
RETURN rt.id AS id, properties(rt).metadata AS metadata
"""


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _document(client: BoltMemoryClient) -> OntologyDocument | None:
    return client.ontology_document


def _ref(row: dict[str, Any]) -> dict[str, Any]:
    """An entity summary: ``{"id", "name", "type"}``."""
    return {"id": row["id"], "name": row["name"], "type": row.get("type")}


def _touched(*groups: list[dict[str, Any]] | dict[str, Any] | None) -> list[dict[str, Any]]:
    """Collect ``{"id", "name", "type"}`` refs, de-duplicated and capped."""
    seen: set[str] = set()
    refs: list[dict[str, Any]] = []
    for group in groups:
        if group is None:
            continue
        for item in [group] if isinstance(group, dict) else group:
            entity_id = item.get("id")
            if not entity_id or entity_id in seen:
                continue
            seen.add(entity_id)
            refs.append({"id": entity_id, "name": item.get("name"), "type": item.get("type")})
            if len(refs) >= MAX_TOUCHED:
                return refs
    return refs


def _snippet(text: str | None, limit: int = 220) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _record(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, str) or not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


async def _conversations(client: BoltMemoryClient, entity_id: str) -> list[dict[str, Any]]:
    rows = await client.query.cypher(CONVERSATIONS_MENTIONING, {"id": entity_id})
    return [
        {
            "session_id": row["session_id"],
            "title": row.get("title"),
            "mentions": row["mentions"],
            "last_mentioned": iso(row.get("last_mentioned")),
        }
        for row in rows
    ]


async def _trace_metadata(client: BoltMemoryClient, ids: list[str]) -> dict[str, dict[str, Any]]:
    if not ids:
        return {}
    rows = await client.query.cypher(TRACE_METADATA, {"ids": ids})
    return {row["id"]: parse_json_map(row.get("metadata")) for row in rows}


async def _conversation_records(
    client: BoltMemoryClient, session_ids: set[str]
) -> dict[str, dict[str, list[dict[str, Any]]]]:
    """``{session_id: {"orders": [...], "products": [...]}}`` under the active labels."""
    if not session_ids:
        return {}
    document = _document(client)
    order, product = role_label(document, ORDER), role_label(document, PRODUCT)
    rows = await client.query.cypher(
        CONVERSATION_RECORDS, {"sessions": sorted(session_ids), "labels": [order, product]}
    )
    records: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for row in rows:
        kind = "orders" if order in row["labels"] else "products"
        bucket = records.setdefault(row["session_id"], {"orders": [], "products": []})[kind]
        bucket.append(_ref(row))
    return records


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


async def recall_similar_tasks(client: BoltMemoryClient, task: str) -> dict[str, Any]:
    """Reasoning-memory retrieval: earlier successful traces for a similar task."""
    traces = await client.reasoning.get_similar_traces(
        task, limit=3, success_only=True, threshold=RECALL_THRESHOLD
    )
    metadata = await _trace_metadata(client, [str(trace.id) for trace in traces])
    similar: list[dict[str, Any]] = []
    for trace in traces:
        full = await client.reasoning.get_trace_with_steps(trace.id)
        steps = full.steps if full is not None else []
        meta = metadata.get(str(trace.id), {})
        similar.append(
            {
                "id": str(trace.id),
                "task": trace.task,
                "similarity": round(float(trace.metadata.get("similarity", 0.0)), 3),
                "outcome": _snippet(trace.outcome, 300),
                "seeded": bool(meta.get("seeded")),
                "task_type": meta.get("task_type"),
                "steps": [
                    {
                        "tool": call.tool_name,
                        "arguments": call.arguments,
                    }
                    for step in steps
                    for call in step.tool_calls
                ],
            }
        )
    return {"task": task, "similar_tasks": similar}


async def find_customer(client: BoltMemoryClient, name: str) -> dict[str, Any]:
    """A customer by full name, first name, email or customer id, with their orders."""
    document = _document(client)
    rows = await client.query.cypher(
        FIND_CUSTOMER, {"label": role_label(document, CUSTOMER), "name": name.strip()}
    )
    if not rows:
        return {"query": name, "found": False, "customer": None, "touched": []}
    row = dict(rows[0])
    record = _record(row.get("record"))
    orders = [
        {
            **_ref(order),
            "status": _record(order.get("record")).get("status"),
            "order_date": _record(order.get("record")).get("order_date"),
            "products": [product for product in order.get("products") or [] if product],
        }
        for order in await client.query.cypher(CUSTOMER_ORDERS, {"id": row["id"]})
    ]
    pending = await client.query.cypher(PENDING_SAME_AS, {"id": row["id"]})
    return {
        "query": name,
        "found": True,
        "customer": {
            "id": row["id"],
            "name": row["name"],
            "customer_id": record.get("customer_id"),
            "membership_tier": record.get("membership_tier"),
            "aliases": list(row.get("aliases") or []),
        },
        "other_matches": [{"name": other["name"]} for other in rows[1:]],
        "possible_duplicates": [dict(p) for p in pending],
        "orders": orders,
        "conversations": await _conversations(client, row["id"]),
        "touched": _touched(_ref(row), orders),
    }


async def search_support_history(
    client: BoltMemoryClient, query: str, *, exclude_session_id: str | None = None
) -> dict[str, Any]:
    """Semantic search over earlier sessions' messages, plus matching entities.

    ``exclude_session_id`` leaves the conversation the agent is answering in
    out: its messages, including the question just asked, are already in the
    model's context. Each hit lists the orders and products its conversation
    mentions.
    """
    found = await client.short_term.search_messages(
        query, limit=MESSAGE_HITS + MESSAGE_HITS_SLACK, threshold=0.5
    )
    sessions = {
        row["id"]: row
        for row in await client.query.cypher(MESSAGE_SESSIONS, {"ids": [str(m.id) for m in found]})
    }

    def session_of(message_id: object) -> str | None:
        session_id = sessions.get(str(message_id), {}).get("session_id")
        return str(session_id) if session_id else None

    messages = [
        message
        for message in found
        if exclude_session_id is None or session_of(message.id) != exclude_session_id
    ][:MESSAGE_HITS]
    records = await _conversation_records(
        client, {s for s in (session_of(m.id) for m in messages) if s}
    )
    entities = await client.long_term.search_entities(query, limit=8, threshold=0.5)
    stored = {
        row["id"]: row
        for row in await client.query.cypher(GET_BY_IDS, {"ids": [str(e.id) for e in entities]})
    }
    # A merged-away duplicate keeps its embedding; report the survivor only.
    entities = [e for e in entities if not stored.get(str(e.id), {}).get("merged_into")]
    labels = {entity_id: entity_labels(row["labels"]) for entity_id, row in stored.items()}
    entity_rows = [
        {
            "id": str(entity.id),
            "name": entity.name,
            "type": entity.type,
            "subtype": entity.subtype,
            "labels": labels.get(str(entity.id), []),
            "similarity": round(float(entity.metadata.get("similarity", 0.0)), 3),
        }
        for entity in entities
    ]
    return {
        "query": query,
        "messages": [
            {
                "message_id": str(message.id),
                "session_id": session_of(message.id),
                "title": sessions.get(str(message.id), {}).get("title"),
                "role": message.role.value,
                "snippet": _snippet(message.content),
                "similarity": round(float(message.metadata.get("similarity", 0.0)), 3),
                # What the rest of that conversation names.
                "conversation_orders": records.get(session_of(message.id) or "", {}).get(
                    "orders", []
                ),
                "conversation_products": records.get(session_of(message.id) or "", {}).get(
                    "products", []
                ),
            }
            for message in messages
        ],
        "entities": entity_rows,
        "touched": _touched(entity_rows),
    }


async def get_ontology(client: BoltMemoryClient) -> dict[str, Any]:
    """The active ontology revision, and what this client resolved at connect time."""
    try:
        active = await client.ontology.get_active()
    except NotSupportedError:
        active_view: dict[str, Any] | None = None
    else:
        document = active.document
        active_view = {
            "domain_id": document.domain.id,
            "revision": active.revision,
            "validation_mode": active.validation_mode,
            "labels": document.labels(),
            "relationships": [
                f"({rel.source})-[:{rel.type}]->({rel.target})" for rel in document.relationships
            ],
        }
    return {
        "active": active_view,
        "client": resolved_binding(client),
        "order_line_label": role_label(_document(client), ORDER_LINE),
    }


async def entity_refs(
    client: BoltMemoryClient, touched: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Project tool ``touched`` entries onto the SSE shape ``{"id", "name", "type", "labels"}``.

    ``labels`` are the node's labels, ontology label first, so the chat shows
    the same ``Customer`` / ``Order`` the Memory and Reasoning panels show,
    not the POLE+O ``type``.
    """
    refs = [item for item in touched if isinstance(item, dict) and item.get("name")]
    ids = [str(item["id"]) for item in refs if item.get("id")]
    labels: dict[str, list[str]] = {}
    if ids:
        rows = await client.query.cypher(ENTITY_LABELS, {"ids": ids})
        labels = {row["id"]: entity_labels(row["labels"]) for row in rows}
    return [
        {
            "id": str(item.get("id") or ""),
            "name": str(item["name"]),
            "type": str(item.get("type") or ""),
            "labels": labels.get(str(item.get("id")), []),
        }
        for item in refs
    ]
