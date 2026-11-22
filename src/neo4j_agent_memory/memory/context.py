"""Context assembly: what to put in front of the model for one question.

:class:`ContextAssembler` builds the context for a question about one session
from everything the memory holds about it, within a token budget:

* **Standing instructions and preferences** the user has given. All active
  instructions are included, because a question rarely resembles the
  instruction that should shape its answer ("show me how to log in" against
  "always use syntax highlighting"); preferences are ranked by similarity.
* **Statements** the user made that bear on the question (``:Fact``), each with
  where and when it was said. A value that was later replaced is marked as such,
  and statements that contradict each other are listed together.
* **A timeline** of the dated statements among them, with the days in between.
* **The outline** of the conversation (``:Summary``), in order.
* **Excerpts**: the user/assistant exchanges most relevant to the question,
  laid out in conversation order.

The excerpts are chosen by reciprocal rank fusion of three rankings of the
session's messages: vector search over message embeddings; a hop from the
question's entities to the messages that mention them, weighted so that a
rarely mentioned entity counts for more; and the source messages of the
statements that match the question. The best vector hits are always kept, so a
direct hit cannot be displaced by exchanges that are middling in every ranking.

Statements, preferences, decisions and summaries exist once
:meth:`~neo4j_agent_memory.memory.distillation.DistillationMemory.distill_session`
has run; without them the context is the excerpts and the entities.

With a :class:`~neo4j_agent_memory.judge.Judge` configured, the assembler asks
it a few closed questions per query, in parallel with one another:

* which contradictions, standing instructions and decisions bear on the
  question, so that a contradiction is reported only when the question is
  about it and only the instructions that apply are repeated at the end;
* for each candidate exchange, whether it holds information the answer needs,
  which joins the fusion as a fourth ranking;
* for a question about the time between two things, which dated statements they
  are. The days between them are then computed here, not left to the reader.

A judgement that fails is done without: the context falls back to what it
would have been with no judge.
"""

from __future__ import annotations

import asyncio
import math
import re
from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING, Any

from neo4j_agent_memory.judge import OneOf, Question, YesNo, judge_safely

if TYPE_CHECKING:
    from neo4j_agent_memory.embeddings.base import Embedder
    from neo4j_agent_memory.graph.client import Neo4jClient
    from neo4j_agent_memory.judge import Judge

#: Constant of reciprocal rank fusion (Cormack et al., 2009).
RRF_K = 60
#: Depth of each ranking that enters the fusion.
CANDIDATES = 30

GUIDANCE = """\
## How to answer from this memory
Everything above is what the assistant's memory holds about its conversation with \
the user; #n is a message's position in that conversation. When you answer:
- Follow the user's standing instructions and preferences listed above, also when \
the question does not mention them.
- If "Contradictions in what the user has said" lists something the question \
touches, do not answer yes or no. Say that the user has given contradictory \
information, repeat both statements, and ask which one is correct.
- Where a statement is marked as later changed, the later value is the current one.
- For a question about dates or durations, find the two dates on the timeline, \
subtract their day numbers, and answer with the result and both dates ("18 days, \
from March 12 to March 30"). Where the timeline ends with a span worked out for \
this question and it is about the two things asked for, answer with that span; \
where it ends with the days between its dates, take the number from there.
- When asked in what order things came up, go by message position, earliest \
first, and draw the items from the whole length of the conversation, not only \
from its start.
- When asked to summarize, cover the whole course of the topic in order, with its \
specifics; the outline shows that course.
- If the question asks about something from the conversation that the memory does \
not contain, say that there is no information about it instead of guessing."""


@dataclass
class AssembledContext:
    """The context for one question, and how it was put together."""

    text: str
    details: dict[str, Any] = field(default_factory=dict)


# --- ranking ----------------------------------------------------------------


def entities_named_in(question: str, names: dict[str, str]) -> set[str]:
    """Ids of the entities whose name appears in the question as whole words.

    Names shorter than three characters are ignored: they match too much to be
    evidence of anything.
    """
    text = question.lower()
    found: set[str] = set()
    for entity_id, name in names.items():
        name = (name or "").strip().lower()
        if len(name) < 3:
            continue
        if re.search(rf"(?<!\w){re.escape(name)}(?!\w)", text):
            found.add(entity_id)
    return found


def graph_message_scores(
    entity_relevance: dict[str, float],
    entity_mentions: dict[str, set[int]],
    message_count: int,
) -> dict[int, float]:
    """Score each message by the question entities it mentions.

    An entity contributes its relevance times an inverse-document-frequency
    weight, so an entity mentioned in three messages points at them far more
    strongly than one mentioned in half the conversation.
    """
    scores: dict[int, float] = {}
    for entity_id, relevance in entity_relevance.items():
        mentions = entity_mentions.get(entity_id, set())
        if not mentions:
            continue
        idf = math.log(1 + message_count / len(mentions))
        for position in mentions:
            scores[position] = scores.get(position, 0.0) + relevance * idf
    return scores


def walk_message_scores(
    seeds: dict[str, float],
    entity_mentions: dict[str, set[int]],
    related: dict[str, set[str]],
    *,
    restart: float = 0.5,
    steps: int = 6,
) -> dict[int, float]:
    """Score each message by a random walk that keeps returning to the seeds.

    The walk moves over the session's graph: from an entity to a message that
    mentions it or to an entity it is related to, and from a message to an
    entity it mentions. With probability ``restart`` it goes back to a seed
    entity (in proportion to the seed's weight). A message scores by how much
    of the walk passes through it, so it can score without mentioning any seed,
    by mentioning things that the seeds' messages mention too.

    This is personalized PageRank with the question's entities as the
    personalization. An entity mentioned everywhere spreads its share thin,
    which does here what the inverse-frequency weight does for a single hop.
    """
    total = sum(weight for weight in seeds.values() if weight > 0)
    if total <= 0:
        return {}
    start = {entity: weight / total for entity, weight in seeds.items() if weight > 0}
    message_entities: dict[int, list[str]] = {}
    for entity, positions in entity_mentions.items():
        for position in positions:
            message_entities.setdefault(position, []).append(entity)
    at_entities = dict(start)
    at_messages: dict[int, float] = {}
    for _ in range(steps):
        to_entities: dict[str, float] = {}
        to_messages: dict[int, float] = {}
        for entity, mass in at_entities.items():
            mentions = entity_mentions.get(entity, set())
            neighbours = related.get(entity, set())
            degree = len(mentions) + len(neighbours)
            if degree == 0:
                continue
            share = mass / degree
            for position in mentions:
                to_messages[position] = to_messages.get(position, 0.0) + share
            for other in neighbours:
                to_entities[other] = to_entities.get(other, 0.0) + share
        for position, mass in at_messages.items():
            entities = message_entities.get(position, [])
            if not entities:
                continue
            share = mass / len(entities)
            for entity in entities:
                to_entities[entity] = to_entities.get(entity, 0.0) + share
        at_entities = {e: (1 - restart) * mass for e, mass in to_entities.items()}
        for entity, weight in start.items():
            at_entities[entity] = at_entities.get(entity, 0.0) + restart * weight
        at_messages = {m: (1 - restart) * mass for m, mass in to_messages.items()}
    return at_messages


def kind_of(label: str) -> str:
    """The name a reader would use for an entity type: ``OBJECT:TECHNOLOGY`` is ``technology``."""
    return label.rsplit(":", 1)[-1].replace("_", " ").lower()


def pair_of(position: int, roles: list[str]) -> tuple[int, int]:
    """The user/assistant exchange a message belongs to, as (first, last)."""
    role = roles[position]
    if role == "user" and position + 1 < len(roles) and roles[position + 1] == "assistant":
        return position, position + 1
    if role == "assistant" and position > 0 and roles[position - 1] == "user":
        return position - 1, position
    return position, position


def to_pair_ranking(positions: list[int], roles: list[str]) -> list[tuple[int, int]]:
    """A ranking of messages as a ranking of exchanges, each at its best rank."""
    ranking: list[tuple[int, int]] = []
    seen: set[tuple[int, int]] = set()
    for position in positions:
        pair = pair_of(position, roles)
        if pair not in seen:
            seen.add(pair)
            ranking.append(pair)
    return ranking


def fuse_rankings(
    rankings: dict[str, list[tuple[int, int]]],
    weights: dict[str, float] | None = None,
) -> list[dict[str, Any]]:
    """Reciprocal rank fusion; best first, with each exchange's rank per source.

    ``weights`` counts a source more than once: a ranking with weight 2 adds
    twice what the others add at the same rank.
    """
    fused: dict[tuple[int, int], dict[str, Any]] = {}
    for source, ranking in rankings.items():
        weight = (weights or {}).get(source, 1.0)
        for rank, pair in enumerate(ranking, start=1):
            entry = fused.setdefault(pair, {"pair": pair, "score": 0.0, "ranks": {}})
            entry["score"] += weight / (RRF_K + rank)
            entry["ranks"][source] = rank
    # Ties fall to the earlier exchange, which keeps the order deterministic.
    return sorted(fused.values(), key=lambda e: (-e["score"], e["pair"]))


def select_pairs(
    ranked: list[dict[str, Any]],
    always: list[tuple[int, int]],
    recent: list[tuple[int, int]],
    pair_tokens: Any,
    *,
    top_k: int,
    budget: float,
) -> list[dict[str, Any]]:
    """Choose the exchanges to show, within the budget, in conversation order.

    ``always`` (the best vector hits) are taken first, then the fused ranking
    until ``top_k`` exchanges are chosen, then the most recent exchanges. An
    exchange that would overflow the budget is skipped and the next is tried.
    """
    by_pair = {entry["pair"]: entry for entry in ranked}
    chosen: dict[tuple[int, int], dict[str, Any]] = {}
    used = 0.0

    def take(pair: tuple[int, int], *, recent_pair: bool = False) -> None:
        nonlocal used
        if pair in chosen:
            chosen[pair]["recent"] = chosen[pair]["recent"] or recent_pair
            return
        cost = pair_tokens(pair)
        if used + cost >= budget:
            return
        entry = by_pair.get(pair, {"pair": pair, "score": 0.0, "ranks": {}})
        chosen[pair] = {**entry, "recent": recent_pair}
        used += cost

    for pair in always:
        if len(chosen) < top_k:
            take(pair)
    for entry in ranked:
        if len(chosen) >= top_k:
            break
        take(entry["pair"])
    for pair in recent:
        take(pair, recent_pair=True)
    return sorted(chosen.values(), key=lambda e: e["pair"])


def clauses_of(question: str, *, limit: int = 3) -> list[str]:
    """The parts of a multi-part question, to be searched for one by one.

    "How long between when I rejected the raise and when I rescheduled the
    meeting?" is about two things, and its embedding sits between them. Its
    parts are split at commas and conjunctions; parts shorter than four words
    are dropped, and a question with a single part has no clauses.
    """
    parts = re.split(r"\s*(?:[,;]|\band\b|\bor\b|\bversus\b|\bvs\b)\s*", question)
    kept = [part.strip(" ?.!") for part in parts]
    kept = [part for part in kept if len(part.split()) >= 4]
    return kept[:limit] if len(kept) > 1 else []


_ELAPSED = re.compile(
    r"\bhow (?:long|much time|many (?:day|week|month|year)s?)\b"
    r"|\b(?:day|week|month|year)s? (?:between|from|after|before|until|since|passed|elapsed)\b"
    r"|\btime (?:between|from)\b",
    re.IGNORECASE,
)


def asks_for_elapsed_time(question: str) -> bool:
    """Whether the question asks how much time lies between two things."""
    return bool(_ELAPSED.search(question))


_COUNTING = re.compile(
    r"\bhow many\b"
    r"|\blist (?:all|the|every|each)\b"
    r"|\bwhich (?:\w+ ){0,4}(?:have|did|do|am|was|were) i\b"
    r"|\bwhat (?:are|were) (?:all )?(?:the|my)\b"
    r"|\ball (?:of )?the (?:\w+ ){0,3}(?:i|that i)\b",
    re.IGNORECASE,
)


def asks_to_count_or_list(question: str) -> bool:
    """Whether the question wants things counted or listed, and not time measured.

    A wide net on purpose: it only decides whether the judge is asked at all.
    "How many days between ..." is a question about elapsed time, not a count
    of things.
    """
    return bool(_COUNTING.search(question)) and not asks_for_elapsed_time(question)


def describe_span(days: int) -> str:
    """A number of days in the units a person would use for it."""
    days = abs(days)
    text = f"{days} day{'s' if days != 1 else ''}"
    if days >= 14:
        weeks, rest = divmod(days, 7)
        text += f" ({weeks} weeks" + (
            f" and {rest} day{'s' if rest != 1 else ''})" if rest else ")"
        )
    return text


#: The shapes a question can have, as offered to the judge. "lookup" is first:
#: it is the layout that changes nothing.
QUESTION_SHAPES: dict[str, str] = {
    "lookup": "It asks for a particular fact, detail, value or piece of advice.",
    "elapsed": "It asks how much time lies between two things, or when something is.",
    "order": "It asks in what order things were brought up or happened.",
    "summary": "It asks for a summary or recap of how something went over time.",
}


def _days_between(earlier: str, later: str) -> int | None:
    try:
        return (date.fromisoformat(later) - date.fromisoformat(earlier)).days
    except ValueError:
        return None


# --- Cypher -----------------------------------------------------------------

_MESSAGE_COUNT = """
MATCH (c:Conversation {session_id: $session_id})-[:HAS_MESSAGE]->(m:Message)
RETURN count(m) AS messages
"""

# NULL sorts last in ascending order, so messages written before ``index``
# existed fall back to their timestamps.
_MESSAGE_INDEX = """
MATCH (c:Conversation {session_id: $session_id})-[:HAS_MESSAGE]->(m:Message)
RETURN m.id AS id, m.role AS role, size(m.content) AS chars,
       toString(m.timestamp) AS timestamp
ORDER BY m.index ASC, m.timestamp ASC, m.id ASC
"""

_ENTITY_INDEX = """
MATCH (c:Conversation {session_id: $session_id})-[:HAS_MESSAGE]->(m:Message)
      -[:MENTIONS]->(e:Entity)
RETURN e.id AS id, e.name AS name, e.type AS type, e.subtype AS subtype,
       collect(DISTINCT m.id) AS message_ids
"""

# The vector indexes cover every session, hence the over-fetch ($fetch).
_VECTOR_MESSAGES = """
CALL db.index.vector.queryNodes('message_embedding_idx', $fetch, $embedding)
YIELD node, score
MATCH (c:Conversation {session_id: $session_id})-[:HAS_MESSAGE]->(node)
RETURN node.id AS id, score
ORDER BY score DESC
LIMIT $limit
"""

_VECTOR_ENTITIES = """
CALL db.index.vector.queryNodes('entity_embedding_idx', $fetch, $embedding)
YIELD node, score
RETURN node.id AS id, score
ORDER BY score DESC
"""

_VECTOR_FACTS = """
CALL db.index.vector.queryNodes('fact_embedding_idx', $fetch, $embedding)
YIELD node, score
WHERE node.session_id = $session_id
RETURN node.id AS id, node.text AS text, node.message_index AS message_index,
       node.said_at AS said_at, node.event_date AS event_date, score
ORDER BY score DESC
LIMIT $limit
"""

_VECTOR_DATED_FACTS = """
CALL db.index.vector.queryNodes('fact_embedding_idx', $fetch, $embedding)
YIELD node, score
WHERE node.session_id = $session_id AND node.event_date IS NOT NULL
RETURN node.id AS id, node.text AS text, node.message_index AS message_index,
       node.said_at AS said_at, node.event_date AS event_date, score
ORDER BY score DESC
LIMIT $limit
"""

# A relationship without a probability was decided outright and counts as 1.0.
_FACT_LINKS = """
MATCH (f:Fact) WHERE f.id IN $ids
MATCH (f)-[r:SUPERSEDED_BY|CONTRADICTS]-(o:Fact)
RETURN type(r) AS type, startNode(r).id AS source, endNode(r).id AS target,
       coalesce(r.probability, 1.0) AS probability,
       o.id AS id, o.text AS text, o.message_index AS message_index,
       o.said_at AS said_at, o.event_date AS event_date
"""

_DECISIONS = """
MATCH (d:Decision {session_id: $session_id})
OPTIONAL MATCH (d)-[r:SUPERSEDED_BY]->(n:Decision)
WITH d, max(CASE WHEN coalesce(r.probability, 1.0) >= $min_probability
                 THEN n.message_index END) AS changed_at
RETURN d.id AS id, d.text AS text, d.chosen AS chosen, d.alternatives AS alternatives,
       d.reason AS reason, d.status AS status, d.message_index AS message_index,
       d.decided_on AS said_at, d.effective_date AS event_date, changed_at,
       CASE WHEN d.embedding IS NULL THEN 0.0
            ELSE vector.similarity.cosine(d.embedding, $embedding) END AS score
"""

_PREFERENCES = """
MATCH (p:Preference {session_id: $session_id})
WHERE NOT (p)-[:SUPERSEDED_BY]->()
RETURN p.id AS id, p.preference AS text, coalesce(p.kind, 'preference') AS kind,
       p.message_index AS message_index,
       CASE WHEN p.embedding IS NULL THEN 0.0
            ELSE vector.similarity.cosine(p.embedding, $embedding) END AS score
"""

_SUMMARIES = """
MATCH (s:Summary {session_id: $session_id})
RETURN s.start_index AS start_index, s.end_index AS end_index, s.text AS text,
       toString(s.start_time) AS start_time
ORDER BY s.start_index ASC
"""

_RELATIONS = """
MATCH (a:Entity)-[r:RELATED_TO]->(b:Entity)
WHERE a.id IN $ids OR b.id IN $ids
WITH a, r, b,
     CASE WHEN a.id IN $ids AND b.id IN $ids THEN 1 ELSE 0 END AS both_named
RETURN a.name AS source, r.type AS type, b.name AS target,
       coalesce(r.support, 1) AS support
ORDER BY both_named DESC, support DESC, source ASC, target ASC
LIMIT $limit
"""

_SESSION_RELATIONS = """
MATCH (c:Conversation {session_id: $session_id})-[:HAS_MESSAGE]->(:Message)
      -[:MENTIONS]->(a:Entity)-[:RELATED_TO]-(b:Entity)
RETURN DISTINCT a.id AS a, b.id AS b
"""

# What the user said about given entities, each statement with how near it is
# to the question and, when a later statement replaced it, where.
_ENTITY_STATEMENTS = """
MATCH (e:Entity)<-[:ABOUT]-(f:Fact {session_id: $session_id})
WHERE e.id IN $ids
OPTIONAL MATCH (f)-[r:SUPERSEDED_BY]->(n:Fact)
WITH e, f, max(CASE WHEN coalesce(r.probability, 1.0) >= $min_probability
                    THEN n.message_index END) AS changed_at
RETURN e.id AS entity, f.id AS id, f.text AS text, f.message_index AS message_index,
       f.said_at AS said_at, f.event_date AS event_date, changed_at,
       CASE WHEN f.embedding IS NULL THEN 0.0
            ELSE vector.similarity.cosine(f.embedding, $embedding) END AS score
"""

_MESSAGE_CONTENT = """
MATCH (m:Message) WHERE m.id IN $ids
RETURN m.id AS id, m.content AS content
"""


async def _ask(
    judge: Judge, state: Any, questions: Any, *, what: str, retries: int = 2
) -> dict[str, Any] | None:
    """Ask the judge for a context. Assembly does without a judge that is unavailable."""
    answers = await judge_safely(judge, state, questions, what=what, retries=retries, degrade=True)
    return dict(answers) if answers is not None else None


async def _nothing() -> None:
    """Stands in for a judgement that was not asked for."""
    return None


@dataclass
class _Session:
    """The skeleton of one session: order, roles, sizes, dates, entities."""

    messages: list[dict[str, Any]]
    position: dict[str, int]
    roles: list[str]
    entities: dict[str, dict[str, Any]]
    related: dict[str, set[str]] = field(default_factory=dict)


class ContextAssembler:
    """Builds the context for a question about one session."""

    def __init__(
        self, client: Neo4jClient, embedder: Embedder | None, judge: Judge | None = None
    ) -> None:
        self._client = client
        self._embedder = embedder
        self._judge = judge
        self._sessions: dict[str, _Session] = {}

    async def _session(self, session_id: str) -> _Session:
        """The session's skeleton, re-read when its message count has changed."""
        rows = await self._client.execute_read(_MESSAGE_COUNT, {"session_id": session_id})
        count = rows[0]["messages"] if rows else 0
        cached = self._sessions.get(session_id)
        if cached is not None and len(cached.messages) == count:
            return cached

        messages = await self._client.execute_read(_MESSAGE_INDEX, {"session_id": session_id})
        position = {row["id"]: i for i, row in enumerate(messages)}
        entities: dict[str, dict[str, Any]] = {}
        for row in await self._client.execute_read(_ENTITY_INDEX, {"session_id": session_id}):
            label = row["type"] or "ENTITY"
            if row.get("subtype"):
                label = f"{label}:{row['subtype']}"
            entities[row["id"]] = {
                "name": row["name"],
                "type": label,
                "mentions": {position[mid] for mid in row["message_ids"] if mid in position},
            }
        related: dict[str, set[str]] = {}
        for row in await self._client.execute_read(_SESSION_RELATIONS, {"session_id": session_id}):
            if row["a"] in entities and row["b"] in entities:
                related.setdefault(row["a"], set()).add(row["b"])
                related.setdefault(row["b"], set()).add(row["a"])
        session = _Session(
            messages=messages,
            position=position,
            roles=[row["role"] for row in messages],
            entities=entities,
            related=related,
        )
        self._sessions[session_id] = session
        return session

    def forget(self, session_id: str | None = None) -> None:
        """Drop the cached skeleton of one session, or of all of them."""
        if session_id is None:
            self._sessions.clear()
        else:
            self._sessions.pop(session_id, None)

    async def assemble(
        self,
        query: str,
        *,
        session_id: str,
        token_budget: int = 16000,
        chars_per_token: float = 4.0,
        top_k: int = 10,
        vector_floor: int = 3,
        recent_pairs: int = 2,
        max_entities: int = 10,
        max_statements: int = 25,
        max_preferences: int = 20,
        max_instructions: int = 40,
        max_relations: int = 15,
        max_decisions: int = 10,
        conflict_depth: int = 8,
        max_timeline: int = 8,
        relevant_instructions: int = 3,
        min_update_probability: float = 0.75,
        min_conflict_probability: float = 0.5,
        include_guidance: bool = True,
        include_outline: bool = True,
        gate: bool = True,
        gate_threshold: float = 0.5,
        rerank: bool = True,
        rerank_candidates: int = 40,
        rerank_weight: float = 3.0,
        rerank_chars: int = 3000,
        resolve_dates: bool = True,
        date_threshold: float = 0.5,
        route: bool = False,
        broad_top_k: int = 24,
        broad_message_chars: int = 700,
        graph_walk: str = "hop",
        entity_statements: int = 0,
        judge_entities: bool = False,
        enumerate_entities: bool = False,
        max_enumerated: int = 60,
    ) -> AssembledContext:
        """Assemble the context for ``query`` from the memory of ``session_id``.

        Args:
            query: The question to be answered.
            session_id: The session whose memory is read.
            token_budget: Upper bound on the size of the context, in tokens
                estimated as characters / ``chars_per_token``.
            top_k: User/assistant exchanges chosen by relevance.
            vector_floor: The best vector hits that are always among them.
            recent_pairs: Most recent exchanges added on top.
            max_entities: Entities taken from entity vector search.
            max_statements: Statements matched to the question.
            max_preferences: Preferences shown, by similarity.
            max_instructions: Standing instructions shown. A session rarely
                has more, and then all are shown; beyond that the nearest to
                the question are kept.
            max_relations: Relationships between the question's entities shown.
            max_decisions: Decisions of the user shown.
            conflict_depth: Without a judge, a contradiction is reported when
                one of its two statements is among this many best-matching
                statements.
            max_timeline: Dated statements on the timeline: the closest ones
                to the question, also from beyond the ``max_statements`` shown.
            relevant_instructions: Without a judge, the standing instructions
                closest to the question that are repeated at the very end.
            min_update_probability: A statement counts as replaced when the
                relationship that says so has at least this probability.
            min_conflict_probability: The same for contradictions.
            include_guidance: Close with a short note on how to use the memory
                (follow instructions, surface contradictions, prefer the latest
                value, say when nothing is known).
            include_outline: Include the outline of the whole conversation.

        The remaining options take effect only with a judge
        (:class:`~neo4j_agent_memory.judge.Judge`), which is asked a few closed
        questions per query:

        Args:
            gate: Ask which contradictions, standing instructions and decisions
                bear on the question; only those at ``gate_threshold`` or more
                are reported as contradictions, repeated as applicable
                instructions, and listed as decisions.
            rerank: Ask, for each of the ``rerank_candidates`` best exchanges
                (their messages cut to ``rerank_chars``), whether it holds
                information the answer needs, and fuse that ranking with the
                others at ``rerank_weight`` times their weight.
            resolve_dates: For a question about the time between two things,
                ask which dated statements they are; when both are found with
                probability ``date_threshold`` or more, the span is computed
                and stated under the timeline.
            route: Ask what shape the question has. A question about the order
                of things, or for a summary, is given ``broad_top_k`` exchanges
                with each message cut to ``broad_message_chars`` characters, in
                place of fewer exchanges in full. Off by default: on the BEAM
                benchmark the wider layout found more of the evidence for such
                questions without improving the answers.

        Four further options change how the entity graph is used:

        Args:
            graph_walk: How messages are ranked from the question's entities.
                ``"hop"`` scores the messages that mention them. ``"walk"``
                runs a random walk with restart over mentions and
                relationships (:func:`walk_message_scores`), which also
                reaches messages that mention none of them.
            entity_statements: When positive, go from the entities the question
                names to what the user said about them (``ABOUT``) and show up
                to this many statements per entity that the statement search
                did not already find, nearest to the question first.
            judge_entities: With a judge, ask which of the candidate entities
                the question is about, and keep only those.
            enumerate_entities: With a judge, and for a question worded as a
                count or a list (:func:`asks_to_count_or_list`), ask whether it
                wants things of one kind counted or listed, and of which kind
                (the entity types of the session). The entities of that kind which
                the user mentioned are then judged one by one, up to
                ``max_enumerated``, and those that fit are listed with where
                they came up.

        Returns:
            The context text and a record of what went into it.
        """
        session = await self._session(session_id)
        count = len(session.messages)
        if count == 0 or self._embedder is None:
            return AssembledContext(text="", details={"strategy": "hybrid", "messages": count})
        judge = self._judge

        def tokens(text: str) -> float:
            return len(text) / chars_per_token

        clauses = clauses_of(query)
        embeddings = await self._embedder.embed_batch([query, *clauses])
        embedding = embeddings[0]

        # --- what the memory knows that bears on the question -----------------
        preference_rows = await self._client.execute_read(
            _PREFERENCES, {"session_id": session_id, "embedding": embedding}
        )
        instructions = sorted(
            (p for p in preference_rows if p["kind"] == "instruction"),
            key=lambda p: -p["score"],
        )[:max_instructions]
        preferences = sorted(
            (p for p in preference_rows if p["kind"] != "instruction"),
            key=lambda p: -p["score"],
        )[:max_preferences]

        fact_hits = await self._client.execute_read(
            _VECTOR_FACTS,
            {
                "embedding": embedding,
                "session_id": session_id,
                "fetch": (max_statements + 2 * CANDIDATES) * 4,
                "limit": max_statements + 2 * CANDIDATES,
            },
        )
        if clauses:
            # Statements are also searched for each part of the question, and
            # the rankings fused, so that each part is represented.
            rankings_of_facts = [fact_hits]
            for clause_embedding in embeddings[1:]:
                rankings_of_facts.append(
                    await self._client.execute_read(
                        _VECTOR_FACTS,
                        {
                            "embedding": clause_embedding,
                            "session_id": session_id,
                            "fetch": CANDIDATES * 4,
                            "limit": CANDIDATES,
                        },
                    )
                )
            by_id: dict[str, dict[str, Any]] = {}
            fused_score: dict[str, float] = {}
            for ranking in rankings_of_facts:
                for rank, fact in enumerate(ranking, start=1):
                    by_id.setdefault(fact["id"], fact)
                    fused_score[fact["id"]] = fused_score.get(fact["id"], 0.0) + 1.0 / (
                        RRF_K + rank
                    )
            fact_hits = sorted(by_id.values(), key=lambda f: -fused_score[f["id"]])
        shown_facts = {f["id"]: dict(f) for f in fact_hits[:max_statements]}
        fact_rank = {f["id"]: rank for rank, f in enumerate(fact_hits)}

        decision_rows = sorted(
            await self._client.execute_read(
                _DECISIONS,
                {
                    "session_id": session_id,
                    "embedding": embedding,
                    "min_probability": min_update_probability,
                },
            ),
            key=lambda d: -d["score"],
        )

        # The timeline also takes dated statements from further down the
        # ranking: a question about the time between two events rarely
        # resembles both of them equally.
        # It is kept short: the closest few dated statements, whichever part
        # of the ranking they come from. Every extra entry is one more date
        # the reader can pick by mistake.
        dated_hits = [dict(f) for f in fact_hits if f.get("event_date")]
        timeline_facts = dated_hits[:max_timeline]
        links = (
            await self._client.execute_read(_FACT_LINKS, {"ids": list(shown_facts)})
            if shown_facts
            else []
        )
        replaced_by: dict[str, str] = {}
        # A denial ("has never ...") and the statements it conflicts with.
        conflicts: dict[str, list[str]] = {}
        conflict_probability: dict[str, float] = {}
        for link in links:
            threshold = (
                min_update_probability
                if link["type"] == "SUPERSEDED_BY"
                else min_conflict_probability
            )
            if link.get("probability", 1.0) < threshold:
                continue
            shown_facts.setdefault(
                link["id"],
                {
                    "id": link["id"],
                    "text": link["text"],
                    "message_index": link["message_index"],
                    "said_at": link["said_at"],
                    "event_date": link["event_date"],
                    "score": 0.0,
                },
            )
            if link["type"] == "SUPERSEDED_BY":
                replaced_by[link["source"]] = link["target"]
            elif link["target"] not in conflicts.setdefault(link["source"], []):
                conflicts[link["source"]].append(link["target"])
                # How sure the conflict is ranks the statements shown for a denial.
                conflict_probability[link["target"]] = max(
                    conflict_probability.get(link["target"], 0.0), link.get("probability", 1.0)
                )

        summaries = (
            await self._client.execute_read(_SUMMARIES, {"session_id": session_id})
            if include_outline
            else []
        )

        # --- the question's entities ------------------------------------------
        question_entities: dict[str, dict[str, Any]] = {}
        entity_rows = await self._client.execute_read(
            _VECTOR_ENTITIES, {"embedding": embedding, "fetch": max_entities * 3}
        )
        for row in entity_rows:
            if row["id"] in session.entities and len(question_entities) < max_entities:
                question_entities[row["id"]] = {
                    "relevance": float(row["score"]),
                    "source": "vector",
                }
        names = {entity_id: e["name"] for entity_id, e in session.entities.items()}
        for entity_id in entities_named_in(query, names):
            source = "name+vector" if entity_id in question_entities else "name"
            question_entities[entity_id] = {"relevance": 1.0, "source": source}
        if judge_entities and judge is not None and question_entities:
            question_entities = await self._judge_entities(query, session, question_entities)

        # What the user said about those entities, beyond what resembles the
        # question: the statements are reached through the graph.
        entity_facts: dict[str, list[dict[str, Any]]] = {}
        if entity_statements > 0 and any("name" in e["source"] for e in question_entities.values()):
            # Only the entities the question names, the least mentioned first:
            # an entity that is merely near the question in embedding space
            # leads to statements that have nothing to do with it.
            seeds = sorted(
                (eid for eid, e in question_entities.items() if "name" in e["source"]),
                key=lambda eid: (len(session.entities[eid]["mentions"]), eid),
            )[:5]
            rows = await self._client.execute_read(
                _ENTITY_STATEMENTS,
                {
                    "ids": seeds,
                    "session_id": session_id,
                    "embedding": embedding,
                    "min_probability": min_update_probability,
                },
            )
            taken: set[str] = set(shown_facts)
            for seed in seeds:
                about = sorted(
                    (r for r in rows if r["entity"] == seed and r["id"] not in taken),
                    key=lambda r: -r["score"],
                )[:entity_statements]
                if about:
                    entity_facts[seed] = sorted(about, key=lambda r: (r["message_index"], r["id"]))
                    taken.update(r["id"] for r in about)
        relations = (
            await self._client.execute_read(
                _RELATIONS, {"ids": list(question_entities), "limit": max_relations}
            )
            if question_entities and max_relations > 0
            else []
        )

        # --- rank the exchanges -----------------------------------------------
        vector_rows = await self._client.execute_read(
            _VECTOR_MESSAGES,
            {
                "embedding": embedding,
                "session_id": session_id,
                "fetch": CANDIDATES * 4,
                "limit": CANDIDATES,
            },
        )
        vector_positions = [
            session.position[r["id"]] for r in vector_rows if r["id"] in session.position
        ]
        if graph_walk == "walk":
            graph_scores = walk_message_scores(
                {eid: e["relevance"] for eid, e in question_entities.items()},
                {eid: e["mentions"] for eid, e in session.entities.items()},
                session.related,
            )
        else:
            graph_scores = graph_message_scores(
                {eid: e["relevance"] for eid, e in question_entities.items()},
                {eid: session.entities[eid]["mentions"] for eid in question_entities},
                count,
            )
        graph_positions = sorted(graph_scores, key=lambda p: (-graph_scores[p], p))[:CANDIDATES]
        statement_positions = [
            f["message_index"]
            for f in fact_hits[:CANDIDATES]
            if f["message_index"] is not None and 0 <= f["message_index"] < count
        ]
        # Statements reached through the question's entities rank their
        # messages too, nearest to the question first.
        entity_statement_positions = [
            f["message_index"]
            for f in sorted(
                (f for facts in entity_facts.values() for f in facts), key=lambda f: -f["score"]
            )
            if 0 <= f["message_index"] < count
        ]
        # Decisions are few, so only those that resemble the question rank.
        decision_positions = [
            d["message_index"]
            for d in decision_rows[:CANDIDATES]
            if d["score"] >= 0.72 and 0 <= d["message_index"] < count
        ]
        vector_ranking = to_pair_ranking(vector_positions, session.roles)
        rankings = {
            "vector": vector_ranking,
            "graph": to_pair_ranking(graph_positions, session.roles),
            "statements": to_pair_ranking(statement_positions, session.roles),
            "decisions": to_pair_ranking(decision_positions, session.roles),
            "entity_statements": to_pair_ranking(entity_statement_positions, session.roles),
        }
        rankings = {name: r for name, r in rankings.items() if r}
        ranked = fuse_rankings(rankings)

        # --- ask the judge ----------------------------------------------------
        # Contradictions that could be reported: those with a statement among
        # the ones shown, nearest the question first.
        def nearness(denial: str) -> int:
            ids = [denial, *conflicts[denial]]
            return min(fact_rank.get(i, len(fact_hits)) for i in ids)

        candidate_conflicts = sorted(
            (d for d, others in conflicts.items() if d in shown_facts and others), key=nearness
        )[:conflict_depth]
        candidate_decisions = decision_rows[: max(max_decisions, 15)]
        elapsed = asks_for_elapsed_time(query)
        date_candidates: list[dict[str, Any]] = []
        if elapsed:
            # The two things asked about are searched for among the dated
            # statements only, one search per part of the question: in the
            # ranking of all statements a dated one is easily crowded out.
            dated_rankings = [
                await self._client.execute_read(
                    _VECTOR_DATED_FACTS,
                    {
                        "embedding": part,
                        "session_id": session_id,
                        "fetch": 400,
                        "limit": 8,
                    },
                )
                for part in (embeddings[1:] or [embedding])
            ]
            date_candidates = self._date_candidates([*dated_rankings, dated_hits], decision_rows)
            # The timeline shows the same statements the judge chooses among.
            timeline_facts = date_candidates[:max_timeline]
        judged_gate, judged_pairs, judged_dates = await asyncio.gather(
            self._judge_gate(
                query,
                instructions if gate else [],
                [
                    (shown_facts[d], [shown_facts[o] for o in conflicts[d]])
                    for d in candidate_conflicts
                ]
                if gate
                else [],
                candidate_decisions if gate else [],
                route=route,
                kinds=self._kinds(session)
                if enumerate_entities and asks_to_count_or_list(query)
                else {},
            ),
            self._judge_pairs(
                query, session, [e["pair"] for e in ranked[:rerank_candidates]], rerank_chars
            )
            if rerank
            else _nothing(),
            self._judge_dates(query, date_candidates)
            if resolve_dates and len(date_candidates) >= 2
            else _nothing(),
        )

        shape = "lookup"
        if judged_gate is not None and "shape" in judged_gate:
            shape = judged_gate["shape"].choice or "lookup"

        # Things of one kind that the question wants counted or listed.
        enumerated: dict[str, Any] | None = None
        if judged_gate is not None and "kind" in judged_gate:
            answer = judged_gate["kind"]
            if (answer.choice or "none") != "none" and answer.probability >= gate_threshold:
                enumerated = await self._enumerate(
                    query, session, str(answer.choice), max_enumerated, gate_threshold
                )
        broad = shape in ("order", "summary")

        # Contradictions: with a judge, those it says the question is about;
        # without, those next to the question in the statement ranking.
        if judged_gate is not None and gate:
            reported_denials = [
                d
                for k, d in enumerate(candidate_conflicts)
                if judged_gate[f"c{k}"].probability >= gate_threshold
            ]
        else:
            near_question = {f["id"] for f in fact_hits[:conflict_depth]}
            reported_denials = [
                d
                for d in candidate_conflicts
                if d in near_question or near_question & set(conflicts[d])
            ]
        reported = {d: [o for o in conflicts[d] if o in shown_facts] for d in reported_denials}
        for fact_id, probability in conflict_probability.items():
            if fact_id in shown_facts:
                shown_facts[fact_id]["conflict_probability"] = probability

        # Instructions repeated at the end: those that apply, or the closest.
        if judged_gate is not None and gate:
            applicable = sorted(
                (
                    (judged_gate[f"i{k}"].probability, item)
                    for k, item in enumerate(instructions)
                    if judged_gate[f"i{k}"].probability >= gate_threshold
                ),
                key=lambda x: -x[0],
            )
            closest = [item for _, item in applicable]
            closing = (
                "The user's standing instructions that apply to this question. "
                "Follow each of them in the answer:"
            )
        else:
            closest = sorted(instructions, key=lambda p: -p["score"])[:relevant_instructions]
            closing = (
                "The user's standing instructions closest to this question. "
                "Apply each one that fits it:"
            )

        # Decisions: those that bear on the question.
        if judged_gate is not None and gate:
            decisions = [
                d
                for k, d in enumerate(candidate_decisions)
                if judged_gate[f"d{k}"].probability >= gate_threshold
            ][:max_decisions]
        else:
            decisions = [d for d in candidate_decisions if d["score"] >= 0.72][:max_decisions]

        # Exchanges the judge finds useful join the fusion as a ranking.
        pair_probability: dict[tuple[int, int], float] = judged_pairs or {}
        if pair_probability:
            rankings["judge"] = [
                pair
                for pair, probability in sorted(
                    pair_probability.items(), key=lambda kv: (-kv[1], kv[0])
                )
                if probability >= 0.3
            ]
            if rankings["judge"]:
                ranked = fuse_rankings(rankings, {"judge": rerank_weight})

        # The span between the two events a question asks about.
        span = self._span(date_candidates, judged_dates, date_threshold)
        if span is not None:
            on_timeline = {f["id"] for f in timeline_facts}
            timeline_facts += [e for e in span["events"] if e["id"] not in on_timeline]

        # --- render what is fixed, then spend the rest on excerpts ------------
        # The excerpts come first and the distilled memory after them, closest
        # to the question: what must not be missed goes last.
        sections = [
            self._render_entities(session, question_entities, relations),
            self._render_enumeration(enumerated),
            self._render_outline(
                summaries,
                count,
                max_chars=0.25 * token_budget * chars_per_token,
                # In a conversation too long to outline in full, the stretches
                # shown are those around what the rankings point at.
                near=[p for entry in ranked[:CANDIDATES] for p in entry["pair"]]
                + [f["message_index"] for f in shown_facts.values()]
                + [d["message_index"] for d in decisions],
            ),
            self._render_statements(shown_facts, replaced_by),
            self._render_entity_statements(session, entity_facts),
            self._render_decisions(decisions),
            self._render_timeline(timeline_facts, replaced_by, span, between=elapsed),
            self._render_preferences(instructions, preferences),
            self._render_conflicts(shown_facts, reported),
        ]
        if include_guidance:
            sections.append(GUIDANCE)
        if closest:
            sections.append("\n".join([closing] + [f"- {item['text']}" for item in closest]))
        fixed = "\n\n".join(s for s in sections if s)

        # A question about the order of things, or for a summary, is better
        # served by many exchanges cut short than by a few in full.
        message_chars = broad_message_chars if broad else None

        def pair_tokens(pair: tuple[int, int]) -> float:
            chars = sum(
                min(session.messages[p]["chars"], message_chars or session.messages[p]["chars"])
                for p in range(pair[0], pair[1] + 1)
            )
            return float((chars + 120) / chars_per_token)  # header and role labels

        recent: list[tuple[int, int]] = []
        position = count - 1
        while position >= 0 and len(recent) < recent_pairs:
            pair = pair_of(position, session.roles)
            recent.append(pair)
            position = pair[0] - 1

        chosen = select_pairs(
            ranked,
            vector_ranking[:vector_floor],
            recent,
            pair_tokens,
            top_k=max(top_k, broad_top_k) if broad else top_k,
            budget=token_budget - tokens(fixed) - 200,
        )
        excerpts = await self._render_excerpts(session, chosen, message_chars)
        text = "\n\n".join(part for part in (excerpts, fixed) if part)

        details: dict[str, Any] = {
            "strategy": "hybrid",
            "top_k": top_k,
            "estimated_tokens": round(tokens(text)),
            "instructions": len(instructions),
            "preferences": len(preferences),
            "clauses": clauses,
            "statements": len(shown_facts),
            "statement_messages": sorted({f["message_index"] + 1 for f in shown_facts.values()}),
            "replaced": len(replaced_by),
            "conflicts": sum(1 for others in reported.values() if others),
            "decisions": len(decisions),
            "decision_messages": sorted({d["message_index"] + 1 for d in decisions}),
            "summaries": len(summaries),
            "question_entities": [
                {
                    "name": session.entities[eid]["name"],
                    "type": session.entities[eid]["type"],
                    "source": e["source"],
                    "relevance": round(e["relevance"], 4),
                    "mentions": len(session.entities[eid]["mentions"]),
                }
                for eid, e in sorted(question_entities.items(), key=lambda kv: -kv[1]["relevance"])
            ],
            "relations": len(relations),
            "graph_walk": graph_walk,
            "entity_statements": {
                session.entities[eid]["name"]: len(facts) for eid, facts in entity_facts.items()
            },
            "enumerated": (
                {
                    "kind": enumerated["kind"],
                    "judged": enumerated["judged"],
                    "listed": [item["name"] for item in enumerated["items"]],
                }
                if enumerated is not None
                else None
            ),
            "pairs": [
                {
                    "messages": [p["pair"][0] + 1, p["pair"][1] + 1],
                    "ranks": p["ranks"],
                    "recent": p["recent"],
                    **(
                        {"judged": round(pair_probability[p["pair"]], 3)}
                        if p["pair"] in pair_probability
                        else {}
                    ),
                }
                for p in chosen
            ],
        }
        if judge is not None:
            details["judge"] = {
                "name": judge.name,
                "shape": shape,
                "candidate_conflicts": len(candidate_conflicts),
                "applicable_instructions": len(closest) if judged_gate is not None else None,
                "pairs_judged": len(pair_probability),
                "span": (
                    {
                        "days": span["days"],
                        "from": span["events"][0]["event_date"],
                        "to": span["events"][1]["event_date"],
                        "probabilities": span["probabilities"],
                    }
                    if span is not None
                    else None
                ),
            }
        return AssembledContext(text=text, details=details)

    # -- judging --------------------------------------------------------------

    async def _judge_gate(
        self,
        query: str,
        instructions: list[dict[str, Any]],
        contradictions: list[tuple[dict[str, Any], list[dict[str, Any]]]],
        decisions: list[dict[str, Any]],
        *,
        route: bool,
        kinds: dict[str, str] | None = None,
    ) -> dict[str, Any] | None:
        """One request: what bears on the question, and what shape it has.

        Question names: ``i<k>`` per instruction, ``c<k>`` per contradiction,
        ``d<k>`` per decision, ``shape``, and ``kind`` (which of ``kinds``, a
        mapping of kind to description, the question wants counted or listed).
        """
        if self._judge is None:
            return None
        questions: dict[str, Question] = {}
        state: dict[str, Any] = {"question": query}
        if instructions:
            state["instructions"] = [item["text"] for item in instructions]
            for k in range(len(instructions)):
                questions[f"i{k}"] = YesNo(
                    f"Should the assistant apply `instructions[{k}]` when it answers `question`?",
                    yes="The instruction covers this kind of question or the kind of "
                    "content its answer will have.",
                    no="The instruction is about another kind of question or content.",
                )
        if contradictions:
            state["contradictions"] = [
                {"user_said": denial["text"], "but_also_said": [o["text"] for o in others[:2]]}
                for denial, others in contradictions
            ]
            for k in range(len(contradictions)):
                questions[f"c{k}"] = YesNo(
                    "Does `question` ask whether the user has done, used or had the thing "
                    f"that `contradictions[{k}].user_said` says they never did?",
                    yes="The question asks about the user's own experience with that very thing.",
                    no="The question asks for something else, even if on a related subject.",
                )
        if decisions:
            state["decisions"] = [d["text"] for d in decisions]
            for k in range(len(decisions)):
                questions[f"d{k}"] = YesNo(
                    f"Does the choice in `decisions[{k}]` matter for answering `question`?",
                    yes="The answer depends on it, or should take it into account.",
                    no="It is about something the question does not touch.",
                )
        if route:
            questions["shape"] = OneOf("What does `question` ask for?", QUESTION_SHAPES)
        if kinds:
            questions["kind"] = OneOf(
                "Does `question` ask to count or list things of one kind that came up in "
                "the conversation, and if so, things of which kind?",
                {
                    "none": "It does not ask to count or list things, or the things are "
                    "of none of these kinds.",
                    **kinds,
                },
            )
        if not questions:
            return None
        return await _ask(self._judge, state, questions, what="context gate")

    @staticmethod
    def _user_mentions(session: _Session, entity: dict[str, Any]) -> list[int]:
        """Where the user, not the assistant, mentioned an entity."""
        return sorted(p for p in entity["mentions"] if session.roles[p] == "user")

    def _kinds(self, session: _Session, *, limit: int = 24) -> dict[str, str]:
        """The kinds of thing the user mentioned in a session, each with examples."""
        members: dict[str, list[tuple[int, str]]] = {}
        for entity in session.entities.values():
            mentions = len(self._user_mentions(session, entity))
            if mentions:
                members.setdefault(kind_of(entity["type"]), []).append((mentions, entity["name"]))
        kinds = sorted(members.items(), key=lambda kv: -len(kv[1]))[:limit]
        return {
            kind: f"{len(found)} in the conversation, such as "
            + ", ".join(name for _, name in sorted(found, key=lambda x: -x[0])[:4])
            for kind, found in kinds
            if len(found) >= 2 and kind != "none"
        }

    async def _enumerate(
        self,
        query: str,
        session: _Session,
        kind: str,
        limit: int,
        threshold: float,
    ) -> dict[str, Any] | None:
        """The entities of one kind that the question asks to count or list.

        The candidates are the entities of that kind the user mentioned, most
        mentioned first; the judge says of each whether it is one of the things
        asked for.
        """
        if self._judge is None:
            return None
        candidates = sorted(
            (
                {"name": e["name"], "mentions": self._user_mentions(session, e)}
                for e in session.entities.values()
                if kind_of(e["type"]) == kind
            ),
            key=lambda c: (-len(c["mentions"]), c["name"]),
        )
        candidates = [c for c in candidates if c["mentions"]][:limit]
        if not candidates:
            return None
        answers = await _ask(
            self._judge,
            {"question": query, "things": [c["name"] for c in candidates]},
            {
                f"t{i}": YesNo(
                    f"Is `things[{i}]` one of the things that `question` asks to count or list?",
                    yes="It is a thing of the sort asked for, within what the question is about.",
                    no="It is of another sort, or outside what the question is about.",
                )
                for i in range(len(candidates))
            },
            what="enumeration",
        )
        if answers is None:
            return None
        items = [
            {**c, "probability": answers[f"t{i}"].probability}
            for i, c in enumerate(candidates)
            if answers[f"t{i}"].probability >= threshold
        ]
        return {"kind": kind, "judged": len(candidates), "items": items}

    async def _judge_entities(
        self,
        query: str,
        session: _Session,
        candidates: dict[str, dict[str, Any]],
        *,
        threshold: float = 0.5,
    ) -> dict[str, dict[str, Any]]:
        """Of the candidate entities, those the judge says the question is about.

        When the judge keeps none, or cannot be asked, the candidates stand.
        """
        if self._judge is None:
            return candidates
        ids = list(candidates)
        answers = await _ask(
            self._judge,
            {
                "question": query,
                "entities": [
                    {
                        "name": session.entities[i]["name"],
                        "kind": kind_of(session.entities[i]["type"]),
                    }
                    for i in ids
                ],
            },
            {
                f"e{k}": YesNo(
                    f"Is `entities[{k}]` something that `question` is about?",
                    yes="The question names it, or asks about it.",
                    no="The question is about other things, even if on a related subject.",
                )
                for k in range(len(ids))
            },
            what="question entities",
        )
        if answers is None:
            return candidates
        kept = {
            entity_id: {**candidates[entity_id], "judged": answers[f"e{k}"].probability}
            for k, entity_id in enumerate(ids)
            if answers[f"e{k}"].probability >= threshold
        }
        return kept or candidates

    async def _judge_pairs(
        self,
        query: str,
        session: _Session,
        pairs: list[tuple[int, int]],
        max_chars: int,
    ) -> dict[tuple[int, int], float] | None:
        """For each exchange, the probability that the answer needs it.

        One request per exchange, so that each is judged against a small state.
        """
        if self._judge is None or not pairs:
            return None
        judge = self._judge
        positions = sorted({p for first, last in pairs for p in range(first, last + 1)})
        rows = await self._client.execute_read(
            _MESSAGE_CONTENT, {"ids": [session.messages[p]["id"] for p in positions]}
        )
        content = {session.position[r["id"]]: r["content"] for r in rows}
        question = YesNo(
            "Does `exchange` contain information that an answer to `question` needs?",
            yes="It states a fact, figure, date, choice, instruction or event that the "
            "answer depends on or should mention.",
            no="It is about something else, or only shares a subject with the question.",
        )

        async def one(pair: tuple[int, int]) -> float | None:
            exchange = {
                session.messages[p]["role"]: (content.get(p) or "")[:max_chars]
                for p in range(pair[0], pair[1] + 1)
            }
            answers = await _ask(
                judge,
                {"question": query, "exchange": exchange},
                {"needed": question},
                what="exchange relevance",
                retries=1,
            )
            return answers["needed"].probability if answers is not None else None

        probabilities = await asyncio.gather(*(one(pair) for pair in pairs))
        return {pair: p for pair, p in zip(pairs, probabilities, strict=True) if p is not None}

    @staticmethod
    def _date_candidates(
        rankings: list[list[dict[str, Any]]],
        decisions: list[dict[str, Any]],
        *,
        limit: int = 12,
    ) -> list[dict[str, Any]]:
        """Dated statements a question about elapsed time may be about.

        The rankings are read in turn, one statement from each, so that every
        part of the question contributes its best matches. A statement that
        was later replaced stays a candidate: what it reports did happen, or
        was the plan, at its time.
        """
        candidates: dict[str, dict[str, Any]] = {}
        depth = max((len(r) for r in rankings), default=0)
        for rank in range(depth):
            for ranking in rankings:
                if rank < len(ranking) and len(candidates) < limit - 2:
                    fact = ranking[rank]
                    candidates.setdefault(fact["id"], dict(fact))
        for decision in decisions:
            dated = decision.get("event_date") and decision.get("changed_at") is None
            if dated and decision["score"] >= 0.72 and len(candidates) < limit:
                candidates.setdefault(decision["id"], dict(decision))
        return list(candidates.values())

    async def _judge_dates(
        self, query: str, candidates: list[dict[str, Any]]
    ) -> dict[str, Any] | None:
        """Which dated statements a question about elapsed time refers to.

        Each entry is asked about on its own, twice (``a<i>``: is it the first
        thing, ``b<i>``: is it the second), so that the judge can say no to
        all of them when the thing asked about is not among the entries.
        """
        if self._judge is None:
            return None
        questions: dict[str, Question] = {}
        for i in range(len(candidates)):
            questions[f"a{i}"] = YesNo(
                "`question` asks about the time between two things. Is "
                f"`entries[{i}]` the first of the two things it names?",
                yes="The entry is that event or deadline itself.",
                no="The entry is something else, or the second of the two.",
            )
            questions[f"b{i}"] = YesNo(
                "`question` asks about the time between two things. Is "
                f"`entries[{i}]` the second of the two things it names?",
                yes="The entry is that event or deadline itself.",
                no="The entry is something else, or the first of the two.",
            )
        return await _ask(
            self._judge,
            {
                "question": query,
                "entries": [{"what": c["text"], "date": c["event_date"]} for c in candidates],
            },
            questions,
            what="date resolution",
        )

    @staticmethod
    def _span(
        candidates: list[dict[str, Any]],
        judged: dict[str, Any] | None,
        threshold: float,
    ) -> dict[str, Any] | None:
        """The days between the two entries the judge is sure of.

        The pair with the highest joint probability is taken, among entries
        that are each at ``threshold`` or more and carry different dates.
        """
        if judged is None:
            return None
        firsts = [
            (judged[f"a{i}"].probability, i)
            for i in range(len(candidates))
            if judged[f"a{i}"].probability >= threshold
        ]
        seconds = [
            (judged[f"b{i}"].probability, i)
            for i in range(len(candidates))
            if judged[f"b{i}"].probability >= threshold
        ]
        best: tuple[float, int, int] | None = None
        for p_first, i in firsts:
            for p_second, j in seconds:
                if i == j or candidates[i]["event_date"] == candidates[j]["event_date"]:
                    continue
                if best is None or p_first * p_second > best[0]:
                    best = (p_first * p_second, i, j)
        if best is None:
            return None
        _, i, j = best
        days = _days_between(candidates[i]["event_date"], candidates[j]["event_date"])
        if days is None:
            return None
        return {
            "events": [candidates[i], candidates[j]],
            "days": days,
            "probabilities": [
                round(judged[f"a{i}"].probability, 3),
                round(judged[f"b{j}"].probability, 3),
            ],
        }

    # -- rendering ------------------------------------------------------------

    @staticmethod
    def _where(fact: dict[str, Any]) -> str:
        where = f"#{fact['message_index'] + 1}"
        return f"{where}, {fact['said_at']}" if fact.get("said_at") else where

    @staticmethod
    def _render_preferences(
        instructions: list[dict[str, Any]], preferences: list[dict[str, Any]]
    ) -> str:
        if not instructions and not preferences:
            return ""
        lines = ["## The user's standing instructions and preferences"]
        if instructions:
            lines.append("Instructions the user gave for all later answers:")
            for item in sorted(instructions, key=lambda p: p["message_index"]):
                lines.append(f"- {item['text']} (#{item['message_index'] + 1})")
        if preferences:
            lines.append("Preferences the user has expressed:")
            for item in sorted(preferences, key=lambda p: p["message_index"]):
                lines.append(f"- {item['text']} (#{item['message_index'] + 1})")
        return "\n".join(lines)

    def _render_conflicts(
        self,
        facts: dict[str, dict[str, Any]],
        conflicts: dict[str, list[str]],
        *,
        per_denial: int = 3,
    ) -> str:
        entries = [
            (
                facts[denial],
                sorted(
                    (facts[o] for o in others),
                    key=lambda f: (-f.get("conflict_probability", 1.0), -f.get("score", 0.0)),
                ),
            )
            for denial, others in conflicts.items()
            if others
        ]
        if not entries:
            return ""
        lines = [
            "## Contradictions in what the user has said",
            "On each of these the user has said two things that cannot both be true, "
            "and the conversation does not settle which one is right:",
        ]
        seen: set[str] = set()
        for denial, others in sorted(entries, key=lambda e: e[0]["message_index"]):
            if denial["text"] in seen:  # the same denial, repeated in a later message
                continue
            seen.add(denial["text"])
            against = "; and ".join(
                f'(at {self._where(other)}) "{other["text"]}"' for other in others[:per_denial]
            )
            lines.append(
                f'- The user said (at {self._where(denial)}): "{denial["text"]}" '
                f"But the user also said {against}"
            )
        return "\n".join(lines)

    def _render_statements(
        self, facts: dict[str, dict[str, Any]], replaced_by: dict[str, str]
    ) -> str:
        if not facts:
            return ""
        replaces = {new: old for old, new in replaced_by.items() if old in facts and new in facts}
        lines = [
            "## What the user has said",
            "Statements from the user that relate to the question, in the order they were made.",
        ]
        for fact in sorted(facts.values(), key=lambda f: (f["message_index"], f["id"])):
            line = f"- [{self._where(fact)}] {fact['text']}"
            newer = replaced_by.get(fact["id"])
            if newer in facts:
                line += f" (later changed: see #{facts[newer]['message_index'] + 1})"
            elif fact["id"] in replaces:
                older = facts[replaces[fact["id"]]]
                line += f" (changes what was said at #{older['message_index'] + 1})"
            lines.append(line)
        return "\n".join(lines)

    @staticmethod
    def _render_enumeration(enumerated: dict[str, Any] | None, *, places: int = 5) -> str:
        if enumerated is None or not enumerated["items"]:
            return ""
        lines = [
            f"## Things of the kind the question asks about ({enumerated['kind']})",
            "From the memory's index of everything the conversation mentions: the "
            f"{enumerated['kind']} entries the user brought up that fit the question, with the "
            "messages where the user mentioned each. The index can miss a thing or split "
            "one thing under two names, so check it against the rest before counting.",
        ]
        for item in sorted(enumerated["items"], key=lambda i: i["mentions"][0]):
            where = ", ".join(f"#{p + 1}" for p in item["mentions"][:places])
            more = len(item["mentions"]) - places
            lines.append(f"- {item['name']}: {where}" + (f" and {more} more" if more > 0 else ""))
        return "\n".join(lines)

    def _render_entity_statements(
        self, session: _Session, entity_facts: dict[str, list[dict[str, Any]]]
    ) -> str:
        if not entity_facts:
            return ""
        lines = [
            "## What else the user has said about the things in the question",
            "Statements found through the things the question names, in the order they were made.",
        ]
        for entity_id, facts in entity_facts.items():
            lines.append(f"About {session.entities[entity_id]['name']}:")
            for fact in facts:
                line = f"- [{self._where(fact)}] {fact['text']}"
                if fact.get("changed_at") is not None:
                    line += f" (later changed: see #{fact['changed_at'] + 1})"
                lines.append(line)
        return "\n".join(lines)

    def _render_decisions(self, decisions: list[dict[str, Any]]) -> str:
        if not decisions:
            return ""
        lines = [
            "## What the user has decided",
            "Choices the user made that bear on the question, in the order they were "
            "made. A choice marked as later changed is no longer the current one.",
        ]
        for decision in sorted(decisions, key=lambda d: (d["message_index"], d["id"])):
            line = f"- [{self._where(decision)}] {decision['text']}"
            notes = []
            if decision.get("alternatives"):
                notes.append("passed over: " + ", ".join(decision["alternatives"][:4]))
            if decision.get("reason"):
                notes.append(f"because: {decision['reason']}")
            if decision.get("event_date"):
                notes.append(f"takes effect {decision['event_date']}")
            if notes:
                line += f" ({'; '.join(notes)})"
            if decision.get("changed_at") is not None:
                line += f" (later changed: see #{decision['changed_at'] + 1})"
            elif decision.get("status") == "considering":
                line += " (still being considered, not settled)"
            lines.append(line)
        return "\n".join(lines)

    @staticmethod
    def _render_timeline(
        facts: list[dict[str, Any]],
        replaced_by: dict[str, str],
        span: dict[str, Any] | None = None,
        *,
        between: bool = False,
        max_dates: int = 6,
    ) -> str:
        """The dated statements in date order, with day numbers.

        A statement that was later replaced is kept and marked. ``span`` is
        stated below them as the answer worked out for the
        question. With ``between`` and no span, the days between every two of
        the first ``max_dates`` dates are listed, so that no subtraction is
        left to the reader.
        """
        dated = sorted(
            (f for f in facts if f.get("event_date")),
            key=lambda f: (f["event_date"], f["message_index"]),
        )
        if len(dated) < 2:
            return ""
        lines = [
            "## Timeline",
            "What the user said about dated events, by the date each refers to. "
            '"day N" counts days from the first entry, so the days between two '
            "entries are the difference of their day numbers.",
        ]
        first = dated[0]["event_date"]
        for fact in dated:
            days = _days_between(first, fact["event_date"])
            day = f" (day {days})" if days is not None else ""
            # A replaced statement stays: it is what was true, or planned, then.
            changed = " (a later statement changed this)" if fact["id"] in replaced_by else ""
            lines.append(
                f"- {fact['event_date']}{day}: {fact['text']} "
                f"(#{fact['message_index'] + 1}){changed}"
            )
        if span is not None:
            first, second = span["events"]
            lines.append(
                "Worked out for this question: from "
                f'{first["event_date"]} ("{first["text"]}") to '
                f'{second["event_date"]} ("{second["text"]}") is {describe_span(span["days"])}.'
            )
        elif between:
            dates = sorted({f["event_date"] for f in dated})[:max_dates]
            pairs = [
                f"{a} to {b}: {describe_span(days)}"
                for i, a in enumerate(dates)
                for b in dates[i + 1 :]
                if (days := _days_between(a, b)) is not None
            ]
            if pairs:
                lines.append("Days between these dates, worked out: " + "; ".join(pairs) + ".")
        return "\n".join(lines)

    @staticmethod
    def _render_entities(
        session: _Session,
        question_entities: dict[str, dict[str, Any]],
        relations: list[dict[str, Any]],
    ) -> str:
        if not question_entities:
            return ""
        lines = ["## Entities the question mentions"]
        for entity_id, _ in sorted(question_entities.items(), key=lambda kv: -kv[1]["relevance"]):
            entity = session.entities[entity_id]
            mentions = sorted(entity["mentions"])
            if not mentions:
                continue
            lines.append(
                f"- {entity['name']} ({entity['type']}): mentioned in {len(mentions)} "
                f"messages, first #{mentions[0] + 1}, last #{mentions[-1] + 1}"
            )
        for row in relations:
            lines.append(f"- {row['source']} -[{row['type']}]-> {row['target']}")
        return "\n".join(lines) if len(lines) > 1 else ""

    @staticmethod
    def _render_outline(
        summaries: list[dict[str, Any]],
        count: int,
        *,
        max_chars: float,
        near: list[int] | None = None,
        shortest: int = 200,
    ) -> str:
        """The outline of the conversation, within ``max_chars``.

        Each stretch gets an equal share of the space. When that share would
        fall under ``shortest`` characters the conversation is too long to
        outline in full: only as many stretches as fit are shown, those that
        hold the most of the ``near`` message positions first and the rest
        spread evenly over the conversation, always in order.
        """
        if not summaries:
            return ""
        room = max(1, int(max_chars / (shortest + 60)))  # 60: the header line
        shown = summaries
        if len(summaries) > room:
            hits = [0] * len(summaries)
            for position in near or []:
                for index, summary in enumerate(summaries):
                    if summary["start_index"] <= position <= summary["end_index"]:
                        hits[index] += 1
                        break
            by_hits = sorted(range(len(summaries)), key=lambda i: (-hits[i], i))
            keep = {i for i in by_hits[:room] if hits[i] > 0}
            # What is left of the room is spread evenly, so the outline still
            # spans the whole conversation.
            rest = [i for i in range(len(summaries)) if i not in keep]
            spare = min(room - len(keep), len(rest))
            if spare > 0:
                step = len(rest) / spare
                keep.update(rest[int(k * step)] for k in range(spare))
            shown = [summaries[i] for i in sorted(keep)]
        lines = ["## Outline of the conversation"]
        if len(shown) == len(summaries):
            lines.append(
                f"The whole conversation ({count} messages), stretch by stretch, in order."
            )
        else:
            lines.append(
                f"The conversation has {count} messages in {len(summaries)} stretches. "
                f"These {len(shown)} are the ones nearest the question, with others at "
                "even distances, in order."
            )
        per_summary = max(shortest, int(max_chars / len(shown)) - 60)
        for summary in shown:
            header = f"Messages #{summary['start_index'] + 1}-{summary['end_index'] + 1}"
            if summary.get("start_time"):
                header += f" ({summary['start_time'][:10]})"
            lines.append(f"{header}:")
            lines.append(summary["text"][:per_summary])
        return "\n".join(lines)

    async def _render_excerpts(
        self,
        session: _Session,
        chosen: list[dict[str, Any]],
        message_chars: int | None = None,
    ) -> str:
        if not chosen:
            return ""
        positions = [p for entry in chosen for p in range(entry["pair"][0], entry["pair"][1] + 1)]
        rows = await self._client.execute_read(
            _MESSAGE_CONTENT, {"ids": [session.messages[p]["id"] for p in positions]}
        )
        content = {session.position[r["id"]]: r["content"] for r in rows}

        count = len(session.messages)
        lines = [
            "## Conversation excerpts",
            f"Exchanges from the conversation of {count} messages, in the order "
            "they were said. Those marked 'most recent' are where the conversation "
            "currently ends.",
        ]
        if message_chars is not None:
            lines[-1] += " Long messages are cut short, marked [...]."
        for entry in chosen:
            first, last = entry["pair"]
            where = f"#{first + 1}" if first == last else f"#{first + 1}-{last + 1}"
            header = f"[{where} of {count}"
            timestamp = session.messages[first].get("timestamp")
            if timestamp:
                header += f" | {timestamp[:10]}"
            if entry["recent"]:
                header += " | most recent"
            lines += ["", header + "]"]
            for position in range(first, last + 1):
                role = session.messages[position]["role"].upper()
                said = content.get(position, "")
                if message_chars is not None and len(said) > message_chars:
                    said = said[:message_chars].rstrip() + " [...]"
                lines.append(f"{role}: {said}")
        return "\n".join(lines)
