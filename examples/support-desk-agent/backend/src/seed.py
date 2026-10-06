"""Seed the support-desk demo from STATE-Bench: ontology, records, conversations, traces.

Run from ``backend/``::

    uv run python -m src.seed           # refuses when the demo is already seeded
    uv run python -m src.seed --reset   # removes the seed and every chat, then seeds

What it does:

1. ``--reset`` starts the demo over. It removes the ``customer-support``
   ontology (every revision and migration record), every conversation whose
   session id starts with ``seed-`` or ``chat-`` together with the entities
   its messages mention, every record entity, every reasoning trace whose
   metadata says ``seeded`` and the traces of those chats.
2. Loads ``data/customer-support.ontology.yaml``, creates revision 1
   (permissive) and activates it. Then it reconnects: a client resolves its
   ontology when it connects.
3. Stores the starting records of the 24 STATE-Bench tasks under
   ``data/state-bench`` as ontology-typed entities (customers, products,
   orders, order lines, warranties, plus one Policy per ``get_policies``
   topic), linked by PLACED, CONTAINS, OF_PRODUCT, COVERS and REPLACED_BY.
4. Stores each task's conversation with ``short_term.add_message()``: a
   system line naming the customer, then the customer's and the agent's
   turns. GLiNER2.5 extracts against revision 1 and resolution merges each
   mention onto its record (``ORD-6014``, ``UltraShield Phone Case``).
5. Records every assistant turn that called tools as a reasoning trace, with
   the trajectory's own tool calls, arguments and results, TOUCHED edges to
   the records they name, and ``metadata={"seeded": True}``, so
   ``recall_similar_tasks`` has 24 tasks' worth of earlier work to find.

The trajectories show what happened in each task; the seed does not replay
them, so the records keep their starting state and every task can be worked
again in the app.

**Activation is per database.** The seed leaves ``customer-support`` revision
1 active, so every client that connects to this database with
``use_active_ontology=True`` (the default) extracts against it. Use a database
dedicated to the demo.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from collections import Counter
from typing import Any

from pydantic import ValidationError

from neo4j_agent_memory import BoltMemoryClient, connect
from neo4j_agent_memory.core.exceptions import NotSupportedError
from neo4j_agent_memory.memory.reasoning import ToolCallStatus
from neo4j_agent_memory.schema.models import EntityRef, TraceOutcome
from src.agent.thoughts import thought_for
from src.config import Settings, get_settings
from src.memory import build_memory_settings, parse_json_map, resolved_binding
from src.ontology import DOMAIN_ID, DOMAIN_NAME, entity_labels, load_document
from src.statebench import world as records
from src.statebench.dataset import (
    RECORD_KINDS,
    SOURCE_COMMIT,
    SOURCE_URL,
    Task,
    load_tasks,
    merge_world,
    turns,
)

#: Session ids the seed owns.
SEED_PREFIX = "seed-"

#: Session ids of chats started in the app (``api/routes/threads.py``).
#: ``--reset`` deletes these too.
CHAT_PREFIX = "chat-"

SET_CONVERSATION_TITLE = """
MATCH (c:Conversation {session_id: $session_id})
SET c.title = $title
RETURN c.session_id AS session_id
"""

SEED_SESSION_COUNT = """
MATCH (c:Conversation) WHERE c.session_id STARTS WITH $prefix
RETURN count(c) AS count
"""

RECORD_COUNT = """
MATCH (e:Entity) WHERE 'record_kind' IN keys(e)
RETURN count(e) AS count
"""

DELETE_SEED_ENTITIES = """
MATCH (c:Conversation)-[:HAS_MESSAGE]->(:Message)-[:MENTIONS]->(e:Entity)
WHERE c.session_id STARTS WITH $prefix
WITH DISTINCT e
DETACH DELETE e
RETURN count(e) AS deleted
"""

DELETE_RECORD_ENTITIES = """
MATCH (e:Entity) WHERE 'record_kind' IN keys(e)
WITH collect(e) AS entities
FOREACH (entity IN entities | DETACH DELETE entity)
RETURN size(entities) AS deleted
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

SESSION_TRACE_IDS = """
MATCH (rt:ReasoningTrace) WHERE rt.session_id STARTS WITH $prefix
RETURN rt.id AS id
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

ENTITIES_BY_LABEL = """
MATCH (e:Entity) WHERE NOT 'merged_into' IN keys(e)
RETURN e.name AS name, labels(e) AS labels
ORDER BY name
"""


class SeedRefusedError(RuntimeError):
    """The database already holds a seed; rerun with ``--reset``."""


async def _stored_ontologies(client: BoltMemoryClient) -> list[str]:
    """Ids of stored ontologies this demo created."""
    return [
        stored.id
        for stored in await client.ontology.list()
        if not stored.is_system and stored.name in (DOMAIN_ID, DOMAIN_NAME)
    ]


async def seeded_trace_ids(client: BoltMemoryClient) -> list[str]:
    """Ids of every reasoning trace whose metadata says ``seeded``."""
    rows = await client.query.cypher(TRACE_METADATA)
    return [row["id"] for row in rows if parse_json_map(row.get("metadata")).get("seeded")]


async def reset_seed(client: BoltMemoryClient) -> dict[str, int]:
    """Remove what a previous seed wrote, and every chat (``chat-*``) built on it."""
    chat_traces = await client.query.cypher(SESSION_TRACE_IDS, {"prefix": CHAT_PREFIX})
    trace_ids = await seeded_trace_ids(client) + [row["id"] for row in chat_traces]
    traces = await client.graph.execute_write(DELETE_TRACES, {"ids": trace_ids})
    entities = conversations = 0
    for prefix in (SEED_PREFIX, CHAT_PREFIX):
        rows = await client.graph.execute_write(DELETE_SEED_ENTITIES, {"prefix": prefix})
        entities += int(rows[0]["deleted"]) if rows else 0
        rows = await client.graph.execute_write(DELETE_SEED_CONVERSATIONS, {"prefix": prefix})
        conversations += int(rows[0]["deleted"]) if rows else 0
    rows = await client.graph.execute_write(DELETE_RECORD_ENTITIES, {})
    entities += int(rows[0]["deleted"]) if rows else 0
    ontologies = await _stored_ontologies(client)
    for ontology_id in ontologies:
        await client.ontology.delete(ontology_id)
    # Tool usage counters live on the :Tool nodes; recount them from what is left.
    await client.reasoning.migrate_tool_stats()
    return {
        "traces": int(traces[0]["deleted"]) if traces else 0,
        "entities": entities,
        "conversations": conversations,
        "ontologies": len(ontologies),
    }


async def _active_version_id(client: BoltMemoryClient) -> str | None:
    try:
        return (await client.ontology.get_active()).version_id
    except NotSupportedError:
        return None


async def load_records(client: BoltMemoryClient, tasks: tuple[Task, ...]) -> records.GraphWorld:
    """Every task's starting records as typed entities, linked by typed relationships."""
    merged = merge_world(tasks)
    graph = records.GraphWorld()
    for record in merged.records["customers"].values():
        await records.create_record(client, graph, "customers", record)
    by_name: dict[str, dict[str, dict[str, Any]]] = {}
    for product_id, product in merged.records["products"].items():
        by_name.setdefault(str(product["name"]), {})[product_id] = product
    for name, products in sorted(by_name.items()):
        await records.create_product(client, graph, name, products)
    for kind in ("orders", "order_items", "warranties"):
        for record_id, record in merged.records[kind].items():
            order_id = record_id if kind == "orders" else str(record.get("order_id"))
            await records.create_record(
                client,
                graph,
                kind,
                record,
                as_of=merged.as_of.get(order_id),
                source_task=merged.source_task.get(record_id),
            )
    for kind in ("orders", "order_items", "warranties"):
        for record in merged.records[kind].values():
            await records.link_record(client, graph, kind, record)
    for topic in records.POLICY_TOPICS:
        await records.create_policy(client, graph, topic)
    counts = {kind: len(graph.records[kind]) for kind in RECORD_KINDS}
    print(
        "Records: "
        + ", ".join(f"{count} {kind.replace('_', ' ')}" for kind, count in counts.items())
        + f" ({len(by_name)} product entities), {len(records.POLICY_TOPICS)} policies"
    )
    return graph


async def ingest_conversation(
    client: BoltMemoryClient, task: Task, graph: records.GraphWorld
) -> list[tuple[str, dict[str, Any]]]:
    """Store one task's conversation. Returns ``(message_id, message)`` per turn."""
    customer = graph.records["customers"].get(task.customer_id, {})
    name = str(customer.get("name") or task.customer_id)
    await client.short_term.create_conversation(task.session_id)
    await client.graph.execute_write(
        SET_CONVERSATION_TITLE, {"session_id": task.session_id, "title": f"{name}: {task.topic}"}
    )
    # Who the customer is, as the benchmark's system prompt states it; the
    # mention links the thread to the customer's record.
    await client.short_term.add_message(
        task.session_id,
        "system",
        f"Support request from {name} ({task.customer_id}).",
    )
    stored: list[tuple[str, dict[str, Any]]] = []
    for message in turns(task):
        saved = await client.short_term.add_message(
            task.session_id, message["role"], message["content"]
        )
        stored.append((str(saved.id), message))
    await records.repair_id_mentions(client, [message_id for message_id, _ in stored])
    return stored


async def record_task_traces(
    client: BoltMemoryClient,
    task: Task,
    stored: list[tuple[str, dict[str, Any]]],
    graph: records.GraphWorld,
) -> list[dict[str, Any]]:
    """One seeded trace per assistant turn that called tools.

    Each trace is initiated by the user turn before it, as a live turn's is.
    """
    recorded: list[dict[str, Any]] = []
    user_id: str | None = None
    user_text = ""
    for message_id, message in stored:
        if message["role"] == "user":
            user_id, user_text = message_id, message["content"]
            continue
        calls = message.get("tool_calls") or []
        if not calls or user_id is None:
            continue
        trace = await client.reasoning.start_trace(
            task.session_id,
            user_text,
            metadata={
                "seeded": True,
                "source": "STATE-Bench",
                "task_id": task.id,
                "task_type": task.task_type,
            },
            triggered_by_message_id=user_id,
        )
        touched_total = 0
        for call in calls:
            name, arguments, result = call["name"], call.get("arguments") or {}, call.get("result")
            touched = records.touched(graph, name, arguments, result)
            touched_total += len(touched)
            failed = isinstance(result, dict) and bool(result.get("error"))
            step = await client.reasoning.add_step(
                trace.id, thought=thought_for(name, arguments), action=name
            )
            await client.reasoning.record_tool_call(
                step.id,
                tool_name=name,
                arguments=arguments,
                result=result,
                status=ToolCallStatus.ERROR if failed else ToolCallStatus.SUCCESS,
                error=str(result.get("error")) if failed and isinstance(result, dict) else None,
                message_id=user_id,
                touched_entities=[
                    EntityRef(id=ref["id"], name=ref.get("name"), type=ref.get("type"))
                    for ref in touched
                ],
                auto_observation=True,
            )
        await client.reasoning.complete_trace(
            trace.id,
            outcome=TraceOutcome(
                success=True,
                summary=str(message["content"])[:500],
                metrics={"tool_calls": float(len(calls))},
            ),
        )
        recorded.append(
            {
                "id": str(trace.id),
                "task_id": task.id,
                "session_id": task.session_id,
                "tool_calls": len(calls),
                "touched": touched_total,
            }
        )
    return recorded


async def summarize(client: BoltMemoryClient) -> dict[str, Any]:
    """What the seed left in the graph: entities by label and pending review pairs."""
    by_label: dict[str, list[str]] = {}
    for row in await client.query.cypher(ENTITIES_BY_LABEL):
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
        existing = await _stored_ontologies(client)
        seeded_sessions = (await client.query.cypher(SEED_SESSION_COUNT, {"prefix": SEED_PREFIX}))[
            0
        ]["count"]
        record_entities = (await client.query.cypher(RECORD_COUNT))[0]["count"]
        if (existing or seeded_sessions or record_entities) and not reset:
            raise SeedRefusedError(
                f"This database already holds the {DOMAIN_ID} ontology, its records or seed-* "
                "conversations. Rerun with --reset to remove them and seed again."
            )
        if reset:
            removed = await reset_seed(client)
            print(
                f"Reset: removed {removed['ontologies']} ontology, "
                f"{removed['conversations']} conversation(s), {removed['entities']} "
                f"entities and {removed['traces']} trace(s)"
            )

        previous_version_id = await _active_version_id(client)

        document = load_document()
        version = await client.ontology.create(DOMAIN_ID, document, validation_mode="permissive")
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

        tasks = load_tasks()
        source = f"{SOURCE_URL} @ {SOURCE_COMMIT[:12]}"
        print(f"STATE-Bench customer support: {len(tasks)} tasks ({source})")
        graph = await load_records(client, tasks)

        print("Ingesting conversations (GLiNER2.5 extraction on) and recording their traces:")
        sessions: list[str] = []
        messages = 0
        traces: list[dict[str, Any]] = []
        for task in tasks:
            stored = await ingest_conversation(client, task, graph)
            task_traces = await record_task_traces(client, task, stored, graph)
            sessions.append(task.session_id)
            messages += len(stored) + 1
            traces.extend(task_traces)
            calls = sum(t["tool_calls"] for t in task_traces)
            print(
                f"   {task.session_id:<58} {len(stored):>2} message(s), "
                f"{len(task_traces)} trace(s), {calls:>2} tool call(s)"
            )

        summary = await summarize(client)
        result = {
            "ontology_id": version.ontology_id,
            "version_id": version.id,
            "revision": version.revision,
            "validation_mode": version.validation_mode,
            "previous_version_id": previous_version_id,
            "tasks": [task.id for task in tasks],
            "sessions": sessions,
            "messages": messages,
            "records": {kind: len(graph.records[kind]) for kind in RECORD_KINDS},
            "traces": traces,
            "tool_calls": Counter(
                call["name"]
                for task in tasks
                for message in turns(task)
                for call in message.get("tool_calls") or []
            ),
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
        shown = ", ".join(names[:6]) + (" …" if len(names) > 6 else "")
        print(f"      {label:<10} {len(names):>3}  {shown}")
    print(f"   pending review pairs: {len(result['pending_review_pairs'])}")
    for pair in result["pending_review_pairs"][:8]:
        print(f"      {pair['source']} ~ {pair['target']} ({pair['confidence']})")
    calls = sum(trace["tool_calls"] for trace in result["traces"])
    print(f"   seeded traces: {len(result['traces'])} ({calls} tool calls)")
    for name, count in sorted(result["tool_calls"].items(), key=lambda item: -item[1]):
        print(f"      {name:<24} {count:>3}")
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
        help=(
            "start over: remove the customer-support ontology, its records, the seed-* "
            "and chat-* conversations and their traces first"
        ),
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
