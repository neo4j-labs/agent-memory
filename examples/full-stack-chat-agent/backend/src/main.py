"""FastAPI application entry point."""

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from neo4j import AsyncGraphDatabase

from src.api.routes import chat, memory, threads
from src.config import get_settings
from src.memory.client import (
    close_memory_client,
    get_memory_error,
    init_memory_client,
    is_memory_connected,
)

logger = logging.getLogger(__name__)

# Set OpenAI API key from settings early, before any agent initialization
_settings = get_settings()
if _settings.openai_api_key.get_secret_value():
    os.environ["OPENAI_API_KEY"] = _settings.openai_api_key.get_secret_value()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan handler for startup and shutdown.

    Both Neo4j drivers are application-scoped: the Neo4j driver owns a
    connection pool and is meant to outlive a single request.
    """
    settings = get_settings()

    await init_memory_client()

    # News graph: one driver for the process, verified at startup so a bad
    # NEWS_GRAPH_URI fails loudly here (and shows in /health) instead of
    # surfacing as a mid-stream SSE error on the first chat turn.
    app.state.news_driver = AsyncGraphDatabase.driver(
        settings.news_graph_uri,
        auth=(
            settings.news_graph_username,
            settings.news_graph_password.get_secret_value(),
        ),
    )
    app.state.news_error = None
    try:
        await app.state.news_driver.verify_connectivity()
        logger.info("Connected to news graph at %s", settings.news_graph_uri)
    except Exception as e:
        app.state.news_error = f"{type(e).__name__}: {e}"
        logger.error(
            "News graph unreachable at %s: %s — news tools will return an error",
            settings.news_graph_uri,
            app.state.news_error,
        )

    try:
        yield
    finally:
        await app.state.news_driver.close()
        await close_memory_client()


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    settings = get_settings()

    app = FastAPI(
        title="News Chat Agent",
        description="A chat agent with memory for news research",
        version="0.2.0",
        lifespan=lifespan,
    )

    # Configure CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_origin_regex=settings.cors_origin_regex,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Include routers
    app.include_router(chat.router, prefix="/api", tags=["chat"])
    app.include_router(threads.router, prefix="/api", tags=["threads"])
    app.include_router(memory.router, prefix="/api", tags=["memory"])

    @app.get("/health")
    async def health_check() -> dict[str, object]:
        """Health check endpoint.

        Reports both graphs. ``memory_error`` / ``news_error`` are non-null
        when a connection failed, so a password mismatch between
        ``docker-compose.yml`` and ``.env`` is visible rather than silent.
        """
        return {
            "status": "healthy",
            "memory_connected": is_memory_connected(),
            "memory_error": get_memory_error(),
            "news_connected": getattr(app.state, "news_error", "unset") is None,
            "news_error": getattr(app.state, "news_error", None),
            "extraction_mode": settings.extraction_mode,
        }

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "src.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
    )
