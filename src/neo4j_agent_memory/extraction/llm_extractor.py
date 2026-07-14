"""LLM-based entity and preference extraction.

Provider-aware as of v0.3.0: accepts an injected
:class:`~neo4j_agent_memory.llm.protocol.LLMProvider` (or
:class:`~neo4j_agent_memory.llm.protocol.StructuredExtractor`) instead of
constructing an OpenAI client directly. When the provider also implements
:class:`StructuredExtractor`, the extractor uses
:meth:`StructuredExtractor.complete_structured` for the most reliable
output mode that provider supports — OpenAI strict mode, Anthropic forced
tool use, or schema-aligned retry as the safety net.

The legacy ``model=`` / ``api_key=`` constructor parameters are retained
for backward compatibility: when ``provider=`` is not supplied, a default
provider is constructed via :func:`~neo4j_agent_memory.llm.from_provider`
using the legacy parameters.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from neo4j_agent_memory.core.exceptions import ExtractionError
from neo4j_agent_memory.extraction._payloads import ExtractionPayload
from neo4j_agent_memory.extraction.base import (
    EntityExtractor,
    ExtractedEntity,
    ExtractedPreference,
    ExtractedRelation,
    ExtractionResult,
)

if TYPE_CHECKING:
    from neo4j_agent_memory.llm.protocol import LLMProvider, StructuredExtractor


logger = logging.getLogger(__name__)


# POLE+O entity types as default
DEFAULT_ENTITY_TYPES = [
    "PERSON",
    "ORGANIZATION",
    "LOCATION",
    "EVENT",
    "OBJECT",
]

# Common subtypes for POLE+O model
POLEO_SUBTYPES: dict[str, list[str]] = {
    "PERSON": ["INDIVIDUAL", "ALIAS", "PERSONA"],
    "OBJECT": ["VEHICLE", "PHONE", "EMAIL", "DOCUMENT", "DEVICE", "WEAPON", "PRODUCT"],
    "LOCATION": ["ADDRESS", "CITY", "REGION", "COUNTRY", "LANDMARK", "FACILITY"],
    "EVENT": ["INCIDENT", "MEETING", "TRANSACTION", "COMMUNICATION", "DATE", "TIME"],
    "ORGANIZATION": ["COMPANY", "NONPROFIT", "GOVERNMENT", "EDUCATIONAL", "GROUP"],
}

# Default prompt optimized for POLE+O extraction. The structured-extraction
# path uses Pydantic schema validation instead, so this is the fallback
# for plain-LLM-call extraction (when the provider does not implement
# StructuredExtractor).
DEFAULT_EXTRACTION_PROMPT = """Extract entities, relationships, and preferences from the following text.

## Entity Types
Extract entities of these types:
{entity_types}

{subtype_info}

## Output Format
Return a JSON object with this structure:
{{
    "entities": [
        {{"name": "entity name", "type": "ENTITY_TYPE", "subtype": "SUBTYPE or null", "confidence": 0.9}}
    ],
    "relations": [
        {{"source": "entity1", "target": "entity2", "relation_type": "relationship type", "confidence": 0.8}}
    ],
    "preferences": [
        {{"category": "category", "preference": "the preference", "context": "when/where it applies", "confidence": 0.85}}
    ]
}}

## Guidelines
- PERSON: Individuals, people mentioned by name or role
- OBJECT: Physical or digital items (vehicles, phones, documents, devices)
- LOCATION: Places, addresses, geographic areas, landmarks
- EVENT: Incidents, meetings, transactions, things that happened
- ORGANIZATION: Companies, groups, institutions

For relations:
- Identify how entities are connected
- Use clear relationship types (WORKS_AT, LIVES_IN, OWNS, ATTENDED, KNOWS, etc.)
- Only include relations between entities in the entities list

For preferences:
- User preferences, likes, dislikes, opinions
- Categories: food, music, communication, style, technology, etc.

Confidence: 0.0-1.0 based on certainty of extraction

## Text to Analyze
{text}

Return only valid JSON, no other text."""

SUBTYPE_INFO_TEMPLATE = """
Subtypes (optional, use when you can determine a more specific type):
{subtype_list}
"""

SYSTEM_MESSAGE = (
    "You are an expert at extracting structured information from text. "
    "You follow the configured entity-type schema. Always respond with valid JSON."
)


class LLMEntityExtractor(EntityExtractor):
    """LLM-based entity, relation, and preference extraction.

    Provider-aware. When given a :class:`StructuredExtractor` provider it
    uses ``complete_structured`` for native-quality structured outputs.
    When given a plain :class:`LLMProvider` (or no provider at all) it
    falls back to prompt-engineered JSON extraction.

    Example with explicit provider::

        from neo4j_agent_memory.llm.adapters.anthropic import AnthropicProvider

        provider = AnthropicProvider("anthropic/claude-3-5-sonnet-latest")
        extractor = LLMEntityExtractor(provider=provider)
        result = await extractor.extract("John works at Acme.")

    Example with legacy signature (constructs OpenAI provider internally)::

        extractor = LLMEntityExtractor(model="gpt-4o-mini", api_key="sk-...")
    """

    def __init__(
        self,
        provider: LLMProvider | StructuredExtractor | None = None,
        *,
        # Legacy parameters — used to construct a default provider when
        # ``provider`` is not supplied.
        model: str | None = None,
        api_key: str | None = None,
        # Configuration shared between provider modes
        entity_types: list[str] | None = None,
        subtypes: dict[str, list[str]] | None = None,
        extraction_prompt: str | None = None,
        temperature: float = 0.0,
        extract_relations: bool = True,
        extract_preferences: bool = True,
    ) -> None:
        # Resolve the provider: explicit > legacy-args > default(gpt-4o-mini)
        if provider is None:
            resolved_model = model or "openai/gpt-4o-mini"
            try:
                from neo4j_agent_memory.llm import from_provider
            except ImportError as exc:
                raise ExtractionError(
                    "Could not import neo4j_agent_memory.llm — install a provider extra "
                    "(e.g. pip install 'neo4j-agent-memory[openai]')"
                ) from exc
            kwargs: dict[str, Any] = {}
            if api_key is not None:
                kwargs["api_key"] = api_key
            provider = from_provider(resolved_model, kind="llm", **kwargs)  # type: ignore[assignment]
        self._provider = provider
        self._model_label = getattr(provider, "model", "unknown")
        self._entity_types = entity_types or list(DEFAULT_ENTITY_TYPES)
        self._subtypes = subtypes if subtypes is not None else dict(POLEO_SUBTYPES)
        self._prompt = extraction_prompt or DEFAULT_EXTRACTION_PROMPT
        self._temperature = temperature
        self._extract_relations = extract_relations
        self._extract_preferences = extract_preferences

    @property
    def name(self) -> str:
        """Extractor name for pipeline identification."""
        return "LLMEntityExtractor"

    def _build_subtype_info(self, types_to_use: list[str]) -> str:
        """Build subtype information string for the prompt."""
        subtype_lines = []
        for entity_type in types_to_use:
            subtypes = self._subtypes.get(entity_type, [])
            if subtypes:
                subtype_lines.append(f"- {entity_type}: {', '.join(subtypes)}")
        if subtype_lines:
            return SUBTYPE_INFO_TEMPLATE.format(subtype_list="\n".join(subtype_lines))
        return ""

    def _build_prompt(self, text: str, types_to_use: list[str]) -> str:
        return self._prompt.format(
            entity_types=", ".join(types_to_use),
            subtype_info=self._build_subtype_info(types_to_use),
            text=text,
        )

    async def extract(
        self,
        text: str,
        *,
        entity_types: list[str] | None = None,
        extract_relations: bool | None = None,
        extract_preferences: bool | None = None,
    ) -> ExtractionResult:
        """Extract entities, relations, and preferences from text.

        Picks the right provider call based on capabilities:

        * If the provider implements :class:`StructuredExtractor`, uses
          ``complete_structured`` with the :class:`ExtractionPayload`
          schema. This is the high-quality path.
        * Otherwise falls back to prompt-engineered JSON via
          ``complete``, then parses the response loosely.
        """
        if not text or not text.strip():
            return ExtractionResult(source_text=text)

        types_to_use = entity_types or self._entity_types
        include_relations = (
            extract_relations if extract_relations is not None else self._extract_relations
        )
        include_preferences = (
            extract_preferences if extract_preferences is not None else self._extract_preferences
        )

        # Lazy import to avoid circular dep at module load
        from neo4j_agent_memory.llm.protocol import StructuredExtractor

        if isinstance(self._provider, StructuredExtractor):
            try:
                return await self._extract_structured(
                    text, types_to_use, include_relations, include_preferences
                )
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning(
                    "Structured extraction failed (%s); falling back to plain LLM call",
                    type(exc).__name__,
                )
        return await self._extract_with_complete(
            text, types_to_use, include_relations, include_preferences
        )

    async def _extract_structured(
        self,
        text: str,
        types_to_use: list[str],
        include_relations: bool,
        include_preferences: bool,
    ) -> ExtractionResult:
        """Run extraction via :meth:`StructuredExtractor.complete_structured`."""
        from neo4j_agent_memory.llm.protocol import StructuredExtractor
        from neo4j_agent_memory.llm.types import ChatMessage

        # Called only after isinstance(self._provider, StructuredExtractor) in extract()
        assert isinstance(self._provider, StructuredExtractor)
        provider = self._provider

        prompt = self._build_prompt(text, types_to_use)
        messages = [
            ChatMessage(role="system", content=SYSTEM_MESSAGE),
            ChatMessage(role="user", content=prompt),
        ]
        # The structured-extraction path validates against ExtractionPayload.
        # ``complete_structured`` raises StructuredExtractionError on failure
        # which we let propagate so the pipeline can decide how to handle it.
        payload: ExtractionPayload = await provider.complete_structured(
            messages,
            ExtractionPayload,
            temperature=self._temperature,
        )
        return self._payload_to_result(
            payload, text, types_to_use, include_relations, include_preferences
        )

    async def _extract_with_complete(
        self,
        text: str,
        types_to_use: list[str],
        include_relations: bool,
        include_preferences: bool,
    ) -> ExtractionResult:
        """Run extraction via plain :meth:`LLMProvider.complete`.

        Used when the provider does not implement
        :class:`StructuredExtractor`. Less reliable than the structured
        path but still works for any LLM.
        """
        from neo4j_agent_memory.llm.types import ChatMessage

        prompt = self._build_prompt(text, types_to_use)
        messages = [
            ChatMessage(role="system", content=SYSTEM_MESSAGE),
            ChatMessage(role="user", content=prompt),
        ]
        try:
            completion = await self._provider.complete(  # type: ignore[union-attr]
                messages,
                temperature=self._temperature,
            )
        except Exception as exc:
            raise ExtractionError(f"Failed to extract entities: {exc}") from exc

        try:
            # Strip markdown fence if the model wrapped JSON in one
            content = completion.content.strip()
            if content.startswith("```"):
                # Remove the first line and trailing fence
                lines = content.splitlines()
                if lines and lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].rstrip("`").strip() == "":
                    lines = lines[:-1]
                content = "\n".join(lines)
            data = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ExtractionError(f"Failed to parse LLM response as JSON: {exc}") from exc

        # The raw dict from a non-structured call is shaped the same as
        # ExtractionPayload — validate to coerce strings and silently
        # drop extras. This still gives us the type-safety win.
        try:
            payload = ExtractionPayload.model_validate(data)
        except Exception as exc:
            raise ExtractionError(
                f"LLM response did not match expected extraction shape: {exc}"
            ) from exc

        return self._payload_to_result(
            payload, text, types_to_use, include_relations, include_preferences
        )

    def _payload_to_result(
        self,
        payload: ExtractionPayload,
        source_text: str,
        allowed_types: list[str],
        include_relations: bool,
        include_preferences: bool,
    ) -> ExtractionResult:
        """Convert an :class:`ExtractionPayload` to an :class:`ExtractionResult`."""
        entities: list[ExtractedEntity] = []
        for ent in payload.entities:
            entity_type = (ent.type or "OBJECT").upper()
            if entity_type not in allowed_types:
                entity_type = self._map_to_allowed_type(entity_type, allowed_types)
            subtype = ent.subtype.upper() if ent.subtype else None
            if subtype:
                allowed_subtypes = self._subtypes.get(entity_type, [])
                if allowed_subtypes and subtype not in allowed_subtypes:
                    subtype = None
            entities.append(
                ExtractedEntity(
                    name=ent.name,
                    type=entity_type,
                    subtype=subtype,
                    confidence=ent.confidence,
                    extractor="llm",
                )
            )

        relations: list[ExtractedRelation] = []
        if include_relations:
            entity_names_lower = {e.name.lower() for e in entities}
            for rel in payload.relations:
                if (
                    rel.source.lower() not in entity_names_lower
                    or rel.target.lower() not in entity_names_lower
                ):
                    continue
                relations.append(
                    ExtractedRelation(
                        source=rel.source,
                        target=rel.target,
                        relation_type=rel.relation_type.upper(),
                        confidence=rel.confidence,
                    )
                )

        preferences: list[ExtractedPreference] = []
        if include_preferences:
            for pref in payload.preferences:
                preferences.append(
                    ExtractedPreference(
                        category=pref.category,
                        preference=pref.preference,
                        context=pref.context,
                        confidence=pref.confidence,
                    )
                )

        logger.debug(
            "LLM extracted %d entities, %d relations, %d preferences",
            len(entities),
            len(relations),
            len(preferences),
        )

        return ExtractionResult(
            entities=entities,
            relations=relations,
            preferences=preferences,
            source_text=source_text,
        )

    def _map_to_allowed_type(self, entity_type: str, allowed_types: list[str]) -> str:
        """Map an unknown entity type to the closest allowed type."""
        type_mappings = {
            "CONCEPT": "OBJECT",
            "EMOTION": "OBJECT",
            "PRODUCT": "OBJECT",
            "THING": "OBJECT",
            "ITEM": "OBJECT",
            "FACT": "OBJECT",
            "PREFERENCE": "OBJECT",
            "PLACE": "LOCATION",
            "CITY": "LOCATION",
            "COUNTRY": "LOCATION",
            "ADDRESS": "LOCATION",
            "COMPANY": "ORGANIZATION",
            "ORG": "ORGANIZATION",
            "INDIVIDUAL": "PERSON",
            "HUMAN": "PERSON",
            "INCIDENT": "EVENT",
            "MEETING": "EVENT",
            "DATE": "EVENT",
            "TIME": "EVENT",
        }
        mapped = type_mappings.get(entity_type, "OBJECT")
        return (
            mapped if mapped in allowed_types else (allowed_types[0] if allowed_types else "OBJECT")
        )

    @classmethod
    def for_poleo(
        cls,
        provider: LLMProvider | StructuredExtractor | None = None,
        *,
        model: str = "openai/gpt-4o-mini",
        api_key: str | None = None,
    ) -> LLMEntityExtractor:
        """Create extractor configured for POLE+O model."""
        return cls(
            provider=provider,
            model=model,
            api_key=api_key,
            entity_types=list(DEFAULT_ENTITY_TYPES),
            subtypes=dict(POLEO_SUBTYPES),
        )

    @classmethod
    def for_custom_types(
        cls,
        entity_types: list[str],
        provider: LLMProvider | StructuredExtractor | None = None,
        *,
        model: str = "openai/gpt-4o-mini",
        api_key: str | None = None,
    ) -> LLMEntityExtractor:
        """Create extractor for custom entity types."""
        return cls(
            provider=provider,
            model=model,
            api_key=api_key,
            entity_types=entity_types,
            subtypes={},
        )


__all__ = [
    "LLMEntityExtractor",
    "DEFAULT_ENTITY_TYPES",
    "POLEO_SUBTYPES",
    "DEFAULT_EXTRACTION_PROMPT",
]
