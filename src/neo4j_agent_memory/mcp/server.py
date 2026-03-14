"""MCP Server implementation for Neo4j Agent Memory.

Provides a Model Context Protocol server using FastMCP that exposes
memory capabilities as tools, resources, and prompts for AI platforms.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from neo4j_agent_memory import MemoryClient

logger = logging.getLogger(__name__)


def _build_cors_middleware(allow_origins: list[str] | None = None) -> list[Any]:
    """Build Starlette CORS middleware list for FastMCP.

    Args:
        allow_origins: Allowed origins. Defaults to ["*"].

    Returns:
        List of Starlette Middleware instances.
    """
    from starlette.middleware import Middleware
    from starlette.middleware.cors import CORSMiddleware

    origins = allow_origins or ["*"]
    return [
        Middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
            allow_headers=["*"],
            expose_headers=["Mcp-Session-Id"],
        )
    ]


try:
    from fastmcp import FastMCP

    def create_mcp_server(
        settings: Any = None,
        *,
        server_name: str = "neo4j-agent-memory",
    ) -> FastMCP:
        """Create a configured FastMCP server.

        The server uses a lifespan to manage the async MemoryClient lifecycle.
        Tools, resources, and prompts are registered on the returned server.

        Args:
            settings: MemorySettings for Neo4j connection. If None, the server
                is created without a lifespan (useful for testing).
            server_name: Server name for MCP registration.

        Returns:
            Configured FastMCP server instance.

        Example:
            from neo4j_agent_memory import MemorySettings
            from neo4j_agent_memory.mcp import create_mcp_server

            settings = MemorySettings(...)
            server = create_mcp_server(settings)
            server.run()
        """
        lifespan = None
        if settings is not None:

            @asynccontextmanager
            async def lifespan(server: FastMCP):
                """Manage MemoryClient lifecycle for the MCP server."""
                from neo4j_agent_memory import MemoryClient as _MemoryClient

                async with _MemoryClient(settings) as client:
                    yield {"client": client}

        mcp = FastMCP(
            server_name,
            lifespan=lifespan,
        )

        from neo4j_agent_memory.mcp._prompts import register_prompts
        from neo4j_agent_memory.mcp._resources import register_resources
        from neo4j_agent_memory.mcp._tools import register_tools

        register_tools(mcp)
        register_resources(mcp)
        register_prompts(mcp)

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

        Tools:
            - memory_search: Hybrid vector + graph search
            - memory_store: Store messages, facts, preferences
            - entity_lookup: Get entity with relationships
            - conversation_history: Get conversation for session
            - graph_query: Execute read-only Cypher queries
            - add_reasoning_trace: Store agent reasoning traces
        """

        def __init__(
            self,
            memory_client: MemoryClient,
            *,
            server_name: str = "neo4j-agent-memory",
        ):
            """Initialize the MCP server with a pre-connected client.

            Args:
                memory_client: Connected MemoryClient instance.
                server_name: Server name for MCP registration.
            """
            self._client = memory_client

            @asynccontextmanager
            async def _preconnected_lifespan(server: FastMCP):
                yield {"client": memory_client}

            self._mcp = FastMCP(
                server_name,
                lifespan=_preconnected_lifespan,
            )

            from neo4j_agent_memory.mcp._prompts import register_prompts
            from neo4j_agent_memory.mcp._resources import register_resources
            from neo4j_agent_memory.mcp._tools import register_tools

            register_tools(self._mcp)
            register_resources(self._mcp)
            register_prompts(self._mcp)

        async def run(self) -> None:
            """Run the MCP server using stdio transport."""
            await self._mcp.run_async(transport="stdio")

        async def run_sse(
            self,
            host: str = "127.0.0.1",
            port: int = 8080,
            allow_origins: list[str] | None = None,
        ) -> None:
            """Run the MCP server using SSE transport.

            Args:
                host: Host to bind to.
                port: Port to listen on.
                allow_origins: CORS allowed origins (defaults to ["*"]).
            """
            middleware = _build_cors_middleware(allow_origins)
            await self._mcp.run_async(transport="sse", host=host, port=port, middleware=middleware)

        async def run_http(
            self,
            host: str = "127.0.0.1",
            port: int = 8080,
            allow_origins: list[str] | None = None,
            stateless_http: bool = False,
        ) -> None:
            """Run the MCP server using HTTP transport.

            Clients POST JSON-RPC messages and receive responses as JSON
            or SSE streams.

            Args:
                host: Host to bind to.
                port: Port to listen on.
                allow_origins: CORS allowed origins (defaults to ["*"]).
                stateless_http: If True, disable session ID tracking.
                    Required for browser-based MCP clients that don't
                    forward the Mcp-Session-Id header.
            """
            middleware = _build_cors_middleware(allow_origins)
            await self._mcp.run_async(
                transport="http",
                host=host,
                port=port,
                middleware=middleware,
                stateless_http=stateless_http,
            )

    async def run_server(
        neo4j_uri: str,
        neo4j_user: str,
        neo4j_password: str,
        neo4j_database: str = "neo4j",
        transport: str = "stdio",
        host: str = "127.0.0.1",
        port: int = 8080,
        allow_origins: list[str] | None = None,
        stateless_http: bool = False,
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
            allow_origins: CORS allowed origins for HTTP transports.
            stateless_http: If True, disable session ID tracking for HTTP
                transport. Required for browser-based MCP clients.
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

        server = create_mcp_server(settings, server_name="neo4j-agent-memory")

        if transport in ("sse", "http"):
            middleware = _build_cors_middleware(allow_origins)
            await server.run_async(
                transport=transport,
                host=host,
                port=port,
                middleware=middleware,
                stateless_http=stateless_http,
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
        help="MCP transport type (http uses Streamable HTTP, recommended for browser clients)",
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
        "--allow-origin",
        action="append",
        dest="allow_origins",
        help="Allowed CORS origin (repeatable, defaults to '*'). "
        "Example: --allow-origin https://example.com --allow-origin https://app.example.com",
    )
    parser.add_argument(
        "--stateless",
        action="store_true",
        default=False,
        help="Disable session ID tracking for HTTP transport. "
        "Required for browser-based MCP clients that "
        "don't forward the Mcp-Session-Id header.",
    )
    parser.add_argument(
        "--openai-api-key",
        default=os.environ.get("OPENAI_API_KEY", ""),
        help="OpenAI API key (for embeddings/extraction)",
    )

    args = parser.parse_args()

    if args.openai_api_key:
        os.environ["OPENAI_API_KEY"] = args.openai_api_key

    asyncio.run(
        run_server(
            neo4j_uri=args.neo4j_uri,
            neo4j_user=args.neo4j_user,
            neo4j_password=args.neo4j_password,
            neo4j_database=args.neo4j_database,
            transport=args.transport,
            host=args.host,
            port=args.port,
            allow_origins=args.allow_origins,
            stateless_http=args.stateless,
        )
    )


if __name__ == "__main__":
    main()
