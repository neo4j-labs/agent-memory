"""Explicit Anthropic extraction with local embeddings and storage in AuraDB."""

import asyncio
import os
from uuid import uuid4

from aura_connection import aura_config


async def main():
    from neo4j_agent_memory import MemoryClient, MemorySettings
    from neo4j_agent_memory.llm import from_provider

    llm = from_provider(f"anthropic/{os.environ['ANTHROPIC_MODEL']}", kind="llm")
    embedding = from_provider("BAAI/bge-small-en-v1.5", kind="embedding")
    print(f"LLM adapter: {type(llm).__name__}")
    print(f"Embedding adapter: {type(embedding).__name__}; dimensions={embedding.dimensions}")
    settings = MemorySettings(
        backend="bolt",
        neo4j=aura_config(),
        llm=llm,
        embedding=embedding,
        extraction={"extractor_type": "llm"},
    )
    session_id = f"anthropic-tutorial-{uuid4().hex[:8]}"
    async with MemoryClient(settings) as client:
        message = await client.short_term.add_message(
            session_id=session_id,
            role="user",
            content="Maya Chen works at Northstar Robotics in Denver.",
            extract_entities=True,
        )
        history = (await client.short_term.get_conversation(session_id)).messages
        if not any(stored.id == message.id for stored in history):
            raise RuntimeError("The saved message was missing from readback")
        print(f"Verified message readback; session={session_id}")
        entities = await client.long_term.search_entities("Maya Chen", limit=5)
        if not entities:
            raise RuntimeError(
                "No entity candidates returned; inspect extraction before continuing"
            )
        for entity in entities:
            print(f"Entity candidate: {entity.name} ({entity.full_type})")
        context = await client.get_context("Maya Chen", session_id=session_id)
        if not context.strip():
            raise RuntimeError("Context assembly returned empty text")
        print("Verified: context assembly returned text")
        print(context)


if __name__ == "__main__":
    asyncio.run(main())
