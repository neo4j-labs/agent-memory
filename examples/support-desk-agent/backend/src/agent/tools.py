"""The agent's tools, as plain async functions over a connected client.

``agent.py`` wraps each one as a PydanticAI tool; ``seed.py`` calls the same
functions to record the seeded reasoning traces, so a seeded tool call carries
a real result.

Every read goes through ``client.query.cypher()``, the library's read-only
accessor. Labels are never hard-coded: each lookup asks the client's ontology
document which label a role (``EVENT:TICKET`` and so on) has, so the tools
work before and after the ``Ticket`` -> ``SupportCase`` rename.

How entities are related. Typed ``RELATED_TO`` edges come first (GLiNER2.5
extracts them against the ontology's relationships); a co-mention fills in
what extraction did not connect. Tickets, orders and products are related when
one *message* mentions them together; a customer is related to the tickets and
orders mentioned in the *conversations* they took part in, because a customer
introduces themselves in one message and the ticket is opened in the reply.
Each related item says which it was (``via``).

Every result that names entities carries ``touched`` — ``[{"id", "name",
"type"}]`` — which the chat route records as
``(:ReasoningStep)-[:TOUCHED]->(:Entity)`` audit edges.
"""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from neo4j_agent_memory import BoltMemoryClient
from neo4j_agent_memory.core.exceptions import NotSupportedError
from neo4j_agent_memory.ontology import OntologyDocument
from src.memory import iso, jsonable, parse_json_map, resolved_binding
from src.ontology import CUSTOMER, ORDER, PRODUCT, TICKET, entity_labels, role_label

#: Cap on audit edges per tool call, so one broad listing cannot flood the graph.
MAX_TOUCHED = 12

#: Similarity floor for ``recall_similar_tasks`` (MiniLM cosine).
RECALL_THRESHOLD = 0.6

#: Similarity floor for the last-resort semantic customer lookup (a misspelt
#: name still clears it; a one-letter query does not).
CUSTOMER_SEARCH_THRESHOLD = 0.75

Scope = Literal["message", "conversation"]

ENTITY_FIELDS = """
       e.id AS id, e.name AS name, e.type AS type, e.subtype AS subtype,
       coalesce(properties(e).aliases, []) AS aliases, labels(e) AS labels,
       properties(e).merged_into AS merged_into
"""

FIND_BY_NAME = f"""
MATCH (e:Entity)
WHERE $label IN labels(e)
  AND (toLower(e.name) = toLower($name)
       OR any(alias IN coalesce(properties(e).aliases, []) WHERE toLower(alias) = toLower($name)))
RETURN {ENTITY_FIELDS},
       CASE WHEN toLower(e.name) = toLower($name) THEN 'exact' ELSE 'alias' END AS matched
ORDER BY matched DESC, merged_into IS NULL DESC
LIMIT 1
"""

#: First-name-only lookups: "Priya" finds "Priya Raman".
FIND_BY_PREFIX = f"""
MATCH (e:Entity)
WHERE $label IN labels(e) AND NOT 'merged_into' IN keys(e)
  AND toLower(e.name) STARTS WITH toLower($name) + ' '
RETURN {ENTITY_FIELDS}, 'prefix' AS matched
ORDER BY size(e.name)
LIMIT 5
"""

GET_BY_ID = f"""
MATCH (e:Entity {{id: $id}})
RETURN {ENTITY_FIELDS}
"""

GET_BY_IDS = """
MATCH (e:Entity) WHERE e.id IN $ids
RETURN e.id AS id, labels(e) AS labels, properties(e).merged_into AS merged_into
"""

RELATED_BY_EDGE = """
MATCH (e:Entity {id: $id})-[r:RELATED_TO]-(x:Entity)
WHERE $label IN labels(x) AND NOT 'merged_into' IN keys(x)
RETURN DISTINCT x.id AS id, x.name AS name, x.type AS type, r.type AS via
ORDER BY name
"""

CO_MENTIONED_IN_MESSAGE = """
MATCH (e:Entity {id: $id})<-[:MENTIONS]-(m:Message)-[:MENTIONS]->(x:Entity)
WHERE $label IN labels(x) AND x <> e AND NOT 'merged_into' IN keys(x)
RETURN x.id AS id, x.name AS name, x.type AS type, count(DISTINCT m) AS together
ORDER BY together DESC, name
"""

CO_MENTIONED_IN_CONVERSATION = """
MATCH (e:Entity {id: $id})<-[:MENTIONS]-(:Message)<-[:HAS_MESSAGE]-(c:Conversation)
MATCH (c)-[:HAS_MESSAGE]->(m:Message)-[:MENTIONS]->(x:Entity)
WHERE $label IN labels(x) AND x <> e AND NOT 'merged_into' IN keys(x)
RETURN x.id AS id, x.name AS name, x.type AS type, count(DISTINCT c) AS together
ORDER BY together DESC, name
"""

CONVERSATIONS_MENTIONING = """
MATCH (c:Conversation)-[:HAS_MESSAGE]->(m:Message)-[:MENTIONS]->(e:Entity {id: $id})
WITH c, count(m) AS mentions, max(m.timestamp) AS last_mentioned
RETURN c.session_id AS session_id, c.title AS title, mentions, last_mentioned
ORDER BY last_mentioned DESC
LIMIT 10
"""

MESSAGES_MENTIONING = """
MATCH (c:Conversation)-[:HAS_MESSAGE]->(m:Message)-[:MENTIONS]->(e:Entity {id: $id})
RETURN c.session_id AS session_id, c.title AS title, m.id AS message_id,
       m.role AS role, m.content AS content, m.timestamp AS timestamp
ORDER BY m.timestamp
LIMIT 12
"""

PENDING_SAME_AS = """
MATCH (e:Entity {id: $id})-[r:SAME_AS]-(other:Entity)
WHERE r.status = 'pending'
RETURN other.id AS id, other.name AS name, r.confidence AS confidence,
       r.match_type AS match_type
ORDER BY confidence DESC
"""

ALL_WITH_LABEL = """
MATCH (e:Entity)
WHERE $label IN labels(e) AND NOT 'merged_into' IN keys(e)
RETURN e.id AS id, e.name AS name, e.type AS type, e.subtype AS subtype
ORDER BY e.name
LIMIT $limit
"""

NAMES_ANY_LABEL = """
MATCH (e:Entity)
WHERE toLower(e.name) = toLower($name)
RETURN e.name AS name, labels(e) AS labels
LIMIT 5
"""

MESSAGE_SESSIONS = """
MATCH (c:Conversation)-[:HAS_MESSAGE]->(m:Message)
WHERE m.id IN $ids
RETURN m.id AS id, c.session_id AS session_id, c.title AS title
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
    """An entity summary: ``{"id", "name", "type"}`` (plus ``via`` when known)."""
    ref = {"id": row["id"], "name": row["name"], "type": row.get("type")}
    if row.get("via"):
        ref["via"] = row["via"]
    return ref


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


def _entity_view(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "type": row.get("type"),
        "subtype": row.get("subtype"),
        "labels": entity_labels(row.get("labels") or []),
        "aliases": list(row.get("aliases") or []),
    }


async def _find(client: BoltMemoryClient, label: str, name: str) -> dict[str, Any] | None:
    """Exact name or alias match under ``label``, following ``merged_into``."""
    rows = await client.query.cypher(FIND_BY_NAME, {"label": label, "name": name.strip()})
    if not rows:
        return None
    row = dict(rows[0])
    if row.get("merged_into"):
        merged = await client.query.cypher(GET_BY_ID, {"id": row["merged_into"]})
        if merged:
            return {**dict(merged[0]), "matched": "merged", "matched_name": row["name"]}
    return row


async def related(
    client: BoltMemoryClient, entity_id: str, label: str, scope: Scope
) -> list[dict[str, Any]]:
    """Entities under ``label`` related to ``entity_id``: typed edges, then co-mentions."""
    params = {"id": entity_id, "label": label}
    found: dict[str, dict[str, Any]] = {}
    for row in await client.query.cypher(RELATED_BY_EDGE, params):
        found.setdefault(row["id"], _ref(row))
    co_mentions = CO_MENTIONED_IN_MESSAGE if scope == "message" else CO_MENTIONED_IN_CONVERSATION
    via = "same message" if scope == "message" else "same conversation"
    for row in await client.query.cypher(co_mentions, params):
        found.setdefault(row["id"], _ref({**row, "via": via}))
    return list(found.values())


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
        similar.append(
            {
                "id": str(trace.id),
                "task": trace.task,
                "similarity": round(float(trace.metadata.get("similarity", 0.0)), 3),
                "outcome": _snippet(trace.outcome, 300),
                "seeded": bool(metadata.get(str(trace.id), {}).get("seeded")),
                "steps": [
                    {
                        "action": step.action,
                        "tools": [call.tool_name for call in step.tool_calls],
                    }
                    for step in steps
                ],
            }
        )
    return {"task": task, "similar_tasks": similar}


async def find_customer(client: BoltMemoryClient, name: str) -> dict[str, Any]:
    """A customer by exact name, alias, first name or meaning, with orders and tickets."""
    document = _document(client)
    customer_label = role_label(document, CUSTOMER)
    row = await _find(client, customer_label, name)
    candidates: list[dict[str, Any]] = []
    if row is None:
        prefix_rows = await client.query.cypher(
            FIND_BY_PREFIX, {"label": customer_label, "name": name.strip()}
        )
        if prefix_rows:
            row = dict(prefix_rows[0])
            candidates = [_entity_view(dict(r)) for r in prefix_rows[1:]]
    if row is None:
        hits = await client.long_term.search_entities(
            name, entity_types=[CUSTOMER[0]], limit=5, threshold=CUSTOMER_SEARCH_THRESHOLD
        )
        hits = [hit for hit in hits if (hit.subtype or "").upper() == CUSTOMER[1]]
        if hits:
            best = await client.query.cypher(GET_BY_ID, {"id": str(hits[0].id)})
            if best:
                row = {**dict(best[0]), "matched": "semantic"}
            candidates = [{"id": str(hit.id), "name": hit.name} for hit in hits[1:]]
    if row is None:
        return {"query": name, "found": False, "customer": None, "touched": []}

    customer = _entity_view(row)
    orders = await related(client, row["id"], role_label(document, ORDER), "conversation")
    tickets = await related(client, row["id"], role_label(document, TICKET), "conversation")
    pending = await client.query.cypher(PENDING_SAME_AS, {"id": row["id"]})
    return {
        "query": name,
        "found": True,
        "matched_by": row.get("matched"),
        "customer": customer,
        "possible_duplicates": [jsonable(dict(p)) for p in pending],
        "other_candidates": candidates,
        "orders": orders,
        "tickets": tickets,
        "conversations": await _conversations(client, row["id"]),
        "touched": _touched(
            {"id": row["id"], "name": row["name"], "type": row.get("type")}, orders, tickets
        ),
    }


async def get_ticket(client: BoltMemoryClient, reference: str) -> dict[str, Any]:
    """One ticket under the active ticket label, with its order, product and customer."""
    document = _document(client)
    ticket_label = role_label(document, TICKET)
    row = await _find(client, ticket_label, reference)
    if row is None:
        others = await client.query.cypher(NAMES_ANY_LABEL, {"name": reference.strip()})
        return {
            "reference": reference,
            "found": False,
            "ticket_label": ticket_label,
            "same_name_under_other_labels": [
                {"name": o["name"], "labels": entity_labels(o["labels"])} for o in others
            ],
            "touched": [],
        }
    orders = await related(client, row["id"], role_label(document, ORDER), "message")
    products = await related(client, row["id"], role_label(document, PRODUCT), "message")
    customers = await related(client, row["id"], role_label(document, CUSTOMER), "conversation")
    messages = await client.query.cypher(MESSAGES_MENTIONING, {"id": row["id"]})
    return {
        "reference": reference,
        "found": True,
        "ticket_label": ticket_label,
        "ticket": _entity_view(row),
        "orders": orders,
        "products": products,
        "customers": customers,
        "mentions": [
            {
                "session_id": m["session_id"],
                "title": m.get("title"),
                "message_id": m["message_id"],
                "role": m["role"],
                "snippet": _snippet(m.get("content")),
                "timestamp": iso(m.get("timestamp")),
            }
            for m in messages
        ],
        "touched": _touched(
            {"id": row["id"], "name": row["name"], "type": row.get("type")},
            orders,
            products,
            customers,
        ),
    }


async def list_tickets(client: BoltMemoryClient, customer: str | None = None) -> dict[str, Any]:
    """Tickets under the active label, each with its order and product."""
    document = _document(client)
    label = role_label(document, TICKET)
    customer_row: dict[str, Any] | None = None
    if customer:
        customer_row = await _find(client, role_label(document, CUSTOMER), customer)
        if customer_row is None:
            return {
                "ticket_label": label,
                "customer": customer,
                "customer_found": False,
                "tickets": [],
                "touched": [],
            }
        rows = await related(client, customer_row["id"], label, "conversation")
    else:
        rows = [
            dict(r)
            for r in await client.query.cypher(ALL_WITH_LABEL, {"label": label, "limit": 50})
        ]

    tickets: list[dict[str, Any]] = []
    for row in rows:
        orders = await related(client, row["id"], role_label(document, ORDER), "message")
        products = await related(client, row["id"], role_label(document, PRODUCT), "message")
        tickets.append(
            {
                "id": row["id"],
                "name": row["name"],
                "type": row.get("type"),
                "orders": orders,
                "products": products,
            }
        )
    result: dict[str, Any] = {"ticket_label": label, "tickets": tickets}
    if customer_row is not None:
        result["customer"] = {"id": customer_row["id"], "name": customer_row["name"]}
        result["customer_found"] = True
    result["touched"] = _touched(
        [{"id": customer_row["id"], "name": customer_row["name"], "type": customer_row.get("type")}]
        if customer_row
        else None,
        tickets,
    )
    return result


async def get_order(client: BoltMemoryClient, order_number: str) -> dict[str, Any]:
    """One order with its products, tickets and customer."""
    document = _document(client)
    row = await _find(client, role_label(document, ORDER), order_number)
    if row is None:
        return {"order_number": order_number, "found": False, "touched": []}
    products = await related(client, row["id"], role_label(document, PRODUCT), "message")
    tickets = await related(client, row["id"], role_label(document, TICKET), "message")
    customers = await related(client, row["id"], role_label(document, CUSTOMER), "conversation")
    return {
        "order_number": order_number,
        "found": True,
        "order": _entity_view(row),
        "products": products,
        "tickets": tickets,
        "customers": customers,
        "conversations": await _conversations(client, row["id"]),
        "touched": _touched(
            {"id": row["id"], "name": row["name"], "type": row.get("type")},
            products,
            tickets,
            customers,
        ),
    }


async def search_support_history(client: BoltMemoryClient, query: str) -> dict[str, Any]:
    """Semantic search over every session's messages, plus matching entities."""
    messages = await client.short_term.search_messages(query, limit=8, threshold=0.5)
    sessions = {
        row["id"]: row
        for row in await client.query.cypher(
            MESSAGE_SESSIONS, {"ids": [str(m.id) for m in messages]}
        )
    }
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
                "session_id": sessions.get(str(message.id), {}).get("session_id"),
                "title": sessions.get(str(message.id), {}).get("title"),
                "role": message.role.value,
                "snippet": _snippet(message.content),
                "similarity": round(float(message.metadata.get("similarity", 0.0)), 3),
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
        "ticket_label": role_label(_document(client), TICKET),
    }


def entity_refs(touched: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Project tool ``touched`` entries onto the SSE shape ``{"name", "type"}``."""
    return [
        {"name": str(item.get("name") or ""), "type": str(item.get("type") or "")}
        for item in touched
        if isinstance(item, dict) and item.get("name")
    ]


def is_uuid(value: str) -> bool:
    try:
        UUID(str(value))
    except ValueError:
        return False
    return True
