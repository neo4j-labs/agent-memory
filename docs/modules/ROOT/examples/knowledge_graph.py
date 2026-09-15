"""Extract a custom domain, preserve message provenance, and query stored edges."""

import argparse
import asyncio
import os
from uuid import UUID

from core_memory_settings import settings

from neo4j_agent_memory import MemoryClient
from neo4j_agent_memory.extraction.gliner_extractor import (
    DomainSchema,
    GLiNEREntityExtractor,
    GLiNERWithRelationsExtractor,
    GLiRELExtractor,
)

SESSION = "docs-knowledge-sources"
DOCUMENTS = {
    "leadership.txt": "Maya Chen is CEO of Northstar Robotics. Northstar Robotics is in Denver.",
    "partnership.txt": "Northstar Robotics partners with Summit Research. Ravi Shah works at Summit Research.",
}
SCHEMA = DomainSchema(
    name="tutorial_business",
    entity_types={
        "person": "A named individual",
        "company": "A named business or organization",
        "location": "A named city or place",
    },
    relation_types={
        "works_at": "Person works at a company",
        "ceo_of": "Person is CEO of a company",
        "located_in": "Company is located in a place",
        "partner_of": "Company partners with another company",
    },
)
EDGES_QUERY = (
    "MATCH (a:Entity)-[r:RELATED_TO]->(b:Entity) "
    "WHERE a.name IN $names AND b.name IN $names "
    "RETURN a.name AS source, r.type AS relation, b.name AS target"
)


def extractor():
    return GLiNERWithRelationsExtractor(
        GLiNEREntityExtractor(
            schema=SCHEMA,
            label_mapping={
                "person": ("PERSON", None),
                "company": ("ORGANIZATION", None),
                "location": ("LOCATION", None),
            },
            threshold=0.5,
        ),
        GLiRELExtractor(relation_types=SCHEMA.relation_types, threshold=0.5),
    )


async def store_document(client, filename, text, result):
    """Resolve endpoints only against unambiguous names in this extraction result."""
    if not result.entities:
        raise RuntimeError(f"No entities extracted from {filename}; inspect model output")
    source = await client.short_term.add_message(
        SESSION, "user", text, metadata={"filename": filename}, extract_entities=False
    )
    by_name = {}
    ambiguous = set()
    for candidate in result.entities:
        entity, _ = await client.long_term.add_entity(
            candidate.name,
            candidate.type,
            subtype=candidate.subtype,
            description=f"Mentioned in {filename}",
            resolve=False,
            deduplicate=False,
        )
        # Repeated name/type writes MERGE in Bolt, but add_entity currently returns
        # a newly allocated ID even on a MERGE hit. Use the persisted ID for links.
        persisted = await client.query.cypher(
            "MATCH (e:Entity {name: $name, type: $type}) RETURN e.id AS id",
            {"name": entity.name, "type": entity.type},
        )
        if len(persisted) != 1:
            raise RuntimeError(f"Expected one stored entity for {entity.name} / {entity.type}")
        entity = entity.model_copy(update={"id": UUID(persisted[0]["id"])})
        key = candidate.name.casefold()
        if key in by_name and by_name[key].id != entity.id:
            ambiguous.add(key)
        by_name[key] = entity
        await client.long_term.link_entity_to_message(
            entity,
            source.id,
            confidence=candidate.confidence,
            start_pos=candidate.start_pos,
            end_pos=candidate.end_pos,
            context=text,
        )
    stored = skipped = 0
    for relation in result.relations:
        start, end = relation.source.casefold(), relation.target.casefold()
        if start not in by_name or end not in by_name or start in ambiguous or end in ambiguous:
            print(f"Skipped unresolved/ambiguous relation: {relation.as_triple}")
            skipped += 1
            continue
        await client.long_term.add_relationship(
            source=by_name[start],
            target=by_name[end],
            relationship_type=relation.relation_type,
            confidence=relation.confidence,
        )
        stored += 1
    print(
        f"{filename}: stored {len(result.entities)} mentions, {stored} relationships; skipped {skipped}"
    )
    return stored


async def ingest(client, selected_extractor):
    if (await client.short_term.get_conversation(SESSION)).messages:
        raise RuntimeError("Already ingested; run inspect or use a fresh database")
    total = 0
    for filename, text in DOCUMENTS.items():
        result = await selected_extractor.extract(text)
        print(f"Candidates for {filename}: {[e.name for e in result.entities]}")
        total += await store_document(client, filename, text, result)
    if total == 0:
        raise RuntimeError("No resolved relationships were stored; inspect extraction output")
    print("Verified: extraction produced storable relationships")


async def inspect_graph(client):
    history = await client.short_term.get_conversation(SESSION)
    if len(history.messages) != len(DOCUMENTS):
        raise RuntimeError("Expected both source documents; run ingest on a fresh database")
    # Query by source-message IDs, so extracted spellings need not match a fixed list.
    provenance = await client.query.cypher(
        "MATCH (e:Entity)-[:EXTRACTED_FROM]->(m:Message) "
        "WHERE m.id IN $ids RETURN DISTINCT e.name AS name",
        {"ids": [str(message.id) for message in history.messages]},
    )
    rows = await client.query.cypher(EDGES_QUERY, {"names": [r["name"] for r in provenance]})
    assert provenance, "Expected EXTRACTED_FROM provenance"
    assert rows, "Expected RELATED_TO edges"
    assert all(isinstance(row["relation"], str) and row["relation"].strip() for row in rows), (
        "Expected nonempty logical relationship names in RELATED_TO.type"
    )
    for row in rows:
        print(f"{row['source']} --{row['relation']}--> {row['target']}")
    print(
        f"Verified: {len(history.messages)} source documents and {len(rows)} relationships read back"
    )
    return rows, history.messages


async def answer(client, llm, model):
    rows, sources = await inspect_graph(client)
    evidence = "\n".join(f"{r['source']} | {r['relation']} | {r['target']}" for r in rows)
    evidence += "\nSources:\n" + "\n".join(message.content for message in sources)
    response = await llm.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "system",
                "content": "Answer only from the supplied graph and source text. "
                "Say when evidence is missing.\n" + evidence,
            },
            {
                "role": "user",
                "content": "Who works at Northstar Robotics, and where is it located?",
            },
        ],
    )
    text = response.choices[0].message.content
    if not text:
        raise RuntimeError("The model returned no text")
    print("Answer using retrieved evidence (inspect its factual accuracy):")
    print(text)


async def main():
    from openai import AsyncOpenAI

    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["ingest", "inspect", "answer"])
    args = parser.parse_args()
    async with MemoryClient(settings()) as client:
        if args.command == "ingest":
            await ingest(client, extractor())
        elif args.command == "inspect":
            await inspect_graph(client)
        else:
            async with AsyncOpenAI() as llm:
                await answer(client, llm, os.environ["OPENAI_MODEL"])


if __name__ == "__main__":
    asyncio.run(main())
