"""Round-trip LlamaIndex ChatMessage objects through the shipped async memory."""

import asyncio
from uuid import uuid4

from common import settings


async def exercise(client):
    from llama_index.core.base.llms.types import ChatMessage, MessageRole

    from neo4j_agent_memory.integrations.llamaindex import Neo4jLlamaIndexMemory

    session_id = f"docs-llama-{uuid4().hex[:8]}"
    memory = Neo4jLlamaIndexMemory(memory_client=client, session_id=session_id)
    text = "The fictional project code is cedar-lantern-47."
    await memory.aput(ChatMessage(role=MessageRole.USER, content=text))
    await memory.aput(ChatMessage(role=MessageRole.ASSISTANT, content="Project code recorded."))
    # Rebuild the adapter; its history must come from the supplied storage client.
    restored = Neo4jLlamaIndexMemory(memory_client=client, session_id=session_id)
    messages = await restored.aget_all()
    if not any(message.content == text for message in messages):
        raise RuntimeError(f"LlamaIndex memory readback failed for {session_id}")
    print(f"Verified LlamaIndex ChatMessage readback; session={session_id}")
    return messages


async def main():
    from neo4j_agent_memory import MemoryClient

    async with MemoryClient(settings()) as client:
        await exercise(client)


if __name__ == "__main__":
    asyncio.run(main())
