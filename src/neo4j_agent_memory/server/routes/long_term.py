"""Long-term memory endpoints: entities, preferences, and relationships."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from neo4j_agent_memory import MemoryClient
from neo4j_agent_memory.server.dependencies import get_memory_client
from neo4j_agent_memory.server.models import (
    AddEntityRequest,
    AddEntityResponse,
    AddPreferenceRequest,
    AddRelationshipRequest,
    DeduplicationResultResponse,
    EntityResponse,
    PreferenceResponse,
    RelationshipResponse,
    SearchEntitiesRequest,
    SearchPreferencesRequest,
)

router = APIRouter(tags=["long-term"])


# ---------------------------------------------------------------------------
# Entities
# ---------------------------------------------------------------------------


@router.post("/entities", response_model=AddEntityResponse, status_code=201)
async def add_entity(
    request: AddEntityRequest,
    memory: MemoryClient = Depends(get_memory_client),
) -> AddEntityResponse:
    """Create or upsert an entity in the knowledge graph."""
    entity, dedup_result = await memory.long_term.add_entity(
        name=request.name,
        entity_type=request.entity_type,
        subtype=request.subtype,
        description=request.description,
        aliases=request.aliases,
        attributes=request.attributes,
        metadata=request.metadata,
    )
    return AddEntityResponse(
        entity=EntityResponse.from_domain(entity),
        deduplication=DeduplicationResultResponse.from_domain(dedup_result),
    )


@router.post("/entities/search", response_model=list[EntityResponse])
async def search_entities(
    request: SearchEntitiesRequest,
    memory: MemoryClient = Depends(get_memory_client),
) -> list[EntityResponse]:
    """Search entities using semantic similarity."""
    entities = await memory.long_term.search_entities(
        query=request.query,
        entity_types=request.entity_types,
        limit=request.limit,
        threshold=request.threshold,
    )
    return [EntityResponse.from_domain(e) for e in entities]


@router.get("/entities/{name}", response_model=EntityResponse)
async def get_entity_by_name(
    name: str,
    memory: MemoryClient = Depends(get_memory_client),
) -> EntityResponse:
    """Get an entity by name."""
    entity = await memory.long_term.get_entity_by_name(name)
    if entity is None:
        raise HTTPException(status_code=404, detail=f"Entity '{name}' not found")
    return EntityResponse.from_domain(entity)


@router.get("/entities/{entity_id}/related")
async def get_related_entities(
    entity_id: str,
    depth: int = Query(default=1, ge=1, le=3),
    memory: MemoryClient = Depends(get_memory_client),
) -> list[dict]:
    """Get entities related to a given entity via graph traversal."""
    # First look up the entity by name or ID
    entity = await memory.long_term.get_entity_by_name(entity_id)
    if entity is None:
        raise HTTPException(status_code=404, detail=f"Entity '{entity_id}' not found")

    related = await memory.long_term.get_related_entities(entity, depth=depth)
    return [
        {
            "entity": EntityResponse.from_domain(ent).model_dump(),
            "relationship": RelationshipResponse.from_domain(rel).model_dump(),
        }
        for ent, rel in related
    ]


# ---------------------------------------------------------------------------
# Preferences
# ---------------------------------------------------------------------------


@router.post("/preferences", response_model=PreferenceResponse, status_code=201)
async def add_preference(
    request: AddPreferenceRequest,
    memory: MemoryClient = Depends(get_memory_client),
) -> PreferenceResponse:
    """Add a user preference."""
    pref = await memory.long_term.add_preference(
        category=request.category,
        preference=request.preference,
        context=request.context,
        confidence=request.confidence,
    )
    return PreferenceResponse.from_domain(pref)


@router.post("/preferences/search", response_model=list[PreferenceResponse])
async def search_preferences(
    request: SearchPreferencesRequest,
    memory: MemoryClient = Depends(get_memory_client),
) -> list[PreferenceResponse]:
    """Search preferences using semantic similarity."""
    prefs = await memory.long_term.search_preferences(
        query=request.query,
        category=request.category,
        limit=request.limit,
        threshold=request.threshold,
    )
    return [PreferenceResponse.from_domain(p) for p in prefs]


@router.get("/preferences/category/{category}", response_model=list[PreferenceResponse])
async def get_preferences_by_category(
    category: str,
    limit: int = Query(default=100, ge=1, le=500),
    memory: MemoryClient = Depends(get_memory_client),
) -> list[PreferenceResponse]:
    """Get preferences by category."""
    prefs = await memory.long_term.get_preferences_by_category(category=category, limit=limit)
    return [PreferenceResponse.from_domain(p) for p in prefs]


# ---------------------------------------------------------------------------
# Relationships
# ---------------------------------------------------------------------------


@router.post("/relationships", response_model=RelationshipResponse, status_code=201)
async def add_relationship(
    request: AddRelationshipRequest,
    memory: MemoryClient = Depends(get_memory_client),
) -> RelationshipResponse:
    """Create a relationship between two entities."""
    # Look up entities by name to get the Entity objects
    source = await memory.long_term.get_entity_by_name(request.source_id)
    target = await memory.long_term.get_entity_by_name(request.target_id)
    if source is None:
        raise HTTPException(
            status_code=404, detail=f"Source entity '{request.source_id}' not found"
        )
    if target is None:
        raise HTTPException(
            status_code=404, detail=f"Target entity '{request.target_id}' not found"
        )

    rel = await memory.long_term.add_relationship(
        source=source,
        target=target,
        relationship_type=request.relationship_type,
        description=request.description,
        confidence=request.confidence,
    )
    return RelationshipResponse.from_domain(rel)
