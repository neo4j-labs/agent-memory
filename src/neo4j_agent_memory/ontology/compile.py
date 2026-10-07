"""Compile an ontology document into the artefacts each extractor wants.

GLiNER2.5's JointIE engine enforces relationship endpoint types *during*
beam search, so the ontology has to reach the model as a compiled
``JointSchema`` rather than as a post-hoc filter. The other extractors
take cheaper projections of the same document: a label map, an LLM prompt
fragment, a spaCy label map.

``gliner2`` is imported lazily inside the functions that need it, and
through :func:`importlib.import_module` rather than ``from gliner2 import
...``: the package ships ``py.typed`` but leaves its methods unannotated,
so a typed handle would fail ``mypy --strict`` with "call to untyped
function". Module attribute access yields ``Any``, which is the honest
type here.
"""

from __future__ import annotations

import importlib
from collections.abc import Collection, Hashable, Iterable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, NamedTuple, TypeVar

if TYPE_CHECKING:
    from neo4j_agent_memory.ontology.models import OntologyDocument, RelationshipDef

_T = TypeVar("_T", bound=Hashable)

# spaCy's NER label set, mapped onto POLE+O types. Mirrors
# ``SpacyEntityExtractor.DEFAULT_TYPE_MAPPING`` (kept here so an ontology can
# be compiled without importing the extractor package).
_SPACY_POLE_TYPES: dict[str, str] = {
    "PERSON": "PERSON",
    "ORG": "ORGANIZATION",
    "NORP": "ORGANIZATION",
    "GPE": "LOCATION",
    "LOC": "LOCATION",
    "FAC": "LOCATION",
    "EVENT": "EVENT",
    "PRODUCT": "OBJECT",
    "WORK_OF_ART": "OBJECT",
    "LAW": "OBJECT",
    "LANGUAGE": "OBJECT",
    "DATE": "EVENT",
    "TIME": "EVENT",
    "MONEY": "OBJECT",
    "QUANTITY": "OBJECT",
    "ORDINAL": "OBJECT",
    "CARDINAL": "OBJECT",
    "PERCENT": "OBJECT",
}

# The subtype spaCy's own mapping gives each label (``SpacyEntityExtractor.
# DEFAULT_SUBTYPE_MAPPING``), duplicated so this module stays extractor-free.
_SPACY_SUBTYPES: dict[str, str] = {
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


def compile_joint_schema(
    doc: OntologyDocument,
    joint: Any = None,
    *,
    labels: Collection[str] | None = None,
    include_relations: bool = True,
) -> Any:
    """Compile an ontology into a GLiNER2.5 ``JointSchema``.

    Args:
        doc: The ontology to compile. It must be structurally sound.
        joint: A ``JointIEEngine`` (or anything with ``create_schema()``) whose
            schema factory should be used. ``None`` constructs a bare
            ``gliner2.joint_ie.JointSchema``.
        labels: Restrict the compiled schema to these entity labels
            (case-insensitive). Relationships survive only when *every*
            endpoint they declare survives — ``JointSchema.relation()`` raises
            on unknown entity types.
        include_relations: Set ``False`` to compile an entity-only schema.

    Returns:
        The compiled ``JointSchema``.

    Raises:
        ValueError: If :meth:`OntologyDocument.validate_structure` reports
            problems.
        ImportError: If ``gliner2`` is not installed and no ``joint`` was
            supplied.
    """
    problems = doc.validate_structure()
    if problems:
        raise ValueError(f"Cannot compile ontology {doc.domain.name!r}: " + "; ".join(problems))

    if joint is not None and hasattr(joint, "create_schema"):
        schema: Any = joint.create_schema()
    else:
        joint_ie = importlib.import_module("gliner2.joint_ie")
        schema = joint_ie.JointSchema()

    if labels is None:
        active = {et.label.lower() for et in doc.entity_types}
    else:
        wanted = {label.lower() for label in labels}
        active = {et.label.lower() for et in doc.entity_types if et.label.lower() in wanted}

    for entity_type in doc.entity_types:
        if entity_type.label.lower() not in active:
            continue
        schema.entity(
            entity_type.label,
            entity_type.description or None,
            threshold=entity_type.threshold,
        )

    if not include_relations:
        return schema

    grouped = _group_relationships(doc)
    kept = {
        rel_type: defs
        for rel_type, defs in grouped.items()
        if all(d.source.lower() in active and d.target.lower() in active for d in defs)
    }

    for rel_type, defs in kept.items():
        merged = _merge_defs(defs)
        # Endpoints are canonicalised to the declared label's own spelling:
        # ``validate_structure`` compares them case-insensitively, so
        # ``source='person'`` against a ``Person`` label is a sound document —
        # but ``JointSchema.relation()`` matches the entity names verbatim and
        # raises "unknown entity types" on the mismatch.
        pairs = _ordered_unique(
            (_canonical_label(doc, d.source), _canonical_label(doc, d.target)) for d in defs
        )
        heads = _ordered_unique(head for head, _ in pairs)
        tails = _ordered_unique(tail for _, tail in pairs)
        # ``symmetric`` is never passed: it compiles to a constraint set that
        # rejects every candidate edge in gliner2 2.0.0. Use ``inverse=``.
        schema.relation(
            rel_type,
            heads,
            tails,
            merged.description,
            threshold=merged.threshold,
            allow_self=merged.allow_self,
            inverse=merged.inverse if merged.inverse in kept else None,
            unique_head=merged.unique_source,
            unique_tail=merged.unique_target,
            acyclic=merged.acyclic,
        )
        # ``relation()`` permits every head x tail combination. When the
        # declared pairs are fewer (WORKS_AT Person->Company plus Robot->Lab
        # must not decode Person->Lab), constrain the decoder to the pairs.
        if len(pairs) < len(heads) * len(tails):
            schema.constraint(_declared_pairs_constraint(rel_type, pairs))

    if doc.no_self_loops:
        # A global no-self-loops constraint would override any relation that
        # explicitly permits them, so apply it per relation instead — and
        # ``allow_self`` is OR-ed across the type's defs, so one permissive
        # endpoint pair is enough to keep self-loops legal.
        for rel_type, defs in kept.items():
            if not _merge_defs(defs).allow_self:
                schema.no_self_loops(rel_type)

    return schema


def compile_attribute_schema(doc: OntologyDocument, model: Any) -> Any:
    """Compile the ontology's enum properties into a span-attribute schema.

    ``JointSchema`` carries no node properties, so enum-valued properties are
    recovered by a second pass whose results are joined back onto the JointIE
    mentions by character offset.

    ``Schema.entity_attributes()`` raises ``ValueError: entity_attributes()
    requires entities() to be called first`` — each ``AttributeGroup.applies_to``
    names an entity the schema must already know — so the entity types carrying
    enum properties are declared first, in one ``entities()`` call.

    Args:
        doc: The ontology to compile.
        model: A loaded extractor, used for its ``create_schema()`` factory.

    Returns:
        The compiled ``Schema``, or ``None`` when the ontology declares no
        enum properties (in which case the second pass is skipped entirely).
    """
    enum_properties = [
        (entity_type, prop)
        for entity_type in doc.entity_types
        for prop in entity_type.properties
        if prop.enum
    ]
    if not enum_properties:
        return None

    attribute_group = importlib.import_module("gliner2").AttributeGroup

    groups: dict[str, Any] = {}
    entities: dict[str, str] = {}
    for entity_type, prop in enum_properties:
        key = prop.name if prop.name not in groups else f"{entity_type.label}.{prop.name}"
        groups[key] = attribute_group(
            labels=list(prop.enum or []),
            applies_to=[entity_type.label],
            qualify_labels=True,
            threshold=entity_type.threshold if entity_type.threshold is not None else 0.5,
        )
        # The description doubles as the annotation guideline here too; fall
        # back to the label so the value is never empty.
        entities.setdefault(entity_type.label, entity_type.description or entity_type.label)

    schema: Any = model.create_schema()
    schema.entities(entities)
    schema.entity_attributes(groups)
    return schema


def label_map(doc: OntologyDocument) -> dict[str, tuple[str, str | None]]:
    """The extractor label -> ``(POLE_TYPE, SUBTYPE)`` table for this ontology.

    The ontology's own labels win; the shared default table stays underneath
    so labels the ontology does not declare still land somewhere sensible.
    """
    from neo4j_agent_memory.extraction.label_mapping import DEFAULT_LABEL_MAPPING

    merged = dict(DEFAULT_LABEL_MAPPING)
    merged.update(doc.label_map())
    return merged


def prompt_fragment(doc: OntologyDocument, *, include_relations: bool = True) -> str:
    """Render the ontology as a Markdown fragment for an LLM extractor prompt.

    Args:
        doc: The ontology to render.
        include_relations: Set ``False`` to describe entity types only.

    Returns:
        A Markdown fragment listing the entity types with their annotation
        guidelines and, optionally, the legal relationship patterns.
    """
    lines: list[str] = ["## Entity types", ""]
    if not doc.entity_types:
        lines.append("_None declared._")
    for entity_type in doc.entity_types:
        pole = entity_type.pole_type.upper()
        if entity_type.subtype:
            pole = f"{pole}:{entity_type.subtype.upper()}"
        description = entity_type.description or "No description."
        lines.append(f"- {entity_type.label} ({pole}): {description}")

    if not include_relations:
        return "\n".join(lines)

    lines += ["", "## Relationship types", ""]
    grouped = _group_relationships(doc)
    if not grouped:
        lines.append("_None declared._")
    for rel_type, defs in grouped.items():
        merged = _merge_defs(defs)
        description = merged.description or "No description."
        for d in defs:
            source = _canonical_label(doc, d.source)
            target = _canonical_label(doc, d.target)
            lines.append(f"- {rel_type}: {source} -> {target} — {description}")
        notes = _constraint_notes(merged)
        if notes:
            lines.append(f"  - Constraints: {', '.join(notes)}")

    return "\n".join(lines)


def spacy_label_map(doc: OntologyDocument) -> dict[str, tuple[str, str | None]] | None:
    """Map spaCy NER labels onto this ontology's ``(pole_type, subtype)`` pairs.

    spaCy's label set is fixed and coarse, so a label is only mapped onto a
    pair it actually decides. Each label resolves to its POLE+O type, then to
    the first of these the ontology declares:

    1. the subtype spaCy's own mapping gives it (``GPE`` -> ``GEOPOLITICAL``),
    2. the spaCy label itself as the subtype (``LAW`` -> ``OBJECT:LAW``),
    3. the bare POLE+O type (``PERSON``).

    A label none of those match is left out. It is never mapped onto some other
    declared subtype of the same type: an ontology whose only person label is
    ``author``, or whose object labels are all diseases, would otherwise
    receive every spaCy person as an author and every amount as a disease.

    Args:
        doc: The ontology to project.

    Returns:
        The label map, or ``None`` when the ontology declares no entity types
        (callers then keep the extractor's own defaults).
    """
    if not doc.entity_types:
        return None

    declared = {
        (et.pole_type.upper(), et.subtype.upper() if et.subtype else None)
        for et in doc.entity_types
    }
    mapping: dict[str, tuple[str, str | None]] = {}
    for spacy_label, pole in _SPACY_POLE_TYPES.items():
        for subtype in (_SPACY_SUBTYPES.get(spacy_label), spacy_label, None):
            if (pole, subtype) in declared:
                mapping[spacy_label] = (pole, subtype)
                break
    return mapping


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------


_DECLARED_PAIRS_TYPE: Any = None


def _declared_pairs_constraint(relation: str, pairs: list[tuple[str, str]]) -> Any:
    """A gliner2 decoding constraint admitting only ``pairs`` for ``relation``.

    gliner2's own ``TypedEndpoints`` checks the head and tail types
    independently, so a relation compiled from several defs admits their cross
    product. This one checks the ``(head, tail)`` pair, during beam search like
    the built-in constraints. Built lazily because ``gliner2`` is optional:
    ``JointSchema.constraint()`` needs a subclass of gliner2's ``Constraint``,
    but a caller that compiles through its own ``joint`` factory may not have
    ``gliner2`` installed, and then a plain object with the same interface is
    enough.
    """
    global _DECLARED_PAIRS_TYPE
    if _DECLARED_PAIRS_TYPE is None:
        base: Any
        try:
            base = importlib.import_module("gliner2.joint_ie.constraints").Constraint
        except ImportError:
            base = object

        def endpoint_type(candidate: Any, side: str) -> Any:
            node = getattr(candidate, side, None)
            for attr in ("entity_type", "label", "type"):
                value = getattr(node, attr, None)
                if value is not None:
                    return value
            return None

        @dataclass(frozen=True)
        class DeclaredEndpointPairs(base):  # type: ignore[misc]  # base is Any (gliner2 is untyped, or absent)
            relation: str
            pairs: frozenset[tuple[str, str]]

            def allows(
                self,
                candidate: Any,
                relations: Sequence[Any] = (),
                entities: Sequence[Any] = (),
            ) -> bool:
                label = getattr(candidate, "relation_type", None) or getattr(
                    candidate, "type", None
                )
                if label != self.relation:
                    return True
                pair = (endpoint_type(candidate, "head"), endpoint_type(candidate, "tail"))
                return pair in self.pairs

        _DECLARED_PAIRS_TYPE = DeclaredEndpointPairs
    return _DECLARED_PAIRS_TYPE(relation, frozenset(pairs))


def _group_relationships(doc: OntologyDocument) -> dict[str, list[RelationshipDef]]:
    """Group relationship defs by ``type``, preserving declaration order."""
    grouped: dict[str, list[RelationshipDef]] = {}
    for rel in doc.relationships:
        grouped.setdefault(rel.type, []).append(rel)
    return grouped


def _canonical_label(doc: OntologyDocument, name: str) -> str:
    """The declared spelling of ``name``, or ``name`` when nothing declares it."""
    entity_type = doc.entity_type(name)
    return entity_type.label if entity_type is not None else name


class _MergedRelation(NamedTuple):
    """One relationship type's constraints, folded over all its defs."""

    description: str | None
    threshold: float | None
    allow_self: bool
    inverse: str | None
    unique_source: bool
    unique_target: bool
    acyclic: bool


def _merge_defs(defs: list[RelationshipDef]) -> _MergedRelation:
    """Fold the defs sharing one ``type`` into a single constraint set.

    ``RelationshipDef`` carries one source/target pair, so a relationship with
    several legal endpoint pairs is several defs — and they can disagree about
    the per-relation constraints. Taking ``defs[0]`` silently discarded the
    rest: a second def with ``allow_self=True`` was overridden *and* still got
    a ``no_self_loops`` constraint.

    The three that have a defensible merge are merged: ``allow_self`` is
    OR-ed (one permissive pair legalises the relation), ``threshold`` takes
    the lowest floor any def asks for, and ``description`` the first non-empty
    one. The other four — ``inverse``, ``unique_source``, ``unique_target``,
    ``acyclic`` — cannot be merged without changing the relation's meaning, so
    :meth:`OntologyDocument.validate_structure` reports a disagreement as a
    problem and ``compile_joint_schema`` refuses the document. The
    conservative OR/first-set values below therefore only ever apply to a
    sound document, where every def agrees.

    Args:
        defs: Every ``RelationshipDef`` sharing one ``type``. Must be non-empty.

    Returns:
        The merged constraint set.
    """
    thresholds = [d.threshold for d in defs if d.threshold is not None]
    return _MergedRelation(
        description=next((d.description for d in defs if d.description), None),
        threshold=min(thresholds) if thresholds else None,
        allow_self=any(d.allow_self for d in defs),
        inverse=next((d.inverse for d in defs if d.inverse), None),
        unique_source=any(d.unique_source for d in defs),
        unique_target=any(d.unique_target for d in defs),
        acyclic=any(d.acyclic for d in defs),
    )


def _ordered_unique(values: Iterable[_T]) -> list[_T]:
    """De-duplicate an iterable, keeping first-seen order."""
    seen: dict[_T, None] = {}
    for value in values:
        seen.setdefault(value, None)
    return list(seen)


def _constraint_notes(rel: _MergedRelation) -> list[str]:
    """Human-readable notes for the decoding constraints on a relationship."""
    notes: list[str] = []
    if rel.unique_source:
        notes.append("at most one per source")
    if rel.unique_target:
        notes.append("at most one per target")
    if rel.acyclic:
        notes.append("acyclic")
    if not rel.allow_self:
        notes.append("no self-loops")
    if rel.inverse:
        notes.append(f"inverse of {rel.inverse}")
    if rel.threshold is not None:
        notes.append(f"confidence floor {rel.threshold}")
    return notes


__all__ = [
    "compile_attribute_schema",
    "compile_joint_schema",
    "label_map",
    "prompt_fragment",
    "spacy_label_map",
]
