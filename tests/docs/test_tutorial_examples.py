"""Run the Bolt tutorial programs against Neo4j.

Each tutorial page includes a complete maintained program from
``docs/modules/ROOT/examples``. These tests run the phases a reader runs, in
order, through the program's own command line, against the Neo4j test database:

* ``first_agent_memory.py``: seed, verify, search
* ``conversation_memory.py``: seed, resume
* ``knowledge_graph.py``: ingest, inspect, answer
* ``anthropic_local_memory.py``: the single run
* ``mcp_local_tutorial.py``: the stdio server, driven in process by an MCP client

The ``live_programs`` fixture replaces the Aura connection, the embedding
provider and the chat or extraction model calls; every SDK call and Cypher
query runs for real. The three lessons that use fixed session names get an
empty database, as their pages ask for.
"""

from __future__ import annotations

import inspect
import json

import pytest

pytestmark = [pytest.mark.docs, pytest.mark.integration]


async def test_first_agent_memory_phases(live_programs, capsys):
    live_programs.wipe()
    lesson = live_programs.load("first_agent_memory")

    await live_programs.run(lesson, "seed")
    await live_programs.run(lesson, "verify")
    await live_programs.run(lesson, "search")

    out = capsys.readouterr().out
    assert "Verified: stored history, preference, and relationship survived restart" in out
    assert "Maya Chen works at Northstar Robotics" in out
    assert "Search returned" in out
    with pytest.raises(RuntimeError, match="already seeded"):
        await live_programs.run(lesson, "seed")


async def test_conversation_memory_phases(live_programs, capsys):
    live_programs.wipe()
    lesson = live_programs.load("conversation_memory")

    await live_programs.run(lesson, "seed")
    await live_programs.run(lesson, "resume")

    assert "Verified: new response and product-lookup trace read back" in (capsys.readouterr().out)
    [request] = live_programs.chat.requests
    system, *history, question = request["messages"]
    assert "Spend at most USD 60 on walking shoes" in system["content"]
    assert "Trail Starter" in system["content"]
    assert [message["role"] for message in history] == ["user", "assistant"]
    assert question["content"] == "What would you recommend for the trip I mentioned?"


async def test_knowledge_graph_phases(live_programs, capsys):
    lesson = live_programs.load("knowledge_graph")
    if "GLiNER" in inspect.getsource(lesson.extractor):
        pytest.importorskip("gliner2", reason="the lesson's extractor needs the gliner2 extra")
    live_programs.wipe()

    await live_programs.run(lesson, "ingest")
    await live_programs.run(lesson, "inspect")
    await live_programs.run(lesson, "answer")

    out = capsys.readouterr().out
    assert "Verified: extraction produced storable relationships" in out
    assert "relationships read back" in out
    [request] = live_programs.chat.requests
    assert "Northstar Robotics" in request["messages"][0]["content"]


async def test_anthropic_local_memory_run(live_programs, capsys):
    lesson = live_programs.load("anthropic_local_memory")

    await live_programs.run(lesson)

    out = capsys.readouterr().out
    assert "Verified message readback" in out
    assert "Entity candidate: Maya Chen" in out
    assert "Verified: context assembly returned text" in out
    assert live_programs.llm.prompts, "the message was not sent for extraction"


async def test_mcp_local_tutorial_server_stores_and_reads_back(live_programs):
    fastmcp = pytest.importorskip("fastmcp", reason="the MCP lesson needs the mcp extra")
    lesson = live_programs.load("mcp_local_tutorial")
    session = "docs-mcp-lesson"
    content = "Remember that Maya Chen prefers concise answers."

    async with fastmcp.Client(lesson.build_server()) as client:
        tools = {tool.name for tool in await client.list_tools()}
        stored = await client.call_tool(
            "memory_store_message", {"content": content, "session_id": session, "role": "user"}
        )
        context = await client.call_tool("memory_get_context", {"session_id": session})

    assert {"memory_store_message", "memory_get_context", "memory_search"} <= tools
    assert json.loads(stored.content[0].text)["stored"] is True
    assert content in context.content[0].text
