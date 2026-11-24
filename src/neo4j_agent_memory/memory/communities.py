"""Communities: the threads of a session, found in its entity graph.

Entities that keep coming up together belong to one thread of the conversation:
the people and places of a trip, the parts of a feature being built. This module
finds those groups and writes them to the graph:

1. **Co-occurrence.** Two entities are linked when the same exchange (a user
   message and the reply to it) mentions both, or a statement taken from it is
   about both. The weight of the link is the share of their exchanges they have
   in common, so an entity that is everywhere links to nothing in particular.
   Relationships between entities (``RELATED_TO``) add to it.
2. **Hierarchical Louvain.** The weighted graph is projected into Neo4j Graph
   Data Science and clustered with ``gds.louvain``; the intermediate levels are
   kept, so that a community of the finest level has a parent that takes in a
   wider theme.
3. **Threads.** Every exchange is given to the community most of its entities
   belong to. A community is then a thread: a set of exchanges spread over the
   conversation, with the statements the user made in them.
4. **Summaries.** An LLM writes a title and a few dated bullet points for each
   thread, in the order things came up, and the result is embedded.

The graph afterwards holds ``(:Entity)-[:IN_COMMUNITY]->(:Community)`` for the
finest level, ``(:Community)-[:PARENT_COMMUNITY]->(:Community)`` between levels
and ``(:Conversation)-[:HAS_COMMUNITY]->(:Community)``.

Graph Data Science is a Neo4j plugin. Without it :meth:`CommunityDetector.detect`
raises :class:`GraphDataScienceUnavailable`; nothing else in the library needs it.
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
from collections.abc import Awaitable, Callable, Iterable
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from neo4j_agent_memory.llm.types import ChatMessage

if TYPE_CHECKING:
    from neo4j_agent_memory.embeddings.base import Embedder
    from neo4j_agent_memory.graph.client import Neo4jClient
    from neo4j_agent_memory.llm.protocol import LLMProvider

logger = logging.getLogger(__name__)


class GraphDataScienceUnavailable(RuntimeError):
    """The Neo4j Graph Data Science plugin is not installed on the server."""


COMMUNITY_PROMPT = """\
You are keeping notes on one thread of a long conversation between a user and an \
AI assistant: what the user said, across the whole conversation, about a group of \
related things. Between the <statements> tags are the user's statements on it, in \
the order they were made, each with the position (#n) of the message it comes from \
and, where known, the date it was said.

First line: a title for the thread, at most 8 words, naming what it is about in \
words the user would recognise.

Then at most {high} bullet points that tell how the thread went, in the order \
things came up, from its first statement to its last and not only its beginning. \
Do not copy the statements one by one: put the statements that belong to one step \
or aspect into one bullet. Each bullet names that step or aspect in a few words, \
then gives its specifics (names, numbers, dates, choices made), and starts with \
the position of the message where it first came up, like "- #12: ...". At most 35 \
words per bullet. Where a later statement changed an earlier value, give the later \
value and say that it changed. No closing remark.
"""

EXCHANGE_THREAD_PROMPT = """\
You are keeping notes on one thread of a long conversation between a user and an \
AI assistant: the exchanges, across the whole conversation, that are about a group \
of related things. Between the <exchanges> tags are those exchanges in the order \
they took place, each with the position (#n) of the user's message and, where \
known, its date. Long messages are cut short.

First line: a title for the thread, at most 8 words, naming what it is about in \
words the user would recognise.

Then at most {high} bullet points that tell how the thread went, in order, from \
its first exchange to its last and not only its beginning. One bullet per exchange \
that moved the thread on; exchanges about the same step go into one bullet. Each \
bullet starts with the position of the user's message, like "- #12: ...", and says \
two things: what the user brought up, asked for or decided, with its specifics \
(names, numbers, dates), and what the assistant gave in reply (the plan and its \
phases, the options compared, the advice, the fix), in a clause. At most 45 words \
per bullet. No closing remark.
"""


# --- Cypher -----------------------------------------------------------------

_GDS_VERSION = "RETURN gds.version() AS version"

_SESSION_MESSAGES = """
MATCH (c:Conversation {session_id: $session_id})-[:HAS_MESSAGE]->(m:Message)
RETURN m.id AS id, m.role AS role
ORDER BY m.index ASC, m.timestamp ASC, m.id ASC
"""

_SESSION_CONTENT = """
MATCH (c:Conversation {session_id: $session_id})-[:HAS_MESSAGE]->(m:Message)
RETURN m.id AS id, m.role AS role, m.content AS content, toString(m.timestamp) AS timestamp
ORDER BY m.index ASC, m.timestamp ASC, m.id ASC
"""

_MENTIONS = """
MATCH (c:Conversation {session_id: $session_id})-[:HAS_MESSAGE]->(m:Message)
      -[:MENTIONS]->(e:Entity)
RETURN e.id AS id, e.name AS name, collect(DISTINCT m.id) AS message_ids
"""

_STATEMENT_ENTITIES = """
MATCH (s)-[:ABOUT|CHOSE|REJECTED]->(e:Entity)
WHERE (s:Fact OR s:Decision OR s:Preference) AND s.session_id = $session_id
      AND s.message_index IS NOT NULL
RETURN e.id AS id, e.name AS name, collect(DISTINCT s.message_index) AS positions
"""

_RELATIONS = """
MATCH (c:Conversation {session_id: $session_id})-[:HAS_MESSAGE]->(:Message)
      -[:MENTIONS]->(a:Entity)-[:RELATED_TO]-(b:Entity)
WHERE a.id < b.id
RETURN DISTINCT a.id AS a, b.id AS b
"""

# The projection lives in the Graph Data Science catalog only: the weighted
# links between entities are not written to the database.
_PROJECT = """
UNWIND $pairs AS pair
MATCH (a:Entity {id: pair.a})
MATCH (b:Entity {id: pair.b})
WITH gds.graph.project(
    $graph, a, b,
    {relationshipProperties: {weight: pair.weight}},
    {undirectedRelationshipTypes: ['*']}
) AS g
RETURN g.nodeCount AS nodes, g.relationshipCount AS relationships
"""

_LOUVAIN = """
CALL gds.louvain.stream($graph, {
    relationshipWeightProperty: 'weight',
    includeIntermediateCommunities: true,
    maxLevels: $max_levels,
    concurrency: 1
})
YIELD nodeId, communityId, intermediateCommunityIds
RETURN gds.util.asNode(nodeId).id AS id, communityId, intermediateCommunityIds
"""

_DROP_PROJECTION = "CALL gds.graph.drop($graph, false) YIELD graphName RETURN graphName"

_DELETE_COMMUNITIES = """
MATCH (c:Community {session_id: $session_id})
DETACH DELETE c
"""

_STORE_COMMUNITIES = """
MATCH (conversation:Conversation {session_id: $session_id})
WITH conversation ORDER BY conversation.created_at DESC LIMIT 1
UNWIND $rows AS row
CREATE (c:Community {
    id: row.id, session_id: $session_id, level: row.level, size: row.size,
    entity_names: row.entity_names, messages: row.messages, weight: row.weight,
    first_index: row.first_index, last_index: row.last_index, created_at: datetime()
})
CREATE (conversation)-[:HAS_COMMUNITY]->(c)
"""

_LINK_ENTITIES = """
UNWIND $rows AS row
MATCH (e:Entity {id: row.entity})
MATCH (c:Community {id: row.community})
MERGE (e)-[:IN_COMMUNITY]->(c)
"""

_LINK_PARENTS = """
UNWIND $rows AS row
MATCH (child:Community {id: row.child})
MATCH (parent:Community {id: row.parent})
MERGE (child)-[:PARENT_COMMUNITY]->(parent)
"""

_SESSION_COMMUNITIES = """
MATCH (c:Community {session_id: $session_id})
RETURN c.id AS id, c.level AS level, c.messages AS messages,
       c.entity_names AS entity_names, c.summary AS summary
ORDER BY c.level ASC, c.first_index ASC
"""

_SESSION_STATEMENTS = """
CALL {
    MATCH (f:Fact {session_id: $session_id})
    OPTIONAL MATCH (f)-[r:SUPERSEDED_BY]->(:Fact)
    WITH f, count(r) > 0 AS replaced
    RETURN f.text AS text, f.message_index AS message_index, f.said_at AS said_at, replaced
    UNION ALL
    MATCH (d:Decision {session_id: $session_id})
    RETURN d.text AS text, d.message_index AS message_index, d.decided_on AS said_at,
           d.status = 'reversed' AS replaced
    UNION ALL
    MATCH (p:Preference {session_id: $session_id})
    RETURN p.preference AS text, p.message_index AS message_index, p.said_at AS said_at,
           false AS replaced
}
RETURN text, message_index, said_at, replaced
ORDER BY message_index ASC
"""

_STORE_SUMMARY = """
MATCH (c:Community {id: $id})
SET c.title = $title, c.summary = $summary, c.embedding = $embedding,
    c.summarized_at = datetime()
"""


# --- the graph to cluster ---------------------------------------------------

_VALUE = re.compile(r"^[\s\W\d]*(?:\d[\d\s.,:/%-]*)(?:[a-z]{0,3})?[\s\W]*$", re.IGNORECASE)


def is_value_like(name: str) -> bool:
    """Whether an entity name is a value and not a thing: "$85,000", "15%", "3.2".

    Extraction turns amounts and figures into entities. They say nothing about
    which thread an exchange belongs to, and two exchanges that share "10" have
    nothing in common.
    """
    name = (name or "").strip()
    if not name:
        return True
    if _VALUE.match(name):
        return True
    digits = sum(ch.isdigit() for ch in name)
    return digits >= 3 and digits / len(name) >= 0.4


def unit_of(position: int, roles: list[str]) -> int:
    """The exchange a message belongs to, named by the position of its first message."""
    if (
        0 < position < len(roles)
        and roles[position] == "assistant"
        and roles[position - 1] == "user"
    ):
        return position - 1
    return position


def cooccurrence(
    occurrences: dict[str, set[int]],
    *,
    unit_count: int,
    min_units: int = 2,
    max_share: float = 0.4,
    related: Iterable[tuple[str, str]] = (),
    relation_weight: float = 0.5,
) -> list[tuple[str, str, float]]:
    """The weighted links between entities that occur in the same units.

    Args:
        occurrences: For each entity, the units (exchanges) it occurs in.
        unit_count: How many units the session has.
        min_units: Entities in fewer units than this are left out: an entity
            that came up once ties nothing together.
        max_share: Entities in more than this share of the units are left out:
            the subject of the whole conversation belongs to every thread.
        related: Pairs of entities with a relationship between them.
        relation_weight: What a relationship adds to the weight of a pair.

    Returns:
        ``(a, b, weight)`` with ``a < b``. The weight is the number of shared
        units over the geometric mean of the two entities' unit counts (their
        cosine similarity as sets), plus ``relation_weight`` for related pairs.
    """
    ceiling = max(min_units, math.floor(max_share * unit_count)) if unit_count else min_units
    kept = {
        entity: units for entity, units in occurrences.items() if min_units <= len(units) <= ceiling
    }
    by_unit: dict[int, list[str]] = {}
    for entity, units in kept.items():
        for unit in units:
            by_unit.setdefault(unit, []).append(entity)
    shared: dict[tuple[str, str], int] = {}
    for entities in by_unit.values():
        entities.sort()
        for i, a in enumerate(entities):
            for b in entities[i + 1 :]:
                shared[(a, b)] = shared.get((a, b), 0) + 1
    weights = {
        pair: count / math.sqrt(len(kept[pair[0]]) * len(kept[pair[1]]))
        for pair, count in shared.items()
    }
    for a, b in related:
        if a in kept and b in kept and a != b:
            pair = (a, b) if a < b else (b, a)
            weights[pair] = weights.get(pair, 0.0) + relation_weight
    return sorted((a, b, round(weight, 6)) for (a, b), weight in weights.items())


def community_levels(assignments: dict[str, list[int]]) -> list[dict[str, Any]]:
    """Communities of every level from each entity's Louvain assignments.

    ``assignments`` maps an entity to its community at each level, finest
    first, as ``intermediateCommunityIds`` gives them. A level that merges
    nothing (every community has one child with the same members) is dropped,
    and so is a community with the same members as one of its children, so
    each community here differs from its children.

    Returns:
        One dict per community with ``key`` (level, Louvain id), ``level``
        (0 is the finest kept level), ``entities`` and ``parent`` (the key of
        the community one level up, or None).
    """
    depth = max((len(levels) for levels in assignments.values()), default=0)
    members: list[dict[int, set[str]]] = [{} for _ in range(depth)]
    for entity, levels in assignments.items():
        for level, number in enumerate(levels):
            members[level].setdefault(number, set()).add(entity)

    kept_levels: list[int] = []
    for level in range(depth):
        if kept_levels:
            below = {frozenset(m) for m in members[kept_levels[-1]].values()}
            if {frozenset(m) for m in members[level].values()} == below:
                continue
        kept_levels.append(level)

    communities: list[dict[str, Any]] = []
    for new_level, level in enumerate(kept_levels):
        above = kept_levels[new_level + 1] if new_level + 1 < len(kept_levels) else None
        for number, entities in sorted(members[level].items()):
            parent_key: tuple[int, int] | None = None
            if above is not None:
                some = next(iter(entities))
                parent_key = (new_level + 1, assignments[some][above])
            communities.append(
                {
                    "key": (new_level, number),
                    "level": new_level,
                    "entities": set(entities),
                    "parent": parent_key,
                }
            )
    # A parent with the members of one of its children adds nothing: the child
    # takes its place, under the parent's own parent.
    by_key = {c["key"]: c for c in communities}
    for community in communities:
        while community["parent"] is not None:
            parent = by_key[community["parent"]]
            if parent["entities"] != community["entities"]:
                break
            community["parent"] = parent["parent"]
            parent["duplicate"] = True
    return [c for c in communities if not c.pop("duplicate", False)]


def assign_units(
    communities: list[dict[str, Any]],
    occurrences: dict[str, set[int]],
    unit_count: int,
) -> None:
    """Give every unit to the community, per level, that most of its entities belong to.

    An entity counts by an inverse-frequency weight, so the rare entity of an
    exchange decides over the one that is in every other exchange. Sets
    ``units`` (sorted) on each community.
    """
    for community in communities:
        community["units"] = []
    for level in sorted({c["level"] for c in communities}):
        of_level = [c for c in communities if c["level"] == level]
        owner = {entity: c for c in of_level for entity in c["entities"]}
        affinity: dict[int, dict[int, float]] = {}
        for entity, community in owner.items():
            units = occurrences.get(entity, set())
            if not units:
                continue
            weight = math.log(1 + unit_count / len(units))
            for unit in units:
                scores = affinity.setdefault(unit, {})
                scores[id(community)] = scores.get(id(community), 0.0) + weight
        by_id = {id(c): c for c in of_level}
        for unit, scores in affinity.items():
            # Ties fall to the community whose first entity sorts first: deterministic.
            best = min(scores.items(), key=lambda kv: (-kv[1], min(by_id[kv[0]]["entities"])))
            by_id[best[0]]["units"].append(unit)
        for community in of_level:
            community["units"].sort()


def parse_thread(text: str) -> tuple[str, str]:
    """The title and the bullet points of what the LLM wrote for a thread."""
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    if not lines:
        return "", ""
    title = re.sub(r"^(?:#+\s*|\*\*|title:\s*)", "", lines[0], flags=re.IGNORECASE)
    title = title.strip(" *#").strip()
    bullets = [line if line.startswith("- ") else f"- {line.lstrip('*• ')}" for line in lines[1:]]
    return title, "\n".join(bullets)


def positions_in(summary: str) -> list[int]:
    """The message positions (#n, one-based) a thread's bullets refer to, as indexes."""
    return [int(n) - 1 for n in re.findall(r"#(\d+)", summary or "")]


# --- detection --------------------------------------------------------------


class CommunityDetector:
    """Finds and summarizes the communities of a session's entities."""

    def __init__(
        self,
        client: Neo4jClient,
        embedder: Embedder | None,
        llm: LLMProvider | None,
        *,
        retries: int = 3,
    ) -> None:
        self._client = client
        self._embedder = embedder
        self._llm = llm
        self._retries = retries

    async def available(self) -> str | None:
        """The version of Graph Data Science on the server, or None without it."""
        try:
            rows = await self._client.execute_read(_GDS_VERSION)
        except Exception:  # noqa: BLE001 - an unknown function is the expected failure
            return None
        return str(rows[0]["version"]) if rows else None

    async def _occurrences(
        self, session_id: str
    ) -> tuple[dict[str, set[int]], dict[str, str], int]:
        """Each entity's exchanges, the entity names, and the number of exchanges."""
        messages = await self._client.execute_read(_SESSION_MESSAGES, {"session_id": session_id})
        position = {row["id"]: i for i, row in enumerate(messages)}
        roles = [row["role"] for row in messages]
        occurrences: dict[str, set[int]] = {}
        names: dict[str, str] = {}
        for row in await self._client.execute_read(_MENTIONS, {"session_id": session_id}):
            names[row["id"]] = row["name"]
            units = {unit_of(position[m], roles) for m in row["message_ids"] if m in position}
            occurrences.setdefault(row["id"], set()).update(units)
        for row in await self._client.execute_read(_STATEMENT_ENTITIES, {"session_id": session_id}):
            names[row["id"]] = row["name"]
            units = {unit_of(p, roles) for p in row["positions"] if 0 <= p < len(roles)}
            occurrences.setdefault(row["id"], set()).update(units)
        unit_count = len({unit_of(p, roles) for p in range(len(roles))})
        return occurrences, names, unit_count

    async def detect(
        self,
        session_id: str,
        *,
        max_levels: int = 4,
        min_units: int = 2,
        max_share: float = 0.4,
        relation_weight: float = 0.5,
        min_size: int = 2,
    ) -> dict[str, int]:
        """Cluster the session's entities and write the communities.

        The communities the session had are replaced.

        Args:
            session_id: The session whose entities are clustered.
            max_levels: Levels of the Louvain hierarchy at most.
            min_units: Entities in fewer exchanges than this are left out.
            max_share: Entities in more than this share of the exchanges are
                left out.
            relation_weight: What a ``RELATED_TO`` relationship adds to the
                weight of a pair.
            min_size: Communities with fewer entities than this are not kept.

        Returns:
            ``entities`` clustered, ``links`` between them, ``communities``
            written and the number of ``levels``.

        Raises:
            GraphDataScienceUnavailable: The plugin is not installed.
        """
        if await self.available() is None:
            raise GraphDataScienceUnavailable(
                "Community detection needs the Neo4j Graph Data Science plugin "
                "(gds.version() is not available on this server)."
            )
        occurrences, names, unit_count = await self._occurrences(session_id)
        occurrences = {e: units for e, units in occurrences.items() if not is_value_like(names[e])}
        related = [
            (row["a"], row["b"])
            for row in await self._client.execute_read(_RELATIONS, {"session_id": session_id})
        ]
        pairs = cooccurrence(
            occurrences,
            unit_count=unit_count,
            min_units=min_units,
            max_share=max_share,
            related=related,
            relation_weight=relation_weight,
        )
        await self._client.execute_write(_DELETE_COMMUNITIES, {"session_id": session_id})
        stats = {"entities": 0, "links": len(pairs), "communities": 0, "levels": 0}
        if not pairs:
            return stats

        graph = f"agent-memory-communities-{uuid4().hex}"
        try:
            await self._client.execute_write(
                _PROJECT,
                {"graph": graph, "pairs": [{"a": a, "b": b, "weight": w} for a, b, w in pairs]},
            )
            rows = await self._client.execute_read(
                _LOUVAIN, {"graph": graph, "max_levels": max_levels}
            )
        finally:
            try:
                await self._client.execute_write(_DROP_PROJECTION, {"graph": graph})
            except Exception:  # noqa: BLE001 - nothing to drop when the projection failed
                logger.debug("No projection %s to drop", graph, exc_info=True)

        assignments = {
            row["id"]: list(row["intermediateCommunityIds"] or [row["communityId"]]) for row in rows
        }
        communities = [c for c in community_levels(assignments) if len(c["entities"]) >= min_size]
        assign_units(communities, occurrences, unit_count)
        communities = [c for c in communities if c["units"]]
        ids = {c["key"]: str(uuid4()) for c in communities}

        def frequency(entity: str) -> tuple[int, str]:
            return (-len(occurrences.get(entity, ())), names.get(entity, ""))

        await self._client.execute_write(
            _STORE_COMMUNITIES,
            {
                "session_id": session_id,
                "rows": [
                    {
                        "id": ids[c["key"]],
                        "level": c["level"],
                        "size": len(c["entities"]),
                        "entity_names": list(
                            dict.fromkeys(names[e] for e in sorted(c["entities"], key=frequency))
                        )[:12],
                        "messages": c["units"],
                        "weight": len(c["units"]),
                        "first_index": c["units"][0],
                        "last_index": c["units"][-1],
                    }
                    for c in communities
                ],
            },
        )
        await self._client.execute_write(
            _LINK_ENTITIES,
            {
                "rows": [
                    {"entity": entity, "community": ids[c["key"]]}
                    for c in communities
                    if c["level"] == 0
                    for entity in sorted(c["entities"])
                ]
            },
        )
        await self._client.execute_write(
            _LINK_PARENTS,
            {
                "rows": [
                    {"child": ids[c["key"]], "parent": ids[c["parent"]]}
                    for c in communities
                    if c["parent"] in ids
                ]
            },
        )
        stats["entities"] = len(assignments)
        stats["communities"] = len(communities)
        stats["levels"] = len({c["level"] for c in communities})
        return stats

    async def summarize(
        self,
        session_id: str,
        *,
        source: str = "exchanges",
        min_statements: int = 3,
        max_statements: int = 80,
        min_exchanges: int = 2,
        max_exchanges: int = 30,
        message_chars: int = 700,
        skip_existing: bool = True,
        max_concurrency: int = 8,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> dict[str, int]:
        """Write a title and dated bullet points for each community, and embed them.

        Args:
            session_id: The session whose communities are summarized.
            source: What the LLM reads. ``"exchanges"``: the exchanges of the
                thread themselves, each message cut to ``message_chars``, so
                that a bullet says what the user brought up and what the
                assistant gave in reply. ``"statements"``: the statements the
                user made in them, which leaves the assistant's part out.
            min_statements: With ``"statements"``, communities with fewer
                statements than this get no summary.
            max_statements: Statements shown to the LLM at most; of a longer
                thread an even sample is shown, so that its end is not lost.
            min_exchanges: With ``"exchanges"``, communities with fewer
                exchanges than this get no summary.
            max_exchanges: Exchanges shown to the LLM at most, sampled evenly.
            skip_existing: Leave communities that have a summary as they are.

        Returns:
            ``summaries`` written, ``skipped`` (too little to summarize) and ``failures``.
        """
        if self._llm is None:
            raise RuntimeError(
                "Community summaries need an LLM. Configure MemorySettings.llm with a provider."
            )
        if source not in ("exchanges", "statements"):
            raise ValueError(f"source must be 'exchanges' or 'statements', not {source!r}")
        llm = self._llm
        stats = {"summaries": 0, "skipped": 0, "failures": 0}
        communities = await self._client.execute_read(
            _SESSION_COMMUNITIES, {"session_id": session_id}
        )
        messages = await self._client.execute_read(_SESSION_CONTENT, {"session_id": session_id})
        roles = [row["role"] for row in messages]
        by_unit: dict[int, list[dict[str, Any]]] = {}
        if source == "statements":
            statements = await self._client.execute_read(
                _SESSION_STATEMENTS, {"session_id": session_id}
            )
            for statement in statements:
                position = statement["message_index"]
                if position is not None and 0 <= position < len(roles):
                    by_unit.setdefault(unit_of(position, roles), []).append(statement)

        def sample(items: list[Any], limit: int) -> list[Any]:
            if len(items) <= limit:
                return items
            step = len(items) / limit
            return [items[int(i * step)] for i in range(limit)]

        def from_statements(community: dict[str, Any]) -> tuple[str, str, int] | None:
            said = [s for unit in community["messages"] or [] for s in by_unit.get(unit, [])]
            if len(said) < min_statements:
                return None
            said = sample(said, max_statements)
            lines = []
            for s in said:
                when = f", {str(s['said_at'])[:10]}" if s.get("said_at") else ""
                changed = " (later changed)" if s.get("replaced") else ""
                lines.append(f"(#{s['message_index'] + 1}{when}) {s['text']}{changed}")
            high = max(3, min(8, round(len(said) / 8) + 2))
            return (
                COMMUNITY_PROMPT.format(high=high),
                "<statements>\n" + "\n".join(lines) + "\n</statements>",
                high,
            )

        def from_exchanges(community: dict[str, Any]) -> tuple[str, str, int] | None:
            units = [u for u in community["messages"] or [] if 0 <= u < len(messages)]
            if len(units) < min_exchanges:
                return None
            units = sample(units, max_exchanges)
            blocks = []
            for unit in units:
                when = (messages[unit].get("timestamp") or "")[:10]
                header = f"[#{unit + 1}" + (f" | {when}]" if when else "]")
                parts = [header]
                for position in (unit, unit + 1):
                    if position >= len(messages) or (
                        position > unit and roles[position] != "assistant"
                    ):
                        continue
                    said = messages[position]["content"] or ""
                    if len(said) > message_chars:
                        said = said[:message_chars].rstrip() + " [...]"
                    parts.append(f"{roles[position].upper()}: {said}")
                blocks.append("\n".join(parts))
            high = max(3, min(14, len(units)))
            return (
                EXCHANGE_THREAD_PROMPT.format(high=high),
                "<exchanges>\n" + "\n\n".join(blocks) + "\n</exchanges>",
                high,
            )

        async def one(community: dict[str, Any]) -> None:
            if skip_existing and community.get("summary"):
                return
            built = (from_exchanges if source == "exchanges" else from_statements)(community)
            if built is None:
                stats["skipped"] += 1
                return
            system, material, high = built
            prompt = [
                ChatMessage(role="system", content=system),
                ChatMessage(
                    role="user",
                    content="Things the thread is about: "
                    + ", ".join(community["entity_names"] or [])
                    + "\n\n"
                    + material
                    + "\n\nWrite the title and the bullet points for this thread.",
                ),
            ]
            completion = await self._with_retries(
                lambda: llm.complete(prompt, temperature=0.0, max_tokens=80 + 80 * high),
                f"community summary ({community['id']})",
            )
            title, summary = parse_thread(completion.content if completion is not None else "")
            if not title or not summary:
                stats["failures"] += 1
                return
            embedding = (
                await self._embedder.embed(f"{title}\n{summary}")
                if self._embedder is not None
                else None
            )
            await self._client.execute_write(
                _STORE_SUMMARY,
                {
                    "id": community["id"],
                    "title": title,
                    "summary": summary,
                    "embedding": embedding,
                },
            )
            stats["summaries"] += 1

        semaphore = asyncio.Semaphore(max(1, max_concurrency))
        done = 0

        async def run(community: dict[str, Any]) -> None:
            nonlocal done
            async with semaphore:
                await one(community)
            done += 1
            if on_progress is not None:
                on_progress(done, len(communities))

        await asyncio.gather(*(run(c) for c in communities))
        return stats

    async def _with_retries(self, call: Callable[[], Awaitable[Any]], what: str) -> Any:
        """Run an LLM call, retrying transient failures. None when it never succeeds."""
        for attempt in range(1, self._retries + 1):
            try:
                return await call()
            except Exception as exc:  # noqa: BLE001 - provider errors vary; all are retried
                if attempt == self._retries:
                    logger.warning("%s failed after %d attempts: %s", what, attempt, exc)
                    return None
                await asyncio.sleep(2.0 * attempt)
        return None
