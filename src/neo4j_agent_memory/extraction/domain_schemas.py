"""Domain catalogs of labelled entity types for zero-shot extraction.

Eight ready-made schemas (POLE+O plus seven verticals) name the entity
types a domain cares about and describe each one in a sentence. The
description is what makes them worth having: GLiNER2.5 reads it as an
annotation guideline, so a described label extracts noticeably better
than a bare one.

A :class:`DomainSchema` is a catalog, not an ontology — it has labels but
no endpoint-typed relationships. :meth:`DomainSchema.to_ontology` converts
one into an :class:`~neo4j_agent_memory.ontology.models.OntologyDocument`,
which is what the extractor actually compiles; the built-in templates in
:mod:`neo4j_agent_memory.ontology.builtin` are these same catalogs with
relationships attached.

    from neo4j_agent_memory.extraction import get_schema

    doc = get_schema("podcast").to_ontology()
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from neo4j_agent_memory.ontology.models import OntologyDocument, RelationshipDef


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

    def to_ontology(
        self,
        *,
        relationships: list[RelationshipDef] | None = None,
    ) -> OntologyDocument:
        """Convert this catalog into an ontology document.

        Each label becomes an entity type mapped onto its POLE+O
        ``(type, subtype)`` pair, keeping the catalog description as the
        annotation guideline. The catalog declares no endpoint typing, so
        relationships have to be supplied by the caller (the built-in
        templates do exactly that).

        Args:
            relationships: Typed relationships to attach. Each ``source`` and
                ``target`` must name a label this schema declares. A
                relationship without a description picks one up from
                :attr:`relation_types` when it names the same type.

        Returns:
            The equivalent :class:`OntologyDocument`.
        """
        from neo4j_agent_memory.ontology.convert import from_domain_schema

        return from_domain_schema(self, relationships=relationships)


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


__all__ = [
    "DEFAULT_POLEO_LABELS",
    "DOMAIN_SCHEMAS",
    "DomainSchema",
    "get_schema",
    "list_schemas",
]
