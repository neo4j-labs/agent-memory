"""FastAPI server for the retail shopping assistant.

This server provides:
- SSE streaming for chat responses
- Memory context, graph and duplicate-review endpoints
- Product search and recommendations

One ``MemoryClient`` is created in the lifespan and shared by every request.
``Neo4jMicrosoftMemory`` is a thin per-session wrapper around it, so N chat
turns still use exactly one Neo4j driver.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from enum import Enum
from typing import Annotated, Any
from uuid import UUID

from agent import create_agent, run_agent_stream
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from memory_config import (
    Settings,
    create_embedder,
    create_long_term_memory,
    create_memory,
    get_memory_settings,
)
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse
from tools import (
    get_brands,
    get_categories,
    get_product_details,
    get_related_products,
    search_products,
)

from neo4j_agent_memory import MemoryClient, MemoryIntegration, SessionStrategy
from neo4j_agent_memory.embeddings.base import Embedder
from neo4j_agent_memory.memory.long_term import LongTermMemory

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

settings = Settings()

# Process-wide state, created in the lifespan.
memory_client: MemoryClient | None = None
#: Resolves session ids (PER_DAY: "{user_id}-YYYY-MM-DD") — replaces the
#: unbounded in-process session dict this example used to keep.
memory_integration: MemoryIntegration | None = None
#: Explicit embedder for product vector search (None without an OpenAI key).
product_embedder: Embedder | None = None
#: LongTermMemory wired with this example's custom DeduplicationConfig.
dedup_memory: LongTermMemory | None = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Manage application lifecycle."""
    global memory_client, memory_integration, product_embedder, dedup_memory

    logger.info("Connecting to Neo4j at %s...", settings.neo4j_uri)
    memory_client = MemoryClient(get_memory_settings())
    await memory_client.connect()
    logger.info("Connected to Neo4j")

    memory_integration = MemoryIntegration(
        client=memory_client,
        session_strategy=SessionStrategy.PER_DAY,
        user_id=settings.default_user_id,
    )
    product_embedder = create_embedder()
    dedup_memory = create_long_term_memory(memory_client, product_embedder)

    # Say which recommendation mode is actually active instead of leaving the
    # reader to guess whether the GDS plugin is installed.
    try:
        probe = create_memory(memory_client, session_id="startup-probe")
        if probe.gds is not None:
            available = await probe.gds.is_gds_available()
            logger.info(
                "Graph Data Science plugin %s — graph algorithms run %s",
                "detected" if available else "NOT detected",
                "natively" if available else "as Cypher fallbacks",
            )
    except Exception as exc:  # pragma: no cover - never block startup on a probe
        logger.warning("Could not probe for the GDS plugin: %s", exc)

    yield

    if memory_client:
        await memory_client.close()
        logger.info("Disconnected from Neo4j")


app = FastAPI(
    title="Smart Shopping Assistant API",
    description="Retail assistant powered by Microsoft Agent Framework and Neo4j Agent Memory",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS configuration for frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Request/Response Models ---


class ChatRequest(BaseModel):
    """Chat request model."""

    message: str
    session_id: str | None = None
    user_id: str | None = None


class ChatResponse(BaseModel):
    """Non-streaming chat response."""

    response: str
    session_id: str


class MemoryContextResponse(BaseModel):
    """Memory context response."""

    short_term: list[dict[str, Any]]
    long_term: dict[str, Any]
    reasoning: list[dict[str, Any]]


class GraphResponse(BaseModel):
    """Memory graph response, shaped for the frontend's graph view."""

    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]


class PreferenceRequest(BaseModel):
    """A preference the UI (or a script) wants recorded."""

    category: str = Field(description="Preference category, e.g. 'brand' or 'budget'")
    preference: str = Field(description="The preference itself, e.g. 'Nike'")
    context: str | None = Field(default=None, description="When it applies")
    session_id: str | None = None
    user_id: str | None = None


class DuplicateReviewRequest(BaseModel):
    """Confirm or reject a flagged duplicate pair."""

    source_id: UUID
    target_id: UUID
    confirm: bool


class RelationshipKind(str, Enum):
    """Product relationship filters the API accepts.

    Relationship types cannot be parameterised in Cypher, so the only safe way
    to accept one from a query string is a closed enum mapped to a constant —
    see :data:`RELATIONSHIP_TYPES`. FastAPI rejects anything else with a 422
    before the handler runs. Every value here is a relationship the sample
    loader actually creates.
    """

    similar = "similar"
    bought_together = "bought_together"
    category = "category"
    brand = "brand"
    attribute = "attribute"


#: The only relationship types the related-products query will ever contain.
RELATIONSHIP_TYPES: dict[RelationshipKind, str] = {
    RelationshipKind.similar: "SIMILAR_TO",
    RelationshipKind.bought_together: "BOUGHT_TOGETHER",
    RelationshipKind.category: "IN_CATEGORY",
    RelationshipKind.brand: "MADE_BY",
    RelationshipKind.attribute: "HAS_ATTRIBUTE",
}

#: Upper bound for the ``center_entity`` traversal depth.
MAX_GRAPH_HOPS = 3


# --- Helpers ---


def _json(payload: dict[str, Any]) -> str:
    """Serialize an SSE payload (SSE data fields are JSON strings)."""
    return json.dumps(payload, default=str)


def _loads(payload: str) -> dict[str, Any]:
    """Parse an SSE payload back, tolerating anything non-dict."""
    try:
        loaded = json.loads(payload)
    except ValueError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _json_safe(value: Any) -> Any:
    """Make Neo4j driver values JSON-serializable.

    Node properties come back with ``neo4j.time.DateTime`` / ``Date`` /
    ``Duration`` values, which Pydantic cannot serialize. Every one of them
    exposes ``to_native()``; anything else unexpected degrades to ``str``.
    """
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    to_native = getattr(value, "to_native", None)
    if callable(to_native):
        native = to_native()
        return native.isoformat() if hasattr(native, "isoformat") else str(native)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def require_client() -> MemoryClient:
    """Return the connected client or fail with a 503."""
    if memory_client is None or not memory_client.is_connected:
        raise HTTPException(status_code=503, detail="Database not connected")
    return memory_client


def resolve_session(session_id: str | None, user_id: str | None = None) -> str:
    """Resolve the session id for a request.

    ``MemoryIntegration`` owns the strategy: an explicit ``session_id`` always
    wins, otherwise PER_DAY yields ``"{user_id}-YYYY-MM-DD"`` so a returning
    shopper continues the same conversation for the rest of the day. That
    replaces the in-process ``sessions`` dict this example used to grow
    without bound.
    """
    if session_id:
        return session_id
    if memory_integration is None:
        raise HTTPException(status_code=503, detail="Server not ready")
    if user_id and user_id != settings.default_user_id:
        # A per-request identity needs its own resolver; constructing one is
        # free (it reuses the connected client and opens nothing).
        return MemoryIntegration(
            client=require_client(),
            session_strategy=SessionStrategy.PER_DAY,
            user_id=user_id,
        ).resolve_session_id(None)
    return memory_integration.resolve_session_id(None)


# --- Chat Endpoints ---


@app.post("/chat")
async def chat_stream(request: ChatRequest) -> EventSourceResponse:
    """
    Chat endpoint with SSE streaming.

    Sends:
    - token: Individual response tokens
    - tool_call: When agent uses a tool
    - tool_result: Tool output
    - done: When response is complete
    - error: On error
    """
    client = require_client()
    session_id = resolve_session(request.session_id, request.user_id)
    user_id = request.user_id or settings.default_user_id

    async def event_generator() -> AsyncGenerator[dict[str, str], None]:
        try:
            # Give the shopper a first-class :User node (idempotent).
            await client.users.upsert_user(identifier=user_id)

            # One connected client backs every session.
            memory = create_memory(client, session_id, user_id)
            agent = await create_agent(memory, product_embedder)

            async for event in run_agent_stream(agent, request.message, memory):
                yield event

            yield {"event": "done", "data": _json({"session_id": session_id})}

        except Exception as e:
            logger.exception("Error in chat stream")
            yield {"event": "error", "data": _json({"error": str(e)})}

    return EventSourceResponse(event_generator())


@app.post("/chat/sync", response_model=ChatResponse)
async def chat_sync(request: ChatRequest) -> ChatResponse:
    """Non-streaming chat endpoint for simple requests."""
    client = require_client()
    session_id = resolve_session(request.session_id, request.user_id)
    user_id = request.user_id or settings.default_user_id

    try:
        await client.users.upsert_user(identifier=user_id)
        memory = create_memory(client, session_id, user_id)
        agent = await create_agent(memory, product_embedder)

        full_response = ""
        async for event in run_agent_stream(agent, request.message, memory):
            if event.get("event") == "token":
                full_response += _loads(event["data"]).get("content", "")
            elif event.get("event") == "error":
                raise HTTPException(status_code=500, detail=_loads(event["data"]).get("error"))

        return ChatResponse(response=full_response, session_id=session_id)

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error in sync chat")
        raise HTTPException(status_code=500, detail=str(e)) from e


# --- Memory Endpoints ---


@app.get("/memory/context", response_model=MemoryContextResponse)
async def get_memory_context(
    session_id: str = Query(..., description="Session ID"),
    query: str = Query("", description="Query for relevant context"),
) -> MemoryContextResponse:
    """Get current memory context for visualization."""
    client = require_client()

    try:
        # Short-term (conversation)
        conversation = await client.short_term.get_conversation(session_id, limit=20)
        short_term = [
            {
                "id": str(m.id),
                "role": m.role.value if hasattr(m.role, "value") else str(m.role),
                "content": m.content[:200] + "..." if len(m.content) > 200 else m.content,
                "timestamp": m.created_at.isoformat() if m.created_at else None,
            }
            for m in conversation.messages
        ]

        # Long-term (entities and preferences)
        entities = await client.long_term.search_entities(query=query or "all", limit=20)
        preferences = await client.long_term.search_preferences(query=query or "all", limit=10)
        long_term = {
            "entities": [
                {
                    "id": str(e.id),
                    "name": e.display_name,
                    "type": e.full_type,
                    "description": e.description,
                }
                for e in entities
            ],
            "preferences": [
                {
                    "id": str(p.id),
                    "category": p.category,
                    "preference": p.preference,
                    "context": p.context,
                }
                for p in preferences
            ],
        }

        # Reasoning traces
        traces = []
        if query:
            trace_results = await client.reasoning.get_similar_traces(task=query, limit=5)
            traces = [
                {
                    "id": str(t.id),
                    "task": t.task[:100],
                    "outcome": t.outcome,
                    "steps": len(t.steps) if t.steps else 0,
                }
                for t in trace_results
            ]

        return MemoryContextResponse(short_term=short_term, long_term=long_term, reasoning=traces)

    except Exception as e:
        logger.exception("Error getting memory context")
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/memory/graph", response_model=GraphResponse)
async def get_memory_graph(
    session_id: str = Query(..., description="Session ID"),
    center_entity: str | None = Query(None, description="Center entity for graph"),
    max_hops: int = Query(2, ge=1, le=MAX_GRAPH_HOPS, description="Maximum relationship hops"),
    limit: int = Query(200, ge=1, le=1000, description="Maximum nodes per memory type"),
) -> GraphResponse:
    """Get memory graph for visualization."""
    client = require_client()

    try:
        if center_entity:
            # Variable-length bounds cannot be parameterised, so max_hops is
            # validated by FastAPI (1..MAX_GRAPH_HOPS) and then interpolated
            # as an int. Nodes and relationships are collected in two stages:
            # two consecutive UNWINDs would cross-product them.
            cypher = f"""
            MATCH path = (center:Entity {{name: $center}})-[*1..{int(max_hops)}]-(related)
            // Stage 1: distinct nodes (and the paths, recovered with DISTINCT
            // because UNWIND duplicated the rows).
            UNWIND nodes(path) AS node
            WITH collect(DISTINCT node) AS nodeList, collect(DISTINCT path) AS paths
            // Stage 2: distinct relationships. Collecting both in one step
            // with two consecutive UNWINDs would cross-product them.
            UNWIND paths AS p
            UNWIND relationships(p) AS rel
            WITH nodeList, collect(DISTINCT rel) AS relList
            RETURN
              [n IN nodeList | {{
                  id: elementId(n),
                  label: coalesce(n.name, n.task, head(labels(n))),
                  type: head(labels(n)),
                  // Embeddings are large and useless to a UI, so project a
                  // curated list of properties rather than properties(n).
                  // Note: client.query.cypher() refuses a query whose raw
                  // text contains a write keyword — comments included.
                  properties: {{
                      name: n.name,
                      type: n.type,
                      subtype: n.subtype,
                      description: n.description,
                      category: n.category,
                      preference: n.preference,
                      role: n.role,
                      content: n.content,
                      task: n.task
                  }}
              }}] AS nodes,
              [r IN relList | {{
                  id: elementId(r),
                  source: elementId(startNode(r)),
                  target: elementId(endNode(r)),
                  type: type(r)
              }}] AS relationships
            """
            rows = await client.query.cypher(cypher, {"center": center_entity})
            if not rows:
                return GraphResponse(nodes=[], edges=[])
            return GraphResponse(
                nodes=_json_safe(rows[0].get("nodes") or []),
                edges=_json_safe(rows[0].get("relationships") or []),
            )

        # Session view: let the library build it. get_graph() knows the real
        # short-term shape — :Message nodes have no session_id property, the
        # session lives on the :Conversation they hang off.
        graph = await client.get_graph(session_id=session_id, limit=limit)
        return GraphResponse(
            nodes=[
                {
                    "id": node.id,
                    "label": node.properties.get("name")
                    or node.properties.get("content")
                    or node.properties.get("task")
                    or (node.labels[0] if node.labels else "Node"),
                    "type": node.labels[0] if node.labels else "Node",
                    "properties": _json_safe(node.properties),
                }
                for node in graph.nodes
            ],
            edges=[
                {
                    "id": rel.id,
                    "source": rel.from_node,
                    "target": rel.to_node,
                    "type": rel.type,
                }
                for rel in graph.relationships
            ],
        )

    except Exception as e:
        logger.exception("Error getting memory graph")
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/memory/preferences")
async def get_preferences(
    session_id: str = Query(..., description="Session ID"),
    category: str | None = Query(None, description="Filter by category"),
) -> dict[str, Any]:
    """Get learned user preferences."""
    client = require_client()

    try:
        if category:
            preferences = await client.long_term.get_preferences_by_category(
                category=category, limit=50
            )
        else:
            preferences = await client.long_term.search_preferences(query="all", limit=50)

        return {
            "preferences": [
                {
                    "id": str(p.id),
                    "category": p.category,
                    "preference": p.preference,
                    "context": p.context,
                    "confidence": getattr(p, "confidence", 1.0),
                }
                for p in preferences
            ]
        }

    except Exception as e:
        logger.exception("Error getting preferences")
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/memory/preferences", status_code=201)
async def add_preference(request: PreferenceRequest) -> dict[str, Any]:
    """Record a preference explicitly.

    The agent also writes preferences on its own through the
    ``remember_preference`` tool that ``create_memory_tools()`` provides; this
    endpoint is the deterministic path — useful for a "save this" button, and
    for demoing preference recall without spending an LLM call.
    """
    client = require_client()
    session_id = resolve_session(request.session_id, request.user_id)
    memory = create_memory(client, session_id, request.user_id)

    try:
        preference = await memory.add_preference(
            category=request.category,
            preference=request.preference,
            context=request.context,
        )
        return {
            "id": str(preference.id),
            "category": preference.category,
            "preference": preference.preference,
            "session_id": session_id,
        }
    except Exception as e:
        logger.exception("Error adding preference")
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/memory/duplicates")
async def get_duplicates(
    limit: int = Query(50, ge=1, le=500, description="Maximum candidate pairs"),
) -> dict[str, Any]:
    """List entity pairs flagged as potential duplicates, plus dedup stats.

    Entities whose similarity lands between ``flag_threshold`` (0.80 here) and
    ``auto_merge_threshold`` (0.95) get a ``SAME_AS`` relationship in
    ``pending`` status instead of being merged. This is where a human decides.
    """
    require_client()
    if dedup_memory is None:
        raise HTTPException(status_code=503, detail="Server not ready")

    try:
        pairs = await dedup_memory.find_potential_duplicates(limit=limit)
        stats = await dedup_memory.get_deduplication_stats()
        return {
            "duplicates": [
                {
                    "source": {"id": str(a.id), "name": a.name, "type": a.full_type},
                    "target": {"id": str(b.id), "name": b.name, "type": b.full_type},
                    "confidence": confidence,
                }
                for a, b, confidence in pairs
            ],
            "stats": {
                "total_entities": stats.total_entities,
                "merged_entities": stats.merged_entities,
                "same_as_relationships": stats.same_as_relationships,
                "pending_reviews": stats.pending_reviews,
            },
        }
    except Exception as e:
        logger.exception("Error listing potential duplicates")
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/memory/duplicates/review")
async def review_duplicate(request: DuplicateReviewRequest) -> dict[str, Any]:
    """Confirm (merge) or reject a flagged duplicate pair."""
    require_client()
    if dedup_memory is None:
        raise HTTPException(status_code=503, detail="Server not ready")

    try:
        processed = await dedup_memory.review_duplicate(
            request.source_id,
            request.target_id,
            confirm=request.confirm,
        )
        return {
            "processed": processed,
            "action": "merged" if request.confirm else "rejected",
        }
    except Exception as e:
        logger.exception("Error reviewing duplicate")
        raise HTTPException(status_code=500, detail=str(e)) from e


# --- Product Endpoints ---


@app.get("/products/search")
async def search_products_endpoint(
    query: str = Query(..., description="Search query"),
    category: str | None = Query(None, description="Filter by category"),
    brand: str | None = Query(None, description="Filter by brand"),
    max_price: float | None = Query(None, description="Maximum price"),
    limit: int = Query(10, ge=1, le=100, description="Maximum results"),
) -> dict[str, Any]:
    """Search product catalog (same implementation the agent tool calls)."""
    client = require_client()

    try:
        result = await search_products(
            client,
            query,
            category=category,
            brand=brand,
            max_price=max_price,
            limit=limit,
            embedder=product_embedder,
        )
        return {
            "products": [
                # The frontend reads `score`; tools/ returns `relevance_score`.
                {**product, "score": product.get("relevance_score", 1.0)}
                for product in result["products"]
            ],
            "total": result["count"],
            "search_mode": result["search_mode"],
        }
    except Exception as e:
        logger.exception("Error searching products")
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/products/categories")
async def list_categories() -> dict[str, Any]:
    """List product categories with counts."""
    client = require_client()
    try:
        return {"categories": await get_categories(client)}
    except Exception as e:
        logger.exception("Error listing categories")
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/products/brands")
async def list_brands(
    category: str | None = Query(None, description="Filter by category"),
) -> dict[str, Any]:
    """List brands, optionally within one category."""
    client = require_client()
    try:
        return {"brands": await get_brands(client, category)}
    except Exception as e:
        logger.exception("Error listing brands")
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/products/{product_id}")
async def get_product(product_id: str) -> dict[str, Any]:
    """Get product details by ID."""
    client = require_client()

    try:
        product = await get_product_details(client, product_id)
    except Exception as e:
        logger.exception("Error getting product")
        raise HTTPException(status_code=500, detail=str(e)) from e

    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    return product


@app.get("/products/{product_id}/related")
async def get_related(
    product_id: str,
    limit: int = Query(5, ge=1, le=50, description="Maximum results"),
    relationship_type: Annotated[
        RelationshipKind | None,
        Query(description="Restrict to one relationship kind"),
    ] = None,
) -> dict[str, Any]:
    """Get products related to a given product."""
    client = require_client()

    try:
        if relationship_type is not None:
            # Relationship types are not parameterisable in Cypher, which is
            # exactly why the allow-list above exists: `rel` can only ever be
            # one of four constants, never caller-supplied text.
            result = await get_related_products(
                client,
                product_id,
                relationship_types=[RELATIONSHIP_TYPES[relationship_type]],
                limit=limit,
            )
        else:
            result = await get_related_products(client, product_id, limit=limit)
        return result
    except Exception as e:
        logger.exception("Error getting related products")
        raise HTTPException(status_code=500, detail=str(e)) from e


# --- Health Check ---


@app.get("/health")
async def health_check() -> dict[str, Any]:
    """Health check endpoint."""
    db_connected = memory_client is not None and memory_client.is_connected

    return {
        "status": "healthy" if db_connected else "degraded",
        "database": "connected" if db_connected else "disconnected",
        "vector_search": "enabled" if product_embedder is not None else "text-only",
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
