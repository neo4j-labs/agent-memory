"""Entity and relation extraction with GLiNER2.5 (the ``gliner2`` package).

GLiNER2.5 decodes entities *and* relations in one pass through its JointIE
engine, and it enforces the ontology's endpoint types during beam search
rather than filtering afterwards: a ``WORKS_AT`` edge can only ever run
from a ``person`` to a ``company`` if that is what the ontology declared.
Relations therefore come back with entity ids, not names, and every
endpoint is guaranteed to be one of the entities in the same result.

The ontology is the input that matters. Pass an
:class:`~neo4j_agent_memory.ontology.models.OntologyDocument`, a
:class:`~neo4j_agent_memory.extraction.domain_schemas.DomainSchema`, an
``EntitySchemaConfig`` or nothing at all (POLE+O is the default)::

    from neo4j_agent_memory.extraction import GLiNER2Extractor

    extractor = GLiNER2Extractor.for_schema("podcast")
    result = await extractor.extract("Brian Chesky founded Airbnb.")

Two failure modes are quiet enough to be worth naming. Relation recall
collapses past roughly 400 words, so input longer than ``max_words`` is
windowed through ``extract_long``; and ``feasible=False`` means the
decoder could not satisfy the ontology's hard constraints, which is *not*
the same as "no facts in this text" — both raise a :class:`RuntimeWarning`.
The infeasibility warning only covers the single-pass path: gliner2's
``extract_long`` merges its chunk results into a fresh ``JointResult``
and does not carry any chunk's ``feasible`` flag through, so a windowed
extraction can never report it (see :meth:`GLiNER2Extractor._convert`).

``gliner2`` is imported through :func:`importlib.import_module` and kept
behind ``Any`` handles: the package ships ``py.typed`` but leaves its
methods unannotated, so a typed handle fails ``mypy --strict`` with "call
to untyped function". Importing at call time also keeps the module
importable — and stubbable in tests — without the extra installed.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import re
import threading
import warnings
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from neo4j_agent_memory.extraction.base import (
    ExtractedEntity,
    ExtractedRelation,
    ExtractionResult,
    normalize_relation_type,
)
from neo4j_agent_memory.extraction.label_mapping import map_label_to_poleo

if TYPE_CHECKING:
    from neo4j_agent_memory.extraction.domain_schemas import DomainSchema
    from neo4j_agent_memory.ontology.models import OntologyDocument
    from neo4j_agent_memory.schema.models import EntitySchemaConfig

logger = logging.getLogger(__name__)

#: The default GLiNER2.5 checkpoint (~407 MB on disk).
#:
#: ``fastino/gliner2.5-small-v1`` (~296 MB) is faster and
#: ``fastino/gliner2.5-multi-v1`` (~594 MB) is multilingual.
DEFAULT_GLINER2_5_MODEL = "fastino/gliner2.5-base-v1"

#: Namespaces of the GLiNER v1 checkpoints this extractor cannot load.
LEGACY_GLINER_PREFIXES = ("urchade/", "gliner-community/", "numind/")

#: Warnings the checkpoint emits on load and that nobody can act on: the
#: tokenizer's ``extra_special_tokens`` serialisation, and DeBERTa-v2/v3
#: having no SDPA kernel so attention falls back to eager.
_LOAD_WARNING_PATTERNS = (".*attn_implementation.*", ".*scaled_dot_product_attention.*")

#: The install hint every first-touch import path raises with.
_MISSING_GLINER2 = (
    "GLiNER2.5 is required for GLiNER2Extractor. Install with: "
    'pip install "neo4j-agent-memory[gliner2]"'
)

#: Mirror of gliner2's default ``WhitespaceTokenSplitter`` pattern
#: (``gliner2/processing/word_splitter.py``). Every punctuation mark is its own
#: token there, so ``str.split()`` undercounts by up to ~1.7x on prose and would
#: skip windowing on input the library considers long. Used to count words
#: before the checkpoint — and therefore its real splitter — has been loaded.
_WORD_PATTERN = re.compile(
    r"""(?:https?://[^\s]+|www\.[^\s]+)
        |[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}
        |@[a-z0-9_]+
        |\w+(?:[-_]\w+)*
        |\S""",
    re.VERBOSE | re.IGNORECASE,
)


def is_gliner2_available() -> bool:
    """Whether the ``gliner2`` package can be imported.

    Returns:
        True when GLiNER2.5 inference is available in this environment.
    """
    try:
        importlib.import_module("gliner2")
        importlib.import_module("gliner2.joint_ie")
    except ImportError:
        return False
    return True


class GLiNER2Extractor:
    """Joint entity and relation extraction with a GLiNER2.5 checkpoint.

    Implements the :class:`~neo4j_agent_memory.extraction.base.EntityExtractor`
    protocol. Preferences are not extracted (no model does that here); pair
    this with the LLM extractor when you need them.

    Example:
        ```python
        # The built-in POLE+O ontology
        extractor = GLiNER2Extractor()

        # A domain catalog (entity labels with descriptions)
        extractor = GLiNER2Extractor.for_schema("news")

        # Your own ontology, with typed relationships
        extractor = GLiNER2Extractor.for_ontology(doc, device="cuda")
        ```
    """

    #: Extractor name recorded on every entity it produces.
    name = "gliner2"

    def __init__(
        self,
        model: str = DEFAULT_GLINER2_5_MODEL,
        *,
        ontology: OntologyDocument | DomainSchema | EntitySchemaConfig | None = None,
        entity_labels: list[str] | dict[str, str] | None = None,
        threshold: float = 0.5,
        relation_threshold: float | None = None,
        device: str = "cpu",
        quantize: bool = False,
        compile: bool = False,
        max_words: int = 384,
        chunk_overlap: int = 64,
        overlap_policy: str | None = None,
        extract_relations: bool = True,
        extract_attributes: bool = False,
        attribute_tolerance: int = 2,
        context_window: int = 50,
        label_mapping: dict[str, tuple[str, str | None]] | None = None,
        joint_config: Any | None = None,
    ):
        """Initialise the extractor.

        Args:
            model: A GLiNER2.5 checkpoint id or local path.
            ontology: The schema to extract against — an ``OntologyDocument``,
                anything with a ``to_ontology()`` method (a ``DomainSchema``,
                an ``EntitySchemaConfig``), or ``None`` for POLE+O.
            entity_labels: Convenience alternative to ``ontology``: labels as a
                list, or as a ``{label: description}`` mapping. Ignored when
                ``ontology`` is given.
            threshold: Entity confidence floor passed to the decoder.
            relation_threshold: Confidence floor applied to decoded relations.
                ``None`` keeps whatever the ontology's per-relation thresholds
                selected.
            device: Inference device (``cpu`` / ``cuda`` / ``mps``).
            quantize: Load the weights in fp16.
            compile: Run the weights through ``torch.compile``.
            max_words: Longest input decoded in one pass; longer text is
                windowed. Relation recall degrades sharply past ~400 words.
            chunk_overlap: Word overlap between windows.
            overlap_policy: Span-overlap policy for the attribute pass
                (``flat`` / ``nested`` / ``allow`` / ``longest``); ``None``
                keeps the checkpoint's default.
            extract_relations: Set ``False`` to decode entities only.
            extract_attributes: Run the opt-in second pass that recovers the
                ontology's enum properties as span attributes.
            attribute_tolerance: Character slack when joining the attribute
                pass back onto the entity spans (the two passes tokenise
                independently).
            context_window: Characters of surrounding text captured per entity.
            label_mapping: Override for the label -> ``(TYPE, SUBTYPE)`` table.
            joint_config: A ready-made ``JointIEConfig``; when given it is used
                verbatim and ``threshold`` no longer applies.

        Raises:
            ValueError: If ``model`` names a GLiNER v1 checkpoint, or if the
                windowing arguments cannot produce a window
                (``max_words <= 0``, ``chunk_overlap < 0`` or
                ``chunk_overlap >= max_words``).
        """
        _reject_legacy_model(model)
        _validate_windowing(max_words, chunk_overlap)

        self._model_name = model
        self._model: Any | None = None
        self._joint: Any | None = None
        # One lock guards both lazy handles: four concurrent extract() calls
        # each land in a worker thread, and without it each would download the
        # checkpoint and build its own engine -- so self.model (used by the
        # attribute pass) could end up on a different weight copy than JointIE.
        self._load_lock = threading.Lock()
        self._word_splitter: Any | None = None
        self._word_splitter_resolved = False
        self._schema_cache: dict[tuple[str, frozenset[str] | None, bool], Any] = {}
        self._attribute_schema: Any | None = None
        self._attribute_schema_built = False
        self._label_table: dict[str, tuple[str, str | None]] | None = None

        self._ontology = _coerce_ontology(ontology, entity_labels)
        self.threshold = threshold
        self.relation_threshold = relation_threshold
        self.device = device
        self.quantize = quantize
        self.compile = compile
        self.max_words = max_words
        self.chunk_overlap = chunk_overlap
        self.overlap_policy = overlap_policy
        self.extract_relations = extract_relations
        self.extract_attributes = extract_attributes
        self.attribute_tolerance = attribute_tolerance
        self.context_window = context_window
        self.label_mapping = label_mapping
        self.joint_config = joint_config

    # -- identity -------------------------------------------------------------

    @property
    def model_id(self) -> str:
        """The checkpoint this extractor loads."""
        return self._model_name

    @property
    def version(self) -> str:
        """The installed ``gliner2`` version, or ``"unknown"``."""
        try:
            from importlib.metadata import version

            return version("gliner2")
        except Exception:  # pragma: no cover - metadata is best effort
            return "unknown"

    @property
    def ontology(self) -> OntologyDocument:
        """The ontology this extractor compiles into a JointIE schema."""
        return self._ontology

    # -- model handles --------------------------------------------------------

    @property
    def model(self) -> Any:
        """The loaded checkpoint (lazily downloaded on first use).

        Loading is guarded by a lock with double-checked locking, so the
        checkpoint is read exactly once however many threads race here.
        """
        if self._model is None:
            auto_extractor = _import_gliner2().AutoExtractor
            with self._load_lock:
                if self._model is None:
                    logger.info("Loading GLiNER2.5 model: %s", self._model_name)
                    try:
                        with warnings.catch_warnings():
                            warnings.simplefilter("ignore", UserWarning)
                            for pattern in _LOAD_WARNING_PATTERNS:
                                warnings.filterwarnings("ignore", message=pattern)
                            self._model = auto_extractor.from_pretrained(
                                self._model_name,
                                map_location=self.device,
                                quantize=self.quantize,
                                compile=self.compile,
                            )
                    except Exception as exc:
                        raise RuntimeError(
                            f"Could not load GLiNER2.5 model {self._model_name!r}: {exc}"
                        ) from exc
                    logger.info("GLiNER2.5 model loaded on %s", self.device)
        return self._model

    @property
    def joint(self) -> Any:
        """The JointIE engine, sharing weights with :attr:`model`.

        Built under the same lock as :attr:`model` (and after it, so the two
        critical sections never nest), which is what guarantees the attribute
        pass and JointIE decode against one and the same weight copy.
        """
        if self._joint is None:
            joint_ie = _import_joint_ie()
            model = self.model
            with self._load_lock:
                if self._joint is None:
                    self._joint = joint_ie.JointIEEngine(model, device=self.device)
        return self._joint

    # -- extraction -----------------------------------------------------------

    async def extract(
        self,
        text: str,
        *,
        entity_types: list[str] | None = None,
        extract_relations: bool = True,
        extract_preferences: bool = True,
    ) -> ExtractionResult:
        """Extract entities and relations from one text.

        Args:
            text: The text to extract from.
            entity_types: Restrict extraction to these ontology labels or
                POLE+O types (case-insensitive).
            extract_relations: Set ``False`` to skip relation decoding for this
                call. Relations are also skipped when the extractor was built
                with ``extract_relations=False`` or the ontology declares none.
            extract_preferences: Ignored — GLiNER2.5 extracts no preferences.

        Returns:
            The entities and relations found in ``text``.
        """
        if not text or not text.strip():
            return ExtractionResult(source_text=text)

        loop = asyncio.get_event_loop()
        entities, relations = await loop.run_in_executor(
            None, self._extract_sync, text, entity_types, extract_relations
        )

        logger.debug(
            "GLiNER2.5 extracted %d entities and %d relations", len(entities), len(relations)
        )
        return ExtractionResult(
            entities=entities,
            relations=relations,
            preferences=[],
            source_text=text,
        )

    async def extract_batch(
        self,
        texts: list[str],
        *,
        entity_types: list[str] | None = None,
        extract_relations: bool = True,
        batch_size: int = 8,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> list[ExtractionResult]:
        """Extract from several texts, using the engine's batched decoding.

        Texts longer than :attr:`max_words` are windowed individually; the
        rest go through ``batch_extract`` in one forward pass per slice.

        Args:
            texts: The texts to extract from, in order.
            entity_types: Restrict extraction to these labels or POLE+O types.
            extract_relations: Set ``False`` to skip relation decoding for this
                call, exactly as on :meth:`extract`.
            batch_size: Texts decoded per forward pass.
            on_progress: Called with ``(completed, total)`` after each slice.

        Returns:
            One :class:`ExtractionResult` per input text, in input order.
        """
        if not texts:
            return []

        loop = asyncio.get_event_loop()
        results: list[ExtractionResult] = []
        total = len(texts)
        completed = 0

        for start in range(0, total, batch_size):
            batch = texts[start : start + batch_size]
            decoded = await loop.run_in_executor(
                None, self._extract_batch_sync, batch, entity_types, extract_relations
            )
            for text, (entities, relations) in zip(batch, decoded):
                results.append(
                    ExtractionResult(
                        entities=entities,
                        relations=relations,
                        preferences=[],
                        source_text=text,
                    )
                )
            completed += len(batch)
            if on_progress:
                try:
                    on_progress(completed, total)
                except Exception as exc:
                    logger.warning("Progress callback error: %s", exc)

        return results

    # -- constructors ---------------------------------------------------------

    @classmethod
    def for_schema(cls, schema_name: str, **kwargs: Any) -> GLiNER2Extractor:
        """Build an extractor from a built-in domain template.

        Args:
            schema_name: One of
                :func:`neo4j_agent_memory.ontology.list_templates`
                (``poleo``, ``podcast``, ``news``, ``scientific``, ``business``,
                ``entertainment``, ``medical``, ``legal``).
            **kwargs: Forwarded to :meth:`__init__`.

        Returns:
            An extractor whose ontology is that template.
        """
        from neo4j_agent_memory.ontology.builtin import get_template

        return cls(ontology=get_template(schema_name), **kwargs)

    @classmethod
    def for_poleo(cls, **kwargs: Any) -> GLiNER2Extractor:
        """Build an extractor on the built-in POLE+O ontology.

        Args:
            **kwargs: Forwarded to :meth:`__init__`.

        Returns:
            An extractor using :data:`~neo4j_agent_memory.ontology.POLEO_ONTOLOGY`.
        """
        from neo4j_agent_memory.ontology.builtin import POLEO_ONTOLOGY

        return cls(ontology=POLEO_ONTOLOGY, **kwargs)

    @classmethod
    def for_ontology(cls, ontology: OntologyDocument, **kwargs: Any) -> GLiNER2Extractor:
        """Build an extractor on an explicit ontology document.

        Args:
            ontology: The ontology to compile.
            **kwargs: Forwarded to :meth:`__init__`.

        Returns:
            An extractor using ``ontology``.
        """
        return cls(ontology=ontology, **kwargs)

    # -- internals ------------------------------------------------------------

    def _extract_sync(
        self,
        text: str,
        entity_types: list[str] | None,
        extract_relations: bool,
    ) -> tuple[list[ExtractedEntity], list[ExtractedRelation]]:
        """Decode one text (blocking; runs in the default executor)."""
        labels = self._labels_for(entity_types)
        want_relations = self._wants_relations(extract_relations)
        schema = self._schema_for(labels, want_relations)
        config = self._config()

        if self._word_count(text) > self.max_words:
            result = self.joint.extract_long(
                text,
                schema,
                config=config,
                chunk_size=self.max_words,
                chunk_overlap=self.chunk_overlap,
            )
        else:
            result = self.joint.extract(text, schema, config=config)

        return self._convert(text, result, entity_types, want_relations)

    def _extract_batch_sync(
        self,
        texts: list[str],
        entity_types: list[str] | None,
        extract_relations: bool = True,
    ) -> list[tuple[list[ExtractedEntity], list[ExtractedRelation]]]:
        """Decode a slice of texts (blocking; runs in the default executor)."""
        labels = self._labels_for(entity_types)
        want_relations = self._wants_relations(extract_relations)
        schema = self._schema_for(labels, want_relations)
        config = self._config()

        short = [i for i, text in enumerate(texts) if self._word_count(text) <= self.max_words]
        raw: dict[int, Any] = {}

        if short:
            batch = [texts[i] for i in short]
            decoded = self.joint.batch_extract(batch, [schema] * len(batch), config=config)
            raw.update(zip(short, decoded))

        for index, text in enumerate(texts):
            if index in raw:
                continue
            raw[index] = self.joint.extract_long(
                text,
                schema,
                config=config,
                chunk_size=self.max_words,
                chunk_overlap=self.chunk_overlap,
            )

        return [
            self._convert(text, raw[index], entity_types, want_relations)
            for index, text in enumerate(texts)
        ]

    def _wants_relations(self, requested: bool) -> bool:
        """Whether relations should be decoded for this call."""
        return bool(requested and self.extract_relations and self._ontology.relationships)

    def _word_count(self, text: str) -> int:
        """Count words the way gliner2's chunker does.

        ``max_words`` is compared against the same token count
        ``split_text_into_chunks`` uses, which comes from the checkpoint's word
        splitter — every punctuation mark is a token of its own there, so
        ``str.split()`` undercounts and punctuation-heavy input would sail past
        the relation-recall cliff without being windowed. Before the model is
        loaded (and if the library's splitter cannot be reached) the equivalent
        regex in :data:`_WORD_PATTERN` stands in.
        """
        splitter = self._resolve_word_splitter()
        if splitter is not None:
            try:
                return sum(1 for _ in splitter(text, lower=False))
            except Exception as exc:  # pragma: no cover - defensive
                logger.debug("Falling back to the built-in word pattern: %s", exc)
        return sum(1 for _ in _WORD_PATTERN.finditer(text))

    def _resolve_word_splitter(self) -> Any | None:
        """The checkpoint's own word splitter, once it has been loaded."""
        if self._model is None:
            # Loading the checkpoint just to count words would defeat the point
            # of the lazy handles; the mirrored regex answers the same question.
            return None
        if not self._word_splitter_resolved:
            try:
                word_splitter = importlib.import_module("gliner2.processing.word_splitter")
                self._word_splitter = word_splitter.word_splitter_from(self._model)
            except Exception as exc:  # pragma: no cover - older/stubbed gliner2
                logger.debug("gliner2 word splitter unavailable: %s", exc)
                self._word_splitter = None
            self._word_splitter_resolved = True
        return self._word_splitter

    def _labels_for(self, entity_types: list[str] | None) -> list[str] | None:
        """The ontology labels to compile, or ``None`` for all of them."""
        if not entity_types:
            return None

        wanted = {value.lower() for value in entity_types}
        labels = [
            et.label
            for et in self._ontology.entity_types
            if et.label.lower() in wanted or et.pole_type.lower() in wanted
        ]
        # An unrecognised filter must not silently compile an empty schema.
        return labels or None

    def _schema_for(self, labels: list[str] | None, include_relations: bool) -> Any:
        """Compile (and cache) the JointIE schema for this label subset.

        Keyed on the ontology's *content*, not its name: two different
        subsets both called ``"poleo"`` are different schemas, and a
        name-keyed cache would hand back the wrong one.
        """
        from neo4j_agent_memory.ontology.compile import compile_joint_schema

        key = (
            self._ontology.content_key(),
            frozenset(label.lower() for label in labels) if labels else None,
            include_relations,
        )
        if key not in self._schema_cache:
            self._schema_cache[key] = compile_joint_schema(
                self._ontology,
                self.joint,
                labels=labels,
                include_relations=include_relations,
            )
        return self._schema_cache[key]

    def _config(self) -> Any:
        """The per-call ``JointIEConfig``."""
        if self.joint_config is not None:
            return self.joint_config
        return _import_joint_ie().JointIEConfig(entity_threshold=self.threshold)

    def _convert(
        self,
        text: str,
        result: Any,
        entity_types: list[str] | None,
        want_relations: bool,
    ) -> tuple[list[ExtractedEntity], list[ExtractedRelation]]:
        """Turn a ``JointResult`` into extracted entities and relations.

        The ``feasible=False`` warning below only covers the single-pass path.
        gliner2's ``extract_long`` merges its chunk results into a brand-new
        ``JointResult`` (``joint_ie/long_text.py``) and never copies a chunk's
        ``feasible`` flag across, so a windowed extraction always looks
        feasible here and the library exposes no per-chunk results to check
        instead.
        """
        if not getattr(result, "feasible", True):
            message = (
                "GLiNER2.5 could not satisfy the ontology's constraints and returned "
                "an empty assignment. This is NOT the same as 'no facts in this text' "
                "-- relax unique_source/acyclic constraints or lower the thresholds."
            )
            warnings.warn(message, RuntimeWarning, stacklevel=2)
            logger.warning(message)

        requested = {value.upper() for value in entity_types} if entity_types else None
        entities: list[ExtractedEntity] = []
        by_id: dict[str, ExtractedEntity] = {}

        for raw in result.entities:
            entity_type, subtype = self._map_label(raw.type)
            if requested is not None and not (
                entity_type in requested or raw.type.upper() in requested
            ):
                continue
            confidence = _confidence(raw.confidence)
            entity = ExtractedEntity(
                id=raw.id,
                name=raw.text.strip(),
                type=entity_type,
                subtype=subtype,
                start_pos=raw.start,
                end_pos=raw.end,
                confidence=confidence,
                context=self._context(text, raw.start, raw.end),
                extractor=self.name,
                attributes={
                    "gliner2_label": raw.type,
                    "gliner2_score": confidence,
                    "rescued": bool(getattr(raw, "rescued", False)),
                    "sentence_id": getattr(raw, "sentence_id", None),
                },
            )
            entities.append(entity)
            by_id[raw.id] = entity

        relations: list[ExtractedRelation] = []
        if want_relations:
            for raw_relation in result.relations:
                head = by_id.get(raw_relation.head)
                tail = by_id.get(raw_relation.tail)
                if head is None or tail is None:
                    # Defensive: JointIE guarantees endpoints are present, but a
                    # type filter above may have dropped one of them.
                    continue
                confidence = _confidence(raw_relation.confidence)
                if self.relation_threshold is not None and confidence < self.relation_threshold:
                    continue
                relations.append(
                    ExtractedRelation(
                        source=head.name,
                        target=tail.name,
                        source_id=raw_relation.head,
                        target_id=raw_relation.tail,
                        relation_type=normalize_relation_type(raw_relation.type),
                        confidence=confidence,
                        derived=bool(getattr(raw_relation, "derived", False)),
                    )
                )

        if self.extract_attributes and entities:
            self._attach_attributes(text, entities)

        return entities, relations

    def _map_label(self, label: str) -> tuple[str, str | None]:
        """Map a JointIE label onto its POLE+O ``(type, subtype)`` pair.

        The ontology's own labels win; the shared default table sits
        underneath so a label the ontology does not declare still lands
        somewhere sensible.
        """
        if self._label_table is None:
            from neo4j_agent_memory.ontology.compile import label_map

            self._label_table = (
                dict(self.label_mapping)
                if self.label_mapping is not None
                else label_map(self._ontology)
            )
        return map_label_to_poleo(label, self._label_table)

    def _context(self, text: str, start: int, end: int) -> str:
        """The surrounding text captured for an entity span."""
        return text[max(0, start - self.context_window) : min(len(text), end + self.context_window)]

    def _attach_attributes(self, text: str, entities: list[ExtractedEntity]) -> None:
        """Run the opt-in attribute pass and join it onto the entity spans.

        The two passes tokenise independently, so spans are matched by label
        and start offset within :attr:`attribute_tolerance` characters.
        """
        schema = self._attribute_schema_for()
        if schema is None:
            return

        raw = self.model.extract(
            text,
            schema,
            threshold=self.threshold,
            include_spans=True,
            include_confidence=True,
            overlap_policy=self.overlap_policy,
        )
        spans = _index_attribute_spans(raw)
        if not spans:
            return

        for entity in entities:
            label = str(entity.attributes.get("gliner2_label", ""))
            start = entity.start_pos
            if start is None:
                continue
            found = _nearest_attribute_span(spans, label, start, self.attribute_tolerance)
            if found:
                entity.attributes.update(found)

    def _attribute_schema_for(self) -> Any:
        """Compile (and cache) the attribute schema, or ``None`` when empty."""
        if not self._attribute_schema_built:
            from neo4j_agent_memory.ontology.compile import compile_attribute_schema

            self._attribute_schema = compile_attribute_schema(self._ontology, self.model)
            self._attribute_schema_built = True
        return self._attribute_schema


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------


def _import_gliner2() -> Any:
    """Import ``gliner2``, or raise the install hint."""
    try:
        return importlib.import_module("gliner2")
    except ImportError as exc:
        raise ImportError(_MISSING_GLINER2) from exc


def _import_joint_ie() -> Any:
    """Import ``gliner2.joint_ie``, or raise the install hint.

    The constructor imports nothing, so this (and :func:`_import_gliner2`) is
    where a missing extra is first noticed: without the translation a caller
    would get a bare ``ModuleNotFoundError`` from deep inside the first
    ``extract()``.
    """
    try:
        return importlib.import_module("gliner2.joint_ie")
    except ImportError as exc:
        raise ImportError(_MISSING_GLINER2) from exc


def _validate_windowing(max_words: int, chunk_overlap: int) -> None:
    """Reject windowing arguments gliner2's chunker would reject later.

    ``split_text_into_chunks`` raises on these, but only once the first long
    text reaches it — by then the failure surfaces as an opaque pipeline stage
    error, several calls away from the constructor that caused it.
    """
    if max_words <= 0:
        raise ValueError(f"max_words must be greater than 0, got {max_words!r}")
    if chunk_overlap < 0:
        raise ValueError(f"chunk_overlap must be non-negative, got {chunk_overlap!r}")
    if chunk_overlap >= max_words:
        raise ValueError(
            f"chunk_overlap must be smaller than max_words, got "
            f"chunk_overlap={chunk_overlap!r} and max_words={max_words!r} "
            f"(windows would never advance)"
        )


def _reject_legacy_model(model: str) -> None:
    """Fail before any download when ``model`` is a GLiNER v1 checkpoint."""
    if model.startswith(LEGACY_GLINER_PREFIXES):
        raise ValueError(
            f"{model!r} is a GLiNER v1 checkpoint and cannot be loaded by GLiNER2Extractor "
            f"(the 2.5 loader rejects the architecture). Use a GLiNER2.5 checkpoint such as "
            f"{DEFAULT_GLINER2_5_MODEL!r}, 'fastino/gliner2.5-small-v1' or "
            f"'fastino/gliner2.5-multi-v1'."
        )


def _coerce_ontology(
    ontology: OntologyDocument | DomainSchema | EntitySchemaConfig | None,
    entity_labels: list[str] | dict[str, str] | None,
) -> OntologyDocument:
    """Resolve the constructor's schema arguments into one ontology document."""
    from neo4j_agent_memory.ontology.builtin import POLEO_ONTOLOGY
    from neo4j_agent_memory.ontology.models import OntologyDocument

    if isinstance(ontology, OntologyDocument):
        return ontology
    if ontology is not None:
        to_ontology = getattr(ontology, "to_ontology", None)
        if to_ontology is None:
            raise TypeError(
                f"ontology must be an OntologyDocument or convertible to one, "
                f"got {type(ontology).__name__}"
            )
        converted = to_ontology()
        if not isinstance(converted, OntologyDocument):  # pragma: no cover - defensive
            raise TypeError(f"{type(ontology).__name__}.to_ontology() returned a non-ontology")
        return converted
    if entity_labels:
        return _ontology_from_labels(entity_labels)
    return POLEO_ONTOLOGY


def _ontology_from_labels(entity_labels: list[str] | dict[str, str]) -> OntologyDocument:
    """Build an ad-hoc, relationship-free ontology from bare labels."""
    from neo4j_agent_memory.ontology.models import DomainInfo, EntityTypeDef, OntologyDocument

    descriptions: dict[str, str | None]
    if isinstance(entity_labels, dict):
        descriptions = dict(entity_labels)
    else:
        descriptions = dict.fromkeys(entity_labels)

    entity_types = []
    for label, description in descriptions.items():
        pole_type, subtype = map_label_to_poleo(label)
        entity_types.append(
            EntityTypeDef(
                label=label,
                pole_type=pole_type,
                subtype=subtype,
                description=description,
            )
        )

    return OntologyDocument(
        domain=DomainInfo(id="ad-hoc", name="ad-hoc"),
        entity_types=entity_types,
    )


def _confidence(value: float | None) -> float:
    """A decoded confidence as a probability (``None`` means "certain")."""
    if value is None:
        return 1.0
    return min(1.0, max(0.0, float(value)))


def _index_attribute_spans(raw: Any) -> dict[str, list[dict[str, Any]]]:
    """Index an attribute-pass result by entity label."""
    if not isinstance(raw, dict):
        return {}
    entities = raw.get("entities")
    if not isinstance(entities, dict):
        return {}
    return {
        str(label): [span for span in spans if isinstance(span, dict)]
        for label, spans in entities.items()
        if isinstance(spans, list)
    }


def _nearest_attribute_span(
    spans: dict[str, list[dict[str, Any]]],
    label: str,
    start: int,
    tolerance: int,
) -> dict[str, Any]:
    """The attribute values decoded for ``(label, start)``, if any.

    The *nearest* span within ``tolerance`` wins, not merely the first one in
    decode order: two spans of the same label can both be in range (adjacent
    mentions, or a tolerance wide enough to cover both), and the closer offset
    is the one that belongs to this entity.

    Only the group values are returned — ``text``/``confidence``/``start``/
    ``end`` belong to the span, not to the entity.
    """
    reserved = {"text", "confidence", "start", "end"}
    best: dict[str, Any] | None = None
    best_distance = tolerance + 1
    for span in spans.get(label, ()):
        span_start = span.get("start")
        if span_start is None:
            continue
        distance = abs(int(span_start) - start)
        if distance > tolerance or distance >= best_distance:
            continue
        best_distance = distance
        best = {key: value for key, value in span.items() if key not in reserved}
    return best or {}


__all__ = [
    "DEFAULT_GLINER2_5_MODEL",
    "LEGACY_GLINER_PREFIXES",
    "GLiNER2Extractor",
    "is_gliner2_available",
]
