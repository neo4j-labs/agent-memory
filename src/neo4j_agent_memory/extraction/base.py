"""Base extraction classes and protocols."""

import re
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field

# Stopwords and invalid entity patterns to filter out during extraction
# These are common words that should never be extracted as named entities
ENTITY_STOPWORDS: frozenset[str] = frozenset(
    {
        # Pronouns
        "i",
        "me",
        "my",
        "myself",
        "we",
        "our",
        "ours",
        "ourselves",
        "you",
        "your",
        "yours",
        "yourself",
        "yourselves",
        "he",
        "him",
        "his",
        "himself",
        "she",
        "her",
        "hers",
        "herself",
        "it",
        "its",
        "itself",
        "they",
        "them",
        "their",
        "theirs",
        "themselves",
        "what",
        "which",
        "who",
        "whom",
        "this",
        "that",
        "these",
        "those",
        # Common verbs
        "am",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "having",
        "do",
        "does",
        "did",
        "doing",
        "would",
        "should",
        "could",
        "ought",
        "might",
        "must",
        "shall",
        "will",
        "can",
        # Articles and determiners
        "a",
        "an",
        "the",
        "some",
        "any",
        "no",
        "every",
        "each",
        "either",
        "neither",
        # Prepositions
        "in",
        "on",
        "at",
        "by",
        "for",
        "with",
        "about",
        "against",
        "between",
        "into",
        "through",
        "during",
        "before",
        "after",
        "above",
        "below",
        "to",
        "from",
        "up",
        "down",
        "out",
        "off",
        "over",
        "under",
        # Conjunctions
        "and",
        "but",
        "or",
        "nor",
        "so",
        "yet",
        "both",
        "not",
        "only",
        "than",
        "when",
        "where",
        "while",
        "if",
        "because",
        "although",
        # Adverbs
        "here",
        "there",
        "why",
        "how",
        "all",
        "few",
        "more",
        "most",
        "other",
        "such",
        "own",
        "same",
        "too",
        "very",
        "just",
        "also",
        "now",
        "then",
        "once",
        "always",
        "never",
        "often",
        "still",
        "already",
        # Common nouns that are too generic
        "thing",
        "things",
        "stuff",
        "way",
        "ways",
        "something",
        "anything",
        "nothing",
        "someone",
        "anyone",
        "everyone",
        "nobody",
        "everybody",
        "somebody",
        "people",
        "person",
        "man",
        "woman",
        "men",
        "women",
        "guy",
        "guys",
        "time",
        "times",
        "day",
        "days",
        "year",
        "years",
        "today",
        "tomorrow",
        "yesterday",
        # Generic references
        "one",
        "ones",
        "two",
        "first",
        "second",
        "third",
        "last",
        "next",
        # Filler words
        "like",
        "really",
        "actually",
        "basically",
        "literally",
        "maybe",
        "probably",
        "perhaps",
        "well",
        "okay",
        "ok",
        "yes",
        "yeah",
        "yep",
        "nope",
        # Conversation artifacts
        "um",
        "uh",
        "ah",
        "oh",
        "hmm",
        "hm",
        "er",
        "eh",
    }
)

# Minimum length for entity names (single characters and very short strings are usually noise)
MIN_ENTITY_LENGTH = 2

# Pattern for purely numeric entities (these are usually not named entities)
NUMERIC_PATTERN = re.compile(r"^[\d\s.,%-]+$")

# Pattern for entities that are just punctuation or special characters
INVALID_CHARS_PATTERN = re.compile(r"^[\s\W]+$")


def is_valid_entity_name(name: str) -> bool:
    """Check if an entity name is valid (not a stopword or noise).

    Args:
        name: The entity name to validate

    Returns:
        True if the entity name is valid, False otherwise
    """
    if not name:
        return False

    # Normalize for comparison
    normalized = name.lower().strip()

    # Check minimum length
    if len(normalized) < MIN_ENTITY_LENGTH:
        return False

    # Check if it's a stopword
    if normalized in ENTITY_STOPWORDS:
        return False

    # Check if it's purely numeric
    if NUMERIC_PATTERN.match(normalized):
        return False

    # Check if it's just punctuation/special characters
    if INVALID_CHARS_PATTERN.match(normalized):
        return False

    return True


class ExtractedEntity(BaseModel):
    """Entity extracted from text.

    Supports the POLE+O model (Person, Object, Location, Event, Organization)
    as well as custom entity types and subtypes.
    """

    name: str = Field(description="Entity name")
    type: str = Field(description="Entity type (PERSON, OBJECT, LOCATION, EVENT, ORGANIZATION)")
    subtype: str | None = Field(
        default=None, description="Entity subtype (e.g., VEHICLE for OBJECT, ADDRESS for LOCATION)"
    )
    start_pos: int | None = Field(default=None, description="Start position in text")
    end_pos: int | None = Field(default=None, description="End position in text")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="Confidence score")
    context: str | None = Field(default=None, description="Surrounding context")
    attributes: dict[str, Any] = Field(
        default_factory=dict, description="Additional attributes extracted for this entity"
    )
    extractor: str | None = Field(
        default=None, description="Name of the extractor that produced this entity"
    )

    @property
    def normalized_name(self) -> str:
        """Return normalized entity name (lowercase, stripped)."""
        return self.name.lower().strip()

    @property
    def full_type(self) -> str:
        """Return full type including subtype if present."""
        if self.subtype:
            return f"{self.type}:{self.subtype}"
        return self.type


class ExtractedRelation(BaseModel):
    """Relation extracted from text."""

    source: str = Field(description="Source entity name")
    target: str = Field(description="Target entity name")
    relation_type: str = Field(description="Type of relationship")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="Confidence score")

    @property
    def as_triple(self) -> tuple[str, str, str]:
        """Return relation as (source, relation, target) triple."""
        return (self.source, self.relation_type, self.target)


class ExtractedPreference(BaseModel):
    """Preference extracted from text."""

    category: str = Field(description="Preference category")
    preference: str = Field(description="The preference statement")
    context: str | None = Field(default=None, description="Context where preference applies")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="Confidence score")


class ExtractionResult(BaseModel):
    """Result of entity and relation extraction."""

    entities: list[ExtractedEntity] = Field(default_factory=list)
    relations: list[ExtractedRelation] = Field(default_factory=list)
    preferences: list[ExtractedPreference] = Field(default_factory=list)
    source_text: str | None = Field(default=None, description="Original source text")

    @property
    def entity_count(self) -> int:
        """Return number of entities."""
        return len(self.entities)

    @property
    def relation_count(self) -> int:
        """Return number of relations."""
        return len(self.relations)

    @property
    def preference_count(self) -> int:
        """Return number of preferences."""
        return len(self.preferences)

    def entities_by_type(self) -> dict[str, list[ExtractedEntity]]:
        """Group entities by type."""
        result: dict[str, list[ExtractedEntity]] = {}
        for entity in self.entities:
            if entity.type not in result:
                result[entity.type] = []
            result[entity.type].append(entity)
        return result

    def get_entities_of_type(self, entity_type: str) -> list[ExtractedEntity]:
        """Get entities of a specific type."""
        return [e for e in self.entities if e.type.upper() == entity_type.upper()]

    def deduplicate_entities(self) -> "ExtractionResult":
        """Return a new ExtractionResult with duplicate entities removed.

        Deduplicates by normalized name + type, keeping the entity with the
        highest confidence score. Also filters relations that reference
        removed entities.

        Returns:
            New ExtractionResult with deduplicated entities and relations
        """
        seen: dict[str, ExtractedEntity] = {}
        for entity in self.entities:
            key = f"{entity.normalized_name}::{entity.type}"
            if key not in seen or entity.confidence > seen[key].confidence:
                seen[key] = entity

        deduped_entities = list(seen.values())

        # Filter relations to only reference surviving entities
        valid_names = {e.normalized_name for e in deduped_entities}
        valid_relations = [
            r
            for r in self.relations
            if r.source.lower().strip() in valid_names and r.target.lower().strip() in valid_names
        ]

        return ExtractionResult(
            entities=deduped_entities,
            relations=valid_relations,
            preferences=self.preferences,
            source_text=self.source_text,
        )

    def filter_invalid_entities(self) -> "ExtractionResult":
        """Return a new ExtractionResult with invalid entities filtered out.

        Filters entities that are:
        - Stopwords (pronouns, articles, common verbs, etc.)
        - Too short (less than 2 characters)
        - Purely numeric
        - Only punctuation/special characters

        Also filters relations that reference removed entities.

        Returns:
            New ExtractionResult with only valid entities and relations
        """
        # Filter entities
        valid_entities = [e for e in self.entities if is_valid_entity_name(e.name)]

        # Get set of valid entity names for relation filtering
        valid_entity_names = {e.normalized_name for e in valid_entities}

        # Filter relations to only include those between valid entities
        valid_relations = [
            r
            for r in self.relations
            if r.source.lower().strip() in valid_entity_names
            and r.target.lower().strip() in valid_entity_names
        ]

        return ExtractionResult(
            entities=valid_entities,
            relations=valid_relations,
            preferences=self.preferences,  # Preferences don't need filtering
            source_text=self.source_text,
        )


@runtime_checkable
class EntityExtractor(Protocol):
    """Protocol for entity extraction implementations."""

    async def extract(
        self,
        text: str,
        *,
        entity_types: list[str] | None = None,
        extract_relations: bool = True,
        extract_preferences: bool = True,
    ) -> ExtractionResult:
        """
        Extract entities and relations from text.

        Args:
            text: The text to extract from
            entity_types: Optional list of entity types to extract
            extract_relations: Whether to extract relations
            extract_preferences: Whether to extract preferences

        Returns:
            ExtractionResult containing entities, relations, and preferences
        """
        ...


class NoOpExtractor:
    """Extractor that does nothing (for when extraction is disabled)."""

    async def extract(
        self,
        text: str,
        *,
        entity_types: list[str] | None = None,
        extract_relations: bool = True,
        extract_preferences: bool = True,
    ) -> ExtractionResult:
        """Return empty extraction result."""
        return ExtractionResult(source_text=text)
