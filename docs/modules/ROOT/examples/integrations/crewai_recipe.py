"""Keep async memory I/O outside CrewAI's synchronous kickoff."""

import asyncio
import os
from uuid import uuid4

from common import recorded_turn, settings


def run_crew(context):
    from crewai import LLM, Agent, Crew, Task

    planner = Agent(
        role="Project planner",
        goal="Produce a concise project checklist",
        backstory="You organize fictional project exercises.",
        llm=LLM(model=os.environ["CREWAI_MODEL"]),
        allow_delegation=False,
    )
    task = Task(
        description=(
            "Suggest a simple project planning checklist. Retrieved background:\n" + context
        ),
        expected_output="A short checklist",
        agent=planner,
    )
    return str(Crew(agents=[planner], tasks=[task], memory=False).kickoff())


async def exercise(client, kickoff=run_crew):
    async def respond(context):
        # The worker runs framework code only. The connected async client stays on its owning loop.
        return await asyncio.to_thread(kickoff, context)

    return await recorded_turn(
        client,
        f"docs-crew-{uuid4().hex[:8]}",
        "Suggest a simple project planning checklist.",
        respond,
    )


async def main():
    from neo4j_agent_memory import MemoryClient

    async with MemoryClient(settings()) as client:
        print(await exercise(client))


if __name__ == "__main__":
    asyncio.run(main())
