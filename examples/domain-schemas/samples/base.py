"""Shared types for the domain-schema sample corpora.

Each module in this package exports a single :class:`SampleSet` describing one
GLiNER domain schema: the documents to extract from, how to summarise the
result, and which optional demos (`relations`, `batch`, `streaming`) the schema
showcases. ``run.py`` is the only consumer.

Every corpus in this package is **synthetic**. Quotes, figures and events are
invented for demonstration purposes.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Optional demos the runner can execute for any schema (``run.py --relations``
# and friends). Each sample declares the ones it showcases by default.
RELATIONS = "relations"
BATCH = "batch"
STREAMING = "streaming"
ALL_DEMOS = (RELATIONS, BATCH, STREAMING)


@dataclass(frozen=True)
class Document:
    """One sample document.

    Attributes:
        title: Heading printed above the extraction result.
        meta: Optional one-line subtitle (source, venue, date, guest...).
        content: The text handed to the extractor.
    """

    title: str
    meta: str
    content: str


@dataclass(frozen=True)
class Highlight:
    """One "interesting entities" section of the run summary.

    Attributes:
        label: Section heading, e.g. ``"Drugs & Medications"``.
        entity_type: POLE+O type to select (``PERSON``, ``OBJECT``, ...).
        subtypes: Optional subtypes to narrow to. Empty means "any subtype".
        limit: Maximum entities printed in this section.
        show_subtype: Print ``[SUBTYPE]`` after each name.
    """

    label: str
    entity_type: str
    subtypes: tuple[str, ...] = ()
    limit: int = 10
    show_subtype: bool = False

    def matches(self, entity_type: str, subtype: str | None) -> bool:
        """Return True when an extracted entity belongs in this section."""
        if entity_type != self.entity_type:
            return False
        if not self.subtypes:
            return True
        return subtype is not None and subtype in self.subtypes


@dataclass(frozen=True)
class SampleSet:
    """A domain corpus plus everything the runner needs to present it.

    Attributes:
        schema_name: GLiNER domain schema name — must be in
            :func:`neo4j_agent_memory.extraction.list_schemas`.
        title: Human title for the run header.
        blurb: One-sentence description of the corpus.
        threshold: Default GLiNER confidence threshold for this domain.
        documents: The corpus (kept verbatim from the pre-0.6 scripts).
        highlights: Summary sections to print.
        use_cases: Free-text "what you would do with this graph" block.
        demos: Optional demos this schema showcases by default.
        source_tag: Value stored as ``attributes["source"]`` in Neo4j.
        readback_query: Query used for the post-storage semantic read-back.
        per_type_limit: Entities printed per type, per document.
        note: Optional compliance/caveat note appended to the use cases.
    """

    schema_name: str
    title: str
    blurb: str
    threshold: float
    documents: tuple[Document, ...]
    highlights: tuple[Highlight, ...]
    use_cases: str
    source_tag: str
    readback_query: str
    demos: tuple[str, ...] = field(default_factory=tuple)
    per_type_limit: int = 8
    note: str = ""

    @property
    def texts(self) -> list[str]:
        """Document bodies, for batch extraction."""
        return [doc.content for doc in self.documents]

    @property
    def long_document(self) -> str:
        """All documents concatenated, for the streaming demo."""
        return "\n\n---\n\n".join(f"# {doc.title}\n{doc.content}" for doc in self.documents)
