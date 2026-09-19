"""Complete local-model extraction recipes without a database or LLM API."""

import argparse
import asyncio

from neo4j_agent_memory.extraction.gliner_extractor import DomainSchema, GLiNEREntityExtractor
from neo4j_agent_memory.extraction.pipeline import ExtractionPipeline, MergeStrategy
from neo4j_agent_memory.extraction.streaming import StreamingExtractor

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
    return GLiNEREntityExtractor(
        schema=schema,
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
    parser.add_argument("command", choices=["extract", "schema", "batch", "streaming"])
    args = parser.parse_args()
    if args.command == "schema":
        await extract(custom_extractor(), "Maya Chen bought a Trail Starter shoe.")
    else:
        selected = GLiNEREntityExtractor.for_schema("business")
        if args.command == "extract":
            await extract(selected, TEXTS[0])
        elif args.command == "batch":
            await batch(selected, TEXTS)
        else:
            await streaming(selected, "\n".join(TEXTS * 12))


if __name__ == "__main__":
    asyncio.run(main())
