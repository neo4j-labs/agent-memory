"""Pydantic payload models used by structured LLM extraction.

These mirror the public :class:`~neo4j_agent_memory.extraction.base.ExtractedEntity`,
:class:`~neo4j_agent_memory.extraction.base.ExtractedRelation`, and
:class:`~neo4j_agent_memory.extraction.base.ExtractedPreference` classes,
but are lean extraction-time payloads suitable for
:meth:`~neo4j_agent_memory.llm.protocol.StructuredExtractor.complete_structured`.

Keeping them separate from the public ``Extracted*`` classes avoids
forcing the LLM to invent values for downstream-only fields like
``start_pos``, ``end_pos``, ``extractor``, or ``attributes``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, create_model


class EntityPayload(BaseModel):
    """An entity payload from LLM structured extraction."""

    name: str = Field(description="Entity name, as it appears in the text.")
    type: str = Field(
        description=(
            "Entity type. Use exactly one of the configured entity types "
            "(the default POLE+O set is PERSON, OBJECT, LOCATION, EVENT, "
            "ORGANIZATION; a custom schema overrides this set)."
        )
    )
    subtype: str | None = Field(
        default=None,
        description=(
            "Optional subtype (e.g., VEHICLE for OBJECT, ADDRESS for LOCATION). "
            "Use null when no specific subtype applies."
        ),
    )
    confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Confidence score for the extraction (0.0 to 1.0).",
    )


class RelationPayload(BaseModel):
    """A relation payload from LLM structured extraction."""

    source: str = Field(description="Source entity name (must match an extracted entity).")
    target: str = Field(description="Target entity name (must match an extracted entity).")
    relation_type: str = Field(
        description=("Relationship type (WORKS_AT, LIVES_IN, OWNS, KNOWS, FOUNDED, etc.).")
    )
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class PreferencePayload(BaseModel):
    """A preference payload from LLM structured extraction."""

    category: str = Field(
        description="Preference category (food, music, communication, style, technology, etc.)."
    )
    preference: str = Field(description="The preference statement.")
    context: str | None = Field(
        default=None, description="Optional context for when/where the preference applies."
    )
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class ExtractionPayload(BaseModel):
    """Top-level payload for LLM structured extraction."""

    entities: list[EntityPayload] = Field(default_factory=list)
    relations: list[RelationPayload] = Field(default_factory=list)
    preferences: list[PreferencePayload] = Field(default_factory=list)


def build_constrained_payload(
    entity_types: list[str],
    relation_types: list[str] | None = None,
) -> type[ExtractionPayload]:
    """Build a per-call payload model that constrains the extracted types.

    Used by the LLM extractor when ``strict_types`` is enabled: the
    ``type`` field on entities (and optionally ``relation_type`` on
    relations) becomes a :data:`typing.Literal` over the configured names,
    so the structured-output schema rejects invented types at the provider
    layer (OpenAI strict mode / Anthropic tool schema) or, failing that, at
    Pydantic-validation time on the schema-aligned fallback path.

    Falls back to the unconstrained :class:`ExtractionPayload` when no
    entity types are supplied (nothing to constrain to).
    """
    if not entity_types:
        return ExtractionPayload

    # ``Literal[tuple(...)]`` expands the tuple into the Literal's args at
    # runtime — the supported idiom for building a Literal dynamically.
    entity_type_literal = Literal[tuple(entity_types)]  # type: ignore[valid-type]
    constrained_entity = create_model(
        "ConstrainedEntityPayload",
        __base__=EntityPayload,
        type=(
            entity_type_literal,
            Field(description="Entity type. Must be one of the configured types."),
        ),
    )

    relation_model: type[BaseModel] = RelationPayload
    if relation_types:
        relation_type_literal = Literal[tuple(relation_types)]  # type: ignore[valid-type]
        relation_model = create_model(
            "ConstrainedRelationPayload",
            __base__=RelationPayload,
            relation_type=(
                relation_type_literal,
                Field(description="Relationship type. Must be one of the configured types."),
            ),
        )

    return create_model(
        "ConstrainedExtractionPayload",
        __base__=ExtractionPayload,
        entities=(list[constrained_entity], Field(default_factory=list)),  # type: ignore[valid-type]
        relations=(list[relation_model], Field(default_factory=list)),  # type: ignore[valid-type]
    )


__all__ = [
    "EntityPayload",
    "RelationPayload",
    "PreferencePayload",
    "ExtractionPayload",
    "build_constrained_payload",
]
