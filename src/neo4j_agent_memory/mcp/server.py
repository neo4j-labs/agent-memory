"""MCP Server implementation for Neo4j Agent Memory.

Provides a Model Context Protocol server using FastMCP that exposes
memory capabilities as tools, resources, and prompts for AI platforms.

Supports two tool profiles:
- Core (6 tools): Essential read/write cycle
- Extended (16 tools): Full surface with reasoning, entities, graph export
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from neo4j_agent_memory import MemoryClient

logger = logging.getLogger(__name__)

try:
    from fastmcp import FastMCP

    def create_mcp_server(
        settings: Any = None,
        *,
        server_name: str = "neo4j-agent-memory",
        profile: str = "extended",
        session_strategy: str = "per_conversation",
        user_id: str | None = None,
        observation_threshold: int = 30000,
        auto_preferences: bool = True,
    ) -> FastMCP:
        """Create a configured FastMCP server.

        The server uses a lifespan to manage the async MemoryClient and
        MemoryIntegration lifecycle. Tools, resources, and prompts are
        registered based on the selected profile.

        Args:
            settings: MemorySettings for Neo4j connection. If None, the server
                is created without a lifespan (useful for testing).
            server_name: Server name for MCP registration.
            profile: Tool profile - 'core' (6 tools) or 'extended' (16 tools).
            session_strategy: Session ID strategy - 'per_conversation',
                'per_day', or 'persistent'.
            user_id: User identifier for per_day and persistent strategies.
            observation_threshold: Token count threshold for triggering
                observational memory compression (default: 30000).
            auto_preferences: Whether to auto-detect preferences from
                user messages (default: True).

        Returns:
            Configured FastMCP server instance.
        """
        from neo4j_agent_memory.mcp._instructions import get_instructions

        lifespan = None
        if settings is not None:

            @asynccontextmanager
            async def lifespan(server: FastMCP):
                """Manage MemoryClient, MemoryIntegration, and Observer lifecycle."""
                from neo4j_agent_memory import MemoryClient as _MemoryClient
                from neo4j_agent_memory.integration import MemoryIntegration
                from neo4j_agent_memory.mcp._observer import MemoryObserver

                async with _MemoryClient(settings) as client:
                    observer = MemoryObserver(
                        client,
                        threshold_tokens=observation_threshold,
                    )
                    integration = MemoryIntegration(
                        client,
                        session_strategy=session_strategy,
                        user_id=user_id,
                        auto_extract=True,
                        auto_preferences=auto_preferences,
                    )
                    integration.observer = observer
                    yield {
                        "client": client,
                        "integration": integration,
                        "observer": observer,
                    }

        mcp = FastMCP(
            server_name,
            instructions=get_instructions(profile),
            lifespan=lifespan,
        )

        from neo4j_agent_memory.mcp._prompts import register_prompts
        from neo4j_agent_memory.mcp._resources import register_resources
        from neo4j_agent_memory.mcp._tools import register_tools

        register_tools(mcp, profile=profile)
        register_resources(mcp, profile=profile)
        register_prompts(mcp, profile=profile)

        return mcp

    class Neo4jMemoryMCPServer:
        """MCP server exposing Neo4j Agent Memory capabilities.

        Backward-compatible wrapper that accepts a pre-connected MemoryClient.
        For new code, prefer ``create_mcp_server(settings)`` instead.

        Example:
            from neo4j_agent_memory import MemoryClient, MemorySettings
            from neo4j_agent_memory.mcp import Neo4jMemoryMCPServer

            settings = MemorySettings(...)
            async with MemoryClient(settings) as client:
                server = Neo4jMemoryMCPServer(client)
                await server.run()

        Tools (extended profile):
            Core: memory_search, memory_get_context, memory_store_message,
                  memory_add_entity, memory_add_preference, memory_add_fact
            Extended: memory_get_conversation, memory_list_sessions,
                  memory_get_entity, memory_export_graph,
                  memory_create_relationship, memory_start_trace,
                  memory_record_step, memory_complete_trace,
                  memory_get_observations, graph_query
        """

        def __init__(
            self,
            memory_client: MemoryClient,
            *,
            server_name: str = "neo4j-agent-memory",
            profile: str = "extended",
            session_strategy: str = "per_conversation",
            user_id: str | None = None,
            observation_threshold: int = 30000,
            auto_preferences: bool = True,
        ):
            """Initialize the MCP server with a pre-connected client.

            Args:
                memory_client: Connected MemoryClient instance.
                server_name: Server name for MCP registration.
                profile: Tool profile - 'core' or 'extended'.
                session_strategy: Session ID strategy.
                user_id: User identifier for session strategies.
                observation_threshold: Token threshold for observer compression.
                auto_preferences: Whether to auto-detect preferences.
            """
            self._client = memory_client

            from neo4j_agent_memory.integration import MemoryIntegration
            from neo4j_agent_memory.mcp._instructions import get_instructions
            from neo4j_agent_memory.mcp._observer import MemoryObserver

            observer = MemoryObserver(
                memory_client,
                threshold_tokens=observation_threshold,
            )
            integration = MemoryIntegration(
                memory_client,
                session_strategy=session_strategy,
                user_id=user_id,
                auto_preferences=auto_preferences,
            )
            integration.observer = observer

            @asynccontextmanager
            async def _preconnected_lifespan(server: FastMCP):
                yield {
                    "client": memory_client,
                    "integration": integration,
                    "observer": observer,
                }

            self._mcp = FastMCP(
                server_name,
                instructions=get_instructions(profile),
                lifespan=_preconnected_lifespan,
            )

            from neo4j_agent_memory.mcp._prompts import register_prompts
            from neo4j_agent_memory.mcp._resources import register_resources
            from neo4j_agent_memory.mcp._tools import register_tools

            register_tools(self._mcp, profile=profile)
            register_resources(self._mcp, profile=profile)
            register_prompts(self._mcp, profile=profile)

        async def run(self) -> None:
            """Run the MCP server using stdio transport."""
            await self._mcp.run_async(transport="stdio")

        async def run_sse(self, host: str = "127.0.0.1", port: int = 8080) -> None:
            """Run the MCP server using SSE transport.

            Args:
                host: Host to bind to.
                port: Port to listen on.
            """
            await self._mcp.run_async(transport="sse", host=host, port=port)

    async def run_server(
        neo4j_uri: str,
        neo4j_user: str,
        neo4j_password: str,
        neo4j_database: str = "neo4j",
        transport: str = "stdio",
        host: str = "127.0.0.1",
        port: int = 8080,
        profile: str = "extended",
        session_strategy: str = "per_conversation",
        user_id: str | None = None,
        observation_threshold: int = 30000,
        auto_preferences: bool = True,
        parent_death_check: bool = True,
        parent_death_poll_interval: float = 5.0,
    ) -> None:
        """Run the MCP server with Neo4j connection.

        Convenience function for CLI usage.

        Args:
            neo4j_uri: Neo4j connection URI.
            neo4j_user: Neo4j username.
            neo4j_password: Neo4j password.
            neo4j_database: Neo4j database name.
            transport: Transport type (stdio, sse, or http).
            host: Host for network transports.
            port: Port for network transports.
            profile: Tool profile ('core' or 'extended').
            session_strategy: Session ID strategy.
            user_id: User identifier for session strategies.
            observation_threshold: Token threshold for observer compression.
            auto_preferences: Whether to auto-detect preferences.
            parent_death_check: When True (default) and using the stdio
                transport, the server self-terminates if its parent process
                exits (reparented to PID 1). Has no effect for SSE/HTTP
                transports, which are expected to outlive their starter.
            parent_death_poll_interval: Seconds between parent-process checks.
                Only used when ``parent_death_check`` is True.
        """
        from pydantic import SecretStr

        from neo4j_agent_memory import MemorySettings
        from neo4j_agent_memory.config.settings import Neo4jConfig

        settings = MemorySettings(
            neo4j=Neo4jConfig(
                uri=neo4j_uri,
                username=neo4j_user,
                password=SecretStr(neo4j_password),
                database=neo4j_database,
            )
        )

        server = create_mcp_server(
            settings,
            server_name="neo4j-agent-memory",
            profile=profile,
            session_strategy=session_strategy,
            user_id=user_id,
            observation_threshold=observation_threshold,
            auto_preferences=auto_preferences,
        )

        if transport == "sse":
            await server.run_async(transport="sse", host=host, port=port)
        elif transport == "http":
            await server.run_async(transport="http", host=host, port=port)
        elif parent_death_check:
            from neo4j_agent_memory.mcp._lifecycle import run_with_watchdog

            await run_with_watchdog(
                server.run_async(transport="stdio"),
                poll_interval=parent_death_poll_interval,
            )
        else:
            await server.run_async(transport="stdio")

except ImportError:
    # FastMCP not installed
    class Neo4jMemoryMCPServer:  # type: ignore[no-redef]
        """Placeholder when FastMCP is not installed."""

        def __init__(self, *args: Any, **kwargs: Any):
            raise ImportError(
                "FastMCP not installed. Install with: pip install neo4j-agent-memory[mcp]"
            )

    def create_mcp_server(*args: Any, **kwargs: Any) -> Neo4jMemoryMCPServer:  # type: ignore[misc]
        raise ImportError(
            "FastMCP not installed. Install with: pip install neo4j-agent-memory[mcp]"
        )

    async def run_server(*args: Any, **kwargs: Any) -> None:
        raise ImportError(
            "FastMCP not installed. Install with: pip install neo4j-agent-memory[mcp]"
        )


def main() -> None:
    """CLI entry point for running the MCP server."""
    import argparse
    import os

    parser = argparse.ArgumentParser(description="Neo4j Agent Memory MCP Server")
    parser.add_argument(
        "--neo4j-uri",
        default=os.environ.get("NEO4J_URI", "bolt://localhost:7687"),
        help="Neo4j connection URI",
    )
    parser.add_argument(
        "--neo4j-user",
        default=os.environ.get("NEO4J_USER", "neo4j"),
        help="Neo4j username",
    )
    parser.add_argument(
        "--neo4j-password",
        default=os.environ.get("NEO4J_PASSWORD", ""),
        help="Neo4j password",
    )
    parser.add_argument(
        "--neo4j-database",
        default=os.environ.get("NEO4J_DATABASE", "neo4j"),
        help="Neo4j database name",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse", "http"],
        default="stdio",
        help="MCP transport type",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host for network transports (use 0.0.0.0 to expose on all interfaces)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="Port for network transports",
    )
    parser.add_argument(
        "--profile",
        choices=["core", "extended"],
        default="extended",
        help="Tool profile: core (6 tools) or extended (16 tools, default)",
    )
    parser.add_argument(
        "--session-strategy",
        choices=["per_conversation", "per_day", "persistent"],
        default="per_conversation",
        help="Session identity strategy (default: per_conversation)",
    )
    parser.add_argument(
        "--user-id",
        default=os.environ.get("MCP_USER_ID"),
        help="User ID for per_day/persistent session strategies",
    )
    parser.add_argument(
        "--observation-threshold",
        type=int,
        default=30000,
        help="Token threshold for observational memory compression (default: 30000)",
    )
    parser.add_argument(
        "--no-auto-preferences",
        action="store_true",
        default=False,
        help="Disable automatic preference detection from user messages",
    )
    parser.add_argument(
        "--no-parent-death-check",
        action="store_true",
        default=False,
        help=(
            "Disable parent-death detection on stdio transport. "
            "By default, the server self-terminates if its parent process "
            "exits (avoids orphaned servers when an MCP client crashes)."
        ),
    )
    parser.add_argument(
        "--parent-death-poll-interval",
        type=float,
        default=5.0,
        help="Seconds between parent-process checks (default: 5.0).",
    )

    args = parser.parse_args()

    asyncio.run(
        run_server(
            neo4j_uri=args.neo4j_uri,
            neo4j_user=args.neo4j_user,
            neo4j_password=args.neo4j_password,
            neo4j_database=args.neo4j_database,
            transport=args.transport,
            host=args.host,
            port=args.port,
            profile=args.profile,
            session_strategy=args.session_strategy,
            user_id=args.user_id,
            observation_threshold=args.observation_threshold,
            auto_preferences=not args.no_auto_preferences,
            parent_death_check=not args.no_parent_death_check,
            parent_death_poll_interval=args.parent_death_poll_interval,
        )
    )


if __name__ == "__main__":
    main()
