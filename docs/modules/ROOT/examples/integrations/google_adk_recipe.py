"""Consume the ADK Runner stream, persist its session, then verify readback."""

import asyncio
import os

from common import settings, verify_messages


async def exercise(client):
    from google.adk.agents import LlmAgent
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.adk.tools import load_memory
    from google.genai import types

    from neo4j_agent_memory.integrations.google_adk import Neo4jMemoryService

    app_name, user_id = "docs-memory", "fictional-user"
    service = Neo4jMemoryService(client, user_id=user_id, extract_on_store=False)
    sessions = InMemorySessionService()
    agent = LlmAgent(
        name="checklist_agent",
        model=os.environ["ADK_MODEL"],
        instruction="Answer briefly. Use load_memory when prior context is needed.",
        tools=[load_memory],
    )
    runner = Runner(
        app_name=app_name, agent=agent, session_service=sessions, memory_service=service
    )
    session = await sessions.create_session(app_name=app_name, user_id=user_id)
    prompt = "The fictional project code is cedar-lantern-47. Acknowledge it briefly."
    async for event in runner.run_async(
        user_id=user_id,
        session_id=session.id,
        new_message=types.Content(role="user", parts=[types.Part(text=prompt)]),
    ):
        if event.error_code:
            raise RuntimeError(f"ADK run failed: {event.error_code}")
        if event.is_final_response() and event.content and event.content.parts:
            print("".join(part.text or "" for part in event.content.parts))
    stored_session = await sessions.get_session(
        app_name=app_name, user_id=user_id, session_id=session.id
    )
    if stored_session is None:
        raise RuntimeError("ADK session is missing after the run")
    await service.add_session_to_memory(stored_session)
    await verify_messages(client, session.id, [prompt])
    response = await service.search_memory(app_name=app_name, user_id=user_id, query="project code")
    print(f"ADK SearchMemoryResponse: {len(response.memories)} candidate(s)")
    return session.id


async def main():
    from neo4j_agent_memory import MemoryClient

    async with MemoryClient(settings()) as client:
        await exercise(client)


if __name__ == "__main__":
    asyncio.run(main())
