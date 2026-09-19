"""Independent Bolt how-to recipes; each command creates a distinct test dataset."""

import argparse
import asyncio
from uuid import uuid4

from core_memory_settings import settings

from neo4j_agent_memory import MemoryClient
from neo4j_agent_memory.core.memory import ToolCallStatus
from neo4j_agent_memory.memory.long_term import DeduplicationConfig, LongTermMemory
from neo4j_agent_memory.schema.models import EntityRef, TraceOutcome


# tag::messages[]
async def messages(client, run_id):
    session = f"docs-messages-{run_id}"
    stored = await client.short_term.add_messages_batch(
        session,
        [
            {
                "role": "user",
                "content": "Please find wide walking shoes.",
                "metadata": {"topic": "shopping"},
            },
            {"role": "assistant", "content": "I will look for wide fits."},
        ],
        user_identifier=f"docs-user-{run_id}",
        extract_entities=False,
    )
    conversation = await client.short_term.get_conversation(session)
    assert [m.id for m in conversation.messages] == [m.id for m in stored]
    summary = await client.short_term.get_conversation_summary(
        session,
        include_entities=False,
        summarizer=lambda _transcript: "A shopper requested wide walking shoes.",
    )
    assert summary.message_count == 2
    print(summary.summary)
    # This semantic search is database-wide; it is not used for user-scoped readback.
    matches = await client.short_term.search_messages("wide walking shoes", threshold=0.0, limit=5)
    for message in matches:
        print(message.content, message.metadata.get("similarity"))
    print(f"Verified: ordered message IDs and summary count; session={session}")


# end::messages[]


# tag::entities[]
async def entities(client, run_id):
    person, _ = await client.long_term.add_entity(
        f"Maya {run_id}",
        "PERSON",
        aliases=[f"M. {run_id}"],
        description="An engineer at the fictional Northstar laboratory.",
        attributes={"team": "robotics"},
        resolve=False,
        deduplicate=False,
    )
    company, _ = await client.long_term.add_entity(
        f"Northstar {run_id}", "ORGANIZATION", resolve=False, deduplicate=False
    )
    edge = await client.long_term.add_relationship(person, company, "WORKS_AT", confidence=1.0)
    fact = await client.long_term.add_fact(person.name, "works_at", company.name)
    assert fact.as_triple == (person.name, "works_at", company.name)
    rows = await client.query.cypher(
        "MATCH (a:Entity {id: $source})-[r:RELATED_TO {id: $edge}]->(b:Entity) "
        "RETURN a.name AS source, r.type AS relation, b.name AS target",
        {"source": str(person.id), "edge": str(edge.id)},
    )
    assert rows == [{"source": person.name, "relation": "WORKS_AT", "target": company.name}]
    facts = await client.long_term.get_facts_about(person.name)
    assert any(item.as_triple == fact.as_triple for item in facts)
    print(f"Verified: entity relationship and fact readback; person={person.id}")


# end::entities[]


# tag::preferences[]
async def preferences(client, run_id):
    user = f"docs-preference-user-{run_id}"
    product, _ = await client.long_term.add_entity(
        f"Walking shoes {run_id}",
        "OBJECT",
        subtype="PRODUCT",
        generate_embedding=False,
        resolve=False,
        deduplicate=False,
    )
    scope = EntityRef(id=str(product.id))
    # Disable embedding-based global preference dedupe for independent user revisions.
    old = await client.long_term.add_preference(
        "budget",
        "Spend at most USD 60",
        user_identifier=user,
        applies_to=[scope],
        generate_embedding=False,
        context="Explicit user statement",
    )
    replacement = await client.long_term.add_preference(
        "budget",
        "Spend at most USD 80",
        user_identifier=user,
        applies_to=[scope],
        generate_embedding=False,
        context="Explicit user revision",
    )
    await client.long_term.supersede_preference(old.id, replacement.id)
    active = await client.long_term.get_preferences_for(user, applies_to=scope)
    historical = await client.long_term.get_preferences_for(user, active_only=False)
    assert [p.id for p in active] == [replacement.id]
    assert {p.id for p in historical} == {old.id, replacement.id}
    context = "\n".join(f"{p.category}: {p.preference}" for p in active)
    print(context)
    print(f"Verified: current preference and retained history; user={user}")


# end::preferences[]


# tag::reasoning[]
async def reasoning(client, run_id):
    session = f"docs-trace-{run_id}"
    trace = await client.reasoning.start_trace(session, "Check a deliberately unavailable product")
    step = await client.reasoning.add_step(trace.id, action="lookup_product")
    try:
        raise LookupError("The fictional product is unavailable")
    except LookupError as error:
        await client.reasoning.record_tool_call(
            step.id,
            "lookup_product",
            {"sku": "missing-example-sku"},
            status=ToolCallStatus.ERROR,
            error=str(error),
            duration_ms=0,
        )
        await client.reasoning.complete_trace(
            trace.id,
            outcome=TraceOutcome(success=False, summary=str(error), error_kind="no_results"),
        )
    saved = await client.reasoning.get_trace_with_steps(trace.id)
    assert saved is not None and saved.success is False
    assert len(saved.steps) == 1 and len(saved.steps[0].tool_calls) == 1
    assert saved.steps[0].tool_calls[0].status == ToolCallStatus.ERROR
    print(f"Verified: failed tool call and completed failure trace; trace={trace.id}")


# end::reasoning[]


# tag::deduplication[]
async def deduplication(client, run_id):
    store = LongTermMemory(
        client.graph,
        embedder=client.long_term.embedder,
        deduplication=DeduplicationConfig(
            auto_merge_threshold=0.95, flag_threshold=0.85, use_fuzzy_matching=False
        ),
    )
    # Create an isolated pair without similarity-based merging so manual review is reproducible.
    target, _ = await store.add_entity(
        f"Northstar Laboratory {run_id}", "ORGANIZATION", resolve=False, deduplicate=False
    )
    source, _ = await store.add_entity(
        f"Northstar Lab {run_id}", "ORGANIZATION", resolve=False, deduplicate=False
    )
    # The application has established that this specific pair denotes the same organization.
    merged = await store.merge_duplicate_entities(source.id, target.id)
    assert merged is not None
    rows = await client.query.cypher(
        "MATCH (s:Entity {id: $source}), (t:Entity {id: $target}) "
        "RETURN s.merged_into AS merged_into, t.aliases AS aliases",
        {"source": str(source.id), "target": str(target.id)},
    )
    assert rows[0]["merged_into"] == str(target.id)
    assert source.name in rows[0]["aliases"]
    stats = await store.get_deduplication_stats()
    print(f"Verified: reviewed pair merged into {target.id}; merged nodes={stats.merged_entities}")


# end::deduplication[]


# tag::audit[]
async def audit(client, run_id):
    session = f"docs-audit-{run_id}"
    client_name = f"Anthem {run_id}"
    consultant_name = f"Sara {run_id}"
    trace = await client.reasoning.start_trace(session, "Recommend a consulting team")
    step = await client.reasoning.add_step(trace.id, action="recommend_team")
    await client.reasoning.record_tool_call(
        step.id,
        tool_name="recommend_team",
        arguments={"client_name": client_name},
        result=[{"consultant": consultant_name}],
        touched_entities=[
            EntityRef(name=client_name, type="CLIENT"),
            EntityRef(name=consultant_name, type="PERSON"),
        ],
    )
    await client.reasoning.complete_trace(
        trace.id,
        outcome=TraceOutcome(success=True, summary="Matched one consultant"),
    )
    rows = await client.query.cypher(
        "MATCH (:Entity {name: $client_name})<-[:TOUCHED]-(s:ReasoningStep)"
        "<-[:HAS_STEP]-(rt:ReasoningTrace) "
        "RETURN rt.task AS task, s.action AS action, rt.outcome AS outcome",
        {"client_name": client_name},
    )
    assert rows == [
        {"task": trace.task, "action": "recommend_team", "outcome": "Matched one consultant"}
    ]
    print(f"Verified: touched-entity audit query found the trace; client={client_name}")


# end::audit[]


async def main():
    commands = {
        "messages": messages,
        "entities": entities,
        "preferences": preferences,
        "reasoning": reasoning,
        "deduplication": deduplication,
        "audit": audit,
    }
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=commands)
    args = parser.parse_args()
    async with MemoryClient(settings()) as client:
        await commands[args.command](client, uuid4().hex[:8])


if __name__ == "__main__":
    asyncio.run(main())
