"""Session distillation: turn a stored conversation into long-term memory.

Entity extraction records *what things* a conversation mentions. It does not
record what the user said is true, how they want to be answered, what changed
along the way, or what the conversation covered. ``client.distillation`` adds
those, as three jobs over the messages of one session:

* :meth:`DistillationMemory.extract_statements` reads each user message and
  stores the **facts** it states (``:Fact``) and the **preferences and standing
  instructions** it gives (``:Preference``), each linked to its source message
  with ``EXTRACTED_FROM`` and carrying the message's position and date. A fact
  about a dated event or a deadline carries that date, resolved against the
  date of the message.
* :meth:`DistillationMemory.reconcile` compares each fact with the earlier facts
  it resembles. A fact that replaces an earlier value closes it
  (``(old)-[:SUPERSEDED_BY]->(new)``, ``old.valid_until``). A *denial* ("has
  never ...", "does not use ...") is checked against what the user said up to
  then, and linked with ``CONTRADICTS`` to each statement in which they did or
  had the thing. Restated preferences are superseded by their latest wording.
* :meth:`DistillationMemory.summarize_session` writes a running outline of the
  conversation as ``:Summary`` nodes, one per stretch of messages.

:meth:`DistillationMemory.distill_session` runs all three. Every job is
resumable: it skips what an earlier run already did.

The jobs call an LLM and are independent per message (or per stretch), so they
run concurrently; ``max_concurrency`` bounds the calls in flight.
"""

from __future__ import annotations

import asyncio
import json
import logging
from functools import partial
from typing import TYPE_CHECKING, Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from neo4j_agent_memory.llm.types import ChatMessage

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

    from neo4j_agent_memory.embeddings.base import Embedder
    from neo4j_agent_memory.graph.client import Neo4jClient
    from neo4j_agent_memory.llm.protocol import LLMProvider

logger = logging.getLogger(__name__)


# --- what the LLM returns ---------------------------------------------------
#
# Every field is required and none has a default: OpenAI's strict structured
# output mode accepts only such schemas.


class FactStatement(BaseModel):
    """One thing a message says is true."""

    statement: str = Field(description="One self-contained sentence stating the fact.")
    subject: str = Field(description="What the fact is about.")
    attribute: str = Field(description="What is said about the subject, in a few words.")
    value: str = Field(description="The value stated.")
    date: str | None = Field(
        description="The calendar date the fact refers to, YYYY-MM-DD, or null when it has none."
    )
    denial: bool = Field(
        description="True when the fact says the user has never done, does not have "
        "or does not use something."
    )


class PreferenceStatement(BaseModel):
    """One preference or standing instruction a message gives."""

    kind: Literal["instruction", "preference"] = Field(
        description="'instruction' for a standing rule on how to respond; else 'preference'."
    )
    category: str = Field(description="A short topic, e.g. 'response format'.")
    statement: str = Field(description="One self-contained sentence.")


class MessageStatements(BaseModel):
    """Everything worth remembering from one message."""

    facts: list[FactStatement]
    preferences: list[PreferenceStatement]


class FactRelation(BaseModel):
    """How one new fact relates to one earlier fact."""

    new: int = Field(description="Number of the NEW statement.")
    earlier: int = Field(description="Number of the EARLIER statement.")
    relation: Literal["update", "same"]


class DenialConflict(BaseModel):
    """One thing the user said that conflicts with a denial."""

    source: Literal["statement", "message"] = Field(
        description="Whether the conflict is with a numbered STATEMENT or a numbered MESSAGE."
    )
    number: int = Field(description="Its number in that list.")
    said: str = Field(
        description="One sentence, in the third person, stating what the user said "
        "there that conflicts with the denial."
    )


class DenialConflicts(BaseModel):
    """What conflicts with a denial."""

    conflicts: list[DenialConflict]


class FactRelations(BaseModel):
    """The related pairs among the statements shown."""

    relations: list[FactRelation]


# --- prompts ----------------------------------------------------------------

STATEMENTS_PROMPT = """\
You maintain the long-term memory of an AI assistant. You are given one message \
that the user wrote to the assistant, with the date it was written. Record what a \
careful assistant would need to remember from it later.

Return two lists.

facts: things the message says are true about the user, their work, their plans, \
and the people and things around them. One fact per item. Include:
- concrete details: names, numbers, amounts, versions, places, people and how they \
relate to the user;
- what the user has done, decided, chosen, declined, finished or changed, and what \
is planned or due;
- what the user says they have never done, do not have or do not use. Record these \
too, keeping the wording ("has never ...");
- a new value for something that may have been stated before (a moved deadline, a \
revised number, a changed plan): record the new value as its own fact.
Do not record questions, requests for help, or what is only the assistant's \
suggestion. Skip code and general knowledge that is not about the user's situation.
For each fact give:
- statement: one self-contained sentence in the third person ("The user ..."), \
naming the things involved instead of "it" or "this project", and keeping exact \
numbers, names and dates;
- subject: what the fact is about ("first sprint", "dashboard API", "Stephen");
- attribute: what is said about it, in a few words ("end date", "average response \
time", "relationship to the user");
- value: the value stated ("March 29", "250ms", "partner of five years");
- date: the calendar date the fact refers to, as YYYY-MM-DD: when the event \
happened or will happen, or the deadline. Resolve "tomorrow", "next Friday" or \
"March 29" against the date of the message, using its year unless another is given. \
null when the fact has no specific date;
- denial: true only when the fact says the user has never done something, has no \
experience of it, or has never had or used something ("I've never written a \
route", "I have never used Docker"). false for everything else, including what \
the user wants to avoid, has not finished yet, or tried without success.

preferences: how the user wants things, of two kinds:
- instruction: a standing request about how the assistant should respond from now \
on ("always ...", "never ...", "from now on ...", "whenever I ask about X, include \
Y"). Only lasting rules, not a one-off request about the task at hand;
- preference: what the user likes, dislikes, values, avoids or puts first when \
choosing (tools, styles, approaches, budgets, ways of working or deciding).
For each give kind, category (a short topic such as "response format", "tools", \
"decision making") and statement: one self-contained sentence, for example "The \
user wants code snippets always formatted with syntax highlighting when asking \
about implementation details."

Return empty lists when the message has nothing of that kind. At most 8 facts and \
4 preferences; prefer the specific and the lasting.
"""

RECONCILE_PROMPT = """\
You keep an assistant's memory of one user consistent. NEW statements were just \
recorded from one message; EARLIER statements were recorded from earlier messages. \
Each is shown with the subject, attribute and value it records. Find the pairs of \
a NEW and an EARLIER statement that record the same attribute of the same thing, \
and say how they relate:

- "update": the NEW statement gives a different value for it, because things \
changed. The earlier value was right when it was said and is now out of date. \
Examples: the same deadline moved, the same measurement was taken again, the same \
count grew, the same task went from planned to done, one choice was replaced by \
another.
- "same": the NEW statement repeats the EARLIER value without changing it.

Two statements about the same broad topic are not a pair unless they are about \
the same attribute of the same thing: a new detail, a next step or a related task \
does not replace anything. When unsure, leave the pair out. Return an empty list \
when no pair qualifies.
"""

DENIAL_PROMPT = """\
An assistant keeps a record of what a user has said. At one point the user made a \
DENIAL: a statement that they have never done, never had or never used something. \
Below it are STATEMENTS (S1, S2, ...) recorded from what the same user said up to \
that point, and MESSAGES (M1, M2, ...) the user wrote up to that point. One of the \
messages may be the one that contains the denial itself.

Find what conflicts with the DENIAL: places where the user had done, was doing or \
was already working on the very thing the denial says they never did or had. \
Implementing it, integrating it, configuring it, using it or testing it all count, \
also when the user was asking for help with it at the time. For example, "has \
never written a Flask route" conflicts with "is implementing the homepage route \
with Flask and has it returning static HTML".

It is not a conflict when the user only wonders whether to do the thing, or talks \
about something else. Do not report the denial itself.

For each conflict give its source ("statement" or "message"), its number in that \
list, and one sentence in the third person stating what the user said there ("The \
user implemented a basic homepage route with Flask."). Return an empty list when \
nothing conflicts.
"""

SUMMARY_PROMPT = """\
You are keeping the outline of a long conversation between a user and an AI \
assistant. Between the <transcript> tags is one stretch of it. Do not continue the \
conversation and do not answer the user: describe what happened in it.

Write 3 or 4 bullet points, in the order things came up. Each bullet names one \
thing the user brought up, asked for or decided, in words the user would recognise, \
with the specifics attached to it (names, numbers, dates, versions, choices made). \
Add what the assistant advised only when it matters, in a few words.

One line per bullet, each starting with "- " and at most 25 words long. No heading \
and no closing remark.
"""


# --- Cypher -----------------------------------------------------------------

# NULL sorts last in ascending order, so messages written before ``index``
# existed fall back to their timestamps.
_SESSION_MESSAGES = """
MATCH (c:Conversation {session_id: $session_id})-[:HAS_MESSAGE]->(m:Message)
RETURN m.id AS id, m.role AS role, m.content AS content,
       toString(m.timestamp) AS timestamp,
       coalesce(m.statements_extracted, false) AS statements_extracted
ORDER BY m.index ASC, m.timestamp ASC, m.id ASC
"""

_STORE_STATEMENTS = """
MATCH (m:Message {id: $message_id})
SET m.statements_extracted = true
WITH m
CALL {
    WITH m
    UNWIND $facts AS fact
    CREATE (f:Fact {
        id: fact.id,
        subject: fact.subject,
        predicate: fact.predicate,
        object: fact.object,
        text: fact.text,
        event_date: fact.event_date,
        denial: fact.denial,
        said_at: $said_at,
        session_id: $session_id,
        message_index: $message_index,
        confidence: 1.0,
        embedding: fact.embedding,
        valid_from: $timestamp,
        valid_until: null,
        created_at: datetime(),
        metadata: $metadata
    })
    CREATE (f)-[:EXTRACTED_FROM]->(m)
    RETURN count(f) AS facts
}
CALL {
    WITH m
    UNWIND $preferences AS pref
    CREATE (p:Preference {
        id: pref.id,
        category: pref.category,
        preference: pref.preference,
        context: null,
        kind: pref.kind,
        said_at: $said_at,
        session_id: $session_id,
        message_index: $message_index,
        confidence: 1.0,
        embedding: pref.embedding,
        valid_from: m.timestamp,
        created_at: datetime(),
        metadata: $metadata
    })
    CREATE (p)-[:EXTRACTED_FROM]->(m)
    RETURN count(p) AS preferences
}
RETURN facts, preferences
"""

_MARK_MESSAGE_DONE = """
MATCH (m:Message {id: $message_id})
SET m.statements_extracted = true
"""

_UNRECONCILED_FACTS = """
MATCH (f:Fact {session_id: $session_id})
WHERE f.reconciled IS NULL AND f.embedding IS NOT NULL
RETURN f.id AS id, f.text AS text, f.message_index AS message_index, f.said_at AS said_at,
       f.subject AS subject, f.predicate AS attribute, f.object AS value,
       coalesce(f.denial, false) AS denial
ORDER BY f.message_index ASC, f.id ASC
"""

# The fact index covers every session, hence the over-fetch.
_EARLIER_SIMILAR_FACTS = """
UNWIND $ids AS fact_id
MATCH (f:Fact {id: fact_id})
CALL db.index.vector.queryNodes('fact_embedding_idx', $fetch, f.embedding)
YIELD node, score
WHERE node.session_id = $session_id
  AND node.message_index < f.message_index
  AND score >= $min_score
WITH fact_id, node, score ORDER BY score DESC
WITH fact_id, collect({id: node.id, text: node.text, message_index: node.message_index,
                       said_at: node.said_at, subject: node.subject,
                       attribute: node.predicate, value: node.object,
                       score: score})[..$limit] AS earlier
RETURN fact_id, earlier
"""

# What the user had said up to a denial, the denial's own message included.
_STATEMENTS_BEFORE_DENIAL = """
MATCH (d:Fact {id: $id})
CALL db.index.vector.queryNodes('fact_embedding_idx', $fetch, d.embedding)
YIELD node, score
WHERE node.session_id = $session_id
  AND node.id <> d.id
  AND node.message_index <= d.message_index
  AND NOT coalesce(node.denial, false)
  AND score >= $min_score
RETURN node.id AS id, node.text AS text, node.message_index AS message_index,
       node.said_at AS said_at, score
ORDER BY score DESC
LIMIT $limit
"""

_APPLY_CONFLICTS = """
MATCH (d:Fact {id: $denial})
UNWIND $ids AS other_id
MATCH (o:Fact {id: other_id})
MERGE (d)-[:CONTRADICTS]->(o)
"""

# User messages that resemble a denial; the caller keeps those said up to it.
_MESSAGES_LIKE_FACT = """
MATCH (d:Fact {id: $id})
CALL db.index.vector.queryNodes('message_embedding_idx', $fetch, d.embedding)
YIELD node, score
MATCH (:Conversation {session_id: $session_id})-[:HAS_MESSAGE]->(node)
WHERE node.role = 'user'
RETURN node.id AS id, node.content AS content, toString(node.timestamp) AS timestamp, score
ORDER BY score DESC
"""

# A conflict found in a message that no recorded statement covers becomes a
# statement of its own.
_STORE_CONFLICTING_STATEMENT = """
MATCH (d:Fact {id: $denial})
MATCH (m:Message {id: $message_id})
CREATE (f:Fact {
    id: $id,
    subject: '',
    predicate: '',
    object: '',
    text: $text,
    event_date: null,
    denial: false,
    said_at: $said_at,
    session_id: $session_id,
    message_index: $message_index,
    confidence: 1.0,
    embedding: $embedding,
    valid_from: $timestamp,
    valid_until: null,
    reconciled: true,
    created_at: datetime(),
    metadata: $metadata
})
CREATE (f)-[:EXTRACTED_FROM]->(m)
MERGE (d)-[:CONTRADICTS]->(f)
"""

_APPLY_FACT_RELATIONS = """
UNWIND $rows AS row
MATCH (n:Fact {id: row.new})
MATCH (e:Fact {id: row.earlier})
FOREACH (_ IN CASE WHEN row.relation = 'update' THEN [1] ELSE [] END |
    MERGE (e)-[:SUPERSEDED_BY]->(n)
    SET e.valid_until = coalesce(e.valid_until, n.valid_from)
)
FOREACH (_ IN CASE WHEN row.relation = 'same' THEN [1] ELSE [] END |
    MERGE (n)-[:RESTATES]->(e)
)
"""

_MARK_FACTS_RECONCILED = """
MATCH (f:Fact) WHERE f.id IN $ids
SET f.reconciled = true
"""

# A preference stated again later, in nearly the same words, replaces the
# earlier wording. The newest restatement wins.
_SUPERSEDE_RESTATED_PREFERENCES = """
MATCH (a:Preference {session_id: $session_id})
WHERE a.embedding IS NOT NULL AND NOT (a)-[:SUPERSEDED_BY]->()
CALL {
    WITH a
    CALL db.index.vector.queryNodes('preference_embedding_idx', $fetch, a.embedding)
    YIELD node, score
    WHERE node.session_id = $session_id
      AND node.message_index > a.message_index
      AND coalesce(node.kind, 'preference') = coalesce(a.kind, 'preference')
      AND score >= $min_score
    RETURN node ORDER BY node.message_index DESC LIMIT 1
}
MERGE (a)-[:SUPERSEDED_BY]->(node)
SET a.valid_until = coalesce(a.valid_until, node.valid_from)
RETURN count(*) AS superseded
"""

_EXISTING_SUMMARY_STARTS = """
MATCH (s:Summary {session_id: $session_id})
RETURN s.start_index AS start_index
"""

_STORE_SUMMARY = """
MATCH (c:Conversation {session_id: $session_id})
WITH c ORDER BY c.created_at DESC LIMIT 1
MERGE (s:Summary {session_id: $session_id, start_index: $start_index})
ON CREATE SET s.id = $id, s.created_at = datetime()
SET s.end_index = $end_index, s.text = $text, s.start_time = $start_time
MERGE (c)-[:HAS_SUMMARY]->(s)
"""


def _date_of(timestamp: str | None) -> str | None:
    """The calendar date of an ISO timestamp, ``YYYY-MM-DD``."""
    return timestamp[:10] if timestamp and len(timestamp) >= 10 else None


def _valid_date(value: str | None) -> str | None:
    """``value`` when it is a plausible ``YYYY-MM-DD`` date, else None."""
    if not value or len(value) != 10 or value[4] != "-" or value[7] != "-":
        return None
    year, month, day = value[:4], value[5:7], value[8:]
    if not (year.isdigit() and month.isdigit() and day.isdigit()):
        return None
    if not (1 <= int(month) <= 12 and 1 <= int(day) <= 31):
        return None
    return value


class DistillationMemory:
    """``client.distillation``: facts, preferences and summaries from a session."""

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

    # -- plumbing -------------------------------------------------------------

    def _require_llm(self) -> Any:
        if self._llm is None:
            raise RuntimeError(
                "Distillation needs an LLM. Configure MemorySettings.llm with a provider."
            )
        return self._llm

    async def _with_retries(self, call: Callable[[], Awaitable[Any]], what: str) -> Any:
        """Run an LLM call, retrying transient failures. None when it never succeeds."""
        for attempt in range(1, self._retries + 1):
            try:
                return await call()
            except Exception as exc:  # noqa: BLE001 - provider errors vary; all are retried
                if attempt == self._retries:
                    logger.warning(
                        "Distillation %s failed after %d attempts: %s", what, attempt, exc
                    )
                    return None
                await asyncio.sleep(2.0 * attempt)
        return None

    async def _session_messages(self, session_id: str) -> list[dict[str, Any]]:
        rows = await self._client.execute_read(_SESSION_MESSAGES, {"session_id": session_id})
        for position, row in enumerate(rows):
            row["position"] = position
        return rows

    @staticmethod
    async def _gather(
        jobs: Sequence[Callable[[], Awaitable[Any]]],
        max_concurrency: int,
        on_progress: Callable[[int, int], None] | None,
    ) -> list[Any]:
        semaphore = asyncio.Semaphore(max(1, max_concurrency))
        done = 0

        async def run(job: Callable[[], Awaitable[Any]]) -> Any:
            nonlocal done
            async with semaphore:
                result = await job()
            done += 1
            if on_progress is not None:
                on_progress(done, len(jobs))
            return result

        return list(await asyncio.gather(*(run(job) for job in jobs)))

    # -- statements -----------------------------------------------------------

    async def extract_statements(
        self,
        session_id: str,
        *,
        roles: Sequence[str] = ("user",),
        skip_existing: bool = True,
        max_concurrency: int = 8,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> dict[str, int]:
        """Store the facts, preferences and instructions stated in a session.

        Args:
            session_id: The session to read.
            roles: Roles whose messages are read. The default reads what the
                user wrote: what the assistant wrote is advice, not a record of
                the user's situation.
            skip_existing: Skip messages an earlier run already read.
            max_concurrency: LLM calls in flight.
            on_progress: Called with (messages done, messages to do).

        Returns:
            ``messages_processed``, ``facts``, ``preferences`` (of which
            ``instructions``) and ``failures`` (messages the LLM never answered
            for; they stay unread and a later run picks them up).
        """
        llm = self._require_llm()
        messages = [
            m
            for m in await self._session_messages(session_id)
            if m["role"] in roles and not (skip_existing and m["statements_extracted"])
        ]
        stats = {
            "messages_processed": 0,
            "facts": 0,
            "preferences": 0,
            "instructions": 0,
            "failures": 0,
        }

        async def one(message: dict[str, Any]) -> None:
            said_at = _date_of(message["timestamp"])
            prompt = [
                ChatMessage(role="system", content=STATEMENTS_PROMPT),
                ChatMessage(
                    role="user",
                    content=f"Date of the message: {said_at or 'unknown'}\n\n"
                    f"Message from the user:\n{message['content']}",
                ),
            ]
            found = await self._with_retries(
                lambda: llm.complete_structured(prompt, MessageStatements, temperature=0.0),
                f"statement extraction (message {message['position'] + 1})",
            )
            if found is None:
                stats["failures"] += 1
                return
            facts = [f for f in found.facts if f.statement.strip()][:8]
            preferences = [p for p in found.preferences if p.statement.strip()][:4]
            if not facts and not preferences:
                await self._client.execute_write(_MARK_MESSAGE_DONE, {"message_id": message["id"]})
                stats["messages_processed"] += 1
                return

            texts = [f.statement for f in facts] + [p.statement for p in preferences]
            embeddings: list[list[float] | None] = [None] * len(texts)
            if self._embedder is not None:
                embeddings = list(await self._embedder.embed_batch(texts))
            await self._client.execute_write(
                _STORE_STATEMENTS,
                {
                    "message_id": message["id"],
                    "session_id": session_id,
                    "message_index": message["position"],
                    "said_at": said_at,
                    "timestamp": message["timestamp"],
                    "metadata": json.dumps({"source": "distillation"}),
                    "facts": [
                        {
                            "id": str(uuid4()),
                            "subject": fact.subject.strip(),
                            "predicate": fact.attribute.strip(),
                            "object": fact.value.strip(),
                            "text": fact.statement.strip(),
                            "event_date": _valid_date(fact.date),
                            "denial": bool(fact.denial),
                            "embedding": embeddings[i],
                        }
                        for i, fact in enumerate(facts)
                    ],
                    "preferences": [
                        {
                            "id": str(uuid4()),
                            "category": pref.category.strip().lower(),
                            "preference": pref.statement.strip(),
                            "kind": pref.kind,
                            "embedding": embeddings[len(facts) + i],
                        }
                        for i, pref in enumerate(preferences)
                    ],
                },
            )
            stats["messages_processed"] += 1
            stats["facts"] += len(facts)
            stats["preferences"] += len(preferences)
            stats["instructions"] += sum(p.kind == "instruction" for p in preferences)

        await self._gather([partial(one, m) for m in messages], max_concurrency, on_progress)
        return stats

    # -- reconciliation -------------------------------------------------------

    async def reconcile(
        self,
        session_id: str,
        *,
        candidates: int = 5,
        min_similarity: float = 0.78,
        denial_candidates: int = 8,
        denial_similarity: float = 0.70,
        denial_messages: int = 4,
        denial_message_chars: int = 1200,
        preference_similarity: float = 0.96,
        max_concurrency: int = 8,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> dict[str, int]:
        """Find the facts that replace or contradict earlier ones.

        Each fact not yet reconciled is compared with up to ``candidates``
        earlier facts of the session whose embedding similarity is at least
        ``min_similarity`` (Neo4j's cosine score, between 0 and 1). An LLM
        decides, for the facts of one message at a time, which pairs are an
        update or a restatement.

        Each denial among them ("has never ...") is compared with up to
        ``denial_candidates`` statements and ``denial_messages`` user messages
        (their first ``denial_message_chars`` characters) from up to and
        including the denial's own message, and linked to what it conflicts
        with. A conflict found in a message becomes a statement of its own. A
        statement made after a denial is not held against it: by then the user
        may have done the thing.

        Returns:
            ``facts_checked``, ``updates``, ``restatements``, ``denials`` and the
            ``contradictions`` found for them, ``preferences_superseded`` and
            ``failures``.
        """
        llm = self._require_llm()
        pending = await self._client.execute_read(_UNRECONCILED_FACTS, {"session_id": session_id})
        by_message: dict[int, list[dict[str, Any]]] = {}
        for row in pending:
            by_message.setdefault(row["message_index"], []).append(row)
        stats = {
            "facts_checked": 0,
            "updates": 0,
            "denials": 0,
            "contradictions": 0,
            "restatements": 0,
            "preferences_superseded": 0,
            "failures": 0,
        }

        async def one(new_facts: list[dict[str, Any]]) -> None:
            ids = [f["id"] for f in new_facts]
            rows = await self._client.execute_read(
                _EARLIER_SIMILAR_FACTS,
                {
                    "ids": ids,
                    "session_id": session_id,
                    "fetch": candidates * 8,
                    "limit": candidates,
                    "min_score": min_similarity,
                },
            )
            earlier: dict[str, dict[str, Any]] = {}
            for row in rows:
                for candidate in row["earlier"]:
                    earlier.setdefault(candidate["id"], candidate)
            if not earlier:
                await self._client.execute_write(_MARK_FACTS_RECONCILED, {"ids": ids})
                stats["facts_checked"] += len(ids)
                return

            earlier_list = sorted(earlier.values(), key=lambda c: (c["message_index"], c["id"]))

            def shown(fact: dict[str, Any]) -> str:
                return (
                    f"{fact['text']} [subject: {fact.get('subject')}; "
                    f"attribute: {fact.get('attribute')}; value: {fact.get('value')}]"
                )

            lines = ["EARLIER statements:"]
            lines += [
                f"E{i}. (message {c['message_index'] + 1}"
                + (f", {c['said_at']}" if c.get("said_at") else "")
                + f") {shown(c)}"
                for i, c in enumerate(earlier_list, start=1)
            ]
            first = new_facts[0]
            lines += [
                "",
                f"NEW statements (message {first['message_index'] + 1}"
                + (f", {first['said_at']}" if first.get("said_at") else "")
                + "):",
            ]
            lines += [f"N{i}. {shown(f)}" for i, f in enumerate(new_facts, start=1)]
            prompt = [
                ChatMessage(role="system", content=RECONCILE_PROMPT),
                ChatMessage(role="user", content="\n".join(lines)),
            ]
            decided = await self._with_retries(
                lambda: llm.complete_structured(prompt, FactRelations, temperature=0.0),
                f"fact reconciliation (message {first['message_index'] + 1})",
            )
            if decided is None:
                stats["failures"] += 1
                return
            applied: list[dict[str, str]] = []
            for relation in decided.relations:
                if not (1 <= relation.new <= len(new_facts)):
                    continue
                if not (1 <= relation.earlier <= len(earlier_list)):
                    continue
                applied.append(
                    {
                        "new": new_facts[relation.new - 1]["id"],
                        "earlier": earlier_list[relation.earlier - 1]["id"],
                        "relation": relation.relation,
                    }
                )
            if applied:
                await self._client.execute_write(_APPLY_FACT_RELATIONS, {"rows": applied})
            await self._client.execute_write(_MARK_FACTS_RECONCILED, {"ids": ids})
            stats["facts_checked"] += len(ids)
            stats["updates"] += sum(r["relation"] == "update" for r in applied)
            stats["restatements"] += sum(r["relation"] == "same" for r in applied)

        positions = {m["id"]: m for m in await self._session_messages(session_id)}

        async def check_denial(denial: dict[str, Any]) -> None:
            statements = await self._client.execute_read(
                _STATEMENTS_BEFORE_DENIAL,
                {
                    "id": denial["id"],
                    "session_id": session_id,
                    "fetch": denial_candidates * 8,
                    "limit": denial_candidates,
                    "min_score": denial_similarity,
                },
            )
            similar = await self._client.execute_read(
                _MESSAGES_LIKE_FACT,
                {"id": denial["id"], "session_id": session_id, "fetch": denial_messages * 12},
            )
            messages = [
                row
                for row in similar
                if row["id"] in positions
                and positions[row["id"]]["position"] <= denial["message_index"]
            ][:denial_messages]
            if not statements and not messages:
                return
            lines = [f"DENIAL: {denial['text']}", ""]
            lines.append("STATEMENTS:")
            lines += [f"S{i}. {s['text']}" for i, s in enumerate(statements, start=1)] or ["(none)"]
            lines += ["", "MESSAGES:"]
            lines += [
                f"M{i}. {m['content'][:denial_message_chars]}"
                for i, m in enumerate(messages, start=1)
            ] or ["(none)"]
            prompt = [
                ChatMessage(role="system", content=DENIAL_PROMPT),
                ChatMessage(role="user", content="\n".join(lines)),
            ]
            decided = await self._with_retries(
                lambda: llm.complete_structured(prompt, DenialConflicts, temperature=0.0),
                f"denial check (message {denial['message_index'] + 1})",
            )
            if decided is None:
                stats["failures"] += 1
                return
            fact_ids: set[str] = set()
            from_messages: dict[str, str] = {}
            for conflict in decided.conflicts:
                if conflict.source == "statement" and 1 <= conflict.number <= len(statements):
                    fact_ids.add(statements[conflict.number - 1]["id"])
                elif conflict.source == "message" and 1 <= conflict.number <= len(messages):
                    from_messages.setdefault(messages[conflict.number - 1]["id"], conflict.said)
            # A message already represented by a conflicting statement needs no new one.
            covered = {s["message_index"] for s in statements if s["id"] in fact_ids}
            if fact_ids:
                await self._client.execute_write(
                    _APPLY_CONFLICTS, {"denial": denial["id"], "ids": sorted(fact_ids)}
                )
            for message_id, said in from_messages.items():
                message = positions[message_id]
                if message["position"] in covered or not said.strip():
                    continue
                # A conflicting statement is something the user did, not
                # another denial: the model sometimes echoes the denial back.
                if "never" in said.lower():
                    continue
                embedding = await self._embedder.embed(said) if self._embedder is not None else None
                await self._client.execute_write(
                    _STORE_CONFLICTING_STATEMENT,
                    {
                        "denial": denial["id"],
                        "message_id": message_id,
                        "id": str(uuid4()),
                        "text": said.strip(),
                        "said_at": _date_of(message["timestamp"]),
                        "session_id": session_id,
                        "message_index": message["position"],
                        "embedding": embedding,
                        "timestamp": message["timestamp"],
                        "metadata": json.dumps({"source": "distillation:denial-check"}),
                    },
                )
                covered.add(message["position"])
                stats["contradictions"] += 1
            stats["contradictions"] += len(fact_ids)

        # Denials first: the general pass marks the facts as reconciled.
        denials = [row for row in pending if row["denial"]]
        stats["denials"] = len(denials)
        await self._gather([partial(check_denial, d) for d in denials], max_concurrency, None)

        groups = [by_message[index] for index in sorted(by_message)]
        await self._gather([partial(one, g) for g in groups], max_concurrency, on_progress)

        superseded = await self._client.execute_write(
            _SUPERSEDE_RESTATED_PREFERENCES,
            {"session_id": session_id, "fetch": 24, "min_score": preference_similarity},
        )
        stats["preferences_superseded"] = superseded[0]["superseded"] if superseded else 0
        return stats

    # -- summaries ------------------------------------------------------------

    async def summarize_session(
        self,
        session_id: str,
        *,
        span: int = 10,
        skip_existing: bool = True,
        max_message_chars: int = 3000,
        max_concurrency: int = 8,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> dict[str, int]:
        """Write the outline of a session: one ``:Summary`` per ``span`` messages.

        A trailing stretch shorter than ``span`` is summarized too; run the job
        again after more messages arrive and that last summary is rewritten.

        Returns:
            ``summaries`` written and ``failures``.
        """
        llm = self._require_llm()
        messages = await self._session_messages(session_id)
        existing = {
            row["start_index"]
            for row in await self._client.execute_read(
                _EXISTING_SUMMARY_STARTS, {"session_id": session_id}
            )
        }
        stats = {"summaries": 0, "failures": 0}
        spans = [messages[start : start + span] for start in range(0, len(messages), span)]

        async def one(stretch: list[dict[str, Any]]) -> None:
            start = stretch[0]["position"]
            complete = len(stretch) == span
            if skip_existing and complete and start in existing:
                return
            transcript = (
                "<transcript>\n"
                + "\n\n".join(
                    f"{m['role'].upper()}: {m['content'][:max_message_chars]}" for m in stretch
                )
                + "\n</transcript>\n\nWrite the outline bullets for this stretch."
            )
            prompt = [
                ChatMessage(role="system", content=SUMMARY_PROMPT),
                ChatMessage(role="user", content=transcript),
            ]
            completion = await self._with_retries(
                lambda: llm.complete(prompt, temperature=0.0, max_tokens=260),
                f"summary (messages {start + 1}-{stretch[-1]['position'] + 1})",
            )
            text = completion.content.strip() if completion is not None else ""
            if not text:
                stats["failures"] += 1
                return
            await self._client.execute_write(
                _STORE_SUMMARY,
                {
                    "session_id": session_id,
                    "id": str(uuid4()),
                    "start_index": start,
                    "end_index": stretch[-1]["position"],
                    "start_time": stretch[0]["timestamp"],
                    "text": text,
                },
            )
            stats["summaries"] += 1

        await self._gather([partial(one, s) for s in spans], max_concurrency, on_progress)
        return stats

    # -- all three ------------------------------------------------------------

    async def distill_session(
        self,
        session_id: str,
        *,
        summary_span: int = 10,
        max_concurrency: int = 8,
    ) -> dict[str, dict[str, int]]:
        """Extract statements, reconcile them and summarize the session."""
        statements = await self.extract_statements(session_id, max_concurrency=max_concurrency)
        reconciliation = await self.reconcile(session_id, max_concurrency=max_concurrency)
        summaries = await self.summarize_session(
            session_id, span=summary_span, max_concurrency=max_concurrency
        )
        return {
            "statements": statements,
            "reconciliation": reconciliation,
            "summaries": summaries,
        }
