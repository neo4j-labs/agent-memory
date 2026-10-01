"""Use the LangChain 1.x middleware as the sole writer of conversation turns."""

import asyncio
import os
from uuid import uuid4

from common import settings, verify_messages


async def exercise(client, model):
    from langchain.agents import create_agent

    from neo4j_agent_memory.integrations.langchain import Neo4jMemoryMiddleware

    session_id = f"docs-langchain-{uuid4().hex[:8]}"
    prompt = "Suggest a simple project planning checklist."
    agent = create_agent(
        model,
        tools=[],
        system_prompt="Answer briefly using the retrieved context.",
        middleware=[Neo4jMemoryMiddleware(client, session_id=session_id, extract_entities=False)],
    )
    result = await agent.ainvoke({"messages": [("user", prompt)]})
    reply = result["messages"][-1].content
    if not reply:
        raise RuntimeError("LangChain returned an empty assistant message")
    await verify_messages(client, session_id, [prompt, reply])
    return reply


async def main():
    from langchain_openai import ChatOpenAI

    from neo4j_agent_memory import MemoryClient

    async with MemoryClient(settings()) as client:
        print(await exercise(client, ChatOpenAI(model=os.environ["OPENAI_MODEL"])))


if __name__ == "__main__":
    asyncio.run(main())
