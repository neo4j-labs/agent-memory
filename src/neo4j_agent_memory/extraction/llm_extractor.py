"""LLM-based entity and preference extraction."""

import json
import logging
from typing import TYPE_CHECKING, Any

from neo4j_agent_memory.core.exceptions import ExtractionError
from neo4j_agent_memory.extraction.base import (
    EntityExtractor,
    ExtractedEntity,
    ExtractedPreference,
    ExtractedRelation,
    ExtractionResult,
)

if TYPE_CHECKING:
    from neo4j_agent_memory.llm.litellm import LiteLLM

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
POLEO_SUBTYPES = {
    "PERSON": ["INDIVIDUAL", "ALIAS", "PERSONA"],
    "OBJECT": ["VEHICLE", "PHONE", "EMAIL", "DOCUMENT", "DEVICE", "WEAPON", "PRODUCT"],
    "LOCATION": ["ADDRESS", "CITY", "REGION", "COUNTRY", "LANDMARK", "FACILITY"],
    "EVENT": ["INCIDENT", "MEETING", "TRANSACTION", "COMMUNICATION", "DATE", "TIME"],
    "ORGANIZATION": ["COMPANY", "NONPROFIT", "GOVERNMENT", "EDUCATIONAL", "GROUP"],
}

# Default prompt optimized for POLE+O extraction
DEFAULT_EXTRACTION_PROMPT = """Extract entities, relationships, and preferences from the following text.

## Entity Types (POLE+O Model)
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


class LLMEntityExtractor(EntityExtractor):
    """
    LLM-based entity and preference extraction using the POLE+O model.

    Uses OpenAI's structured output capabilities for reliable extraction.
    Supports Person, Object, Location, Event, and Organization entity types
    with optional subtypes for finer classification.

    This extractor can:
    - Extract entities with type and optional subtype
    - Extract relationships between entities
    - Extract user preferences

    Example:
        ```python
        extractor = LLMEntityExtractor(
            model="gpt-4o-mini",
            entity_types=["PERSON", "ORGANIZATION", "LOCATION"],
        )

        result = await extractor.extract(
            "John Smith works at Acme Corp in New York City."
        )

        for entity in result.entities:
            print(f"{entity.name}: {entity.type}")
        # John Smith: PERSON
        # Acme Corp: ORGANIZATION
        # New York City: LOCATION
        ```
    """

    def __init__(
        self,
        model: str = "gpt-4o-mini",
        *,
        client: Any | None = None,
        api_key: str | None = None,
        entity_types: list[str] | None = None,
        subtypes: dict[str, list[str]] | None = None,
        extraction_prompt: str | None = None,
        temperature: float = 0.0,
        extract_relations: bool = True,
        extract_preferences: bool = True,
    ):
        """
        Initialize LLM extractor.

        Args:
            model: OpenAI model to use
            client: Pre-initialized LLM client
            api_key: OpenAI API key (or set OPENAI_API_KEY env var)
            entity_types: Entity types to extract (defaults to POLE+O)
            subtypes: Mapping of entity types to allowed subtypes
            extraction_prompt: Custom extraction prompt
            temperature: LLM temperature (0.0 for deterministic)
            extract_relations: Whether to extract relations by default
            extract_preferences: Whether to extract preferences by default
        """
        self._model = model
        self._api_key = api_key
        self._entity_types = entity_types or DEFAULT_ENTITY_TYPES
        self._subtypes = subtypes or POLEO_SUBTYPES
        self._prompt = extraction_prompt or DEFAULT_EXTRACTION_PROMPT
        self._temperature = temperature
        self._extract_relations = extract_relations
        self._extract_preferences = extract_preferences
        self._client: Any | None = client

    @property
    def name(self) -> str:
        """Extractor name for pipeline identification."""
        return "LLMEntityExtractor"

    def _ensure_client(self) -> "LiteLLM":
        """Ensure the LLM client is initialized."""
        if self._client is None:
            try:
                from neo4j_agent_memory.llm.litellm import LiteLLM
            except ImportError:
                raise ExtractionError(
                    "LiteLLM package not installed. Install with: pip install neo4j-agent-memory[litellm]"
                )
            self._client = LiteLLM(
                model=self._model,
                temperature=self._temperature,
                max_tokens=4096,
            )
        return self._client

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

    async def extract(
        self,
        text: str,
        *,
        entity_types: list[str] | None = None,
        extract_relations: bool | None = None,
        extract_preferences: bool | None = None,
    ) -> ExtractionResult:
        """
        Extract entities, relations, and preferences from text.

        Args:
            text: Text to extract from
            entity_types: Override entity types to extract
            extract_relations: Override whether to extract relations
            extract_preferences: Override whether to extract preferences

        Returns:
            ExtractionResult containing entities, relations, and preferences
        """
        if not text or not text.strip():
            return ExtractionResult(source_text=text)

        client = self._ensure_client()
        types_to_use = entity_types or self._entity_types
        include_relations = (
            extract_relations if extract_relations is not None else self._extract_relations
        )
        include_preferences = (
            extract_preferences if extract_preferences is not None else self._extract_preferences
        )

        # Build the prompt with subtype info
        subtype_info = self._build_subtype_info(types_to_use)
        prompt = self._prompt.format(
            entity_types=", ".join(types_to_use),
            subtype_info=subtype_info,
            text=text,
        )

        try:
            # Use the LiteLLM client to extract
            response = await client.extract(prompt=prompt)
            content = json.dumps(response) if isinstance(response, dict) else response

            if not content:
                return ExtractionResult(source_text=text)

            data = json.loads(content)
            result = self._parse_extraction_result(
                data, text, include_relations, include_preferences, types_to_use
            )

            logger.debug(
                f"LLM extracted {result.entity_count} entities, "
                f"{result.relation_count} relations, "
                f"{result.preference_count} preferences"
            )

            return result

        except json.JSONDecodeError as e:
            raise ExtractionError(f"Failed to parse LLM response as JSON: {e}") from e
        except Exception as e:
            raise ExtractionError(f"Failed to extract entities: {e}") from e

    def _parse_extraction_result(
        self,
        data: dict[str, Any],
        source_text: str,
        include_relations: bool,
        include_preferences: bool,
        allowed_types: list[str],
    ) -> ExtractionResult:
        """Parse LLM response into ExtractionResult."""
        entities: list[ExtractedEntity] = []
        relations: list[ExtractedRelation] = []
        preferences: list[ExtractedPreference] = []

        # Parse entities
        for entity_data in data.get("entities", []):
            try:
                entity_type = entity_data.get("type", "OBJECT").upper()

                # Validate type against allowed types
                if entity_type not in allowed_types:
                    # Try to map to closest allowed type
                    entity_type = self._map_to_allowed_type(entity_type, allowed_types)

                # Get subtype
                subtype = entity_data.get("subtype")
                if subtype:
                    subtype = subtype.upper()
                    # Validate subtype
                    allowed_subtypes = self._subtypes.get(entity_type, [])
                    if allowed_subtypes and subtype not in allowed_subtypes:
                        subtype = None  # Invalid subtype, ignore

                entities.append(
                    ExtractedEntity(
                        name=entity_data.get("name", ""),
                        type=entity_type,
                        subtype=subtype,
                        confidence=float(entity_data.get("confidence", 1.0)),
                        extractor="llm",
                    )
                )
            except (ValueError, TypeError) as e:
                logger.warning(f"Failed to parse entity: {e}")
                continue

        # Parse relations
        if include_relations:
            entity_names = {e.name.lower() for e in entities}

            for relation_data in data.get("relations", []):
                try:
                    source = relation_data.get("source", "")
                    target = relation_data.get("target", "")

                    # Only include relations between extracted entities
                    if source.lower() not in entity_names or target.lower() not in entity_names:
                        continue

                    relations.append(
                        ExtractedRelation(
                            source=source,
                            target=target,
                            relation_type=relation_data.get("relation_type", "RELATED_TO").upper(),
                            confidence=float(relation_data.get("confidence", 1.0)),
                        )
                    )
                except (ValueError, TypeError) as e:
                    logger.warning(f"Failed to parse relation: {e}")
                    continue

        # Parse preferences
        if include_preferences:
            for pref_data in data.get("preferences", []):
                try:
                    preferences.append(
                        ExtractedPreference(
                            category=pref_data.get("category", "general"),
                            preference=pref_data.get("preference", ""),
                            context=pref_data.get("context"),
                            confidence=float(pref_data.get("confidence", 1.0)),
                        )
                    )
                except (ValueError, TypeError) as e:
                    logger.warning(f"Failed to parse preference: {e}")
                    continue

        return ExtractionResult(
            entities=entities,
            relations=relations,
            preferences=preferences,
            source_text=source_text,
        )

    def _map_to_allowed_type(self, entity_type: str, allowed_types: list[str]) -> str:
        """Map an unknown entity type to the closest allowed type."""
        # Simple mapping for common types
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
            mapped if mapped in allowed_types else allowed_types[0] if allowed_types else "OBJECT"
        )

    @classmethod
    def for_poleo(
        cls,
        model: str = "gpt-4o-mini",
        api_key: str | None = None,
    ) -> "LLMEntityExtractor":
        """Create extractor configured for POLE+O model."""
        return cls(
            model=model,
            api_key=api_key,
            entity_types=DEFAULT_ENTITY_TYPES,
            subtypes=POLEO_SUBTYPES,
        )

    @classmethod
    def for_custom_types(
        cls,
        entity_types: list[str],
        model: str = "gpt-4o-mini",
        api_key: str | None = None,
    ) -> "LLMEntityExtractor":
        """Create extractor for custom entity types."""
        return cls(
            model=model,
            api_key=api_key,
            entity_types=entity_types,
            subtypes={},  # No subtypes for custom types
        )
