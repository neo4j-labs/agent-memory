"""Entity extraction using spaCy NER."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from spacy.language import Language

from neo4j_agent_memory.extraction.base import (
    ExtractedEntity,
    ExtractionResult,
)

logger = logging.getLogger(__name__)


class SpacyConfig(BaseModel):
    """Configuration for spaCy entity extractor."""

    model: str = Field(default="en_core_web_sm", description="spaCy model to use")
    type_mapping: dict[str, str] = Field(
        default_factory=lambda: SpacyEntityExtractor.DEFAULT_TYPE_MAPPING.copy(),
        description="Mapping from spaCy labels to POLE+O types",
    )
    subtype_mapping: dict[str, str] = Field(
        default_factory=lambda: SpacyEntityExtractor.DEFAULT_SUBTYPE_MAPPING.copy(),
        description="Mapping from spaCy labels to subtypes",
    )
    default_confidence: float = Field(
        default=0.85, ge=0.0, le=1.0, description="Default confidence score for spaCy entities"
    )
    context_window: int = Field(
        default=50, ge=0, description="Characters of context to include around entities"
    )


class SpacyEntityExtractor:
    """Entity extraction using spaCy Named Entity Recognition.

    spaCy provides fast, statistical NER with good performance on common entity types.
    This extractor maps spaCy's entity labels to POLE+O types (Person, Object,
    Location, Event, Organization).

    Note: spaCy does not extract relations or preferences - use in combination
    with other extractors (GLiNER, LLM) for full extraction.
    """

    # Mapping from spaCy labels to POLE+O types
    DEFAULT_TYPE_MAPPING: dict[str, str] = {
        # Person types
        "PERSON": "PERSON",
        # Organization types
        "ORG": "ORGANIZATION",
        "NORP": "ORGANIZATION",  # Nationalities, religious, political groups
        # Location types
        "GPE": "LOCATION",  # Countries, cities, states
        "LOC": "LOCATION",  # Non-GPE locations
        "FAC": "LOCATION",  # Buildings, airports, highways
        # Event types
        "EVENT": "EVENT",
        # Object types (mapped from various spaCy labels)
        "PRODUCT": "OBJECT",
        "WORK_OF_ART": "OBJECT",
        "LAW": "OBJECT",
        "LANGUAGE": "OBJECT",
        # Temporal entities (can be part of events)
        "DATE": "EVENT",
        "TIME": "EVENT",
        # Numeric types (often attributes rather than entities)
        "MONEY": "OBJECT",
        "QUANTITY": "OBJECT",
        "ORDINAL": "OBJECT",
        "CARDINAL": "OBJECT",
        "PERCENT": "OBJECT",
    }

    # Mapping from spaCy labels to subtypes
    DEFAULT_SUBTYPE_MAPPING: dict[str, str] = {
        "GPE": "GEOPOLITICAL",
        "LOC": "GEOGRAPHIC",
        "FAC": "FACILITY",
        "PRODUCT": "PRODUCT",
        "WORK_OF_ART": "CREATIVE_WORK",
        "LAW": "LEGAL_DOCUMENT",
        "LANGUAGE": "LANGUAGE",
        "DATE": "DATE",
        "TIME": "TIME",
        "MONEY": "CURRENCY",
        "QUANTITY": "QUANTITY",
        "NORP": "GROUP",
    }

    def __init__(
        self,
        model: str = "en_core_web_sm",
        type_mapping: dict[str, str] | None = None,
        subtype_mapping: dict[str, str] | None = None,
        default_confidence: float = 0.85,
        context_window: int = 50,
    ):
        """
        Initialize spaCy entity extractor.

        Args:
            model: spaCy model name (e.g., "en_core_web_sm", "en_core_web_lg")
            type_mapping: Custom mapping from spaCy labels to POLE+O types
            subtype_mapping: Custom mapping from spaCy labels to subtypes
            default_confidence: Default confidence score (spaCy doesn't provide per-entity scores)
            context_window: Number of characters of context to include around entities
        """
        self._model_name = model
        self._nlp: Language | None = None  # Lazy load
        self.type_mapping = type_mapping or self.DEFAULT_TYPE_MAPPING.copy()
        self.subtype_mapping = subtype_mapping or self.DEFAULT_SUBTYPE_MAPPING.copy()
        self.default_confidence = default_confidence
        self.context_window = context_window

    @property
    def nlp(self) -> Any:
        """Lazy load spaCy model."""
        if self._nlp is None:
            try:
                import spacy

                self._nlp = spacy.load(self._model_name)
                logger.info(f"Loaded spaCy model: {self._model_name}")
            except ImportError:
                raise ImportError(
                    "spaCy is required for SpacyEntityExtractor. "
                    "Install with: pip install spacy && python -m spacy download en_core_web_sm"
                )
            except OSError as e:
                raise OSError(
                    f"Could not load spaCy model '{self._model_name}'. "
                    f"Download with: python -m spacy download {self._model_name}"
                ) from e
        return self._nlp

    def _get_context(self, text: str, start: int, end: int) -> str:
        """Get surrounding context for an entity."""
        context_start = max(0, start - self.context_window)
        context_end = min(len(text), end + self.context_window)
        return text[context_start:context_end]

    def _extract_sync(
        self, text: str, entity_types: list[str] | None = None
    ) -> list[ExtractedEntity]:
        """Synchronous extraction (spaCy is not async)."""
        doc = self.nlp(text)
        entities = []

        for ent in doc.ents:
            # Map spaCy label to POLE+O type
            mapped_type = self.type_mapping.get(ent.label_, "OBJECT")

            # Filter by requested entity types
            if entity_types and mapped_type not in entity_types:
                continue

            # Get subtype if available
            subtype = self.subtype_mapping.get(ent.label_)

            # Create extracted entity
            entity = ExtractedEntity(
                name=ent.text.strip(),
                type=mapped_type,
                subtype=subtype,
                start_pos=ent.start_char,
                end_pos=ent.end_char,
                confidence=self.default_confidence,
                context=self._get_context(text, ent.start_char, ent.end_char),
                extractor="spacy",
                attributes={
                    "spacy_label": ent.label_,
                    "spacy_label_description": ent.label_,
                },
            )
            entities.append(entity)

        return entities

    async def extract(
        self,
        text: str,
        *,
        entity_types: list[str] | None = None,
        extract_relations: bool = True,
        extract_preferences: bool = True,
    ) -> ExtractionResult:
        """
        Extract entities from text using spaCy NER.

        Note: spaCy NER does not extract relations or preferences.
        For full extraction, combine with GLiNER or LLM extractors.

        Args:
            text: The text to extract from
            entity_types: Optional list of POLE+O entity types to extract
            extract_relations: Ignored (spaCy doesn't extract relations)
            extract_preferences: Ignored (spaCy doesn't extract preferences)

        Returns:
            ExtractionResult containing extracted entities
        """
        if not text or not text.strip():
            return ExtractionResult(source_text=text)

        # Run synchronous spaCy in thread pool to avoid blocking
        loop = asyncio.get_event_loop()
        entities = await loop.run_in_executor(None, self._extract_sync, text, entity_types)

        logger.debug(f"spaCy extracted {len(entities)} entities from text")

        return ExtractionResult(
            entities=entities,
            relations=[],  # spaCy NER doesn't extract relations
            preferences=[],  # spaCy NER doesn't extract preferences
            source_text=text,
        )

    @classmethod
    def from_config(cls, config: SpacyConfig) -> SpacyEntityExtractor:
        """Create extractor from configuration."""
        return cls(
            model=config.model,
            type_mapping=config.type_mapping,
            subtype_mapping=config.subtype_mapping,
            default_confidence=config.default_confidence,
            context_window=config.context_window,
        )
