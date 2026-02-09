"""Neo4j Agent Memory HTTP API server.

Provides a FastAPI application that exposes the MemoryClient over REST.

Usage::

    pip install neo4j-agent-memory[server]
    neo4j-memory serve --port 8000

Or programmatically::

    from neo4j_agent_memory.server import create_app
    app = create_app()
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from neo4j_agent_memory import MemoryClient, MemorySettings
from neo4j_agent_memory.server.config import ServerConfig
from neo4j_agent_memory.server.models import HealthResponse
from neo4j_agent_memory.server.routes import create_api_router

logger = logging.getLogger(__name__)

_PACKAGE_VERSION = "0.0.2"


def create_app(
    settings: MemorySettings | None = None,
    *,
    server_config: ServerConfig | None = None,
    api_key: str | None = None,
    cors_origins: list[str] | None = None,
) -> FastAPI:
    """Create and configure the FastAPI application.

    Args:
        settings: MemorySettings for the MemoryClient. If ``None``, settings
            are loaded from environment variables.
        server_config: Server-specific configuration. If ``None``, defaults are
            used.
        api_key: Optional API key to enable authentication. Overrides
            ``server_config.api_key``.
        cors_origins: Optional list of CORS origins. Overrides
            ``server_config.cors_origins``.
    """
    if server_config is None:
        server_config = ServerConfig()

    effective_origins = cors_origins or server_config.cors_origins

    # Resolve API key (explicit param > config)
    effective_api_key: str | None = api_key
    if effective_api_key is None and server_config.api_key is not None:
        effective_api_key = server_config.api_key.get_secret_value()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """Manage MemoryClient lifecycle."""
        memory_settings = settings or MemorySettings()
        client = MemoryClient(memory_settings)
        try:
            await client.connect()
            app.state.memory_client = client
            logger.info("Memory client connected")
            yield
        finally:
            await client.close()
            app.state.memory_client = None
            logger.info("Memory client closed")

    app = FastAPI(
        title="Neo4j Agent Memory API",
        description="REST API for Neo4j Agent Memory — short-term, long-term, and reasoning memory for AI agents.",
        version=_PACKAGE_VERSION,
        lifespan=lifespan,
    )

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=effective_origins,
        allow_origin_regex=server_config.cors_origin_regex,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Auth middleware (opt-in)
    if effective_api_key:
        from neo4j_agent_memory.server.auth import APIKeyMiddleware

        app.add_middleware(APIKeyMiddleware, api_key=effective_api_key)

    # Routes
    api_router = create_api_router()
    app.include_router(api_router, prefix=server_config.api_prefix)

    # Health check (outside API prefix)
    @app.get("/health", response_model=HealthResponse, tags=["meta"])
    async def health_check() -> HealthResponse:
        """Health check endpoint."""
        client: MemoryClient | None = getattr(app.state, "memory_client", None)
        return HealthResponse(
            status="healthy" if client and client.is_connected else "degraded",
            memory_connected=bool(client and client.is_connected),
            version=_PACKAGE_VERSION,
        )

    return app
