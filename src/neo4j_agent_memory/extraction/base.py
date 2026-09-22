"""Base extraction classes and protocols."""

import logging
import re
from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from neo4j_agent_memory.ontology.models import OntologyDocument

logger = logging.getLogger(__name__)

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

# Separators an author may use inside a relationship type name.
_RELATION_SEPARATORS = re.compile(r"[\s\-]+")


def normalize_relation_type(name: str) -> str:
    """Normalise a relationship type name to UPPER_SNAKE.

    Relationship types are written by hand in ontology documents (``WORKS_AT``,
    ``works at``, ``works-at``) and decoded by the model in whatever casing the
    schema used. Both ends of every comparison — and the ``relation_type``
    stored on an :class:`ExtractedRelation` — go through this one function so a
    tolerantly-spelled declaration still matches what was extracted.

    Args:
        name: A relationship type name in any casing or separator style.

    Returns:
        The name upper-cased with runs of whitespace and hyphens collapsed to a
        single underscore, and repeated underscores collapsed.

    Example:
        ```python
        normalize_relation_type("works at")  # "WORKS_AT"
        normalize_relation_type("Works-At")  # "WORKS_AT"
        ```
    """
    collapsed = _RELATION_SEPARATORS.sub("_", name.strip())
    collapsed = re.sub(r"_+", "_", collapsed)
    return collapsed.strip("_").upper()


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
    id: str | None = Field(
        default=None,
        description=(
            "Per-result mention id assigned by the extractor (e.g. the entity id "
            "produced by a joint entity+relation model such as JointIE), scoped to a "
            "single extraction call. Lets ExtractedRelation.source_id/target_id "
            "reference the exact mention rather than a name, which matters when the "
            "same name is mentioned more than once in one text."
        ),
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
    source_id: str | None = Field(
        default=None,
        description=(
            "Mention id of the source entity, matching ExtractedEntity.id for an "
            "entity produced in the same extraction call. When both source_id and "
            "target_id resolve to known mentions, storage prefers them over a "
            "name-based lookup."
        ),
    )
    target_id: str | None = Field(
        default=None,
        description=(
            "Mention id of the target entity, matching ExtractedEntity.id for an "
            "entity produced in the same extraction call."
        ),
    )
    derived: bool = Field(
        default=False,
        description=(
            "True when this relation was derived (e.g. an inverse mirror of an "
            "asserted relation) rather than directly observed by the extractor. "
            "Storage folds this with AND across repeated observations, so once a "
            "relation is asserted (derived=False) at least once it stays False."
        ),
    )

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

    def validate_relations(
        self,
        ontology: "OntologyDocument",
        *,
        mode: Literal["warn", "drop", "raise"] = "warn",
    ) -> "tuple[ExtractionResult, list[ExtractedRelation]]":
        """Check every relation against the ontology's endpoint typing.

        A relation violates the ontology when any of the following holds:

        * its ``relation_type`` is not a declared relationship type,
        * either endpoint cannot be resolved to an entity in this result,
        * the resolved endpoints' labels are not a declared
          ``(source, type, target)`` pattern
          (:meth:`OntologyDocument.permits`).

        Endpoints resolve by ``source_id``/``target_id`` first (the precise
        path for a joint entity+relation extractor, where one name may be
        mentioned twice), then by normalised name.

        Relationship type names are compared through
        :func:`normalize_relation_type` on *both* sides, so an ontology that
        declares ``"works at"`` still matches an extracted ``WORKS_AT``.

        An ontology that declares no relationships cannot express a violation,
        so the result passes through untouched.

        Args:
            ontology: The ontology to validate against.
            mode: ``"warn"`` logs the violations and keeps them, ``"drop"``
                returns a copy without them, ``"raise"`` raises.

        Returns:
            ``(result, violations)`` — the result to use (``self`` unless
            ``mode="drop"`` removed something) and the violating relations.

        Raises:
            ValueError: If ``mode="raise"`` and at least one relation violates
                the ontology.
        """
        if not ontology.relationships or not self.relations:
            return self, []

        by_id = {e.id: e for e in self.entities if e.id is not None}
        by_name: dict[str, ExtractedEntity] = {}
        for entity in self.entities:
            by_name.setdefault(entity.normalized_name, entity)

        declared_types = {
            normalize_relation_type(rel_type) for rel_type in ontology.relationship_types()
        }
        # Normalised copy of ontology.permits(): the declared patterns carry the
        # author's spelling of the type name, the extracted relations carry the
        # model's, and only the normalised forms are comparable.
        permitted = {
            (source.lower(), normalize_relation_type(rel_type), target.lower())
            for source, rel_type, target in ontology.patterns()
        }

        def endpoint_label(mention_id: str | None, name: str) -> str | None:
            entity = by_id.get(mention_id) if mention_id is not None else None
            if entity is None:
                entity = by_name.get(name.lower().strip())
            if entity is None:
                return None
            return ontology.label_for(entity.type, entity.subtype)

        violations: list[ExtractedRelation] = []
        kept: list[ExtractedRelation] = []
        for relation in self.relations:
            source_label = endpoint_label(relation.source_id, relation.source)
            target_label = endpoint_label(relation.target_id, relation.target)
            rel_type = normalize_relation_type(relation.relation_type)
            is_permitted = (
                rel_type in declared_types
                and source_label is not None
                and target_label is not None
                and (source_label.lower(), rel_type, target_label.lower()) in permitted
            )
            if is_permitted:
                kept.append(relation)
            else:
                violations.append(relation)

        if not violations:
            return self, []

        summary = ", ".join(
            f"{r.source} -[{r.relation_type}]-> {r.target}" for r in violations[:10]
        )
        message = (
            f"{len(violations)} relation(s) are not permitted by ontology "
            f"{ontology.domain.name!r}: {summary}"
        )

        if mode == "raise":
            raise ValueError(message)
        if mode == "drop":
            logger.debug("Dropping %s", message)
            return (
                ExtractionResult(
                    entities=self.entities,
                    relations=kept,
                    preferences=self.preferences,
                    source_text=self.source_text,
                ),
                violations,
            )
        logger.warning(message)
        return self, violations


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
