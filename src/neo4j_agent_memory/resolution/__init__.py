"""Entity resolution strategies for deduplication."""

from neo4j_agent_memory.resolution.base import (
    EntityResolver,
    ResolutionMatch,
    ResolvedEntity,
)
from neo4j_agent_memory.resolution.composite import CompositeResolver
from neo4j_agent_memory.resolution.exact import ExactMatchResolver
from neo4j_agent_memory.resolution.fuzzy import FuzzyMatchResolver
from neo4j_agent_memory.resolution.ontology import (
    EntityResolution,
    NormalizedName,
    OntologyResolver,
    normalize_name,
)
from neo4j_agent_memory.resolution.semantic import SemanticMatchResolver

__all__ = [
    "EntityResolver",
    "ResolvedEntity",
    "ResolutionMatch",
    "ExactMatchResolver",
    "FuzzyMatchResolver",
    "SemanticMatchResolver",
    "CompositeResolver",
    "OntologyResolver",
    "EntityResolution",
    "NormalizedName",
    "normalize_name",
]
