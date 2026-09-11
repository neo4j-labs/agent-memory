#!/usr/bin/env python3
"""MCP Server Demo.

Demonstrates the Neo4j Agent Memory MCP server: 6 core tools, 16 in the
extended profile (the default).

Features demonstrated:
- Tool profiles (core vs extended) listed from a live server
- Tool invocation through ``fastmcp.Client``, reading ``result.data``
- Starting the server over stdio and Streamable HTTP
- ``--schemas`` prints every tool's JSON input schema (no database needed)

Requirements:
    pip install "neo4j-agent-memory[mcp]"     # FastMCP 4 / MCP Python SDK 2

Note on transports: FastMCP 4 serves stdio and Streamable HTTP at ``/mcp/``. The
legacy HTTP+SSE transport is deprecated in the MCP spec; ``--transport sse``
still starts a server but warns and serves Streamable HTTP.

Runs with no API key: the shared settings helper falls back to a local
sentence-transformers embedder. Set ``MEMORY_API_KEY`` to target hosted NAMS.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime
from typing import Any

from _common import build_settings, describe_settings, load_env


def tool_payload(result: Any) -> dict[str, Any]:
    """Read a tool result.

    ``result.data`` is FastMCP 4's parsed output — preferred over reaching into
    ``result.content[0].text``. Every memory tool is annotated ``-> str`` and
    returns ``json.dumps(...)``, so the parsed value is itself a JSON string
    that still needs one ``json.loads``.
    """
    return dict(json.loads(result.data))


async def demo_server_tools() -> None:
    """Demonstrate MCP server tools and their schemas."""
    from fastmcp import Client

    from neo4j_agent_memory.mcp.server import create_mcp_server

    print("=" * 60)
    print("MCP Server - Tool Profiles")
    print("=" * 60)
    print()

    # Show core profile
    print("Core Profile (6 tools):")
    print("-" * 40)
    core_server = create_mcp_server(profile="core")  # No settings → testing mode
    async with Client(core_server) as client:
        tools = await client.list_tools()
        for i, tool in enumerate(tools, 1):
            print(f"  {i}. {tool.name} - {tool.description[:60]}...")
    print()

    # Show extended profile
    print("Extended Profile (16 tools, default):")
    print("-" * 40)
    extended_server = create_mcp_server(profile="extended")
    async with Client(extended_server) as client:
        tools = await client.list_tools()
        for i, tool in enumerate(tools, 1):
            print(f"  {i:2d}. {tool.name}")
    print()


async def demo_tool_usage() -> None:
    """Demonstrate how tools are used via FastMCP Client."""
    from neo4j_agent_memory.mcp.server import create_mcp_server

    print("=" * 60)
    print("MCP Server - Tool Usage Examples")
    print("=" * 60)
    print()

    settings = build_settings()
    print("Configuration:")
    describe_settings(settings)
    print()

    server = create_mcp_server(settings, profile="extended")

    from fastmcp import Client

    async with Client(server) as client:
        session_id = f"mcp-demo-{datetime.now().strftime('%Y%m%d%H%M%S')}"

        # 1. memory_store_message - Store a message
        print("1. memory_store_message - Storing a message")
        print("-" * 40)

        result = await client.call_tool(
            "memory_store_message",
            {
                "content": "I'm working on the Q1 report with the finance team.",
                "session_id": session_id,
                "role": "user",
            },
        )
        data = tool_payload(result)
        print(f"   Stored message ID: {data.get('id', 'N/A')}")
        print()

        # Store another for search
        await client.call_tool(
            "memory_store_message",
            {
                "content": "The deadline for the Q1 report is next Friday.",
                "session_id": session_id,
                "role": "assistant",
            },
        )

        # 2. memory_get_context - Get assembled context
        print("2. memory_get_context - Getting session context")
        print("-" * 40)

        result = await client.call_tool(
            "memory_get_context",
            {"session_id": session_id},
        )
        data = tool_payload(result)
        print(f"   Session: {data.get('session_id')}")
        print(f"   Has context: {data.get('has_context')}")
        print()

        # 3. memory_search - Search memories
        print("3. memory_search - Searching memories")
        print("-" * 40)

        result = await client.call_tool(
            "memory_search",
            {"query": "Q1 report deadline", "limit": 5},
        )
        data = tool_payload(result)
        results = data.get("results", {})
        total = sum(len(v) for v in results.values())
        print("   Query: 'Q1 report deadline'")
        print(f"   Results: {total} found")
        print()

        # 4. memory_add_preference - Store a preference
        print("4. memory_add_preference - Storing a preference")
        print("-" * 40)

        result = await client.call_tool(
            "memory_add_preference",
            {
                "category": "communication",
                "preference": "Prefers detailed weekly status reports",
            },
        )
        data = tool_payload(result)
        print(f"   Stored preference ID: {data.get('id', 'N/A')}")
        print()

        # 5. memory_get_conversation - Get session history
        print("5. memory_get_conversation - Getting session history")
        print("-" * 40)

        result = await client.call_tool(
            "memory_get_conversation",
            {"session_id": session_id, "limit": 10},
        )
        data = tool_payload(result)
        print(f"   Session: {session_id}")
        print(f"   Messages: {data.get('message_count', 0)}")
        print()

        # 6. graph_query - Execute Cypher query
        print("6. graph_query - Executing Cypher query")
        print("-" * 40)

        result = await client.call_tool(
            "graph_query",
            {"query": "MATCH (m:Message) RETURN count(m) as message_count"},
        )
        data = tool_payload(result)
        print("   Query: MATCH (m:Message) RETURN count(m)")
        print(f"   Result: {data.get('rows', [])}")
        print()

        print("   Note: graph_query only allows read-only queries.")
        print("   Write operations (CREATE, MERGE, DELETE) are blocked.")
        print()


async def demo_server_startup() -> None:
    """Show how to start the MCP server."""
    print("=" * 60)
    print("MCP Server - Starting the Server")
    print("=" * 60)
    print()

    print("Option 1: Using the CLI (recommended)")
    print("-" * 40)
    print("""
# Start with stdio transport (for Claude Desktop)
neo4j-agent-memory mcp serve --password secret

# Start with Streamable HTTP (for Cloud Run/HTTP) — endpoint is /mcp/
neo4j-agent-memory mcp serve --transport http --host 0.0.0.0 --port 8080 --password secret

# Core profile (fewer tools, less context overhead)
neo4j-agent-memory mcp serve --profile core --password secret

# With session strategy
neo4j-agent-memory mcp serve --session-strategy per_day --user-id alice --password secret
""")

    print("Option 2: Programmatically")
    print("-" * 40)
    print("""
import asyncio
from neo4j_agent_memory import MemorySettings
from neo4j_agent_memory.mcp.server import create_mcp_server

settings = MemorySettings(...)
server = create_mcp_server(settings, profile="extended")

# stdio transport
await server.run_async(transport="stdio")

# Or Streamable HTTP, served at /mcp/
await server.run_async(transport="http", host="0.0.0.0", port=8080)
""")

    print("Option 3: Claude Desktop Configuration")
    print("-" * 40)
    print("""
Add to ~/Library/Application Support/Claude/claude_desktop_config.json:

{
  "mcpServers": {
    "neo4j-agent-memory": {
      "command": "neo4j-agent-memory",
      "args": ["mcp", "serve", "--password", "your-password"],
      "env": {
        "OPENAI_API_KEY": "sk-..."
      }
    }
  }
}
""")


async def demo_tool_schemas() -> None:
    """Show the JSON schemas for MCP tool inputs."""
    from fastmcp import Client

    from neo4j_agent_memory.mcp.server import create_mcp_server

    server = create_mcp_server(profile="extended")

    print("=" * 60)
    print("MCP Server - Tool JSON Schemas (Extended Profile)")
    print("=" * 60)
    print()

    async with Client(server) as client:
        tools = await client.list_tools()
        for tool in tools:
            print(f"### {tool.name}")
            print("```json")
            print(
                json.dumps(
                    {
                        "name": tool.name,
                        "description": tool.description,
                        "inputSchema": tool.input_schema,
                    },
                    indent=2,
                )
            )
            print("```")
            print()


async def main(*, schemas: bool = False, tools: bool = True) -> None:
    """Run the MCP server demos."""
    load_env()

    print("\n" + "=" * 60)
    print("Neo4j Agent Memory - MCP Server Demo")
    print("=" * 60 + "\n")

    await demo_server_tools()
    await demo_server_startup()

    if schemas:
        await demo_tool_schemas()

    if tools:
        await demo_tool_usage()
    else:
        print("Skipping the tool-usage phase (--no-tools)")
        print()

    print("\n" + "=" * 60)
    print("Demo complete!")
    print("=" * 60 + "\n")
    print("To start the server, run:")
    print("  neo4j-agent-memory mcp serve --password <your-password>")
    print("  neo4j-agent-memory mcp serve --transport http --host 0.0.0.0 --port 8080")
    print()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--schemas",
        action="store_true",
        help="print every extended-profile tool's JSON input schema (no database needed)",
    )
    parser.add_argument(
        "--no-tools",
        action="store_true",
        help="skip the tool-usage phase, which needs a reachable Neo4j",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = parse_args()
    asyncio.run(main(schemas=args.schemas, tools=not args.no_tools))
