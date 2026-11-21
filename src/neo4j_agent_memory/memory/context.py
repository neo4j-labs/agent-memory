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

Statements, preferences and summaries exist once
:meth:`~neo4j_agent_memory.memory.distillation.DistillationMemory.distill_session`
has run; without them the context is the excerpts and the entities.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from neo4j_agent_memory.embeddings.base import Embedder
    from neo4j_agent_memory.graph.client import Neo4jClient

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
from March 12 to March 30").
- When asked in what order things came up, go by message position, earliest first.
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


def fuse_rankings(rankings: dict[str, list[tuple[int, int]]]) -> list[dict[str, Any]]:
    """Reciprocal rank fusion; best first, with each exchange's rank per source."""
    fused: dict[tuple[int, int], dict[str, Any]] = {}
    for source, ranking in rankings.items():
        for rank, pair in enumerate(ranking, start=1):
            entry = fused.setdefault(pair, {"pair": pair, "score": 0.0, "ranks": {}})
            entry["score"] += 1.0 / (RRF_K + rank)
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

_FACT_LINKS = """
MATCH (f:Fact) WHERE f.id IN $ids
MATCH (f)-[r:SUPERSEDED_BY|CONTRADICTS]-(o:Fact)
RETURN type(r) AS type, startNode(r).id AS source, endNode(r).id AS target,
       o.id AS id, o.text AS text, o.message_index AS message_index,
       o.said_at AS said_at, o.event_date AS event_date
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

_MESSAGE_CONTENT = """
MATCH (m:Message) WHERE m.id IN $ids
RETURN m.id AS id, m.content AS content
"""


@dataclass
class _Session:
    """The skeleton of one session: order, roles, sizes, dates, entities."""

    messages: list[dict[str, Any]]
    position: dict[str, int]
    roles: list[str]
    entities: dict[str, dict[str, Any]]


class ContextAssembler:
    """Builds the context for a question about one session."""

    def __init__(self, client: Neo4jClient, embedder: Embedder | None) -> None:
        self._client = client
        self._embedder = embedder
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
        session = _Session(
            messages=messages,
            position=position,
            roles=[row["role"] for row in messages],
            entities=entities,
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
        max_relations: int = 15,
        conflict_depth: int = 8,
        max_timeline: int = 8,
        relevant_instructions: int = 3,
        include_guidance: bool = True,
        include_outline: bool = True,
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
            max_preferences: Preferences shown, by similarity. Standing
                instructions are all shown.
            max_relations: Relationships between the question's entities shown.
            conflict_depth: A contradiction is reported when one of its two
                statements is among this many best-matching statements.
            max_timeline: Dated statements on the timeline: the closest ones
                to the question, also from beyond the ``max_statements`` shown.
            relevant_instructions: Standing instructions closest to the
                question that are repeated at the very end of the context.
            include_guidance: Open with a short note on how to use the memory
                (follow instructions, surface contradictions, prefer the latest
                value, say when nothing is known).
            include_outline: Include the outline of the whole conversation.

        Returns:
            The context text and a record of what went into it.
        """
        session = await self._session(session_id)
        count = len(session.messages)
        if count == 0 or self._embedder is None:
            return AssembledContext(text="", details={"strategy": "hybrid", "messages": count})

        def tokens(text: str) -> float:
            return len(text) / chars_per_token

        clauses = clauses_of(query)
        embeddings = await self._embedder.embed_batch([query, *clauses])
        embedding = embeddings[0]

        # --- what the memory knows that bears on the question -----------------
        preference_rows = await self._client.execute_read(
            _PREFERENCES, {"session_id": session_id, "embedding": embedding}
        )
        instructions = [p for p in preference_rows if p["kind"] == "instruction"]
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
        # The timeline also takes dated statements from further down the
        # ranking: a question about the time between two events rarely
        # resembles both of them equally.
        # It is kept short: the closest few dated statements, whichever part
        # of the ranking they come from. Every extra entry is one more date
        # the reader can pick by mistake.
        timeline_facts = [dict(f) for f in fact_hits if f.get("event_date")][:max_timeline]
        # Contradictions are reported only for what the question is closest to.
        near_question = {f["id"] for f in fact_hits[:conflict_depth]}
        links = (
            await self._client.execute_read(_FACT_LINKS, {"ids": list(shown_facts)})
            if shown_facts
            else []
        )
        replaced_by: dict[str, str] = {}
        # A denial ("has never ...") and the statements it conflicts with.
        conflicts: dict[str, list[str]] = {}
        for link in links:
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
        vector_ranking = to_pair_ranking(vector_positions, session.roles)
        rankings = {
            "vector": vector_ranking,
            "graph": to_pair_ranking(graph_positions, session.roles),
            "statements": to_pair_ranking(statement_positions, session.roles),
        }
        ranked = fuse_rankings({name: r for name, r in rankings.items() if r})

        # --- render what is fixed, then spend the rest on excerpts ------------
        # The excerpts come first and the distilled memory after them, closest
        # to the question: what must not be missed goes last.
        reported = {
            denial: [o for o in others if o in shown_facts]
            for denial, others in conflicts.items()
            if denial in shown_facts and (denial in near_question or near_question & set(others))
        }
        sections = [
            self._render_entities(session, question_entities, relations),
            self._render_outline(summaries, count, max_chars=0.25 * token_budget * chars_per_token),
            self._render_statements(shown_facts, replaced_by),
            self._render_timeline(timeline_facts, replaced_by),
            self._render_preferences(instructions, preferences),
            self._render_conflicts(shown_facts, reported),
        ]
        if include_guidance:
            sections.append(GUIDANCE)
        closest = sorted(instructions, key=lambda p: -p["score"])[:relevant_instructions]
        if closest:
            sections.append(
                "\n".join(
                    [
                        "The user's standing instructions closest to this question. "
                        "Apply each one that fits it:"
                    ]
                    + [f"- {item['text']}" for item in closest]
                )
            )
        fixed = "\n\n".join(s for s in sections if s)

        def pair_tokens(pair: tuple[int, int]) -> float:
            chars = sum(session.messages[p]["chars"] for p in range(pair[0], pair[1] + 1))
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
            top_k=top_k,
            budget=token_budget - tokens(fixed) - 200,
        )
        excerpts = await self._render_excerpts(session, chosen)
        text = "\n\n".join(part for part in (excerpts, fixed) if part)

        return AssembledContext(
            text=text,
            details={
                "strategy": "hybrid",
                "top_k": top_k,
                "estimated_tokens": round(tokens(text)),
                "instructions": len(instructions),
                "preferences": len(preferences),
                "clauses": clauses,
                "statements": len(shown_facts),
                "statement_messages": sorted(
                    {f["message_index"] + 1 for f in shown_facts.values()}
                ),
                "replaced": len(replaced_by),
                "conflicts": sum(1 for others in reported.values() if others),
                "summaries": len(summaries),
                "question_entities": [
                    {
                        "name": session.entities[eid]["name"],
                        "type": session.entities[eid]["type"],
                        "source": e["source"],
                        "relevance": round(e["relevance"], 4),
                        "mentions": len(session.entities[eid]["mentions"]),
                    }
                    for eid, e in sorted(
                        question_entities.items(), key=lambda kv: -kv[1]["relevance"]
                    )
                ],
                "relations": len(relations),
                "pairs": [
                    {
                        "messages": [p["pair"][0] + 1, p["pair"][1] + 1],
                        "ranks": p["ranks"],
                        "recent": p["recent"],
                    }
                    for p in chosen
                ],
            },
        )

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
        per_denial: int = 2,
    ) -> str:
        entries = [
            (facts[denial], sorted((facts[o] for o in others), key=lambda f: -f.get("score", 0.0)))
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
    def _render_timeline(facts: list[dict[str, Any]], replaced_by: dict[str, str]) -> str:
        dated = sorted(
            (f for f in facts if f.get("event_date") and f["id"] not in replaced_by),
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
            lines.append(
                f"- {fact['event_date']}{day}: {fact['text']} (#{fact['message_index'] + 1})"
            )
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
    def _render_outline(summaries: list[dict[str, Any]], count: int, *, max_chars: float) -> str:
        if not summaries:
            return ""
        lines = [
            "## Outline of the conversation",
            f"The whole conversation ({count} messages), stretch by stretch, in order.",
        ]
        per_summary = max(200, int(max_chars / len(summaries)))
        for summary in summaries:
            header = f"Messages #{summary['start_index'] + 1}-{summary['end_index'] + 1}"
            if summary.get("start_time"):
                header += f" ({summary['start_time'][:10]})"
            lines.append(f"{header}:")
            lines.append(summary["text"][:per_summary])
        return "\n".join(lines)

    async def _render_excerpts(self, session: _Session, chosen: list[dict[str, Any]]) -> str:
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
                lines.append(f"{role}: {content.get(position, '')}")
        return "\n".join(lines)
