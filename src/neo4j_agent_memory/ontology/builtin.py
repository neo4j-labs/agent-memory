"""Built-in ontologies: POLE+O plus the eight domain catalog templates.

:data:`POLEO_ONTOLOGY` is the library default — the same five entity types
and sixteen relationship types :mod:`neo4j_agent_memory.schema.models`
declares, but written as GLiNER2.5 annotation guidelines with explicit
endpoint typing and conservative decoding constraints.

The domain templates are derived from ``extraction.DOMAIN_SCHEMAS`` on
first use, so importing this module costs nothing and needs no extractor
runtime installed.
"""

from __future__ import annotations

from typing import Any

from neo4j_agent_memory.ontology.models import (
    DomainInfo,
    EntityTypeDef,
    OntologyDocument,
    RelationshipDef,
)

# -----------------------------------------------------------------------------
# POLE+O
# -----------------------------------------------------------------------------

_PERSON = "Person"
_ORGANIZATION = "Organization"
_LOCATION = "Location"
_EVENT = "Event"
_OBJECT = "Object"

_ALL_POLEO_LABELS = (_PERSON, _ORGANIZATION, _LOCATION, _EVENT, _OBJECT)


def _expand(
    rel_type: str,
    sources: tuple[str, ...],
    targets: tuple[str, ...],
    description: str,
    **constraints: Any,
) -> list[RelationshipDef]:
    """One :class:`RelationshipDef` per ``(source, target)`` pair.

    ``RelationshipDef`` carries single labels (the NAMS wire shape); the
    compiler regroups same-``type`` defs into one JointIE relation with
    head/tail lists.
    """
    return [
        RelationshipDef(
            type=rel_type,
            source=source,
            target=target,
            description=description,
            **constraints,
        )
        for source in sources
        for target in targets
    ]


def _poleo_entity_types() -> list[EntityTypeDef]:
    return [
        EntityTypeDef(
            label=_PERSON,
            pole_type="PERSON",
            description=(
                "A named human individual — a real person referred to by name, "
                "alias or persona. Not a job title, not a fictional character, "
                "and not an organization named after a person."
            ),
            color="#4CAF50",
        ),
        EntityTypeDef(
            label=_ORGANIZATION,
            pole_type="ORGANIZATION",
            description=(
                "A named company, institution, government agency, non-profit or "
                "other formal group of people. Not an industry or market segment, "
                "and not a product the organization sells."
            ),
            color="#F44336",
        ),
        EntityTypeDef(
            label=_LOCATION,
            pole_type="LOCATION",
            description=(
                "A place: an address, city, region, country, or named landmark. "
                "Not an organization that happens to occupy a building, and not a "
                "nationality or demonym."
            ),
            color="#FF9800",
        ),
        EntityTypeDef(
            label=_EVENT,
            pole_type="EVENT",
            description=(
                "Something that happened at a point or interval in time — an "
                "incident, meeting, transaction or milestone. Not a recurring "
                "process or a standing activity, and not a bare date."
            ),
            color="#9C27B0",
        ),
        EntityTypeDef(
            label=_OBJECT,
            pole_type="OBJECT",
            description=(
                "A physical or digital item: a vehicle, device, document, product, "
                "phone number or account identifier. Last resort — check every "
                "other type first. Not a person, place, organization or event."
            ),
            color="#2196F3",
        ),
    ]


def _poleo_relationships() -> list[RelationshipDef]:
    rels: list[RelationshipDef] = []

    # -- person ---------------------------------------------------------------
    rels += _expand(
        "KNOWS",
        (_PERSON,),
        (_PERSON,),
        "One person has a personal or professional acquaintance with another.",
    )
    rels += _expand(
        "ALIAS_OF",
        (_PERSON,),
        (_PERSON,),
        "One person record is an alternative identity of another; the same human.",
        acyclic=True,
    )
    rels += _expand(
        "MEMBER_OF",
        (_PERSON,),
        (_ORGANIZATION,),
        "A person belongs to an organization without necessarily being paid by it.",
    )
    rels += _expand(
        "EMPLOYED_BY",
        (_PERSON,),
        (_ORGANIZATION,),
        "A person is paid to work for an organization. Their current employer only.",
        unique_source=True,
    )

    # -- object ---------------------------------------------------------------
    rels += _expand(
        "OWNS",
        (_PERSON, _ORGANIZATION),
        (_OBJECT,),
        "A person or organization holds legal ownership of an item.",
    )
    rels += _expand(
        "USES",
        (_PERSON,),
        (_OBJECT,),
        "A person operates or makes use of an item without necessarily owning it.",
    )

    # -- location -------------------------------------------------------------
    rels += _expand(
        "LOCATED_AT",
        (_PERSON, _OBJECT, _ORGANIZATION, _EVENT),
        (_LOCATION,),
        "An entity is physically at a place. Use the more specific "
        "RESIDES_AT / HEADQUARTERS_AT / OCCURRED_AT where they apply.",
    )
    rels += _expand(
        "RESIDES_AT",
        (_PERSON,),
        (_LOCATION,),
        "A person lives at a place. Their current home only.",
        unique_source=True,
    )
    rels += _expand(
        "HEADQUARTERS_AT",
        (_ORGANIZATION,),
        (_LOCATION,),
        "An organization has its principal place of business at a location.",
        unique_source=True,
    )

    # -- event ----------------------------------------------------------------
    rels += _expand(
        "PARTICIPATED_IN",
        (_PERSON, _ORGANIZATION),
        (_EVENT,),
        "A person or organization took an active part in an event.",
    )
    rels += _expand(
        "OCCURRED_AT",
        (_EVENT,),
        (_LOCATION,),
        "An event took place at a location.",
    )
    rels += _expand(
        "INVOLVED",
        (_EVENT,),
        (_OBJECT,),
        "An item featured in an event as instrument, subject or evidence.",
    )

    # -- organization ---------------------------------------------------------
    rels += _expand(
        "SUBSIDIARY_OF",
        (_ORGANIZATION,),
        (_ORGANIZATION,),
        "One organization is owned or controlled by another.",
        acyclic=True,
    )
    rels += _expand(
        "PARTNER_WITH",
        (_ORGANIZATION,),
        (_ORGANIZATION,),
        "Two organizations have a declared partnership or alliance.",
    )

    # -- catch-alls (high floor: only take these when nothing specific fits) ---
    rels += _expand(
        "RELATED_TO",
        _ALL_POLEO_LABELS,
        _ALL_POLEO_LABELS,
        "A stated connection that no more specific relationship covers. "
        "Last resort — check every other relationship first.",
        threshold=0.6,
    )
    rels += _expand(
        "MENTIONS",
        _ALL_POLEO_LABELS,
        _ALL_POLEO_LABELS,
        "One entity is referred to in the description of another, with no "
        "stronger relationship asserted. Last resort.",
        threshold=0.6,
    )

    return rels


#: The library default ontology: POLE+O as a typed, described, constrained graph.
POLEO_ONTOLOGY: OntologyDocument = OntologyDocument(
    domain=DomainInfo(
        id="poleo",
        name="poleo",
        description=(
            "Person, Object, Location, Event, Organization — the default "
            "investigative entity model."
        ),
        tagline="The default POLE+O entity model",
    ),
    entity_types=_poleo_entity_types(),
    relationships=_poleo_relationships(),
)


# -----------------------------------------------------------------------------
# Domain catalog templates
# -----------------------------------------------------------------------------

# Extra relationships for the templates whose domains have an obvious typed
# backbone. Everything else stays entity-only: a wrong edge type costs more
# than a missing one. Keys are template names, values are
# ``(type, source_label, target_label, description)`` tuples.
#
# ``poleo`` has no entry: that template is :data:`POLEO_ONTOLOGY` itself, which
# already declares all sixteen relationship types.
_TEMPLATE_RELATIONSHIPS: dict[str, tuple[tuple[str, str, str, str], ...]] = {
    "podcast": (
        ("WORKS_AT", "person", "company", "A person works at a company; their current employer."),
        ("FOUNDED", "person", "company", "A person started a company."),
        ("LOCATED_IN", "company", "location", "A company is based in a place."),
        ("DISCUSSES", "person", "concept", "A speaker talks about an idea or framework."),
    ),
    "news": (
        (
            "WORKS_AT",
            "person",
            "organization",
            "A person works at an organization; their current employer.",
        ),
        ("LOCATED_IN", "organization", "location", "An organization is based in a place."),
        ("PARTICIPATED_IN", "person", "event", "A person took an active part in an event."),
        ("OCCURRED_AT", "event", "location", "An event took place at a location."),
    ),
}

_TEMPLATE_CACHE: dict[str, OntologyDocument] = {}


def _build_template(name: str) -> OntologyDocument:
    """Convert one catalog ``DomainSchema`` into an ontology document.

    ``poleo`` is the exception: the catalog entry is a flat five-label list,
    while :data:`POLEO_ONTOLOGY` is the curated version of the same model
    (annotation guidelines, sixteen typed relationships, decoding
    constraints). The template *is* the curated document, so
    ``get_template("poleo")`` and the library default never diverge.
    """
    if name == "poleo":
        return POLEO_ONTOLOGY

    # Imported lazily: ``extraction`` pulls in the extractor factory, which
    # will import this package back once the runtime is ontology-driven.
    from neo4j_agent_memory.extraction import DOMAIN_SCHEMAS
    from neo4j_agent_memory.ontology.convert import from_domain_schema

    schema = DOMAIN_SCHEMAS[name]
    relationships = [
        RelationshipDef(type=rel_type, source=source, target=target, description=description)
        for rel_type, source, target, description in _TEMPLATE_RELATIONSHIPS.get(name, ())
    ]
    return from_domain_schema(schema, relationships=relationships or None)


def list_templates() -> list[str]:
    """Names of the built-in domain templates."""
    from neo4j_agent_memory.extraction import DOMAIN_SCHEMAS

    return list(DOMAIN_SCHEMAS)


def get_template(name: str) -> OntologyDocument:
    """Return a built-in domain template by name (built on first use).

    Args:
        name: One of :func:`list_templates`.

    Returns:
        The template's :class:`~neo4j_agent_memory.ontology.models.OntologyDocument`.

    Raises:
        ValueError: If ``name`` is not a known template.
    """
    if name not in _TEMPLATE_CACHE:
        available = list_templates()
        if name not in available:
            raise ValueError(
                f"Unknown ontology template {name!r}. Available: {', '.join(available)}"
            )
        _TEMPLATE_CACHE[name] = _build_template(name)
    return _TEMPLATE_CACHE[name]


def __getattr__(name: str) -> Any:
    """Expose ``DOMAIN_TEMPLATES`` as a lazily-materialised mapping."""
    if name == "DOMAIN_TEMPLATES":
        return {template: get_template(template) for template in list_templates()}
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["POLEO_ONTOLOGY", "get_template", "list_templates"]
