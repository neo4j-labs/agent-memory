"""Entity extraction using GLiNER2 zero-shot NER.

GLiNER2 is an improved version of GLiNER with better accuracy and support for
entity type descriptions. This module provides both the extractor and domain
schemas for different use cases.

Reference: https://github.com/urchade/GLiNER
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from spacy.language import Language

from neo4j_agent_memory.extraction.base import (
    ExtractedEntity,
    ExtractedRelation,
    ExtractionResult,
)

logger = logging.getLogger(__name__)


def is_gliner_available() -> bool:
    """Check if GLiNER is installed and available.

    Returns:
        True if GLiNER can be imported, False otherwise.
    """
    try:
        import gliner  # noqa: F401

        return True
    except ImportError:
        return False


def is_glirel_available() -> bool:
    """Check if GLiREL is installed and available.

    GLiREL is a separate package for relation extraction that works with GLiNER entities.

    Returns:
        True if GLiREL can be imported, False otherwise.
    """
    try:
        import glirel  # type: ignore[import-not-found]  # glirel ships no types  # noqa: F401

        return True
    except ImportError:
        return False


# Default GLiNER2 model - significantly improved over v2.1
DEFAULT_GLINER2_MODEL = "gliner-community/gliner_medium-v2.5"

# Alternative models:
# - "gliner-community/gliner_small-v2.5" - faster, less accurate
# - "gliner-community/gliner_large-v2.5" - slower, more accurate
# - "urchade/gliner_medium-v2.1" - legacy model (still works)
# - "numind/NuNER_Zero" - alternative zero-shot NER


class DomainSchema(BaseModel):
    """Schema defining entity types for a specific domain.

    Entity type descriptions help GLiNER2 understand what to extract.
    Using descriptions improves extraction accuracy significantly.
    """

    name: str = Field(description="Schema name identifier")
    entity_types: dict[str, str] = Field(description="Mapping of entity type names to descriptions")
    relation_types: dict[str, str] = Field(
        default_factory=dict,
        description="Mapping of relation type names to descriptions",
    )


# Pre-defined domain schemas with descriptions for improved extraction
DOMAIN_SCHEMAS: dict[str, DomainSchema] = {
    "poleo": DomainSchema(
        name="poleo",
        entity_types={
            "person": "A human individual, including their name, alias, or persona",
            "organization": "A company, institution, government agency, or group",
            "location": "A geographic place, address, city, country, or landmark",
            "event": "An incident, meeting, transaction, or notable occurrence",
            "object": "A physical or digital item like a vehicle, device, or document",
        },
    ),
    "podcast": DomainSchema(
        name="podcast",
        entity_types={
            "person": "A person mentioned in the podcast, including hosts, guests, and people discussed",
            "company": "A company, startup, or business organization",
            "product": "A product, service, app, or software tool",
            "concept": "A business concept, methodology, framework, or strategy",
            "book": "A book, publication, or written work",
            "location": "A city, country, region, or specific place",
            "event": "A conference, meeting, milestone, or notable occurrence",
            "role": "A job title, position, or professional role",
            "metric": "A business metric, KPI, or measurement",
            "technology": "A technology, platform, programming language, or technical tool",
        },
    ),
    "news": DomainSchema(
        name="news",
        entity_types={
            "person": "A person mentioned in the news article",
            "organization": "A company, government body, or institution",
            "location": "A geographic location, city, or country",
            "event": "A news event, incident, or occurrence",
            "date": "A date, time period, or temporal reference",
        },
    ),
    "scientific": DomainSchema(
        name="scientific",
        entity_types={
            "author": "A researcher or paper author",
            "institution": "A university, research lab, or academic organization",
            "method": "A scientific method, algorithm, or technique",
            "dataset": "A dataset, corpus, or data collection",
            "metric": "A performance metric or evaluation measure",
            "concept": "A scientific concept, theory, or term",
            "tool": "A software tool, library, or framework",
        },
    ),
    "business": DomainSchema(
        name="business",
        entity_types={
            "company": "A business, corporation, or startup",
            "person": "A business person, executive, or founder",
            "product": "A product, service, or offering",
            "industry": "An industry sector or market",
            "financial_metric": "A financial metric, revenue, or valuation",
            "location": "A business location, headquarters, or market",
        },
    ),
    "entertainment": DomainSchema(
        name="entertainment",
        entity_types={
            "actor": "An actor, actress, or performer",
            "director": "A film or TV director",
            "film": "A movie, documentary, or film",
            "tv_show": "A television series or show",
            "character": "A fictional character",
            "award": "An award, nomination, or recognition",
            "studio": "A production studio or entertainment company",
            "genre": "A genre or category of entertainment",
        },
    ),
    "medical": DomainSchema(
        name="medical",
        entity_types={
            "disease": "A disease, condition, or disorder",
            "drug": "A medication, drug, or treatment",
            "symptom": "A symptom or clinical sign",
            "procedure": "A medical procedure or intervention",
            "body_part": "An anatomical structure or body part",
            "gene": "A gene, protein, or biomarker",
            "organism": "A pathogen, virus, or organism",
        },
    ),
    "legal": DomainSchema(
        name="legal",
        entity_types={
            "case": "A legal case or lawsuit",
            "person": "A party, lawyer, or judge",
            "organization": "A law firm, court, or legal entity",
            "law": "A law, statute, or regulation",
            "court": "A court or judicial body",
            "date": "A legal date, filing date, or deadline",
            "monetary_amount": "A settlement, fine, or monetary value",
        },
    ),
}


def get_schema(name: str) -> DomainSchema:
    """Get a pre-defined domain schema by name.

    Args:
        name: Schema name (poleo, podcast, news, scientific, business, entertainment, medical, legal)

    Returns:
        DomainSchema for the specified domain

    Raises:
        ValueError: If schema name is not recognized
    """
    if name not in DOMAIN_SCHEMAS:
        available = ", ".join(DOMAIN_SCHEMAS.keys())
        raise ValueError(f"Unknown schema '{name}'. Available schemas: {available}")
    return DOMAIN_SCHEMAS[name]


def list_schemas() -> list[str]:
    """List all available domain schema names."""
    return list(DOMAIN_SCHEMAS.keys())


# Default POLE+O labels for GLiNER (lowercase as GLiNER prefers)
# These are simple labels without descriptions (legacy support)
DEFAULT_POLEO_LABELS = [
    "person",
    "organization",
    "location",
    "event",
    "object",
    # Common subtypes that GLiNER can recognize
    "vehicle",
    "phone number",
    "email address",
    "document",
    "device",
    "weapon",
    "address",
    "city",
    "country",
    "company",
    "meeting",
    "transaction",
]


class GLiNERConfig(BaseModel):
    """Configuration for GLiNER entity extractor."""

    model: str = Field(
        default=DEFAULT_GLINER2_MODEL,
        description="GLiNER model to use (recommend gliner-community/gliner_medium-v2.5)",
    )
    entity_labels: list[str] | dict[str, str] = Field(
        default_factory=lambda: DEFAULT_POLEO_LABELS.copy(),
        description="Entity labels (list) or labels with descriptions (dict) for zero-shot extraction",
    )
    threshold: float = Field(
        default=0.5, ge=0.0, le=1.0, description="Confidence threshold for entities"
    )
    flat_ner: bool = Field(default=True, description="Use flat NER (no nested entities)")
    multi_label: bool = Field(default=False, description="Allow multiple labels per entity")
    device: str = Field(default="cpu", description="Device to run model on (cpu, cuda, mps)")
    context_window: int = Field(
        default=50, ge=0, description="Characters of context to include around entities"
    )
    schema_name: str | None = Field(
        default=None, description="Use a pre-defined domain schema (overrides entity_labels)"
    )


class GLiNEREntityExtractor:
    """Entity extraction using GLiNER2 zero-shot Named Entity Recognition.

    GLiNER2 (Generalist and Lightweight Named Entity Recognition) provides
    zero-shot NER capabilities, allowing extraction of custom entity types
    without retraining. This is ideal for the POLE+O model where we need
    to extract domain-specific entities.

    GLiNER2 improvements over v2.1:
    - Better accuracy with entity type descriptions
    - Improved handling of domain-specific entities
    - Support for both simple labels and label-description pairs

    Note: GLiNER does not extract relations or preferences - use in combination
    with other extractors (LLM) for full extraction.

    Example:
        # Using domain schema (recommended)
        extractor = GLiNEREntityExtractor.for_schema("podcast")

        # Using custom labels with descriptions
        extractor = GLiNEREntityExtractor(
            entity_labels={
                "person": "A person mentioned in the text",
                "company": "A business or organization",
            }
        )

        # Using simple labels (legacy)
        extractor = GLiNEREntityExtractor(
            entity_labels=["person", "company", "location"]
        )
    """

    # Mapping from GLiNER labels to POLE+O types
    DEFAULT_LABEL_MAPPING: dict[str, tuple[str, str | None]] = {
        # Person types -> (TYPE, SUBTYPE)
        "person": ("PERSON", None),
        "individual": ("PERSON", "INDIVIDUAL"),
        "alias": ("PERSON", "ALIAS"),
        "author": ("PERSON", "AUTHOR"),
        "actor": ("PERSON", "ACTOR"),
        "director": ("PERSON", "DIRECTOR"),
        # Organization types
        "organization": ("ORGANIZATION", None),
        "company": ("ORGANIZATION", "COMPANY"),
        "nonprofit": ("ORGANIZATION", "NONPROFIT"),
        "government": ("ORGANIZATION", "GOVERNMENT"),
        "educational institution": ("ORGANIZATION", "EDUCATIONAL"),
        "institution": ("ORGANIZATION", "INSTITUTION"),
        "studio": ("ORGANIZATION", "STUDIO"),
        "court": ("ORGANIZATION", "COURT"),
        # Location types
        "location": ("LOCATION", None),
        "address": ("LOCATION", "ADDRESS"),
        "city": ("LOCATION", "CITY"),
        "country": ("LOCATION", "COUNTRY"),
        "region": ("LOCATION", "REGION"),
        "landmark": ("LOCATION", "LANDMARK"),
        # Event types
        "event": ("EVENT", None),
        "incident": ("EVENT", "INCIDENT"),
        "meeting": ("EVENT", "MEETING"),
        "transaction": ("EVENT", "TRANSACTION"),
        "communication": ("EVENT", "COMMUNICATION"),
        "case": ("EVENT", "CASE"),
        # Object types
        "object": ("OBJECT", None),
        "vehicle": ("OBJECT", "VEHICLE"),
        "phone number": ("OBJECT", "PHONE"),
        "phone": ("OBJECT", "PHONE"),
        "email address": ("OBJECT", "EMAIL"),
        "email": ("OBJECT", "EMAIL"),
        "document": ("OBJECT", "DOCUMENT"),
        "device": ("OBJECT", "DEVICE"),
        "weapon": ("OBJECT", "WEAPON"),
        "product": ("OBJECT", "PRODUCT"),
        "book": ("OBJECT", "BOOK"),
        "film": ("OBJECT", "FILM"),
        "tv_show": ("OBJECT", "TV_SHOW"),
        "dataset": ("OBJECT", "DATASET"),
        "tool": ("OBJECT", "TOOL"),
        "technology": ("OBJECT", "TECHNOLOGY"),
        "drug": ("OBJECT", "DRUG"),
        "law": ("OBJECT", "LAW"),
        # Concept types (map to OBJECT with CONCEPT subtype)
        "concept": ("OBJECT", "CONCEPT"),
        "method": ("OBJECT", "METHOD"),
        "metric": ("OBJECT", "METRIC"),
        "financial_metric": ("OBJECT", "FINANCIAL_METRIC"),
        "role": ("OBJECT", "ROLE"),
        "industry": ("OBJECT", "INDUSTRY"),
        "genre": ("OBJECT", "GENRE"),
        "symptom": ("OBJECT", "SYMPTOM"),
        "procedure": ("OBJECT", "PROCEDURE"),
        "body_part": ("OBJECT", "BODY_PART"),
        "gene": ("OBJECT", "GENE"),
        "organism": ("OBJECT", "ORGANISM"),
        "disease": ("OBJECT", "DISEASE"),
        "character": ("OBJECT", "CHARACTER"),
        "award": ("OBJECT", "AWARD"),
        "monetary_amount": ("OBJECT", "MONETARY_AMOUNT"),
        "date": ("EVENT", "DATE"),
    }

    def __init__(
        self,
        model: str = DEFAULT_GLINER2_MODEL,
        entity_labels: list[str] | dict[str, str] | None = None,
        threshold: float = 0.5,
        flat_ner: bool = True,
        multi_label: bool = False,
        device: str = "cpu",
        context_window: int = 50,
        label_mapping: dict[str, tuple[str, str | None]] | None = None,
        schema: DomainSchema | None = None,
    ):
        """
        Initialize GLiNER2 entity extractor.

        Args:
            model: GLiNER model name from HuggingFace (default: gliner-community/gliner_medium-v2.5)
            entity_labels: Entity labels (list) or labels with descriptions (dict).
                           Descriptions improve extraction accuracy with GLiNER2.
            threshold: Confidence threshold for including entities (0.0-1.0)
            flat_ner: Use flat NER (no nested entities)
            multi_label: Allow multiple labels per entity
            device: Device to run on (cpu, cuda, mps)
            context_window: Characters of context to include around entities
            label_mapping: Custom mapping from GLiNER labels to (TYPE, SUBTYPE)
            schema: Pre-defined DomainSchema to use (overrides entity_labels)
        """
        self._model_name = model
        self._model: Any | None = None  # Lazy load

        # Use schema if provided, otherwise use entity_labels
        self.entity_labels: list[str] | dict[str, str]
        if schema is not None:
            # Schema provides labels with descriptions
            self.entity_labels = schema.entity_types
            self._use_descriptions = True
        elif entity_labels is not None:
            self.entity_labels = entity_labels
            self._use_descriptions = isinstance(entity_labels, dict)
        else:
            self.entity_labels = DEFAULT_POLEO_LABELS.copy()
            self._use_descriptions = False

        self.threshold = threshold
        self.flat_ner = flat_ner
        self.multi_label = multi_label
        self.device = device
        self.context_window = context_window
        self.label_mapping = label_mapping or self.DEFAULT_LABEL_MAPPING.copy()

    @property
    def model(self) -> Any:
        """Lazy load GLiNER model."""
        if self._model is None:
            try:
                from gliner import GLiNER

                logger.info(f"Loading GLiNER model: {self._model_name}")
                self._model = GLiNER.from_pretrained(self._model_name)
                self._model.to(self.device)
                logger.info(f"GLiNER model loaded on {self.device}")
            except ImportError:
                raise ImportError(
                    "GLiNER is required for GLiNEREntityExtractor. Install with: pip install gliner"
                )
            except Exception as e:
                raise RuntimeError(f"Could not load GLiNER model '{self._model_name}': {e}") from e
        return self._model

    def _get_context(self, text: str, start: int, end: int) -> str:
        """Get surrounding context for an entity."""
        context_start = max(0, start - self.context_window)
        context_end = min(len(text), end + self.context_window)
        return text[context_start:context_end]

    def _map_label_to_poleo(self, label: str) -> tuple[str, str | None]:
        """Map GLiNER label to POLE+O type and subtype."""
        label_lower = label.lower()

        # Check direct mapping
        if label_lower in self.label_mapping:
            return self.label_mapping[label_lower]

        # Try to infer from label
        label_upper = label.upper()
        if label_upper in ["PERSON", "ORGANIZATION", "LOCATION", "EVENT", "OBJECT"]:
            return (label_upper, None)

        # Default to OBJECT
        return ("OBJECT", label_upper)

    def _extract_sync(
        self, text: str, entity_types: list[str] | None = None
    ) -> list[ExtractedEntity]:
        """Synchronous extraction using GLiNER2.

        When using descriptions (dict labels), GLiNER2 can leverage the
        descriptions to better understand what entities to extract.
        """
        # Determine which labels to use
        if isinstance(self.entity_labels, dict):
            # entity_labels is dict[str, str] - keys are labels, values are descriptions
            all_labels = list(self.entity_labels.keys())
        else:
            # entity_labels is list[str]
            all_labels = list(self.entity_labels)

        # If specific entity types requested, filter labels
        if entity_types:
            entity_types_lower = [t.lower() for t in entity_types]
            labels = [
                label
                for label in all_labels
                if label.lower() in entity_types_lower
                or self._map_label_to_poleo(label)[0] in entity_types
            ]
        else:
            labels = all_labels

        if not labels:
            labels = all_labels

        # GLiNER2 can accept labels with descriptions for improved accuracy
        # Format: {"label_name": "description of what this entity type is"}
        if isinstance(self.entity_labels, dict):
            # Create labels dict with only the labels we're using
            labels_with_descriptions = {
                label: self.entity_labels[label] for label in labels if label in self.entity_labels
            }
            # GLiNER2 predict_entities accepts dict for labels with descriptions
            predict_labels: list[str] | dict[str, str] = labels_with_descriptions
        else:
            predict_labels = labels

        # Run GLiNER prediction
        predictions = self.model.predict_entities(
            text,
            predict_labels,
            threshold=self.threshold,
            flat_ner=self.flat_ner,
            multi_label=self.multi_label,
        )

        entities = []
        for pred in predictions:
            # Map to POLE+O type
            entity_type, subtype = self._map_label_to_poleo(pred["label"])

            # Filter by requested types
            if entity_types and entity_type not in entity_types:
                continue

            entity = ExtractedEntity(
                name=pred["text"].strip(),
                type=entity_type,
                subtype=subtype,
                start_pos=pred["start"],
                end_pos=pred["end"],
                confidence=pred["score"],
                context=self._get_context(text, pred["start"], pred["end"]),
                extractor="gliner",
                attributes={
                    "gliner_label": pred["label"],
                    "gliner_score": pred["score"],
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
        Extract entities from text using GLiNER zero-shot NER.

        Note: GLiNER does not extract relations or preferences.
        For full extraction, combine with LLM extractor.

        Args:
            text: The text to extract from
            entity_types: Optional list of POLE+O entity types to extract
            extract_relations: Ignored (GLiNER doesn't extract relations)
            extract_preferences: Ignored (GLiNER doesn't extract preferences)

        Returns:
            ExtractionResult containing extracted entities
        """
        if not text or not text.strip():
            return ExtractionResult(source_text=text)

        # Run synchronous GLiNER in thread pool to avoid blocking
        loop = asyncio.get_event_loop()
        entities = await loop.run_in_executor(None, self._extract_sync, text, entity_types)

        logger.debug(f"GLiNER extracted {len(entities)} entities from text")

        return ExtractionResult(
            entities=entities,
            relations=[],  # GLiNER doesn't extract relations
            preferences=[],  # GLiNER doesn't extract preferences
            source_text=text,
        )

    def _extract_batch_sync(
        self,
        texts: list[str],
        entity_types: list[str] | None = None,
    ) -> list[list[ExtractedEntity]]:
        """Synchronous batch extraction using GLiNER2's native batch support.

        GLiNER models support batch inference which is more efficient on GPU
        as it processes multiple texts in a single forward pass.
        """
        # Determine which labels to use
        if isinstance(self.entity_labels, dict):
            all_labels = list(self.entity_labels.keys())
        else:
            all_labels = list(self.entity_labels)

        # Filter labels if specific entity types requested
        if entity_types:
            entity_types_lower = [t.lower() for t in entity_types]
            labels = [
                label
                for label in all_labels
                if label.lower() in entity_types_lower
                or self._map_label_to_poleo(label)[0] in entity_types
            ]
        else:
            labels = all_labels

        if not labels:
            labels = all_labels

        # Prepare labels for GLiNER
        if isinstance(self.entity_labels, dict):
            predict_labels: list[str] | dict[str, str] = {
                label: self.entity_labels[label] for label in labels if label in self.entity_labels
            }
        else:
            predict_labels = labels

        # GLiNER batch prediction - more efficient than sequential
        # predict_entities can accept a list of texts
        batch_predictions = self.model.batch_predict_entities(
            texts,
            predict_labels,
            threshold=self.threshold,
            flat_ner=self.flat_ner,
            multi_label=self.multi_label,
        )

        # Process predictions for each text
        all_entities: list[list[ExtractedEntity]] = []

        for text, predictions in zip(texts, batch_predictions):
            entities = []
            for pred in predictions:
                # Map to POLE+O type
                entity_type, subtype = self._map_label_to_poleo(pred["label"])

                # Filter by requested types
                if entity_types and entity_type not in entity_types:
                    continue

                entity = ExtractedEntity(
                    name=pred["text"].strip(),
                    type=entity_type,
                    subtype=subtype,
                    start_pos=pred["start"],
                    end_pos=pred["end"],
                    confidence=pred["score"],
                    context=self._get_context(text, pred["start"], pred["end"]),
                    extractor="gliner",
                    attributes={
                        "gliner_label": pred["label"],
                        "gliner_score": pred["score"],
                    },
                )
                entities.append(entity)
            all_entities.append(entities)

        return all_entities

    async def extract_batch(
        self,
        texts: list[str],
        *,
        entity_types: list[str] | None = None,
        batch_size: int = 32,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> list[ExtractionResult]:
        """
        Extract entities from multiple texts using GLiNER batch inference.

        This method uses GLiNER's native batch processing which is significantly
        more efficient than processing texts one at a time, especially on GPU.

        Args:
            texts: List of texts to extract from
            entity_types: Optional list of POLE+O entity types to filter
            batch_size: Number of texts to process in each batch (for memory management)
            on_progress: Optional callback called after each batch.
                        Receives (completed_count, total_count).

        Returns:
            List of ExtractionResult objects, one per input text

        Example:
            ```python
            extractor = GLiNEREntityExtractor.for_schema("podcast", device="cuda")
            texts = ["Episode 1 transcript...", "Episode 2 transcript...", ...]
            results = await extractor.extract_batch(texts, batch_size=16)
            ```
        """
        if not texts:
            return []

        loop = asyncio.get_event_loop()
        all_results: list[ExtractionResult] = []
        total = len(texts)
        completed = 0

        # Process in batches to manage memory
        for batch_start in range(0, total, batch_size):
            batch_end = min(batch_start + batch_size, total)
            batch_texts = texts[batch_start:batch_end]

            # Run batch extraction in thread pool
            batch_entities = await loop.run_in_executor(
                None, self._extract_batch_sync, batch_texts, entity_types
            )

            # Convert to ExtractionResults
            for text, entities in zip(batch_texts, batch_entities):
                all_results.append(
                    ExtractionResult(
                        entities=entities,
                        relations=[],
                        preferences=[],
                        source_text=text,
                    )
                )
                completed += 1

            # Report progress
            if on_progress:
                try:
                    on_progress(completed, total)
                except Exception as e:
                    logger.warning(f"Progress callback error: {e}")

        logger.debug(
            f"GLiNER batch extracted {sum(r.entity_count for r in all_results)} entities "
            f"from {len(texts)} texts"
        )

        return all_results

    def update_labels(self, labels: list[str]) -> None:
        """Update entity labels for extraction."""
        self.entity_labels = labels.copy()

    def add_label_mapping(self, label: str, entity_type: str, subtype: str | None = None) -> None:
        """Add a custom label mapping."""
        self.label_mapping[label.lower()] = (entity_type.upper(), subtype)

    @classmethod
    def from_config(cls, config: GLiNERConfig) -> GLiNEREntityExtractor:
        """Create extractor from configuration.

        If schema_name is provided in config, it will override entity_labels.
        """
        schema = None
        if config.schema_name:
            schema = get_schema(config.schema_name)

        return cls(
            model=config.model,
            entity_labels=config.entity_labels if not schema else None,
            threshold=config.threshold,
            flat_ner=config.flat_ner,
            multi_label=config.multi_label,
            device=config.device,
            context_window=config.context_window,
            schema=schema,
        )

    @classmethod
    def for_schema(
        cls,
        schema_name: str,
        model: str = DEFAULT_GLINER2_MODEL,
        threshold: float = 0.5,
        device: str = "cpu",
    ) -> GLiNEREntityExtractor:
        """Create extractor using a pre-defined domain schema.

        Using domain schemas with entity descriptions significantly improves
        extraction accuracy with GLiNER2.

        Args:
            schema_name: Name of the schema (poleo, podcast, news, scientific,
                        business, entertainment, medical, legal)
            model: GLiNER2 model to use
            threshold: Confidence threshold
            device: Device to run on

        Example:
            extractor = GLiNEREntityExtractor.for_schema("podcast")
            result = await extractor.extract("Lenny talked with Sarah about growth...")
        """
        schema = get_schema(schema_name)
        return cls(
            model=model,
            schema=schema,
            threshold=threshold,
            device=device,
        )

    @classmethod
    def for_poleo(
        cls,
        model: str = DEFAULT_GLINER2_MODEL,
        threshold: float = 0.5,
        device: str = "cpu",
        use_descriptions: bool = True,
    ) -> GLiNEREntityExtractor:
        """Create extractor optimized for POLE+O entity extraction.

        Args:
            model: GLiNER2 model to use (default: gliner_medium-v2.5)
            threshold: Confidence threshold
            device: Device to run on
            use_descriptions: Whether to use the poleo schema with descriptions
                             (recommended for better accuracy)
        """
        if use_descriptions:
            return cls(
                model=model,
                schema=DOMAIN_SCHEMAS["poleo"],
                threshold=threshold,
                device=device,
            )
        else:
            return cls(
                model=model,
                entity_labels=DEFAULT_POLEO_LABELS,
                threshold=threshold,
                device=device,
            )


# =============================================================================
# GLiREL Relation Extraction
# =============================================================================

# Default GLiREL model
DEFAULT_GLIREL_MODEL = "jackboyla/glirel-large-v0"

# Default relation types for POLE+O model
DEFAULT_RELATION_TYPES: dict[str, str] = {
    # Person relations
    "works_at": "Person works at or is employed by an organization",
    "lives_in": "Person lives in or resides at a location",
    "born_in": "Person was born in a location",
    "member_of": "Person is a member of an organization or group",
    "knows": "Person knows or is acquainted with another person",
    "related_to": "Person is related to (family) another person",
    "spouse_of": "Person is married to another person",
    "parent_of": "Person is a parent of another person",
    "child_of": "Person is a child of another person",
    # Organization relations
    "located_in": "Organization or object is located in a place",
    "subsidiary_of": "Organization is a subsidiary of another organization",
    "partner_of": "Organization is a partner of another organization",
    "founded_by": "Organization was founded by a person",
    "ceo_of": "Person is CEO of an organization",
    "owns": "Person or organization owns an object or another organization",
    # Event relations
    "occurred_at": "Event occurred at a location",
    "participated_in": "Person participated in an event",
    "organized_by": "Event was organized by a person or organization",
    # Object relations
    "manufactured_by": "Object was manufactured by an organization",
    "used_by": "Object is used by a person or organization",
    "part_of": "Object or entity is part of another entity",
}


class GLiRELConfig(BaseModel):
    """Configuration for GLiREL relation extractor."""

    model: str = Field(
        default=DEFAULT_GLIREL_MODEL,
        description="GLiREL model to use",
    )
    relation_types: dict[str, str] = Field(
        default_factory=lambda: DEFAULT_RELATION_TYPES.copy(),
        description="Relation types with descriptions",
    )
    threshold: float = Field(
        default=0.5, ge=0.0, le=1.0, description="Confidence threshold for relations"
    )
    device: str = Field(default="cpu", description="Device to run model on (cpu, cuda, mps)")


class GLiRELExtractor:
    """Relation extraction using GLiREL (GLiNER for Relations).

    GLiREL extracts relationships between entities without requiring LLM calls.
    It works best when combined with GLiNER for entity extraction.

    Note: Requires the glirel package: pip install glirel

    Example:
        ```python
        from neo4j_agent_memory.extraction import GLiRELExtractor, GLiNEREntityExtractor

        # First extract entities with GLiNER
        entity_extractor = GLiNEREntityExtractor.for_schema("poleo")
        entity_result = await entity_extractor.extract(text)

        # Then extract relations with GLiREL
        relation_extractor = GLiRELExtractor()
        relations = await relation_extractor.extract_relations(
            text,
            entities=entity_result.entities,
        )
        ```
    """

    def __init__(
        self,
        model: str = DEFAULT_GLIREL_MODEL,
        relation_types: dict[str, str] | list[str] | None = None,
        threshold: float = 0.5,
        device: str = "cpu",
    ):
        """Initialize GLiREL relation extractor.

        Args:
            model: GLiREL model name from HuggingFace
            relation_types: Relation types to extract. Can be:
                - dict[str, str]: Relation names with descriptions (recommended)
                - list[str]: Simple list of relation names
                - None: Use default POLE+O relation types
            threshold: Confidence threshold for including relations
            device: Device to run on (cpu, cuda, mps)
        """
        self._model_name = model
        self._model: Any | None = None  # Lazy load
        self._nlp: Language | None = None  # spaCy model for tokenization

        if relation_types is None:
            self.relation_types = DEFAULT_RELATION_TYPES.copy()
        elif isinstance(relation_types, list):
            self.relation_types = {rt: rt for rt in relation_types}
        else:
            self.relation_types = relation_types

        self.threshold = threshold
        self.device = device

    @property
    def model(self) -> Any:
        """Lazy load GLiREL model."""
        if self._model is None:
            try:
                from glirel import GLiREL  # ty: ignore[unresolved-import]  # glirel ships no types

                logger.info(f"Loading GLiREL model: {self._model_name}")
                self._model = GLiREL.from_pretrained(self._model_name)
                # GLiREL uses .to() for device placement
                if hasattr(self._model, "to"):
                    self._model.to(self.device)
                logger.info(f"GLiREL model loaded on {self.device}")
            except ImportError:
                raise ImportError(
                    "GLiREL is required for GLiRELExtractor. Install with: pip install glirel"
                )
            except Exception as e:
                raise RuntimeError(f"Could not load GLiREL model '{self._model_name}': {e}") from e
        return self._model

    @property
    def nlp(self) -> Any:
        """Lazy load spaCy model for tokenization."""
        if self._nlp is None:
            try:
                import spacy

                self._nlp = spacy.load("en_core_web_sm")
                logger.info("spaCy model loaded for tokenization")
            except ImportError:
                raise ImportError(
                    "spaCy is required for GLiREL tokenization. "
                    "Install with: pip install spacy && python -m spacy download en_core_web_sm"
                )
            except OSError:
                raise RuntimeError(
                    "spaCy model not found. Download with: python -m spacy download en_core_web_sm"
                )
        return self._nlp

    def _tokenize(self, text: str) -> list[str]:
        """Tokenize text using spaCy."""
        doc = self.nlp(text)
        return [token.text for token in doc]

    def _entities_to_glirel_format(
        self,
        entities: list[ExtractedEntity],
    ) -> list[list[int | str]]:
        """Convert ExtractedEntity list to GLiREL NER format.

        GLiREL expects entities as: [[start_token, end_token, type, text], ...]
        But it also accepts character positions when using predict_relations with text.
        """
        glirel_entities: list[list[int | str]] = []
        for entity in entities:
            # GLiREL format: [start, end, type, text]
            # Using character positions
            start: int = entity.start_pos if entity.start_pos is not None else 0
            end: int = entity.end_pos if entity.end_pos is not None else len(entity.name)
            glirel_entities.append([start, end, entity.type, entity.name])
        return glirel_entities

    def _extract_relations_sync(
        self,
        text: str,
        entities: list[ExtractedEntity],
        relation_types: list[str] | None = None,
    ) -> list[ExtractedRelation]:
        """Synchronous relation extraction using GLiREL."""
        if not entities or len(entities) < 2:
            # Need at least 2 entities for relations
            return []

        # Determine relation types to use
        if relation_types:
            labels = [rt for rt in relation_types if rt in self.relation_types]
            if not labels:
                labels = relation_types
        else:
            labels = list(self.relation_types.keys())

        # Tokenize text
        tokens = self._tokenize(text)

        # Convert entities to GLiREL format
        ner_entities = self._entities_to_glirel_format(entities)

        # Run GLiREL prediction
        predictions = self.model.predict_relations(
            tokens,
            labels,
            threshold=self.threshold,
            ner=ner_entities,
        )

        relations = []
        for pred in predictions:
            head_text = pred.get("head_text", "")
            tail_text = pred.get("tail_text", "")
            label = pred.get("label", "RELATED_TO")
            score = pred.get("score", 0.0)

            # Create relation
            relation = ExtractedRelation(
                source=head_text,
                target=tail_text,
                relation_type=label.upper().replace(" ", "_"),
                confidence=score,
            )
            relations.append(relation)

        return relations

    async def extract_relations(
        self,
        text: str,
        entities: list[ExtractedEntity],
        *,
        relation_types: list[str] | None = None,
    ) -> list[ExtractedRelation]:
        """Extract relations between entities using GLiREL.

        Args:
            text: The source text
            entities: Pre-extracted entities (from GLiNER or other extractor)
            relation_types: Optional list of relation types to extract.
                           Defaults to all configured relation types.

        Returns:
            List of extracted relations
        """

        if not text or not text.strip() or not entities:
            return []

        # Run in thread pool to avoid blocking
        loop = asyncio.get_event_loop()
        relations = await loop.run_in_executor(
            None,
            self._extract_relations_sync,
            text,
            entities,
            relation_types,
        )

        logger.debug(f"GLiREL extracted {len(relations)} relations")
        return relations

    @classmethod
    def from_config(cls, config: GLiRELConfig) -> GLiRELExtractor:
        """Create extractor from configuration."""
        return cls(
            model=config.model,
            relation_types=config.relation_types,
            threshold=config.threshold,
            device=config.device,
        )

    @classmethod
    def for_poleo(
        cls,
        model: str = DEFAULT_GLIREL_MODEL,
        threshold: float = 0.5,
        device: str = "cpu",
    ) -> GLiRELExtractor:
        """Create extractor with default POLE+O relation types."""
        return cls(
            model=model,
            relation_types=DEFAULT_RELATION_TYPES,
            threshold=threshold,
            device=device,
        )


class GLiNERWithRelationsExtractor:
    """Combined entity and relation extraction using GLiNER + GLiREL.

    This extractor combines GLiNER for entity extraction and GLiREL for
    relation extraction, providing a complete extraction solution without
    requiring LLM calls.

    Example:
        ```python
        extractor = GLiNERWithRelationsExtractor.for_schema("poleo")
        result = await extractor.extract("John works at Acme Corp in New York.")
        print(f"Entities: {result.entities}")
        print(f"Relations: {result.relations}")
        ```
    """

    def __init__(
        self,
        entity_extractor: GLiNEREntityExtractor,
        relation_extractor: GLiRELExtractor,
    ):
        """Initialize combined extractor.

        Args:
            entity_extractor: GLiNER entity extractor
            relation_extractor: GLiREL relation extractor
        """
        self._entity_extractor = entity_extractor
        self._relation_extractor = relation_extractor

    async def extract(
        self,
        text: str,
        *,
        entity_types: list[str] | None = None,
        relation_types: list[str] | None = None,
        extract_relations: bool = True,
        extract_preferences: bool = False,
    ) -> ExtractionResult:
        """Extract entities and relations from text.

        Args:
            text: The text to extract from
            entity_types: Optional list of entity types to extract
            relation_types: Optional list of relation types to extract
            extract_relations: Whether to extract relations (default True)
            extract_preferences: Ignored (GLiNER/GLiREL don't extract preferences)

        Returns:
            ExtractionResult with entities and relations
        """
        if not text or not text.strip():
            return ExtractionResult(source_text=text)

        # Extract entities
        entity_result = await self._entity_extractor.extract(
            text,
            entity_types=entity_types,
        )

        # Extract relations if requested and we have enough entities
        relations = []
        if extract_relations and len(entity_result.entities) >= 2:
            relations = await self._relation_extractor.extract_relations(
                text,
                entity_result.entities,
                relation_types=relation_types,
            )

        return ExtractionResult(
            entities=entity_result.entities,
            relations=relations,
            preferences=[],  # GLiNER/GLiREL don't extract preferences
            source_text=text,
        )

    @classmethod
    def for_schema(
        cls,
        schema_name: str,
        *,
        gliner_model: str = DEFAULT_GLINER2_MODEL,
        glirel_model: str = DEFAULT_GLIREL_MODEL,
        entity_threshold: float = 0.5,
        relation_threshold: float = 0.5,
        device: str = "cpu",
        relation_types: dict[str, str] | None = None,
    ) -> GLiNERWithRelationsExtractor:
        """Create combined extractor for a domain schema.

        Args:
            schema_name: Domain schema name (poleo, podcast, news, etc.)
            gliner_model: GLiNER model for entity extraction
            glirel_model: GLiREL model for relation extraction
            entity_threshold: Confidence threshold for entities
            relation_threshold: Confidence threshold for relations
            device: Device to run models on
            relation_types: Custom relation types (defaults to POLE+O relations)

        Returns:
            GLiNERWithRelationsExtractor instance
        """
        entity_extractor = GLiNEREntityExtractor.for_schema(
            schema_name,
            model=gliner_model,
            threshold=entity_threshold,
            device=device,
        )

        relation_extractor = GLiRELExtractor(
            model=glirel_model,
            relation_types=relation_types or DEFAULT_RELATION_TYPES,
            threshold=relation_threshold,
            device=device,
        )

        return cls(entity_extractor, relation_extractor)

    @classmethod
    def for_poleo(
        cls,
        *,
        gliner_model: str = DEFAULT_GLINER2_MODEL,
        glirel_model: str = DEFAULT_GLIREL_MODEL,
        entity_threshold: float = 0.5,
        relation_threshold: float = 0.5,
        device: str = "cpu",
    ) -> GLiNERWithRelationsExtractor:
        """Create combined extractor optimized for POLE+O extraction.

        Args:
            gliner_model: GLiNER model for entity extraction
            glirel_model: GLiREL model for relation extraction
            entity_threshold: Confidence threshold for entities
            relation_threshold: Confidence threshold for relations
            device: Device to run models on

        Returns:
            GLiNERWithRelationsExtractor instance
        """
        return cls.for_schema(
            "poleo",
            gliner_model=gliner_model,
            glirel_model=glirel_model,
            entity_threshold=entity_threshold,
            relation_threshold=relation_threshold,
            device=device,
        )
