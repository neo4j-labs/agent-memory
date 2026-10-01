"""Check the selected cloud embedder, then store and read back one message."""

import argparse
import asyncio
import os
from uuid import uuid4

from common import settings, verify_messages


def build_embedder(provider):
    if provider == "bedrock":
        from neo4j_agent_memory.llm.adapters.bedrock import BedrockEmbeddingProvider

        return BedrockEmbeddingProvider(
            "bedrock/amazon.titan-embed-text-v2:0", aws_region=os.environ["AWS_REGION"]
        )
    from neo4j_agent_memory.llm.adapters.vertex_ai import VertexAIEmbeddingProvider

    return VertexAIEmbeddingProvider(
        "vertex_ai/gemini-embedding-001",
        project_id=os.environ["GOOGLE_CLOUD_PROJECT"],
        location=os.environ["GOOGLE_CLOUD_LOCATION"],
        dimensions=768,
    )


async def verify_vectors(embedder):
    texts = ["A fictional workshop in Denver", "A fictional workshop in Boston"]
    vectors = await embedder.embed(texts)
    if len(vectors) != len(texts) or any(len(v) != embedder.dimensions for v in vectors):
        raise RuntimeError(
            "Embedding count or vector dimensions did not match the selected configuration"
        )
    print(f"Verified {len(vectors)} vectors of {embedder.dimensions} dimensions")


async def main(provider):
    from neo4j_agent_memory import MemoryClient

    embedder = build_embedder(provider)
    await verify_vectors(embedder)
    async with MemoryClient(settings(embedding=embedder)) as client:
        session_id = f"docs-{provider}-{uuid4().hex[:8]}"
        text = "The fictional workshop takes place in Denver."
        await client.short_term.add_message(session_id, "user", text, extract_entities=False)
        await verify_messages(client, session_id, [text])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("provider", choices=["bedrock", "vertex"])
    asyncio.run(main(parser.parse_args().provider))
