"""NAMS implementation of :class:`LongTermProtocol`.

Endpoint mappings verified against the live NAMS OpenAPI spec.

NAMS provides first-class **Entity** and **relationship** endpoints.
Preferences and facts are NOT exposed as dedicated REST endpoints —
those features must go through the Cypher console
(``client.query.cypher``) or are out of scope on NAMS entirely.

Methods that raise :class:`NotSupportedError`:

* ``add_preference``, ``search_preferences``, ``get_preferences_for``,
  ``supersede_preference``
* ``add_fact``, ``search_facts``, ``get_facts_about``
* ``get_related_entities`` with ``depth > 1`` (the REST API is 1-hop)

NAMS-specific endpoint shapes vs. our Protocol:

* Entity create body is ``{name, type, description?}`` — no subtype,
  aliases, attributes, confidence (those are bolt-only).
* Entity search returns ``{"entities": [...], "searchType": ...}``
  envelope.
* ``POST /v1/relationships`` validates the relationship type against the
  workspace's allowed-relations vocabulary — unknown types collapse to
  ``RELATED_TO`` with the caller's name preserved as ``predicate``
  (ADR-0016; mirrors the MCP ``memory_create_relation`` tool).
* ``GET /v1/entities/by-name`` is resolver-normalized (case/punctuation/
  corporate-suffix-insensitive + aliases) and returns an ordered
  ``{"entities": [...]}`` list, best match first — 200 with an empty
  list when nothing matches, never 404.
* Entity feedback is **PUT** not POST, body
  ``{userScore?, confirmed?}`` (no free-form ``feedback`` string).
* Entity provenance lives under ``/v1/reasoning/provenance/{entityId}``
  (not ``/v1/entities/{id}/provenance``).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any
from uuid import UUID

from neo4j_agent_memory.core.exceptions import NotSupportedError
from neo4j_agent_memory.memory.long_term import (
    Entity,
    Fact,
    Preference,
    Relationship,
)
from neo4j_agent_memory.nams._serialization import payload_to_model, snakeize_keys
from neo4j_agent_memory.nams.endpoints import EndpointSpec
from neo4j_agent_memory.nams.short_term import (
    _NONTERMINAL_EXTRACTION_STATUSES,
    _SPEC_EXTRACTION_STATUS,
)

if TYPE_CHECKING:
    from neo4j_agent_memory.nams.transport import HttpTransport


# -----------------------------------------------------------------------------
# Endpoint registry — verified against live NAMS OpenAPI spec.
# -----------------------------------------------------------------------------

_SPEC_LIST_ENTITIES = EndpointSpec(
    rest_method="GET", rest_path="/entities", bridge_method="list_entities"
)
_SPEC_ADD_ENTITY = EndpointSpec(
    rest_method="POST", rest_path="/entities", bridge_method="add_entity"
)
_SPEC_GET_ENTITY = EndpointSpec(
    rest_method="GET", rest_path="/entities/{entity_id}", bridge_method="get_entity"
)
_SPEC_UPDATE_ENTITY = EndpointSpec(
    rest_method="PUT", rest_path="/entities/{entity_id}", bridge_method="update_entity"
)
_SPEC_DELETE_ENTITY = EndpointSpec(
    rest_method="DELETE", rest_path="/entities/{entity_id}", bridge_method="delete_entity"
)
_SPEC_SET_ENTITY_FEEDBACK = EndpointSpec(
    rest_method="PUT",  # NAMS uses PUT for feedback
    rest_path="/entities/{entity_id}/feedback",
    bridge_method="set_entity_feedback",
)
_SPEC_GET_ENTITY_HISTORY = EndpointSpec(
    rest_method="GET",
    rest_path="/entities/{entity_id}/history",
    bridge_method="get_entity_history",
)
_SPEC_MERGE_ENTITIES = EndpointSpec(
    rest_method="POST",
    rest_path="/entities/{entity_id}/merge",
    bridge_method="merge_entities",
)
_SPEC_ENTITY_GRAPH = EndpointSpec(
    rest_method="GET", rest_path="/entities/graph", bridge_method="entity_graph"
)
_SPEC_EXPAND_GRAPH = EndpointSpec(
    rest_method="POST", rest_path="/graph/expand", bridge_method="expand_graph"
)
_SPEC_SEARCH_ENTITIES = EndpointSpec(
    rest_method="POST", rest_path="/entities/search", bridge_method="search_entities"
)
_SPEC_GET_ENTITY_BY_NAME = EndpointSpec(
    rest_method="GET", rest_path="/entities/by-name", bridge_method="get_entity_by_name"
)
_SPEC_ADD_RELATIONSHIP = EndpointSpec(
    rest_method="POST", rest_path="/relationships", bridge_method="add_relationship"
)

# Entity provenance is under the reasoning namespace per verified spec.
_SPEC_GET_ENTITY_PROVENANCE = EndpointSpec(
    rest_method="GET",
    rest_path="/reasoning/provenance/{entity_id}",
    bridge_method="get_entity_provenance",
)


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------


# NAMS accepts only this set of entity types (lowercase) per the live spec:
#   person, organization, location, concept, tool, custom
# Our package uses uppercase POLE+O types: PERSON, ORGANIZATION, LOCATION,
# OBJECT, EVENT. We map POLE+O → NAMS for outbound writes/searches and
# uppercase NAMS types on the way back for round-trip consistency.
_NAMS_TYPES = {"person", "organization", "location", "concept", "tool", "custom"}
_POLEO_TO_NAMS = {
    "PERSON": "person",
    "ORGANIZATION": "organization",
    "LOCATION": "location",
    # OBJECT / EVENT have no first-class NAMS analog — fall through to custom.
    "OBJECT": "custom",
    "EVENT": "custom",
    "CONCEPT": "concept",
    "TOOL": "tool",
    "CUSTOM": "custom",
}


def _to_nams_type(entity_type: str | None) -> str | None:
    """Map a package entity type to a NAMS-accepted lowercase value.

    Strips off any subtype suffix (``PERSON:INDIVIDUAL`` → ``PERSON``),
    uppercases for lookup, and falls back to ``custom`` for unknown
    types. ``None`` passes through.
    """
    if entity_type is None:
        return None
    base = entity_type.split(":", 1)[0].strip()
    if not base:
        return "custom"
    upper = base.upper()
    if upper in _POLEO_TO_NAMS:
        return _POLEO_TO_NAMS[upper]
    lower = base.lower()
    if lower in _NAMS_TYPES:
        return lower
    return "custom"


def _drop_none(d: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in d.items() if v is not None}


def _to_str(value: UUID | str) -> str:
    return str(value)


def _normalize_entity(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Map NAMS Entity response → bolt Pydantic shape.

    NAMS Entity fields: ``id, name, type, description, confidence,
    sourceStage, createdAt, updatedAt`` (camelCase). The bolt Entity
    model adds ``aliases``, ``attributes``, ``subtype`` which NAMS
    doesn't provide — we default them so Pydantic parsing succeeds.
    NAMS types come back lowercase; uppercase them so package-side
    consumers see the same type values they sent.
    """
    from datetime import datetime, timezone

    data = snakeize_keys(payload) if isinstance(payload, dict) else {}
    if "created_at" not in data:
        data["created_at"] = datetime.now(timezone.utc).isoformat()
    if "metadata" not in data:
        data["metadata"] = {}
    if "aliases" not in data:
        data["aliases"] = []
    if "attributes" not in data:
        data["attributes"] = {}
    if isinstance(data.get("type"), str):
        data["type"] = data["type"].upper()
    return data


# -----------------------------------------------------------------------------
# NamsLongTermMemory
# -----------------------------------------------------------------------------


class NamsLongTermMemory:
    """Long-term memory backed by the NAMS HTTP service.

    Provides entity and relationship operations (NAMS exposes no
    first-class preference / fact endpoints). Preference / fact Protocol
    methods raise :class:`NotSupportedError`.
    """

    def __init__(self, transport: HttpTransport) -> None:
        self._transport = transport

    # ------------------------------------------------------------------ Bronze

    async def add_entity(
        self,
        name: str,
        entity_type: str | None = None,
        **kwargs: Any,
    ) -> Entity:
        """Create an entity on NAMS.

        ``entity_type`` is canonical; ``type`` and ``label`` are accepted
        as aliases (``entity_type`` wins). NAMS accepts only
        ``{name, type, description?}`` per spec. Bolt-only kwargs
        (``subtype``, ``aliases``, ``attributes``, ``confidence``,
        ``deduplicate``, ``geocode``, ``enrich``, etc.) are silently dropped.
        """
        et = entity_type or kwargs.get("type") or kwargs.get("label")
        if et is None:
            raise TypeError("add_entity requires entity_type (aliases: type, label).")
        body = _drop_none(
            {
                "name": name,
                "type": _to_nams_type(et),
                "description": kwargs.get("description"),
            }
        )
        payload = await self._transport.request(_SPEC_ADD_ENTITY, json=body)
        return payload_to_model(_normalize_entity(payload), Entity)

    async def add_preference(self, category: str, preference: str, **kwargs: Any) -> Preference:
        raise NotSupportedError(
            backend="nams",
            method="LongTermMemory.add_preference",
            message="NAMS does not expose a preferences endpoint.",
            workaround=(
                "Store preferences via client.query.cypher with an explicit "
                "MERGE (:Preference {category, value}) — but note NAMS is "
                "read-only for Cypher. For full preference support, use bolt."
            ),
        )

    async def add_fact(self, subject: str, predicate: str, object: str, **kwargs: Any) -> Fact:  # noqa: A002
        raise NotSupportedError(
            backend="nams",
            method="LongTermMemory.add_fact",
            message="NAMS does not expose a facts endpoint.",
            workaround="For full facts support, use the bolt backend.",
        )

    async def add_relationship(
        self,
        source_id: UUID | str,
        relationship_type: str,
        target_id: UUID | str,
        **kwargs: Any,
    ) -> Relationship:
        """Create a typed relationship between two entities.

        ``POST /v1/relationships``. The type is validated against the
        workspace's allowed-relations vocabulary server-side: an unknown
        type **collapses** to ``RELATED_TO`` (the caller's original name is
        preserved on the edge as ``predicate``), so the returned
        :class:`Relationship` carries the type that was actually written.
        Re-asserting an existing ``(source, type, target)`` edge is
        idempotent (confidence is averaged server-side).

        Supported kwargs: ``confidence`` (0..1] and ``properties`` (alias
        ``attributes``) — extra edge properties with scalar or scalar-list
        values; the keys ``id``/``confidence``/``method``/``predicate``/
        ``firstSeenAt``/``lastSeenAt`` are reserved and rejected with
        :class:`ValidationError`. Bolt-only kwargs (``description``,
        ``valid_from``, ``valid_until``) are silently dropped.
        """
        properties = kwargs.get("properties")
        if properties is None:
            properties = kwargs.get("attributes")
        body = _drop_none(
            {
                "sourceId": _to_str(source_id),
                "targetId": _to_str(target_id),
                "relationshipType": relationship_type,
                "confidence": kwargs.get("confidence"),
                "properties": properties,
            }
        )
        payload = await self._transport.request(_SPEC_ADD_RELATIONSHIP, json=body)
        data = snakeize_keys(payload) if isinstance(payload, dict) else {}
        normalized = _drop_none(
            {
                "id": data.get("id"),
                "source_id": data.get("source_id") or _to_str(source_id),
                "target_id": data.get("target_id") or _to_str(target_id),
                # The WRITTEN type — RELATED_TO when the server collapsed it.
                "type": data.get("relationship_type") or relationship_type,
                "confidence": data.get("confidence"),
                "attributes": dict(properties or {}),
            }
        )
        return payload_to_model(normalized, Relationship)

    async def search_entities(self, query: str, **kwargs: Any) -> list[Entity]:
        """Vector/keyword search across entities.

        NAMS response: ``{"entities": [...], "searchType": "vector"|"text"}``.
        """
        body = _drop_none(
            {
                "query": query,
                "type": _to_nams_type(
                    kwargs.get("entity_type") or kwargs.get("type") or kwargs.get("label")
                ),
                "limit": kwargs.get("limit"),
            }
        )
        payload = await self._transport.request(_SPEC_SEARCH_ENTITIES, json=body)
        items: list[Any]
        if isinstance(payload, dict) and "entities" in payload:
            items = payload["entities"]
        elif isinstance(payload, list):
            items = payload
        else:
            items = []
        return [payload_to_model(_normalize_entity(item), Entity) for item in items]

    async def wait_for_extraction(
        self,
        *,
        query: str | None = None,
        expected_names: list[str] | None = None,
        min_results: int = 1,
        predicate: Callable[[list[Entity]], bool] | None = None,
        timeout: float = 30.0,
        interval: float = 1.0,
        session_id: str | None = None,
        **kwargs: Any,
    ) -> bool:
        """Await NAMS' asynchronous extraction pipeline catching up, or time out.

        NAMS extracts entities in a background pipeline, so writes return
        before the entities are searchable. This helper lets application and
        test code await consistency explicitly instead of racing a fixed sleep.

        Two readiness signals, used in order:

        1. **Authoritative (conversation-scoped).** When ``session_id`` (alias
           ``conversation_id``) is given, poll the conversation's
           ``/extraction-status`` until no message is still pending — the real
           pipeline signal. If no entity-level assertion is also requested, a
           completed status returns ``True``.
        2. **Entity-level confirmation (workspace-scoped search).** When
           ``predicate`` / ``expected_names`` / ``query`` is given, additionally
           confirm those entities are searchable:

           * ``predicate`` — called with the current search results; return
             ``True`` when satisfied.
           * ``expected_names`` — succeed once every name appears (case-insensitive).
           * otherwise — succeed once at least ``min_results`` entities match.
             (NAMS entity search is nearest-neighbor and returns top-k existing
             entities regardless of relevance, so ``min_results=1`` is satisfied
             almost immediately on a non-empty workspace — prefer
             ``expected_names``/``predicate`` to confirm a *specific* extraction.)

        Returns ``True`` if satisfied within ``timeout`` seconds, ``False``
        otherwise (it does **not** raise, so callers can branch or skip).
        """
        conv = session_id if session_id is not None else kwargs.get("conversation_id")
        q = query if query is not None else (expected_names[0] if expected_names else None)
        if conv is None and q is None and predicate is None:
            raise ValueError(
                "wait_for_extraction requires one of: session_id (alias "
                "conversation_id), query, expected_names, or predicate."
            )
        deadline = time.monotonic() + timeout

        # 1. Authoritative per-conversation status when a conversation is known.
        if conv is not None:
            while True:
                if await self._extraction_complete(conv):
                    break
                if time.monotonic() >= deadline:
                    return False
                await asyncio.sleep(interval)
            # Conversation-only wait: completion is the answer.
            if predicate is None and not expected_names and query is None:
                return True

        # 2. Entity-level confirmation via search (also the bolt-free fallback
        #    when no conversation id was supplied).
        if q is None and predicate is None:
            return True
        want = [n.lower() for n in (expected_names or [])]
        fetch = max(min_results, len(want), kwargs.get("limit") or 10)
        while True:
            results = await self.search_entities(query=q or "", limit=fetch)
            if predicate is not None:
                ok = predicate(results)
            elif want:
                found = {e.name.lower() for e in results}
                ok = all(name in found for name in want)
            else:
                ok = len(results) >= min_results
            if ok:
                return True
            if time.monotonic() >= deadline:
                return False
            await asyncio.sleep(interval)

    async def _extraction_complete(self, conversation_id: str) -> bool:
        """Return ``True`` when the conversation has no pending extraction."""
        payload = await self._transport.request(
            _SPEC_EXTRACTION_STATUS, path_params={"conversation_id": conversation_id}
        )
        summary = (payload or {}).get("summary") if isinstance(payload, dict) else None
        summary = summary or {}
        return not any(int(summary.get(s, 0)) > 0 for s in _NONTERMINAL_EXTRACTION_STATUSES)

    async def expand_graph(
        self, node_id: str, *, loaded_ids: list[str] | None = None
    ) -> dict[str, list[dict[str, Any]]]:
        """Expand one entity's 1-hop neighborhood for graph visualization.

        Returns ``{"nodes": [...], "edges": [...]}`` — the canonical-resolved
        neighbors of ``node_id``, excluding any ids in ``loaded_ids`` (pass the
        ids already on screen to fetch only the delta).
        """
        payload = await self._transport.request(
            _SPEC_EXPAND_GRAPH,
            json={"nodeId": node_id, "loadedIds": loaded_ids or []},
        )
        payload = payload if isinstance(payload, dict) else {}
        return {
            "nodes": list(payload.get("nodes") or []),
            "edges": list(payload.get("edges") or []),
        }

    async def search_preferences(self, query: str, **kwargs: Any) -> list[Preference]:
        raise NotSupportedError(
            backend="nams",
            method="LongTermMemory.search_preferences",
            message="NAMS does not expose a preferences endpoint.",
        )

    async def search_facts(self, query: str, **kwargs: Any) -> list[Fact]:
        raise NotSupportedError(
            backend="nams",
            method="LongTermMemory.search_facts",
            message="NAMS does not expose a facts endpoint.",
        )

    async def get_entity_by_name(self, name: str) -> Entity | None:
        """Look up an entity by name via ``GET /v1/entities/by-name``.

        Matching is resolver-normalized — case/punctuation/corporate-suffix
        insensitive, and aliases count — so this answers "would a create
        dedupe onto something?" the same way the write path would. The
        response is a deterministically ordered ``{"entities": [...]}``
        list with the best match first; returns that first match, or
        ``None`` when the list is empty (the endpoint returns 200 with an
        empty list, never 404).
        """
        payload = await self._transport.request(_SPEC_GET_ENTITY_BY_NAME, params={"name": name})
        first: Any = None
        if isinstance(payload, dict) and "entities" in payload:
            items = payload.get("entities") or []
            first = items[0] if items else None
        elif isinstance(payload, list):
            first = payload[0] if payload else None
        elif payload:
            # Bridge servers may return the entity object directly.
            first = payload
        if not isinstance(first, dict):
            return None
        return payload_to_model(_normalize_entity(first), Entity)

    # ------------------------------------------------------------------ Silver

    async def get_related_entities(self, entity_id: UUID | str, **kwargs: Any) -> list[Entity]:
        """Return entities related to ``entity_id`` (1 hop, both directions).

        NAMS exposes inline relationships on ``GET /entities/{id}`` — we
        fetch the entity and map each relationship's target
        (``{relType, targetId, targetName, targetType}``) to an
        :class:`Entity`. Optionally filter with ``relationship_type=``
        (single type) or ``relationship_types=`` (list, bolt-style alias).

        ``depth`` beyond 1 is not representable on the NAMS REST API and
        raises :class:`NotSupportedError`.
        """
        depth = kwargs.get("depth")
        if depth is not None and int(depth) > 1:
            raise NotSupportedError(
                backend="nams",
                method="LongTermMemory.get_related_entities",
                message=(
                    "depth > 1 is not supported on the NAMS REST API — "
                    "GET /v1/entities/{id} inlines 1-hop relationships only."
                ),
                workaround="Use the bolt backend for multi-hop traversal, or chain 1-hop calls.",
            )
        rel_types = kwargs.get("relationship_types")
        single = kwargs.get("relationship_type")
        if single is not None:
            rel_types = [single]
        payload = await self._transport.request(
            _SPEC_GET_ENTITY,
            path_params={"entity_id": _to_str(entity_id)},
        )
        if not isinstance(payload, dict):
            return []
        related: list[Entity] = []
        for r in payload.get("relationships") or []:
            if not isinstance(r, dict):
                continue
            rel_type = r.get("relType") or r.get("rel_type") or r.get("type")
            if rel_types and rel_type not in rel_types:
                continue
            target_id = r.get("targetId") or r.get("target_id")
            if not target_id:
                continue
            target = {
                "id": target_id,
                "name": r.get("targetName") or r.get("target_name") or "",
                "type": r.get("targetType") or r.get("target_type") or "custom",
            }
            related.append(payload_to_model(_normalize_entity(target), Entity))
        return related

    async def merge_duplicate_entities(
        self,
        source_id: UUID | str,
        target_id: UUID | str,
        *,
        canonical_name: str | None = None,
        **kwargs: Any,
    ) -> Entity:
        """Merge ``source_id`` into ``target_id`` and return the merged entity.

        A two/three-call composition over the REST API:

        1. ``POST /v1/entities/{source_id}/merge`` — the response's
           ``targetId`` is the *canonical-resolved* merge target (the
           requested target may itself have been merged already); that id
           is used for the follow-up calls.
        2. ``PUT /v1/entities/{canonical_id}`` with ``{name}`` — only when
           ``canonical_name`` is given.
        3. ``GET /v1/entities/{canonical_id}`` — returned as the merged
           :class:`Entity`.

        Note: unlike bolt's ``merge_duplicate_entities`` (which returns a
        ``(source, target)`` tuple), this returns the surviving target
        entity only — the source no longer exists after the merge.
        """
        merge_payload = await self._transport.request(
            _SPEC_MERGE_ENTITIES,
            path_params={"entity_id": _to_str(source_id)},
            json={"targetId": _to_str(target_id)},
        )
        merged = snakeize_keys(merge_payload) if isinstance(merge_payload, dict) else {}
        canonical_id = str(merged.get("target_id") or _to_str(target_id))
        if canonical_name is not None:
            await self._transport.request(
                _SPEC_UPDATE_ENTITY,
                path_params={"entity_id": canonical_id},
                json={"name": canonical_name},
            )
        payload = await self._transport.request(
            _SPEC_GET_ENTITY,
            path_params={"entity_id": canonical_id},
        )
        return payload_to_model(_normalize_entity(payload), Entity)

    async def get_preferences_for(self, **kwargs: Any) -> list[Preference]:
        raise NotSupportedError(
            backend="nams",
            method="LongTermMemory.get_preferences_for",
            message="NAMS does not expose a preferences endpoint.",
        )

    async def supersede_preference(self, preference_id: UUID | str, **kwargs: Any) -> None:
        raise NotSupportedError(
            backend="nams",
            method="LongTermMemory.supersede_preference",
            message="NAMS does not expose a preferences endpoint.",
        )

    async def get_facts_about(self, entity_name: str) -> list[Fact]:
        raise NotSupportedError(
            backend="nams",
            method="LongTermMemory.get_facts_about",
            message="NAMS does not expose a facts endpoint.",
        )

    async def get_entity_relationships(self, entity_id: UUID | str) -> list[Relationship]:
        """Return relationships from an entity (inline on NAMS).

        NAMS returns relationships inline on ``GET /v1/entities/{id}``
        with shape ``{relType, targetId, targetName, targetType}``.
        These don't carry full :class:`Relationship` fields
        (``source_id``, ``confidence``, ``valid_from``, etc.), so we
        synthesize what we can — the result is a list of
        :class:`Relationship` with bolt-flavored field names where
        the source field is the entity_id parameter.
        """
        from datetime import datetime, timezone

        payload = await self._transport.request(
            _SPEC_GET_ENTITY,
            path_params={"entity_id": _to_str(entity_id)},
        )
        if not isinstance(payload, dict):
            return []
        rels = payload.get("relationships") or []
        now_iso = datetime.now(timezone.utc).isoformat()
        out: list[Relationship] = []
        for r in rels:
            if not isinstance(r, dict):
                continue
            from uuid import uuid4

            normalized = {
                "id": str(uuid4()),
                "source_id": _to_str(entity_id),
                "target_id": r.get("targetId") or r.get("target_id") or "",
                "type": r.get("relType") or r.get("rel_type") or r.get("type") or "RELATED_TO",
                "created_at": now_iso,
                "metadata": {},
                "attributes": {
                    "target_name": r.get("targetName") or r.get("target_name"),
                    "target_type": r.get("targetType") or r.get("target_type"),
                },
            }
            out.append(payload_to_model(normalized, Relationship))
        return out

    async def get_context(self, query: str, **kwargs: Any) -> str:
        """Long-term context — not exposed by NAMS as a dedicated endpoint.

        Returns an empty string. Use ``client.long_term.search_entities``
        and ``client.short_term.get_context`` to assemble context yourself,
        or use the bolt backend.
        """
        return ""

    # -------------------------------------------------------------------- Gold

    async def get_entity_provenance(self, entity_id: UUID | str) -> dict[str, Any]:
        """Return source-of-truth provenance for an entity.

        Per verified spec, this is under the reasoning namespace:
        ``GET /v1/reasoning/provenance/{entityId}``. Response:
        ``{entityId, steps: [...]}``.
        """
        payload = await self._transport.request(
            _SPEC_GET_ENTITY_PROVENANCE,
            path_params={"entity_id": _to_str(entity_id)},
        )
        return dict(payload or {})

    # ---------------------------------------------------------------- Platinum

    async def set_entity_feedback(
        self,
        entity_id: UUID | str,
        feedback: str,
        **kwargs: Any,
    ) -> None:
        """Record feedback on an entity.

        Per verified spec, NAMS uses **PUT** (not POST) at
        ``/v1/entities/{id}/feedback`` with body
        ``{userScore?, confirmed?}``. There is no free-form
        ``feedback`` string field — we map the Protocol's
        ``feedback`` parameter to ``userScore``:

        * ``"positive"`` → ``userScore=1.0, confirmed=True``
        * ``"negative"`` → ``userScore=0.0, confirmed=False``
        * float-stringed (e.g. ``"0.75"``) → ``userScore=<float>``

        Pass ``user_score=`` and ``confirmed=`` kwargs to override.
        """
        # Priority: explicit kwargs > derived from feedback string.
        user_score: float | None = kwargs.get("user_score")
        confirmed: bool | None = kwargs.get("confirmed")

        if user_score is None and confirmed is None:
            # Derive from feedback string.
            feedback_lc = (feedback or "").lower()
            if feedback_lc == "positive":
                user_score, confirmed = 1.0, True
            elif feedback_lc == "negative":
                user_score, confirmed = 0.0, False
            else:
                try:
                    user_score = float(feedback)
                except (TypeError, ValueError):
                    pass

        body = _drop_none({"userScore": user_score, "confirmed": confirmed})
        await self._transport.request(
            _SPEC_SET_ENTITY_FEEDBACK,
            path_params={"entity_id": _to_str(entity_id)},
            json=body,
        )

    async def get_entity_history(
        self,
        entity_id: UUID | str,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """Return mention/edit history for an entity.

        NAMS response: ``{entityId, mentions: [...]}``. We return the
        ``mentions`` array.
        """
        payload = await self._transport.request(
            _SPEC_GET_ENTITY_HISTORY,
            path_params={"entity_id": _to_str(entity_id)},
        )
        if isinstance(payload, dict) and "mentions" in payload:
            return list(payload["mentions"])
        return []


__all__ = ["NamsLongTermMemory"]
