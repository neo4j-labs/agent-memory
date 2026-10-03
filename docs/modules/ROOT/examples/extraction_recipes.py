"""Complete local-model extraction recipes without a database or LLM API."""

import argparse
import asyncio

from neo4j_agent_memory.extraction.domain_schemas import DomainSchema, get_schema
from neo4j_agent_memory.extraction.gliner2_extractor import GLiNER2Extractor
from neo4j_agent_memory.extraction.pipeline import ExtractionPipeline, MergeStrategy
from neo4j_agent_memory.extraction.streaming import StreamingExtractor
from neo4j_agent_memory.ontology import RelationshipDef

TEXTS = [
    "Maya Chen works at Northstar Robotics in Denver.",
    "Ravi Shah joined Summit Research in Boulder.",
]


# tag::schema[]
def custom_extractor():
    schema = DomainSchema(
        name="support_catalog",
        entity_types={"customer": "A named customer", "product": "A named purchased item"},
    )
    return GLiNER2Extractor(
        ontology=schema,
        label_mapping={"customer": ("PERSON", None), "product": ("OBJECT", "PRODUCT")},
        threshold=0.5,
    )


# end::schema[]


# tag::extract[]
async def extract(selected, text):
    result = await selected.extract(text, extract_relations=False, extract_preferences=False)
    if not result.entities:
        raise RuntimeError("No candidate entities; inspect the input/schema/model threshold")
    for entity in result.entities:
        print(entity.name, entity.type, entity.subtype, entity.confidence)
    print(f"Verified: {result.entity_count} candidate entities returned; inspect their accuracy")
    return result


# end::extract[]


# tag::relations[]
def relation_extractor():
    # The business catalog declares labels only; attach typed relationships.
    ontology = get_schema("business").to_ontology(
        relationships=[
            RelationshipDef(
                type="EMPLOYED_BY",
                source="person",
                target="company",
                description="The person works for or has joined this company",
            ),
            RelationshipDef(
                type="LOCATED_IN",
                source="company",
                target="location",
                description="The company is based or operates in this place",
            ),
        ]
    )
    return GLiNER2Extractor.for_ontology(ontology, threshold=0.5)


async def relations(selected, text):
    result = await selected.extract(text, extract_preferences=False)
    if not result.relations:
        raise RuntimeError("No relations; check the declared relationships and thresholds")
    for relation in result.relations:
        print(relation.source, relation.relation_type, relation.target, relation.confidence)
    print(f"Verified: {len(result.relations)} candidate relations returned; inspect them")
    return result


# end::relations[]


# tag::batch[]
async def batch(selected, texts):
    # Propagate stage errors so the batch can distinguish a failed item from an empty extraction.
    pipeline = ExtractionPipeline(
        stages=[selected], merge_strategy=MergeStrategy.CONFIDENCE, fallback_on_error=False
    )
    result = await pipeline.extract_batch(
        texts,
        batch_size=2,
        max_concurrency=1,
        fail_fast=False,
        extract_relations=False,
        extract_preferences=False,
        on_progress=lambda done, total: print(f"Progress: {done}/{total}"),
    )
    assert result.total_items == len(texts)
    for item in result.results:
        if item.success:
            print(f"Input {item.index}: {item.result.entity_count} entities")
        else:
            print(f"Input {item.index} failed: {item.error}")
    if result.failed_items:
        raise RuntimeError(
            f"Retry failed source indexes after fixing errors: {result.get_errors()}"
        )
    print(f"Verified: all {result.total_items} inputs accounted for")
    return result


# end::batch[]


# tag::streaming[]
async def streaming(selected, text):
    streamer = StreamingExtractor(selected, chunk_size=400, overlap=40, chunk_by_tokens=False)
    result = await streamer.extract(text, extract_relations=False)
    errors = [
        (chunk.chunk.index, chunk.error) for chunk in result.chunk_results if not chunk.success
    ]
    if errors:
        raise RuntimeError(f"Chunk extraction failed: {errors}")
    assert result.chunk_results
    combined = result.to_extraction_result(source_text=text)
    print(
        f"Verified: {len(result.chunk_results)} chunks completed; {combined.entity_count} merged entities"
    )
    return result


# end::streaming[]


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["extract", "schema", "relations", "batch", "streaming"])
    args = parser.parse_args()
    if args.command == "schema":
        await extract(custom_extractor(), "Maya Chen bought a Trail Starter shoe.")
    elif args.command == "relations":
        await relations(relation_extractor(), TEXTS[0])
    else:
        selected = GLiNER2Extractor.for_schema("business")
        if args.command == "extract":
            await extract(selected, TEXTS[0])
        elif args.command == "batch":
            await batch(selected, TEXTS)
        else:
            await streaming(selected, "\n".join(TEXTS * 12))


if __name__ == "__main__":
    asyncio.run(main())
