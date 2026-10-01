"""FastAPI entry point: ``uv run uvicorn src.main:app --reload --port 8000``."""

from __future__ import annotations

import hashlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from neo4j_agent_memory.core.exceptions import NotSupportedError
from src.api.routes import chat, graph, memory, ontology, threads, traces
from src.api.schemas import Health, OntologyStatus
from src.config import get_settings
from src.memory import MemoryService, MemoryUnavailableError

logger = logging.getLogger(__name__)

# Until an alias has been written, the library's resolution blocking query
# makes Neo4j warn that the `aliases` property key does not exist, once per
# mention. Harmless, and loud on a fresh database.
logging.getLogger("neo4j.notifications").setLevel(logging.ERROR)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """One :class:`MemoryService` per process; it owns (and swaps) the client."""
    service = MemoryService(get_settings())
    await service.connect()
    app.state.memory = service
    try:
        yield
    finally:
        await service.close()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Support Desk Agent",
        description=(
            "A support-desk agent on neo4j-agent-memory: an ontology-typed memory graph, "
            "entity resolution with a review queue, and reasoning traces."
        ),
        version="0.1.0",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    for router in (chat, threads, memory, graph, ontology, traces):
        app.include_router(router.router, prefix="/api")

    @app.get("/api/health", response_model=Health)
    @app.get("/health", response_model=Health, include_in_schema=False)
    async def health(request: Request) -> Health:
        """Neo4j reachability, the agent model, and the ontology the app's client uses."""
        service: MemoryService = request.app.state.memory
        try:
            client = await service.get_client()
        except MemoryUnavailableError:
            return Health(neo4j=False, agent_model=settings.agent_model, ontology=OntologyStatus())
        try:
            await client.query.cypher("RETURN 1 AS ok")
            reachable = True
        except Exception:
            reachable = False
        resolved = service.resolved()
        revision: int | None = None
        if reachable:
            try:
                active = await client.ontology.get_active()
            except NotSupportedError:
                active = None
            # The stored revision number, when the client runs on exactly that document
            # (revisions are content-addressed by ``schema_hash``).
            document = client.ontology_document
            if active is not None and document is not None:
                content_hash = hashlib.sha256(document.content_key().encode("utf-8")).hexdigest()
                if content_hash == active.schema_hash:
                    revision = active.revision
        return Health(
            neo4j=reachable,
            agent_model=settings.agent_model,
            ontology=OntologyStatus(
                domain_id=resolved["domain_id"],
                revision=revision,
                validation_mode=resolved["validation_mode"],
            ),
        )

    return app


app = create_app()
