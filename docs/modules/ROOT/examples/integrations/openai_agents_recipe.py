"""Assemble an actual Agents SDK run with explicit message persistence."""

import asyncio
import os
from uuid import uuid4

from common import settings, verify_messages


# tag::function_tool[]
def make_recall_tool(memory):
    from agents import function_tool

    @function_tool
    async def recall_context(query: str) -> str:
        """Retrieve prior conversation context relevant to `query`."""
        return await memory.get_context(query)

    return recall_context


# end::function_tool[]


async def exercise(client, run):
    from neo4j_agent_memory.integrations.openai_agents import Neo4jOpenAIMemory, record_agent_trace

    session_id = f"docs-openai-{uuid4().hex[:8]}"
    memory = Neo4jOpenAIMemory(memory_client=client, session_id=session_id)
    if hasattr(run, "bind_memory"):
        run.bind_memory(memory)
    prompt = "Suggest a simple project planning checklist."
    await memory.save_message("user", prompt, extract_entities=False)
    messages = [{"role": "user", "content": prompt}]
    try:
        context = await memory.get_context(prompt)
        reply = str(await run(prompt, context))
        if not reply.strip():
            raise RuntimeError("The Agents SDK returned no text")
        await memory.save_message("assistant", reply, extract_entities=False)
        messages.append({"role": "assistant", "content": reply})
        await verify_messages(client, session_id, [prompt, reply])
    except Exception as exc:
        await record_agent_trace(
            memory,
            messages=messages,
            task=prompt,
            outcome=f"Run failed: {type(exc).__name__}",
            success=False,
        )
        raise
    trace = await record_agent_trace(
        memory, messages=messages, task=prompt, outcome="Reply stored and read back", success=True
    )
    stored = await client.reasoning.get_trace_with_steps(trace.id)
    if stored is None or stored.success is not True:
        raise RuntimeError(f"Trace readback failed for {trace.id}")
    print(f"Verified task trace: {trace.id}")
    return reply


async def main():
    from agents import Agent, Runner

    from neo4j_agent_memory import MemoryClient

    memory_holder = []

    async def run(prompt, context):
        agent = Agent(
            name="Checklist assistant",
            model=os.environ["OPENAI_MODEL"],
            instructions=f"Answer briefly. Retrieved background:\n{context}",
            tools=[make_recall_tool(memory_holder[0])],
        )
        return (await Runner.run(agent, prompt)).final_output

    run.bind_memory = memory_holder.append

    async with MemoryClient(settings()) as client:
        print(await exercise(client, run))


if __name__ == "__main__":
    asyncio.run(main())
