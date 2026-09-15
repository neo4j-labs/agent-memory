"""Store and reconstruct a small memory context across two process runs."""

import argparse
import asyncio

from core_memory_settings import settings

from neo4j_agent_memory import MemoryClient

SESSION = "docs-first-memory"
USER = "docs-first-user"
TEXT = "Maya Chen works at Northstar Robotics. She prefers concise answers."


async def seed(client):
    existing = await client.short_term.get_conversation(SESSION)
    if existing.messages:
        raise RuntimeError("This lesson is already seeded; run verify or use a fresh database")
    message = await client.short_term.add_message(
        SESSION, "user", TEXT, user_identifier=USER, extract_entities=False
    )
    await client.short_term.add_message(
        SESSION,
        "assistant",
        "I will keep responses concise.",
        user_identifier=USER,
        extract_entities=False,
    )
    person, _ = await client.long_term.add_entity(
        "Maya Chen",
        "PERSON",
        resolve=False,
        deduplicate=False,
        description="Maya Chen works at Northstar Robotics.",
    )
    company, _ = await client.long_term.add_entity(
        "Northstar Robotics",
        "ORGANIZATION",
        resolve=False,
        deduplicate=False,
        description="The fictional employer of Maya Chen.",
    )
    await client.long_term.add_relationship(person, company, "WORKS_AT")
    await client.long_term.link_entity_to_message(person, message.id, context=TEXT)
    await client.long_term.add_preference(
        "communication",
        "Use concise answers",
        user_identifier=USER,
        context="Explicitly supplied by the lesson; not inferred from behavior",
    )
    print("Stored: 2 messages, 2 entities, 1 relationship, 1 explicit preference")


async def verify(client):
    conversation = await client.short_term.get_conversation(SESSION)
    preferences = await client.long_term.get_preferences_for(USER)
    rows = await client.query.cypher(
        "MATCH (person:Entity {name: $person})-[r:RELATED_TO]->"
        "(company:Entity {name: $company}) "
        "WHERE r.type = 'WORKS_AT' "
        "RETURN person.name AS person, company.name AS company",
        {"person": "Maya Chen", "company": "Northstar Robotics"},
    )
    assert len(conversation.messages) == 2, "Expected both stored messages"
    assert any(p.preference == "Use concise answers" for p in preferences)
    assert rows, "Expected the stored WORKS_AT relationship"
    context = "\n".join(
        [f"{m.role.value}: {m.content}" for m in conversation.messages]
        + [f"Preference: {p.preference}" for p in preferences]
        + [f"{row['person']} works at {row['company']}" for row in rows]
    )
    print("Verified: stored history, preference, and relationship survived restart")
    print(context)
    return context


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["seed", "verify", "search"])
    args = parser.parse_args()
    async with MemoryClient(settings()) as client:
        if args.command == "seed":
            await seed(client)
        elif args.command == "verify":
            await verify(client)
        else:
            candidates = await client.long_term.search_entities(
                "Maya Chen's employer", threshold=0.0, limit=5
            )
            for entity in candidates:
                print(entity.name, entity.metadata.get("similarity"))
            print(f"Search returned {len(candidates)} candidates; ranking is model-dependent")


if __name__ == "__main__":
    asyncio.run(main())
