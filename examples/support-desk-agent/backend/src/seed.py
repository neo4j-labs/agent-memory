"""Seed the support-desk demo: ontology revision 1, conversations, earlier agent work.

Run from ``backend/``::

    uv run python -m src.seed           # refuses when support-desk already exists
    uv run python -m src.seed --reset   # removes what a previous seed wrote, then seeds

What it does:

1. ``--reset`` removes the ``support-desk`` ontology (every revision and
   migration record), every conversation whose session id starts with
   ``seed-`` together with the entities its messages mention, and every
   reasoning trace whose metadata says ``seeded``.
2. Imports ``data/support-desk.arrows.json``, repairs the draft, creates
   revision 1 (permissive) and activates it. Then it reconnects: a client
   resolves its ontology when it connects.
3. Stores ``data/conversations.json`` with ``short_term.add_message()``.
   GLiNER2.5 extracts against revision 1 and ingest-time resolution merges
   repeated mentions, so tickets land as ``:Entity:Event:Ticket``.
4. Records three reasoning traces that model earlier agent work, using the
   agent's real tools against the seeded graph, so ``recall_similar_tasks``
   has something to find on the first chat. They carry
   ``metadata={"seeded": True}`` and the API labels them as seeded.

**Activation is per database.** The seed leaves ``support-desk`` revision 1
active, so every client that connects to this database with
``use_active_ontology=True`` (the default) extracts against it. Use a database
dedicated to the demo.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from typing import Any

from pydantic import ValidationError

from neo4j_agent_memory import BoltMemoryClient, connect
from neo4j_agent_memory.core.exceptions import NotSupportedError
from neo4j_agent_memory.memory.reasoning import ToolCallStatus
from neo4j_agent_memory.schema.models import EntityRef, TraceOutcome
from src.agent import tools
from src.config import DATA_DIR, Settings, get_settings
from src.memory import build_memory_settings, parse_json_map, resolved_binding
from src.ontology import DOMAIN_ID, DOMAIN_NAME, entity_labels, import_support_desk

CONVERSATIONS_FILE = DATA_DIR / "conversations.json"

#: Session ids the seed owns. ``--reset`` deletes exactly these.
SEED_PREFIX = "seed-"

SET_CONVERSATION_TITLE = """
MATCH (c:Conversation {session_id: $session_id})
SET c.title = $title
RETURN c.session_id AS session_id
"""

SEED_SESSION_COUNT = """
MATCH (c:Conversation) WHERE c.session_id STARTS WITH $prefix
RETURN count(c) AS count
"""

DELETE_SEED_ENTITIES = """
MATCH (c:Conversation)-[:HAS_MESSAGE]->(:Message)-[:MENTIONS]->(e:Entity)
WHERE c.session_id STARTS WITH $prefix
WITH DISTINCT e
DETACH DELETE e
RETURN count(e) AS deleted
"""

DELETE_SEED_CONVERSATIONS = """
MATCH (c:Conversation) WHERE c.session_id STARTS WITH $prefix
OPTIONAL MATCH (c)-[:HAS_MESSAGE]->(m:Message)
WITH c, collect(m) AS messages
FOREACH (message IN messages | DETACH DELETE message)
DETACH DELETE c
RETURN count(c) AS deleted
"""

TRACE_METADATA = """
MATCH (rt:ReasoningTrace)
RETURN rt.id AS id, properties(rt).metadata AS metadata
"""

DELETE_TRACES = """
MATCH (rt:ReasoningTrace) WHERE rt.id IN $ids
OPTIONAL MATCH (rt)-[:HAS_STEP]->(s:ReasoningStep)
OPTIONAL MATCH (s)-[:USES_TOOL]->(tc:ToolCall)
WITH rt, collect(DISTINCT s) + collect(DISTINCT tc) AS parts
FOREACH (part IN parts | DETACH DELETE part)
DETACH DELETE rt
RETURN count(rt) AS deleted
"""

SEED_ENTITIES_BY_LABEL = """
MATCH (c:Conversation)-[:HAS_MESSAGE]->(:Message)-[:MENTIONS]->(e:Entity)
WHERE c.session_id STARTS WITH $prefix AND NOT 'merged_into' IN keys(e)
WITH DISTINCT e
RETURN e.name AS name, labels(e) AS labels
ORDER BY name
"""

#: Earlier agent work, recorded with the agent's own tools against the seeded
#: graph. ``message`` is the (session, index) of the user message that
#: initiated it, which becomes ``(:ReasoningTrace)-[:INITIATED_BY]->(:Message)``.
SEEDED_TRACES: list[dict[str, Any]] = [
    {
        "task": "Find the open tickets for a customer",
        "message": ("seed-priya-replacement-follow-up", 0),
        "steps": [
            ("Identify the customer the user named", "find_customer", {"name": "Priya Raman"}),
            (
                "List that customer's tickets with their orders and products",
                "list_tickets",
                {"customer": "Priya Raman"},
            ),
        ],
        "summary": (
            "Priya Raman has two tickets: TK-2210, a replacement Aurora Desk Lamp for "
            "order SO-4417, and TK-2211, a Northwind Cable Tray in the wrong colour on "
            "order SO-4390."
        ),
    },
    {
        "task": "Check the status of an order",
        "message": ("seed-marcus-chair-arrived", 0),
        "steps": [
            ("Look up the order the customer quoted", "get_order", {"order_number": "SO-4452"}),
            ("Check the ticket raised against that order", "get_ticket", {"reference": "TK-2214"}),
        ],
        "summary": (
            "Order SO-4452 (Halcyon Ergonomic Chair) has been delivered, so ticket TK-2214 "
            "for the late delivery can be closed."
        ),
    },
    {
        "task": "Summarise a customer's refund request",
        "message": ("seed-daniel-desk-refund", 2),
        "steps": [
            ("Identify the customer", "find_customer", {"name": "Daniel Okafor"}),
            (
                "Read the ticket that holds the refund request",
                "get_ticket",
                {"reference": "TK-2219"},
            ),
        ],
        "summary": (
            "Daniel Okafor wants a refund rather than a repair for the Breeze Standing Desk "
            "on order SO-4471; ticket TK-2219 tracks the return."
        ),
    },
]

TOOL_FUNCTIONS: dict[str, Any] = {
    "recall_similar_tasks": tools.recall_similar_tasks,
    "find_customer": tools.find_customer,
    "get_ticket": tools.get_ticket,
    "list_tickets": tools.list_tickets,
    "get_order": tools.get_order,
    "search_support_history": tools.search_support_history,
    "get_ontology": tools.get_ontology,
}


class SeedRefusedError(RuntimeError):
    """The database already holds a seed; rerun with ``--reset``."""


def load_conversations() -> list[dict[str, Any]]:
    """The seed conversations from ``data/conversations.json``."""
    data = json.loads(CONVERSATIONS_FILE.read_text(encoding="utf-8"))
    conversations: list[dict[str, Any]] = data["conversations"]
    return conversations


async def _stored_support_desk(client: BoltMemoryClient) -> list[str]:
    """Ids of stored ontologies named ``support-desk``."""
    return [s.id for s in await client.ontology.list() if not s.is_system and s.name == DOMAIN_ID]


async def seeded_trace_ids(client: BoltMemoryClient) -> list[str]:
    """Ids of every reasoning trace whose metadata says ``seeded``."""
    rows = await client.query.cypher(TRACE_METADATA)
    return [row["id"] for row in rows if parse_json_map(row.get("metadata")).get("seeded")]


async def reset_seed(client: BoltMemoryClient) -> dict[str, int]:
    """Remove what a previous seed wrote. Chat threads (``chat-*``) are kept."""
    trace_ids = await seeded_trace_ids(client)
    traces = await client.graph.execute_write(DELETE_TRACES, {"ids": trace_ids})
    entities = await client.graph.execute_write(DELETE_SEED_ENTITIES, {"prefix": SEED_PREFIX})
    conversations = await client.graph.execute_write(
        DELETE_SEED_CONVERSATIONS, {"prefix": SEED_PREFIX}
    )
    ontologies = await _stored_support_desk(client)
    for ontology_id in ontologies:
        await client.ontology.delete(ontology_id)
    # Tool usage counters live on the :Tool nodes; recount them from what is left.
    await client.reasoning.migrate_tool_stats()
    return {
        "traces": int(traces[0]["deleted"]) if traces else 0,
        "entities": int(entities[0]["deleted"]) if entities else 0,
        "conversations": int(conversations[0]["deleted"]) if conversations else 0,
        "ontologies": len(ontologies),
    }


async def _active_version_id(client: BoltMemoryClient) -> str | None:
    try:
        return (await client.ontology.get_active()).version_id
    except NotSupportedError:
        return None


async def ingest_conversations(
    client: BoltMemoryClient, conversations: list[dict[str, Any]]
) -> dict[str, list[str]]:
    """Store every seed conversation. Returns the message ids per session."""
    message_ids: dict[str, list[str]] = {}
    for conversation in conversations:
        session_id = conversation["session_id"]
        await client.short_term.create_conversation(session_id)
        await client.graph.execute_write(
            SET_CONVERSATION_TITLE, {"session_id": session_id, "title": conversation["title"]}
        )
        ids: list[str] = []
        for message in conversation["messages"]:
            stored = await client.short_term.add_message(
                session_id, message["role"], message["content"]
            )
            ids.append(str(stored.id))
        message_ids[session_id] = ids
        print(f"   {session_id:<36} {len(ids)} message(s)")
    return message_ids


async def record_seeded_traces(
    client: BoltMemoryClient, message_ids: dict[str, list[str]]
) -> list[dict[str, Any]]:
    """Record :data:`SEEDED_TRACES` with real tool results and TOUCHED edges."""
    recorded: list[dict[str, Any]] = []
    for spec in SEEDED_TRACES:
        session_id, index = spec["message"]
        session_messages = message_ids.get(session_id, [])
        message_id = session_messages[index] if index < len(session_messages) else None
        trace = await client.reasoning.start_trace(
            session_id,
            spec["task"],
            metadata={"seeded": True},
            triggered_by_message_id=message_id,
        )
        touched_total = 0
        for thought, tool_name, arguments in spec["steps"]:
            started = time.monotonic()
            result = await TOOL_FUNCTIONS[tool_name](client, **arguments)
            duration_ms = int((time.monotonic() - started) * 1000)
            touched = [
                EntityRef(id=ref["id"], name=ref.get("name"), type=ref.get("type"))
                for ref in result.get("touched", [])
            ]
            touched_total += len(touched)
            step = await client.reasoning.add_step(trace.id, thought=thought, action=tool_name)
            await client.reasoning.record_tool_call(
                step.id,
                tool_name=tool_name,
                arguments=arguments,
                result=result,
                status=ToolCallStatus.SUCCESS,
                duration_ms=duration_ms,
                message_id=message_id,
                touched_entities=touched,
                auto_observation=True,
            )
        await client.reasoning.complete_trace(
            trace.id,
            outcome=TraceOutcome(
                success=True,
                summary=spec["summary"],
                metrics={"tool_calls": float(len(spec["steps"]))},
            ),
        )
        recorded.append(
            {
                "id": str(trace.id),
                "task": spec["task"],
                "session_id": session_id,
                "tool_calls": len(spec["steps"]),
                "touched": touched_total,
            }
        )
    return recorded


async def summarize(client: BoltMemoryClient) -> dict[str, Any]:
    """What the seed left in the graph: entities by label and pending review pairs."""
    by_label: dict[str, list[str]] = {}
    for row in await client.query.cypher(SEED_ENTITIES_BY_LABEL, {"prefix": SEED_PREFIX}):
        labels = entity_labels(row["labels"])
        by_label.setdefault(labels[0] if labels else "Entity", []).append(row["name"])
    pending = [
        {"source": a.name, "target": b.name, "confidence": round(confidence, 3)}
        for a, b, confidence in await client.long_term.find_potential_duplicates()
    ]
    return {"entities_by_label": by_label, "pending_review_pairs": pending}


async def seed(settings: Settings, *, reset: bool = False) -> dict[str, Any]:
    """Run the seed. Returns a summary (the tests read it)."""
    memory_settings = build_memory_settings(settings)
    client = await connect(memory_settings)
    try:
        existing = await _stored_support_desk(client)
        seeded_sessions = (await client.query.cypher(SEED_SESSION_COUNT, {"prefix": SEED_PREFIX}))[
            0
        ]["count"]
        if (existing or seeded_sessions) and not reset:
            raise SeedRefusedError(
                f"This database already holds the {DOMAIN_ID} ontology or seed-* "
                "conversations. Rerun with --reset to remove them and seed again."
            )
        if reset:
            removed = await reset_seed(client)
            print(
                f"Reset: removed {removed['ontologies']} ontology, "
                f"{removed['conversations']} conversation(s), {removed['entities']} "
                f"entities and {removed['traces']} seeded trace(s)"
            )

        previous_version_id = await _active_version_id(client)

        # Import, repair, create revision 1 and activate it.
        document = await import_support_desk(client)
        version = await client.ontology.create(DOMAIN_NAME, document, validation_mode="permissive")
        await client.ontology.activate(version.id)
        print(
            f"Ontology {DOMAIN_ID}: revision {version.revision} ({version.validation_mode}) "
            f"created and activated — labels {', '.join(document.labels())}"
        )

        # A client resolves its ontology at connect time: reconnect to extract
        # against revision 1.
        await client.close()
        client = await connect(memory_settings)
        binding = resolved_binding(client)
        if binding["domain_id"] != DOMAIN_ID:
            raise RuntimeError(f"The reconnected client resolved {binding}, not {DOMAIN_ID}")

        conversations = load_conversations()
        print(f"Ingesting {len(conversations)} conversation(s) (GLiNER2.5 extraction on):")
        message_ids = await ingest_conversations(client, conversations)

        traces = await record_seeded_traces(client, message_ids)
        summary = await summarize(client)
        result = {
            "ontology_id": version.ontology_id,
            "version_id": version.id,
            "revision": version.revision,
            "validation_mode": version.validation_mode,
            "previous_version_id": previous_version_id,
            "sessions": list(message_ids),
            "messages": sum(len(ids) for ids in message_ids.values()),
            "traces": traces,
            **summary,
        }
        _print_summary(result)
        return result
    finally:
        await client.close()


def _print_summary(result: dict[str, Any]) -> None:
    print("\nSeed summary")
    print(
        f"   ontology   {DOMAIN_ID} revision {result['revision']} "
        f"({result['validation_mode']}), version {result['version_id']}"
    )
    print(f"   sessions   {len(result['sessions'])} ({result['messages']} messages)")
    print("   entities by label:")
    for label, names in sorted(result["entities_by_label"].items()):
        print(f"      {label:<12} {len(names):>2}  {', '.join(names)}")
    print(f"   pending review pairs: {len(result['pending_review_pairs'])}")
    for pair in result["pending_review_pairs"]:
        print(f"      {pair['source']} ~ {pair['target']} ({pair['confidence']})")
    print(f"   seeded traces: {len(result['traces'])}")
    for trace in result["traces"]:
        print(
            f"      {trace['task']} — {trace['tool_calls']} tool call(s), "
            f"{trace['touched']} touched entities ({trace['session_id']})"
        )
    if result["previous_version_id"]:
        print(
            "\nAnother ontology version was active before the seed. To restore it:\n"
            f'   await client.ontology.activate("{result["previous_version_id"]}")'
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m src.seed", description=__doc__.split("\n")[0])
    parser.add_argument(
        "--reset",
        action="store_true",
        help="remove the support-desk ontology, the seed-* conversations and seeded traces first",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    try:
        settings = get_settings()
    except ValidationError as exc:
        missing = ", ".join(str(error["loc"][0]).upper() for error in exc.errors())
        print(f"Set {missing} (see backend/.env.example).", file=sys.stderr)
        return 2
    try:
        asyncio.run(seed(settings, reset=args.reset))
    except SeedRefusedError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
