"""Smallest hosted round trip: write one message, read it back."""

import asyncio

from neo4j_agent_memory import MemoryClient, NamsSettings


async def main() -> None:
    settings = NamsSettings()  # reads MEMORY_API_KEY from the environment
    async with MemoryClient(settings) as client:
        session_id = "docs-nams-first-write"
        await client.short_term.add_message(session_id, "user", "Hello, NAMS!")
        conversation = await client.short_term.get_conversation(session_id)
        last = conversation.messages[-1]
        print(f"Stored and read back: {last.role.value} said {last.content!r}")


if __name__ == "__main__":
    asyncio.run(main())
