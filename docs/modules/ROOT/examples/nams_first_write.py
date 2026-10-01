"""Smallest hosted round trip: create, write, read back, then delete by returned ID."""

import asyncio

from neo4j_agent_memory import MemoryClient, NamsSettings, NotFoundError

# Entities NAMS extracted from this message. Deleting the conversation does not
# remove them, and an extracted entity can be shared with other workspace data.
EXTRACTED_ENTITIES = "MATCH (m {id: $id})-[:EXTRACTED_FROM]-(e:Entity) RETURN e.id AS id"


async def main() -> None:
    settings = NamsSettings()  # reads MEMORY_API_KEY from the environment
    async with MemoryClient(settings) as client:
        # NAMS assigns the conversation ID; this argument is not sent to the service.
        conversation = await client.short_term.create_conversation("docs-nams-first-write")
        conversation_id = str(conversation.id)
        print(f"Created conversation {conversation_id}")
        content = "Hello from my first hosted write."
        message = await client.short_term.add_message(conversation_id, "user", content)
        history = await client.short_term.get_conversation(conversation_id)
        stored = [item for item in history.messages if item.id == message.id]
        if len(stored) != 1 or stored[0].content != content:
            raise RuntimeError(f"Message {message.id} was not read back; conversation kept")
        print(f"Stored and read back: {stored[0].role.value} said {stored[0].content!r}")

        # Let background extraction finish before deleting its source conversation.
        settled = await client.long_term.wait_for_extraction(
            session_id=conversation_id, timeout=60.0
        )
        if not settled:
            raise TimeoutError(f"Extraction still pending; conversation {conversation_id} kept")
        rows = await client.query.cypher(EXTRACTED_ENTITIES, {"id": str(message.id)})

        await client.short_term.clear_session(conversation_id)
        try:
            await client.short_term.get_conversation(conversation_id)
        except NotFoundError:
            print(f"Deleted conversation {conversation_id}; readback returned 404")
        else:
            raise RuntimeError(f"Conversation {conversation_id} still exists after DELETE")
        if rows:
            ids = sorted(str(row["id"]) for row in rows)
            print(f"Kept entities extracted from the message (not deleted here): {ids}")


if __name__ == "__main__":
    asyncio.run(main())
