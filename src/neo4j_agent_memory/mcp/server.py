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
import os
from collections.abc import AsyncIterator
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
            async def lifespan(server: FastMCP) -> AsyncIterator[dict[str, Any]]:
                """Manage MemoryClient, MemoryIntegration, and Observer lifecycle."""
                from neo4j_agent_memory import MemoryClient as _MemoryClient
                from neo4j_agent_memory.integration import MemoryIntegration
                from neo4j_agent_memory.mcp._observer import MemoryObserver

                async with _MemoryClient(settings) as client:
                    # Wire the configured LLMProvider (if any) into the
                    # observer so reflections are produced by the same
                    # provider the rest of the server uses. Falls back to
                    # keyword extraction when settings.llm is a legacy
                    # LLMConfig (not a Provider instance).
                    from neo4j_agent_memory.llm.protocol import (
                        LLMProvider as _LLMProvider,
                    )

                    llm_for_observer = (
                        settings.llm if isinstance(settings.llm, _LLMProvider) else None
                    )
                    observer = MemoryObserver(
                        client,
                        threshold_tokens=observation_threshold,
                        llm_provider=llm_for_observer,
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

        # v0.4: Platinum tools (NAMS-only) are registered when the
        # configured backend is NAMS. Settings is None means no lifespan
        # client; default to bolt (no Platinum).
        register_platinum = settings is not None and settings.backend == "nams"
        register_tools(mcp, profile=profile, register_platinum=register_platinum)
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
            async def _preconnected_lifespan(server: FastMCP) -> AsyncIterator[dict[str, Any]]:
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

            # v0.4: register Platinum tools when the pre-connected client
            # is NAMS-backed.
            register_platinum = memory_client._settings.backend == "nams"
            register_tools(self._mcp, profile=profile, register_platinum=register_platinum)
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
        llm: str | None = None,
        llm_api_key: str | None = None,
        llm_api_base: str | None = None,
        embedding: str | None = None,
        embedding_dimensions: int | None = None,
        backend: str = "bolt",
        nams_api_key: str | None = None,
        nams_endpoint: str | None = None,
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
            llm: Provider string for the LLM (e.g. 'anthropic/claude-3-5-sonnet-latest').
                When ``None`` the existing default (OpenAI gpt-4o-mini via the
                lenient fallback in :class:`MemorySettings`) is used.
            llm_api_key: API key override for the LLM provider.
            llm_api_base: Base URL override for the LLM provider (vLLM, Ollama, …).
            embedding: Provider string for embeddings (e.g. 'BAAI/bge-small-en-v1.5').
            embedding_dimensions: Override for embedding dimensions when the
                model is not in the defaults lookup table.
        """
        from pydantic import SecretStr

        from neo4j_agent_memory import MemorySettings, NamsConfig
        from neo4j_agent_memory.config.settings import Neo4jConfig

        settings_kwargs: dict[str, Any] = {"backend": backend}
        if backend == "nams":
            nams_api_key = nams_api_key or os.environ.get("MEMORY_API_KEY")
            if not nams_api_key:
                raise ValueError(
                    "NAMS backend requires nams_api_key or a MEMORY_API_KEY environment variable."
                )
            nams_kwargs: dict[str, Any] = {"api_key": SecretStr(nams_api_key)}
            nams_endpoint = nams_endpoint or os.environ.get("MEMORY_ENDPOINT")
            if nams_endpoint:
                nams_kwargs["endpoint"] = nams_endpoint
            settings_kwargs["nams"] = NamsConfig(**nams_kwargs)
        else:
            settings_kwargs["neo4j"] = Neo4jConfig(
                uri=neo4j_uri,
                username=neo4j_user,
                password=SecretStr(neo4j_password),
                database=neo4j_database,
            )
        if llm:
            from neo4j_agent_memory.llm import from_provider

            provider_prefix, _, _ = llm.partition("/")
            provider_prefix = provider_prefix.lower()
            llm_kwargs: dict[str, Any] = {}
            if llm_api_key:
                if provider_prefix == "bedrock":
                    raise ValueError("--llm-api-key is not supported for bedrock/* providers")
                llm_kwargs["api_key"] = llm_api_key
            if llm_api_base:
                if provider_prefix == "bedrock":
                    raise ValueError("--llm-api-base is not supported for bedrock/* providers")
                llm_kwargs["api_base"] = llm_api_base
            settings_kwargs["llm"] = from_provider(llm, kind="llm", **llm_kwargs)
        if embedding:
            from neo4j_agent_memory.llm import from_provider

            emb_kwargs: dict[str, Any] = {}
            if embedding_dimensions is not None:
                emb_kwargs["dimensions"] = embedding_dimensions
            settings_kwargs["embedding"] = from_provider(embedding, kind="embedding", **emb_kwargs)

        settings = MemorySettings(**settings_kwargs)

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
            await server.run_async(transport="sse", host=host, port=port)  # ty: ignore[possibly-missing-attribute]  # run_async exists on FastMCP; ty unifies with the ImportError-fallback stubs
        elif transport == "http":
            await server.run_async(transport="http", host=host, port=port)  # ty: ignore[possibly-missing-attribute]  # same
        else:
            await server.run_async(transport="stdio")  # ty: ignore[possibly-missing-attribute]  # same

except ImportError:
    # FastMCP not installed — these stubs have different signatures from the real
    # definitions above, which mypy flags as no-redef / misc. Each stub carries a
    # paired mypy and ty suppression because the two tools disagree on which codes
    # are needed here.
    class Neo4jMemoryMCPServer:  # type: ignore[no-redef]  # ty: ignore[unused-ignore-comment]
        """Placeholder when FastMCP is not installed."""

        def __init__(self, *args: Any, **kwargs: Any):
            raise ImportError(
                "FastMCP not installed. Install with: pip install neo4j-agent-memory[mcp]"
            )

        async def run_async(self, *args: Any, **kwargs: Any) -> None:
            raise ImportError(
                "FastMCP not installed. Install with: pip install neo4j-agent-memory[mcp]"
            )

    def create_mcp_server(*args: Any, **kwargs: Any) -> Neo4jMemoryMCPServer:  # type: ignore[misc]  # ty: ignore[unused-ignore-comment]
        raise ImportError(
            "FastMCP not installed. Install with: pip install neo4j-agent-memory[mcp]"
        )

    async def run_server(*args: Any, **kwargs: Any) -> None:  # type: ignore[misc]  # ty: ignore[unused-ignore-comment]  # fallback stub has wider *args/**kwargs signature than the real one
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
        )
    )


if __name__ == "__main__":
    main()
