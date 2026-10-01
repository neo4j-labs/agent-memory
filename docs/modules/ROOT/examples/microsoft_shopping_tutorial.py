"""Small Microsoft Agent Framework shopping exercise; no GDS or hidden catalog."""

import asyncio
import os
from uuid import uuid4

from aura_connection import aura_config


async def main():
    model = os.environ.get("OPENAI_MODEL", "").strip()
    if not model or model.startswith("replace-with-"):
        raise ValueError("Export an accessible OPENAI_MODEL before storing the preference")

    from agent_framework.openai import OpenAIChatClient

    from neo4j_agent_memory import BoltSettings, MemoryClient
    from neo4j_agent_memory.integrations.microsoft_agent import (
        Neo4jMicrosoftMemory,
        record_agent_trace,
    )

    settings = BoltSettings(
        neo4j=aura_config(),
        embedding="openai/text-embedding-3-small",
        extraction={"extractor_type": "none"},
    )
    session_id = f"shopping-docs-{uuid4().hex[:8]}"
    async with MemoryClient(settings) as client:
        preference = await client.long_term.add_preference(
            preference="Maya prefers Northstar running shoes under 150 dollars.",
            category="shopping",
        )
        print(f"Stored explicit preference: {preference.id}")
        memory = Neo4jMicrosoftMemory(
            client,
            session_id,
            extract_entities=False,
            include_reasoning=False,
            similarity_threshold=0.0,
        )
        chat = OpenAIChatClient(model=model, api_key=os.environ["OPENAI_API_KEY"])
        agent = chat.as_agent(
            name="TutorialShoppingAssistant",
            instructions=(
                "Use the provided memory to recommend one item from this fictional catalog: "
                "Northstar Trail Runner costs 120 dollars; Harbor Road Runner costs 180 dollars. "
                "Explain the match. These are tutorial products, not real inventory."
            ),
            context_providers=[memory.context_provider],
        )
        prompt = "Which running shoe fits Maya's preferences and budget?"
        try:
            response = await agent.run(prompt)
        except Exception as exc:
            await record_agent_trace(
                memory,
                messages=[{"role": "user", "content": prompt}],
                task=prompt,
                outcome=f"Agent call failed: {type(exc).__name__}",
                success=False,
            )
            raise
        text = response.text or ""
        if not text.strip():
            raise RuntimeError("The agent returned no text")
        print(f"Assistant: {text}")
        history = (await client.short_term.get_conversation(session_id)).messages
        if not any(message.content == prompt for message in history):
            raise RuntimeError("Context-provider hook did not persist the user turn")
        print("Verified: context-provider hook persisted the user turn")
        trace = await record_agent_trace(
            memory,
            messages=[{"role": "user", "content": prompt}, {"role": "assistant", "content": text}],
            task=prompt,
            outcome="Shopping response recorded",
            success=True,
        )
        recorded = await client.reasoning.get_session_traces(session_id)
        if not any(item.id == trace.id for item in recorded):
            raise RuntimeError("The recorded trace was missing from readback")
        print(f"Verified: trace read back; session={session_id}")


if __name__ == "__main__":
    asyncio.run(main())
