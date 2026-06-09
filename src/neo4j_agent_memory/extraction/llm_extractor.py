"""LLM-based entity and preference extraction.

Provider-aware as of v0.3.0: accepts an injected
:class:`~neo4j_agent_memory.llm.protocol.LLMProvider` (or
:class:`~neo4j_agent_memory.llm.protocol.StructuredExtractor`) instead of
constructing an OpenAI client directly. When the provider also implements
:class:`StructuredExtractor`, the extractor uses
:meth:`StructuredExtractor.complete_structured` for the most reliable
output mode that provider supports — OpenAI strict mode, Anthropic forced
tool use, or schema-aligned retry as the safety net.

The legacy ``model=`` / ``api_key=`` constructor parameters are retained
for backward compatibility: when ``provider=`` is not supplied, a default
provider is constructed via :func:`~neo4j_agent_memory.llm.from_provider`
using the legacy parameters.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from neo4j_agent_memory.core.exceptions import ExtractionError
from neo4j_agent_memory.extraction._payloads import (
    ExtractionPayload,
    build_constrained_payload,
)
from neo4j_agent_memory.extraction.base import (
    EntityExtractor,
    ExtractedEntity,
    ExtractedPreference,
    ExtractedRelation,
    ExtractionResult,
)

if TYPE_CHECKING:
    from neo4j_agent_memory.llm.protocol import LLMProvider, StructuredExtractor
    from neo4j_agent_memory.schema.models import EntityTypeConfig, RelationTypeConfig


logger = logging.getLogger(__name__)


# POLE+O entity types as default
DEFAULT_ENTITY_TYPES = [
    "PERSON",
    "ORGANIZATION",
    "LOCATION",
    "EVENT",
    "OBJECT",
]

# Common subtypes for POLE+O model
POLEO_SUBTYPES: dict[str, list[str]] = {
    "PERSON": ["INDIVIDUAL", "ALIAS", "PERSONA"],
    "OBJECT": ["VEHICLE", "PHONE", "EMAIL", "DOCUMENT", "DEVICE", "WEAPON", "PRODUCT"],
    "LOCATION": ["ADDRESS", "CITY", "REGION", "COUNTRY", "LANDMARK", "FACILITY"],
    "EVENT": ["INCIDENT", "MEETING", "TRANSACTION", "COMMUNICATION", "DATE", "TIME"],
    "ORGANIZATION": ["COMPANY", "NONPROFIT", "GOVERNMENT", "EDUCATIONAL", "GROUP"],
}

# Built-in POLE+O type descriptions. Used both to render the default
# ``{type_guidelines}`` block (names-only path) and to backfill a typed-block
# line for a configured POLE+O type that the caller left undescribed, so a
# partially-described POLE+O schema does not regress its untouched core types.
POLEO_TYPE_DESCRIPTIONS: dict[str, str] = {
    "PERSON": "Individuals, people mentioned by name or role",
    "OBJECT": "Physical or digital items (vehicles, phones, documents, devices)",
    "LOCATION": "Places, addresses, geographic areas, landmarks",
    "EVENT": "Incidents, meetings, transactions, things that happened",
    "ORGANIZATION": "Companies, groups, institutions",
}

# The default POLE+O type-definition block. Rendered into ``{type_guidelines}``
# only on the names-only path; when a typed block carries descriptions we drop
# this so the configured descriptions are the single source of type guidance.
DEFAULT_POLEO_TYPE_GUIDELINES = "\n".join(
    f"- {name}: {desc}" for name, desc in POLEO_TYPE_DESCRIPTIONS.items()
)

# Generic relationship guidance, used when no relationship-type specs are
# configured. When specs ARE configured they replace this block (E9).
GENERIC_RELATION_GUIDANCE = (
    "- Identify how entities are connected\n"
    "- Use clear relationship types (WORKS_AT, LIVES_IN, OWNS, ATTENDED, KNOWS, etc.)"
)

# Default prompt optimized for POLE+O extraction. The structured-extraction
# path uses Pydantic schema validation instead, so this is the fallback
# for plain-LLM-call extraction (when the provider does not implement
# StructuredExtractor).
#
# Template slots:
#   {entity_types}     -- comma-joined names (back-compat) OR a typed block
#                         (name + description + examples per type)
#   {subtype_info}     -- optional subtype hints
#   {type_guidelines}  -- POLE+O type definitions on the names-only path; empty
#                         when a typed block already carries descriptions
#   {relation_guidance}-- generic guidance OR a configured relationship-type block
#   {text}             -- the document under analysis
# Relation/preference/confidence guidance is fixed template text and is present
# on every path so custom-type extraction never loses it.
DEFAULT_EXTRACTION_PROMPT = """Extract entities, relationships, and preferences from the following text.

## Entity Types
Extract entities of these types. Assign exactly one type per distinct entity; \
prefer the most specific type that applies.
{entity_types}
{subtype_info}{type_guidelines}

## Output Format
Return a JSON object with this structure:
{{
    "entities": [
        {{"name": "entity name", "type": "ENTITY_TYPE", "subtype": "SUBTYPE or null", "confidence": 0.9}}
    ],
    "relations": [
        {{"source": "entity1", "target": "entity2", "relation_type": "relationship type", "confidence": 0.8}}
    ],
    "preferences": [
        {{"category": "category", "preference": "the preference", "context": "when/where it applies", "confidence": 0.85}}
    ]
}}

## Relationships
{relation_guidance}
- Only include relations between entities in the entities list

## Preferences
- User preferences, likes, dislikes, opinions
- Categories: food, music, communication, style, technology, etc.

Confidence: 0.0-1.0 based on certainty of extraction

## Text to Analyze
{text}

Return only valid JSON, no other text."""

SUBTYPE_INFO_TEMPLATE = """
Subtypes (optional, use when you can determine a more specific type):
{subtype_list}
"""


@dataclass(frozen=True)
class _TypeSpec:
    """Normalized internal entity-type spec carried from config to prompt."""

    name: str
    description: str | None = None
    examples: tuple[str, ...] = ()
    subtypes: tuple[str, ...] = ()


@dataclass(frozen=True)
class _RelationSpec:
    """Normalized internal relationship-type spec (E9)."""

    name: str
    description: str | None = None
    source_types: tuple[str, ...] = ()
    target_types: tuple[str, ...] = ()


def _normalize_types(
    types: list[str] | list[EntityTypeConfig] | None,
) -> list[_TypeSpec]:
    """Normalize a name list or :class:`EntityTypeConfig` list to ``_TypeSpec``.

    Strings carry no description/examples; :class:`EntityTypeConfig` objects
    contribute their ``description``, ``examples``, and ``subtypes``. Empty
    input falls back to the default POLE+O type names.
    """
    if not types:
        return [_TypeSpec(name=n) for n in DEFAULT_ENTITY_TYPES]
    specs: list[_TypeSpec] = []
    for ty in types:
        if isinstance(ty, str):
            specs.append(_TypeSpec(name=ty.upper()))
        else:  # EntityTypeConfig (duck-typed to avoid an import cycle)
            specs.append(
                _TypeSpec(
                    name=ty.name.upper(),
                    description=ty.description,
                    examples=tuple(getattr(ty, "examples", []) or []),
                    subtypes=tuple(ty.subtypes or []),
                )
            )
    return specs


def _normalize_relations(
    relations: list[RelationTypeConfig] | None,
) -> list[_RelationSpec]:
    """Normalize a :class:`RelationTypeConfig` list to ``_RelationSpec``."""
    if not relations:
        return []
    specs: list[_RelationSpec] = []
    for rt in relations:
        specs.append(
            _RelationSpec(
                name=rt.name.upper(),
                description=rt.description,
                source_types=tuple(t.upper() for t in (rt.source_types or [])),
                target_types=tuple(t.upper() for t in (rt.target_types or [])),
            )
        )
    return specs


def _truncate(text: str, limit: int | None) -> str:
    """Truncate ``text`` to ``limit`` chars at a word boundary with an ellipsis.

    Reserves one character for the ellipsis and only backs off to the previous
    word boundary when the cut would split a word (i.e. the character at the cut
    point is not whitespace), so a clean boundary keeps its final word.
    """
    if not limit or len(text) <= limit:
        return text
    budget = max(0, limit - 1)
    cut = text[:budget]
    if budget < len(text) and not text[budget].isspace() and " " in cut:
        cut = cut[: cut.rfind(" ")]
    return f"{cut.rstrip()}…"


SYSTEM_MESSAGE = (
    "You are an expert at extracting structured information from text. "
    "You follow the configured entity-type schema. Always respond with valid JSON."
)


class LLMEntityExtractor(EntityExtractor):
    """LLM-based entity, relation, and preference extraction.

    Provider-aware. When given a :class:`StructuredExtractor` provider it
    uses ``complete_structured`` for native-quality structured outputs.
    When given a plain :class:`LLMProvider` (or no provider at all) it
    falls back to prompt-engineered JSON extraction.

    Example with explicit provider::

        from neo4j_agent_memory.llm.adapters.anthropic import AnthropicProvider

        provider = AnthropicProvider("anthropic/claude-3-5-sonnet-latest")
        extractor = LLMEntityExtractor(provider=provider)
        result = await extractor.extract("John works at Acme.")

    Example with legacy signature (constructs OpenAI provider internally)::

        extractor = LLMEntityExtractor(model="gpt-4o-mini", api_key="sk-...")
    """

    def __init__(
        self,
        provider: LLMProvider | StructuredExtractor | None = None,
        *,
        # Legacy parameters — used to construct a default provider when
        # ``provider`` is not supplied.
        model: str | None = None,
        api_key: str | None = None,
        # Configuration shared between provider modes
        entity_types: list[str] | list[EntityTypeConfig] | None = None,
        subtypes: dict[str, list[str]] | None = None,
        relation_types: list[RelationTypeConfig] | None = None,
        strict_types: bool = False,
        extraction_prompt: str | None = None,
        temperature: float = 0.0,
        extract_relations: bool = True,
        extract_preferences: bool = True,
        max_type_description_chars: int = 500,
        max_type_examples: int = 5,
        max_typed_block_chars: int = 4000,
    ) -> None:
        # Resolve the provider: explicit > legacy-args > default(gpt-4o-mini)
        if provider is None:
            resolved_model = model or "openai/gpt-4o-mini"
            try:
                from neo4j_agent_memory.llm import from_provider
            except ImportError as exc:
                raise ExtractionError(
                    "Could not import neo4j_agent_memory.llm — install a provider extra "
                    "(e.g. pip install 'neo4j-agent-memory[openai]')"
                ) from exc
            kwargs: dict[str, Any] = {}
            if api_key is not None:
                kwargs["api_key"] = api_key
            provider = from_provider(resolved_model, kind="llm", **kwargs)  # type: ignore[assignment]
        self._provider = provider
        self._model_label = getattr(provider, "model", "unknown")
        self._type_specs = _normalize_types(entity_types)
        self._entity_types = [s.name for s in self._type_specs]
        self._relation_specs = _normalize_relations(relation_types)
        self._strict_types = strict_types
        # Subtype resolution: an explicit ``subtypes`` map wins; otherwise
        # derive from each spec's own subtypes, backfilling the built-in
        # POLE+O subtypes for any configured type that matches a core POLE+O
        # name and declares none of its own. This keeps the default POLE+O
        # extractor identical to prior behaviour while letting custom types
        # carry (only) their declared subtypes.
        if subtypes is not None:
            self._subtypes = subtypes
        else:
            derived: dict[str, list[str]] = {}
            for s in self._type_specs:
                if s.subtypes:
                    derived[s.name] = list(s.subtypes)
                elif s.name in POLEO_SUBTYPES:
                    derived[s.name] = list(POLEO_SUBTYPES[s.name])
            self._subtypes = derived
        self._prompt = extraction_prompt or DEFAULT_EXTRACTION_PROMPT
        self._temperature = temperature
        self._extract_relations = extract_relations
        self._extract_preferences = extract_preferences
        self._max_type_description_chars = max_type_description_chars
        self._max_type_examples = max_type_examples
        self._max_typed_block_chars = max_typed_block_chars
        # Tracer is resolved lazily and memoized once per instance so we don't
        # re-run provider auto-detection (and emit warnings) on every extract.
        self._tracer: Any = None
        self._tracer_resolved = False

    def _get_tracer(self) -> Any:
        """Return a memoized tracer (NoOp by default; zero overhead)."""
        if not self._tracer_resolved:
            self._tracer_resolved = True
            try:
                from neo4j_agent_memory.observability import get_tracer

                self._tracer = get_tracer()
            except Exception:  # pragma: no cover - observability is optional
                self._tracer = None
        return self._tracer

    @property
    def name(self) -> str:
        """Extractor name for pipeline identification."""
        return "LLMEntityExtractor"

    def _build_subtype_info(self, types_to_use: list[str]) -> str:
        """Build subtype information string for the prompt."""
        subtype_lines = []
        for entity_type in types_to_use:
            subtypes = self._subtypes.get(entity_type, [])
            if subtypes:
                subtype_lines.append(f"- {entity_type}: {', '.join(subtypes)}")
        if subtype_lines:
            return SUBTYPE_INFO_TEMPLATE.format(subtype_list="\n".join(subtype_lines))
        return ""

    def _build_prompt(self, text: str, specs: list[_TypeSpec] | None = None) -> str:
        specs = specs if specs is not None else self._type_specs
        names = [s.name for s in specs]
        if any(s.description or s.examples for s in specs):
            entity_block = "\n" + self._render_typed_block(specs)
            # The typed block carries the type guidance, so drop the built-in
            # POLE+O definitions rather than double-specifying.
            type_guidelines = ""
        else:
            entity_block = ", ".join(names)
            type_guidelines = "\n\n" + DEFAULT_POLEO_TYPE_GUIDELINES
        return self._prompt.format(
            entity_types=entity_block,
            subtype_info=self._build_subtype_info(names),
            type_guidelines=type_guidelines,
            relation_guidance=self._render_relation_block(),
            text=text,
        )

    def _render_typed_block(self, specs: list[_TypeSpec]) -> str:
        """Render one line per type with description and examples.

        Undescribed POLE+O types backfill the built-in description. Applies
        the configured caps: per-type description truncation, per-type example
        count, and a global block-length cap. When the global cap is exceeded
        the renderer degrades deterministically — first dropping examples, then
        rendering the overflow types as a single name-only line — and logs what
        it dropped (never silently).
        """

        def render_entry(s: _TypeSpec, with_examples: bool) -> str:
            desc = s.description or POLEO_TYPE_DESCRIPTIONS.get(s.name)
            if desc:
                desc = _truncate(desc, self._max_type_description_chars)
            entry = f"- {s.name}" + (f": {desc}" if desc else "")
            if with_examples and s.examples:
                shown = list(s.examples)[: self._max_type_examples]
                entry += "\n  Examples: " + ", ".join(f'"{e}"' for e in shown)
            return entry

        cap = self._max_typed_block_chars

        entries = [render_entry(s, True) for s in specs]
        block = "\n".join(entries)
        if not cap or len(block) <= cap:
            return block

        logger.info(
            "Typed-entity block (%d chars) exceeded cap (%d); dropping per-type examples",
            len(block),
            cap,
        )
        entries = [render_entry(s, False) for s in specs]
        block = "\n".join(entries)
        if len(block) <= cap:
            return block

        kept: list[str] = []
        dropped: list[str] = []
        used = 0
        for s, entry in zip(specs, entries):
            if used + len(entry) + 1 <= cap:
                kept.append(entry)
                used += len(entry) + 1
            else:
                dropped.append(s.name)
        if dropped:
            logger.warning(
                "Typed-entity block still over cap (%d); rendering %d type(s) as name-only: %s",
                cap,
                len(dropped),
                ", ".join(dropped),
            )
            other_line = "- Other types: " + ", ".join(dropped)
            used = len("\n".join(kept))
            available = cap - used - (1 if kept else 0)
            if available > 0:
                if len(other_line) > available:
                    if available == 1:
                        other_line = "…"
                    else:
                        other_line = f"{other_line[: available - 1].rstrip()}…"
                if other_line and len(other_line) <= available:
                    kept.append(other_line)
        return "\n".join(kept)

    def _render_relation_block(self) -> str:
        """Render configured relationship types, or generic guidance (E9)."""
        if not self._relation_specs:
            return GENERIC_RELATION_GUIDANCE
        lines = ["Use only these relationship types:"]
        for r in self._relation_specs:
            arrow = ""
            if r.source_types and r.target_types:
                arrow = f" ({'/'.join(r.source_types)} → {'/'.join(r.target_types)})"
            line = f"- {r.name}{arrow}"
            if r.description:
                line += f": {r.description}"
            lines.append(line)
        return "\n".join(lines)

    async def extract(
        self,
        text: str,
        *,
        entity_types: list[str] | list[EntityTypeConfig] | None = None,
        extract_relations: bool | None = None,
        extract_preferences: bool | None = None,
    ) -> ExtractionResult:
        """Extract entities, relations, and preferences from text.

        Picks the right provider call based on capabilities:

        * If the provider implements :class:`StructuredExtractor`, uses
          ``complete_structured`` with the :class:`ExtractionPayload`
          schema. This is the high-quality path.
        * Otherwise falls back to prompt-engineered JSON via
          ``complete``, then parses the response loosely.

        A per-call ``entity_types`` override may be a name list or a list of
        :class:`EntityTypeConfig` (carrying descriptions/examples), matching
        the construction-time contract.
        """
        if not text or not text.strip():
            return ExtractionResult(source_text=text)

        specs = _normalize_types(entity_types) if entity_types is not None else self._type_specs
        include_relations = (
            extract_relations if extract_relations is not None else self._extract_relations
        )
        include_preferences = (
            extract_preferences if extract_preferences is not None else self._extract_preferences
        )

        # Lazy import to avoid circular dep at module load
        from neo4j_agent_memory.llm.protocol import StructuredExtractor

        async def _run() -> ExtractionResult:
            if isinstance(self._provider, StructuredExtractor):
                try:
                    return await self._extract_structured(
                        text, specs, include_relations, include_preferences
                    )
                except Exception as exc:  # pragma: no cover - defensive
                    logger.warning(
                        "Structured extraction failed (%s); falling back to plain LLM call",
                        type(exc).__name__,
                    )
            return await self._extract_with_complete(
                text, specs, include_relations, include_preferences
            )

        # Best-effort observability: NoOp tracer by default (zero overhead).
        tracer = self._get_tracer()
        if tracer is None:
            return await _run()

        async with tracer.async_span(
            "llm_extract", {"text_length": len(text), "requested_types": len(specs)}
        ) as span:
            result = await _run()
            try:
                span.set_attribute("entity_count", result.entity_count)
                for type_name, count in result.type_coverage.items():
                    span.set_attribute(f"type_coverage.{type_name}", count)
            except Exception:  # pragma: no cover - span is best-effort
                pass
            return result

    async def _extract_structured(
        self,
        text: str,
        specs: list[_TypeSpec],
        include_relations: bool,
        include_preferences: bool,
    ) -> ExtractionResult:
        """Run extraction via :meth:`StructuredExtractor.complete_structured`."""
        from neo4j_agent_memory.llm.types import ChatMessage

        prompt = self._build_prompt(text, specs)
        messages = [
            ChatMessage(role="system", content=SYSTEM_MESSAGE),
            ChatMessage(role="user", content=prompt),
        ]
        # When strict_types is enabled, constrain the structured-output schema
        # to the configured types so the provider (OpenAI strict mode /
        # Anthropic tool schema) — or, failing that, Pydantic validation on the
        # schema-aligned fallback — rejects invented types (E6). Otherwise use
        # the permissive free-string payload and rely on schema-aware mapping.
        payload_model: type[ExtractionPayload] = ExtractionPayload
        if self._strict_types:
            names = [s.name for s in specs]
            relation_names = (
                [r.name for r in self._relation_specs]
                if include_relations and self._relation_specs
                else None
            )
            payload_model = build_constrained_payload(names, relation_names)
        # ``complete_structured`` raises StructuredExtractionError on failure
        # which we let propagate so the pipeline can decide how to handle it.
        payload: ExtractionPayload = await self._provider.complete_structured(  # type: ignore[union-attr]
            messages,
            payload_model,
            temperature=self._temperature,
        )
        return self._payload_to_result(payload, text, specs, include_relations, include_preferences)

    async def _extract_with_complete(
        self,
        text: str,
        specs: list[_TypeSpec],
        include_relations: bool,
        include_preferences: bool,
    ) -> ExtractionResult:
        """Run extraction via plain :meth:`LLMProvider.complete`.

        Used when the provider does not implement
        :class:`StructuredExtractor`. Less reliable than the structured
        path but still works for any LLM.
        """
        from neo4j_agent_memory.llm.types import ChatMessage

        prompt = self._build_prompt(text, specs)
        messages = [
            ChatMessage(role="system", content=SYSTEM_MESSAGE),
            ChatMessage(role="user", content=prompt),
        ]
        try:
            completion = await self._provider.complete(  # type: ignore[union-attr]
                messages,
                temperature=self._temperature,
            )
        except Exception as exc:
            raise ExtractionError(f"Failed to extract entities: {exc}") from exc

        try:
            # Strip markdown fence if the model wrapped JSON in one
            content = completion.content.strip()
            if content.startswith("```"):
                # Remove the first line and trailing fence
                lines = content.splitlines()
                if lines and lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].rstrip("`").strip() == "":
                    lines = lines[:-1]
                content = "\n".join(lines)
            data = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ExtractionError(f"Failed to parse LLM response as JSON: {exc}") from exc

        # The raw dict from a non-structured call is shaped the same as
        # ExtractionPayload — validate to coerce strings and silently
        # drop extras. This still gives us the type-safety win.
        try:
            payload = ExtractionPayload.model_validate(data)
        except Exception as exc:
            raise ExtractionError(
                f"LLM response did not match expected extraction shape: {exc}"
            ) from exc

        return self._payload_to_result(payload, text, specs, include_relations, include_preferences)

    def _payload_to_result(
        self,
        payload: ExtractionPayload,
        source_text: str,
        specs: list[_TypeSpec],
        include_relations: bool,
        include_preferences: bool,
    ) -> ExtractionResult:
        """Convert an :class:`ExtractionPayload` to an :class:`ExtractionResult`."""
        allowed_types = [s.name for s in specs]
        allowed_set = set(allowed_types)
        # A "custom schema is active" when the configured types differ from the
        # default POLE+O set. Under a custom schema we keep off-list types
        # (flag-not-drop) rather than collapsing them to a POLE+O default that
        # the schema may not even contain.
        is_custom = allowed_set != set(DEFAULT_ENTITY_TYPES)

        entities: list[ExtractedEntity] = []
        for ent in payload.entities:
            entity_type = (ent.type or "OBJECT").upper()
            if entity_type not in allowed_set:
                entity_type = self._map_to_allowed_type(entity_type, allowed_types, is_custom)
            subtype = ent.subtype.upper() if ent.subtype else None
            if subtype:
                allowed_subtypes = self._subtypes.get(entity_type, [])
                if allowed_subtypes and subtype not in allowed_subtypes:
                    subtype = None
            entities.append(
                ExtractedEntity(
                    name=ent.name,
                    type=entity_type,
                    subtype=subtype,
                    confidence=ent.confidence,
                    extractor="llm",
                )
            )

        # Intra-call same-name guard (E5): collapse entities that share a
        # normalized name to a single node, keeping the highest-confidence
        # classification. First-seen order is preserved. Cross-call / cross-
        # document resolution remains the job of the deduplication stage.
        deduped: dict[str, ExtractedEntity] = {}
        order: list[str] = []
        for entity in entities:
            key = entity.name.strip().lower()
            existing = deduped.get(key)
            if existing is None:
                deduped[key] = entity
                order.append(key)
            elif entity.confidence > existing.confidence:
                deduped[key] = entity
        entities = [deduped[k] for k in order]

        relations: list[ExtractedRelation] = []
        if include_relations:
            entity_names_lower = {e.name.lower() for e in entities}
            for rel in payload.relations:
                if (
                    rel.source.lower() not in entity_names_lower
                    or rel.target.lower() not in entity_names_lower
                ):
                    continue
                relations.append(
                    ExtractedRelation(
                        source=rel.source,
                        target=rel.target,
                        relation_type=rel.relation_type.upper(),
                        confidence=rel.confidence,
                    )
                )

        preferences: list[ExtractedPreference] = []
        if include_preferences:
            for pref in payload.preferences:
                preferences.append(
                    ExtractedPreference(
                        category=pref.category,
                        preference=pref.preference,
                        context=pref.context,
                        confidence=pref.confidence,
                    )
                )

        # Per-type coverage (E10): requested types start at zero so a
        # requested-but-unextracted type is visible; off-list types kept under
        # a custom schema also appear.
        type_coverage: dict[str, int] = dict.fromkeys(allowed_types, 0)
        for entity in entities:
            type_coverage[entity.type] = type_coverage.get(entity.type, 0) + 1
        missing = [t for t in allowed_types if type_coverage.get(t, 0) == 0]

        logger.debug(
            "LLM extracted %d entities, %d relations, %d preferences; "
            "type_coverage=%s; requested-but-empty=%s",
            len(entities),
            len(relations),
            len(preferences),
            type_coverage,
            missing,
        )

        return ExtractionResult(
            entities=entities,
            relations=relations,
            preferences=preferences,
            source_text=source_text,
            type_coverage=type_coverage,
        )

    def _map_to_allowed_type(
        self, entity_type: str, allowed_types: list[str], is_custom: bool = False
    ) -> str:
        """Map an off-list entity type to an allowed type (schema-aware).

        Behaviour:

        * A type already in ``allowed_types`` is returned unchanged (callers
          only invoke this for off-list types, but this keeps the contract
          honest — e.g. PRODUCT is no longer coerced to OBJECT when PRODUCT is
          itself configured).
        * A POLE+O-style coercion is applied only when its target is in
          ``allowed_types``.
        * Under a custom schema, an unmappable type is kept as-is
          (flag-not-drop) instead of being collapsed to ``allowed_types[0]`` —
          which previously turned, e.g., an extracted date into the first
          custom type. Downstream resolution/validation can flag it.
        * Under the default POLE+O schema, legacy behaviour is preserved:
          fall back to the closest POLE+O default, else the first allowed type.
        """
        allowed_set = set(allowed_types)
        if entity_type in allowed_set:
            return entity_type

        type_mappings = {
            "CONCEPT": "OBJECT",
            "EMOTION": "OBJECT",
            "PRODUCT": "OBJECT",
            "THING": "OBJECT",
            "ITEM": "OBJECT",
            "FACT": "OBJECT",
            "PREFERENCE": "OBJECT",
            "PLACE": "LOCATION",
            "CITY": "LOCATION",
            "COUNTRY": "LOCATION",
            "ADDRESS": "LOCATION",
            "COMPANY": "ORGANIZATION",
            "ORG": "ORGANIZATION",
            "INDIVIDUAL": "PERSON",
            "HUMAN": "PERSON",
            "INCIDENT": "EVENT",
            "MEETING": "EVENT",
            "DATE": "EVENT",
            "TIME": "EVENT",
        }
        mapped = type_mappings.get(entity_type)
        if mapped and mapped in allowed_set:
            return mapped
        if is_custom:
            # Keep the model's type rather than inventing a POLE+O default that
            # isn't part of this schema.
            return entity_type
        fallback = mapped or "OBJECT"
        return (
            fallback
            if fallback in allowed_set
            else (allowed_types[0] if allowed_types else "OBJECT")
        )

    @classmethod
    def for_poleo(
        cls,
        provider: LLMProvider | StructuredExtractor | None = None,
        *,
        model: str = "openai/gpt-4o-mini",
        api_key: str | None = None,
    ) -> LLMEntityExtractor:
        """Create extractor configured for POLE+O model."""
        return cls(
            provider=provider,
            model=model,
            api_key=api_key,
            entity_types=list(DEFAULT_ENTITY_TYPES),
            subtypes=dict(POLEO_SUBTYPES),
        )

    @classmethod
    def for_custom_types(
        cls,
        entity_types: list[str] | list[EntityTypeConfig],
        provider: LLMProvider | StructuredExtractor | None = None,
        *,
        model: str = "openai/gpt-4o-mini",
        api_key: str | None = None,
        relation_types: list[RelationTypeConfig] | None = None,
        strict_types: bool = False,
    ) -> LLMEntityExtractor:
        """Create extractor for custom entity types.

        ``entity_types`` may be plain names or :class:`EntityTypeConfig`
        objects; when configs carry ``description``/``examples`` those reach
        the extraction prompt. Subtypes are derived from the configs (or the
        built-in POLE+O subtypes for any POLE+O-named type), not forced empty.
        """
        return cls(
            provider=provider,
            model=model,
            api_key=api_key,
            entity_types=entity_types,
            relation_types=relation_types,
            strict_types=strict_types,
        )


__all__ = [
    "LLMEntityExtractor",
    "DEFAULT_ENTITY_TYPES",
    "POLEO_SUBTYPES",
    "POLEO_TYPE_DESCRIPTIONS",
    "DEFAULT_EXTRACTION_PROMPT",
]
