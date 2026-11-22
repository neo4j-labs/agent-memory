"""Session distillation: turn a stored conversation into long-term memory.

Entity extraction records *what things* a conversation mentions. It does not
record what the user said is true, how they want to be answered, what they
decided, what changed along the way, or what the conversation covered.
``client.distillation`` adds those, as three jobs over the messages of one
session:

* :meth:`DistillationMemory.extract_statements` reads each user message and
  stores the **facts** it states (``:Fact``), the **preferences and standing
  instructions** it gives (``:Preference``) and, on request, the **decisions**
  the user makes (``:Decision``: what was chosen, what was passed over, why,
  and whether it is settled). Each is linked to its source message with
  ``EXTRACTED_FROM`` and carries the message's position and date. A fact about
  a dated event or a deadline carries that date, resolved against the date of
  the message.
* :meth:`DistillationMemory.reconcile` compares each fact with the earlier facts
  it resembles. A fact that replaces an earlier value is linked to it
  (``(old)-[:SUPERSEDED_BY]->(new)``). A *denial* ("has never ...", "does not
  use ...") is checked against what the user said up to then, and linked with
  ``CONTRADICTS`` to each statement in which they did or had the thing. A
  decision that settles the same question differently replaces the earlier
  one. Restated preferences are superseded by their latest wording.
* :meth:`DistillationMemory.summarize_session` writes a running outline of the
  conversation as ``:Summary`` nodes, one per stretch of messages.

:meth:`DistillationMemory.distill_session` runs all three. Every job is
resumable: it skips what an earlier run already did.

An LLM writes: the statements and the summaries. What remains is judging, and a
:class:`~neo4j_agent_memory.judge.Judge` can do it (``MemorySettings.judge``):
whether a statement is in the message it was drawn from, whether it is a denial
or a standing instruction, which numbers in a message no statement recorded,
and how two statements relate. Its answers are probabilities, and they are
stored: on the statements (``support``, ``denial_probability``,
``instruction_probability``, ``commit_probability``) and on the relationships
(``probability``, ``judge``), so that a reader of the graph applies its own
threshold. Without a judge the LLM decides, and its answers are final.

The jobs are independent per message (or per stretch), so they run
concurrently; ``max_concurrency`` bounds the calls in flight.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from functools import partial
from typing import TYPE_CHECKING, Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from neo4j_agent_memory.judge import (
    Judge,
    Judgement,
    LLMJudge,
    OneOf,
    Question,
    YesNo,
    judge_safely,
)
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


class DecisionStatement(BaseModel):
    """One choice the user commits to, or is weighing, in a message."""

    statement: str = Field(description="One self-contained sentence stating the decision.")
    chosen: str = Field(description="What was chosen, in a few words.")
    alternatives: list[str] = Field(
        description="Other options the exchange names that the user passed over; may be empty."
    )
    reason: str | None = Field(description="Why, in a few words, when the user says; else null.")
    status: Literal["considering", "decided"] = Field(
        description="'decided' when the user commits to the choice; 'considering' while weighing it."
    )
    date: str | None = Field(
        description="The date the choice takes effect or the chosen thing happens, "
        "YYYY-MM-DD, or null when none is stated."
    )


class MessageStatementsAndDecisions(BaseModel):
    """Everything worth remembering from one message, decisions included."""

    facts: list[FactStatement]
    preferences: list[PreferenceStatement]
    decisions: list[DecisionStatement]


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

REPLY_CONTEXT_NOTE = """
Before the user's message you are shown the end of the assistant's message it \
replies to. Use it only to understand what the user means. When the user accepts, \
picks, confirms or turns down something the assistant proposed ("I'll go with \
that", "let's do the second one", "no, I'd rather not"), record what was accepted \
or turned down by its name, as said by the user. Record nothing else from the \
assistant's message: its advice is not a fact about the user.
"""

DECISIONS_NOTE = """
Return a third list.

decisions: choices the user makes or weighs in this message: picking one option \
over others, adopting a tool, plan or approach, accepting or declining an offer, \
agreeing to something, or changing an earlier choice. For each give:
- statement: one self-contained sentence in the third person ("The user decided \
to use the HTML5 validation API to validate the form fields.");
- chosen: what was picked, in a few words ("HTML5 validation API");
- alternatives: the other options named in the exchange that the user passed \
over; an empty list when none are named;
- reason: why, in a few words, when the user says so; else null;
- status: "decided" when the user commits to the choice, "considering" when they \
are still weighing it;
- date: the calendar date the choice takes effect or the chosen thing is to \
happen, as YYYY-MM-DD, resolved like the dates of facts; null when none is given.
Record a choice once, as a decision, and do not repeat it among the facts. A \
report of what exists, what happened or what is planned, with no choice between \
options in it, is a fact. At most 3 decisions; an empty list when there are none.
"""

MISSED_DETAILS_PROMPT = """\
You maintain the long-term memory of an AI assistant. A message from the user was \
read and what it says was recorded, but some details in it were left out. You are \
given the message, its date, and the DETAILS that were missed: numbers, amounts \
and dates that the user states about their own situation.

Write one fact for each missed detail, saying what the message says it is: what \
the number counts or measures, or what happens on the date. Give each fact as \
statement (one self-contained sentence in the third person, "The user ...", that \
contains the detail itself), subject, attribute, value, date (YYYY-MM-DD when the \
fact refers to a calendar date, resolved against the date of the message; else \
null) and denial (false). Leave out a detail that turns out not to be about the \
user's situation. Return the preferences list empty.
"""

CONFLICT_SENTENCE_PROMPT = """\
At one point a user made a DENIAL: a statement that they have never done, never \
had or never used something. In the MESSAGE below, written by the same user, they \
were doing or had done that very thing. Write one sentence, in the third person, \
stating what the user said in the MESSAGE that conflicts with the DENIAL ("The \
user implemented a basic homepage route with Flask."). Write only that sentence.
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
       coalesce(m.statements_extracted, false) AS statements_extracted,
       m.topic_start AS topic_start
ORDER BY m.index ASC, m.timestamp ASC, m.id ASC
"""

_SET_TOPIC_STARTS = """
UNWIND $rows AS row
MATCH (m:Message {id: row.id})
SET m.topic_start = row.probability
"""

_EXISTING_SUMMARY_SPANS = """
MATCH (s:Summary {session_id: $session_id})
RETURN s.start_index AS start_index, s.end_index AS end_index
"""

_DELETE_SUMMARIES_AT = """
MATCH (s:Summary {session_id: $session_id})
WHERE s.start_index IN $starts
DETACH DELETE s
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
        denial_probability: fact.denial_probability,
        support: fact.support,
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
        instruction_probability: pref.instruction_probability,
        support: pref.support,
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
CALL {
    WITH m
    UNWIND $decisions AS decision
    CREATE (d:Decision {
        id: decision.id,
        text: decision.text,
        chosen: decision.chosen,
        alternatives: decision.alternatives,
        reason: decision.reason,
        status: decision.status,
        commit_probability: decision.commit_probability,
        support: decision.support,
        decided_on: $said_at,
        effective_date: decision.effective_date,
        session_id: $session_id,
        message_index: $message_index,
        confidence: 1.0,
        embedding: decision.embedding,
        valid_from: $timestamp,
        valid_until: null,
        created_at: datetime(),
        metadata: $metadata
    })
    CREATE (d)-[:EXTRACTED_FROM]->(m)
    RETURN count(d) AS decisions
}
RETURN facts, preferences, decisions
"""

_SESSION_DECISIONS = """
MATCH (d:Decision {session_id: $session_id})
RETURN d.id AS id, d.text AS text, d.chosen AS chosen, d.alternatives AS alternatives,
       d.reason AS reason, d.status AS status, d.message_index AS message_index,
       d.decided_on AS decided_on, d.effective_date AS effective_date,
       d.embedding AS embedding, coalesce(d.reconciled, false) AS reconciled
ORDER BY d.message_index ASC, d.id ASC
"""

_SESSION_ENTITY_NAMES = """
MATCH (c:Conversation {session_id: $session_id})-[:HAS_MESSAGE]->(:Message)
      -[:MENTIONS]->(e:Entity)
RETURN DISTINCT e.id AS id, e.name AS name
"""

_LINK_DECISION_ENTITIES = """
UNWIND $rows AS row
MATCH (d:Decision {id: row.decision})
MATCH (e:Entity {id: row.entity})
FOREACH (_ IN CASE WHEN row.chosen THEN [1] ELSE [] END | MERGE (d)-[:CHOSE]->(e))
FOREACH (_ IN CASE WHEN row.chosen THEN [] ELSE [1] END | MERGE (d)-[:REJECTED]->(e))
"""

# A later decision that replaces an earlier one. The earlier decision is closed
# only when the judge is sure; a weaker link stays as evidence.
_APPLY_DECISION_RELATIONS = """
UNWIND $rows AS row
MATCH (n:Decision {id: row.new})
MATCH (e:Decision {id: row.earlier})
MERGE (e)-[r:SUPERSEDED_BY]->(n)
SET r.probability = row.probability, r.judge = $judge
FOREACH (_ IN CASE WHEN row.probability >= $close_above THEN [1] ELSE [] END |
    SET e.status = 'reversed', e.valid_until = coalesce(e.valid_until, n.valid_from)
)
"""

_MARK_DECISIONS_RECONCILED = """
MATCH (d:Decision) WHERE d.id IN $ids
SET d.reconciled = true
"""

# Relationships judged with a probability. ``valid_until`` is a hard statement
# other queries rely on, so it is set only above ``$close_above``.
_APPLY_JUDGED_FACT_RELATIONS = """
UNWIND $rows AS row
MATCH (n:Fact {id: row.new})
MATCH (e:Fact {id: row.earlier})
FOREACH (_ IN CASE WHEN row.relation = 'update' THEN [1] ELSE [] END |
    MERGE (e)-[r:SUPERSEDED_BY]->(n)
    SET r.probability = row.probability, r.judge = $judge
)
FOREACH (_ IN CASE WHEN row.relation = 'update' AND row.probability >= $close_above
              THEN [1] ELSE [] END |
    SET e.valid_until = coalesce(e.valid_until, n.valid_from)
)
FOREACH (_ IN CASE WHEN row.relation = 'same' THEN [1] ELSE [] END |
    MERGE (n)-[r:RESTATES]->(e)
    SET r.probability = row.probability, r.judge = $judge
)
"""

_APPLY_JUDGED_CONFLICTS = """
MATCH (d:Fact {id: $denial})
UNWIND $rows AS row
MATCH (o:Fact {id: row.id})
MERGE (d)-[r:CONTRADICTS]->(o)
SET r.probability = row.probability, r.judge = $judge
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
                       denial: coalesce(node.denial, false),
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
MERGE (d)-[r:CONTRADICTS]->(f)
SET r.probability = $probability, r.judge = $judge
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


def _with_value(fact: FactStatement) -> str:
    """The fact's sentence, with its value added when the sentence left a number out.

    The model sometimes writes "The user is optimizing the dashboard API response
    time" and puts "250ms" only in the value field; a reader of the sentence
    alone would never see it.
    """
    text = fact.statement.strip()
    value = fact.value.strip()
    if value and digits_of(value) and not mentions_detail(text, value) and len(value) <= 80:
        attribute = fact.attribute.strip()
        text = f"{text.rstrip('.')} ({attribute}: {value})." if attribute else f"{text} ({value})"
    return text


def _fact_relation_question(index: int) -> OneOf:
    """How the new fact relates to ``earlier[index]``. "unrelated" comes first:
    a judge that is unsure leans toward the first option, and should link nothing.

    "follows" keeps a later event apart from a new value: a second meeting does
    not replace the first one, while a moved deadline does replace the old date.
    """
    return OneOf(
        f"How does the statement in `new` relate to the one in `earlier[{index}]`?",
        {
            "unrelated": "They are about different things, or about different attributes "
            "of the same thing: a new detail or a related task.",
            "same": f"`new` repeats the value that `earlier[{index}]` gave, unchanged.",
            "follows": "`new` reports a later, separate event or step in the same matter: "
            "another meeting, the next milestone, a further action. What "
            f"`earlier[{index}]` reports still stands as it was.",
            "update": "`new` gives the present value of the very thing whose value "
            f"`earlier[{index}]` gave: the same deadline moved, the same measurement "
            "taken again, the same count grown, the same amount or choice changed. "
            "The earlier value is no longer the current one.",
        },
    )


_CODE = re.compile(r"```.*?```|`[^`\n]*`", re.DOTALL)
_MONTH = (
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|"
    r"Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
)
_DETAIL = re.compile(
    rf"{_MONTH}\.? \d{{1,2}}(?:st|nd|rd|th)?(?:, \d{{4}})?"  # March 29, April 6, 2024
    r"|[$€£]\s?\d(?:[\d,]*\d)?(?:\.\d+)?\s?[kKmM]?\b"  # $6,200
    r"|\d(?:[\d,]*\d)?(?:\.\d+)?\s?%"  # 25%
    r"|\d(?:[\d,]*\d)?(?:\.\d+)?(?:[-–]\d(?:[\d,]*\d)?(?:\.\d+)?)?"  # 165, 5-7, 2.3
    r"(?: ?(?:ms|s|sec|seconds?|minutes?|mins?|hours?|hrs?|days?|weeks?|months?|years?|"
    r"GB|MB|KB|TB|k|K|x))?\b"
)


def stated_details(text: str, *, limit: int = 12) -> list[str]:
    """The numbers, amounts and dates in a message, outside its code.

    Version numbers and other dotted identifiers (``2.3.1``, ``v0.6.2``) are
    left out: they name things, they are not values of them.
    """
    prose = _CODE.sub(" ", text)
    found: list[str] = []
    for match in _DETAIL.finditer(prose):
        detail = match.group(0).strip()
        before = prose[max(0, match.start() - 1) : match.start()]
        after = prose[match.end() : match.end() + 2]
        if before in (".", "v", "V") or re.match(r"\.\d", after):
            continue
        if not any(ch.isdigit() for ch in detail) or detail in found:
            continue
        found.append(detail)
    return found[:limit]


def digits_of(detail: str) -> list[str]:
    """The numbers in a detail, without thousands separators: ``$6,200`` gives ``6200``."""
    return re.findall(r"\d+(?:\.\d+)?", detail.replace(",", ""))


def mentions_detail(text: str, detail: str) -> bool:
    """Whether ``text`` contains every number of ``detail`` as a number of its own."""
    numbers = set(re.findall(r"\d+(?:\.\d+)?", text.replace(",", "")))
    wanted = digits_of(detail)
    return bool(wanted) and all(number in numbers for number in wanted)


def topic_spans(
    topic_start: Sequence[float | None],
    *,
    threshold: float = 0.5,
    shortest: int = 4,
    longest: int = 20,
) -> list[tuple[int, int]]:
    """Cut a conversation into stretches at its topic changes.

    ``topic_start[i]`` is the probability that message ``i`` opens a new topic
    (None for a message that was not judged, such as an assistant's reply). A
    stretch begins at a message whose probability is at least ``threshold``,
    provided the stretch before it is at least ``shortest`` messages long; a
    stretch longer than ``longest`` is cut into equal parts. Returns
    ``(first, last)`` message positions, covering every message.
    """
    count = len(topic_start)
    if count == 0:
        return []
    starts = [0]
    for position in range(1, count):
        probability = topic_start[position]
        if probability is not None and probability >= threshold:
            if position - starts[-1] >= shortest:
                starts.append(position)
    spans: list[tuple[int, int]] = []
    for start, stop in zip(starts, [*starts[1:], count], strict=True):
        length = stop - start
        parts = -(-length // longest)
        size = -(-length // parts)
        for first in range(start, stop, size):
            spans.append((first, min(first + size, stop) - 1))
    # A short tail joins the stretch before it.
    if len(spans) > 1 and spans[-1][1] - spans[-1][0] + 1 < shortest:
        tail = spans.pop()
        spans[-1] = (spans[-1][0], tail[1])
    return spans


def _cosine(a: Sequence[float] | None, b: Sequence[float] | None) -> float:
    """Cosine similarity of two embeddings; 0.0 when either is missing."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm = (sum(x * x for x in a) * sum(y * y for y in b)) ** 0.5
    return dot / norm if norm else 0.0


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
    """``client.distillation``: facts, preferences, decisions and summaries from a session.

    Args:
        client: The Neo4j client.
        embedder: Embeds the statements, for search and for finding candidates.
        llm: Writes the statements and summaries.
        judge: Optional :class:`~neo4j_agent_memory.judge.Judge`. When given, it
            types and verifies the statements the LLM writes and makes the
            judgements of reconciliation, each stored with its probability.
        retries: Attempts per LLM call.
    """

    def __init__(
        self,
        client: Neo4jClient,
        embedder: Embedder | None,
        llm: LLMProvider | None,
        judge: Judge | None = None,
        *,
        retries: int = 3,
    ) -> None:
        self._client = client
        self._embedder = embedder
        self._llm = llm
        self._judge = judge
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

    async def _judge_statements(
        self,
        message: dict[str, Any],
        replied_to: str | None,
        facts: list[FactStatement],
        preferences: list[PreferenceStatement],
        decisions: list[DecisionStatement],
        *,
        max_chars: int,
    ) -> dict[str, Judgement] | None:
        """Ask the judge what each statement is and whether the user said it.

        One request per message. Question names are ``supported<i>``, and by
        kind ``denial<i>``, ``instruction<i>`` or ``commit<i>``, where ``i`` is
        the statement's place among facts, then preferences, then decisions.
        """
        if self._judge is None:
            return None
        texts = (
            [f.statement for f in facts]
            + [p.statement for p in preferences]
            + [d.statement for d in decisions]
        )
        state: dict[str, Any] = {"user_message": message["content"][:max_chars]}
        if replied_to:
            state["assistant_message_before"] = replied_to
        state["statements"] = texts
        questions: dict[str, Question] = {}
        for i in range(len(texts)):
            questions[f"supported{i}"] = YesNo(
                f"Does the user say, confirm or accept `statements[{i}]` in `user_message`?",
                yes="The user states it, or accepts or confirms it where the assistant proposed it.",
                no="The user does not say it: it is only the assistant's suggestion, a "
                "question the user asks, or absent from the message.",
            )
        for i in range(len(facts)):
            questions[f"denial{i}"] = YesNo(
                f"Does `statements[{i}]` say that the user has never done something, has no "
                "experience of it, or has never had or used it?",
                yes="It states that the user never did, had or used the thing.",
                no="Anything else, including what the user wants to avoid, has not "
                "finished yet, plans to do, or tried without success.",
            )
        for i in range(len(facts), len(facts) + len(preferences)):
            questions[f"instruction{i}"] = YesNo(
                f"Is `statements[{i}]` a standing rule for how the assistant should write "
                "its answers from now on?",
                yes="A lasting instruction about the assistant's answers: what to "
                "always include, how to format them, what never to do.",
                no="Something the user likes, values or chooses, or a one-off request "
                "about the task at hand.",
            )
        for i in range(len(facts) + len(preferences), len(texts)):
            questions[f"commit{i}"] = YesNo(
                f"In `user_message`, does the user commit to the choice in `statements[{i}]`?",
                yes="The user has made the choice, or says they will go with it.",
                no="The user is still weighing it or asking about it, or it is only "
                "suggested to them.",
            )
        return await judge_safely(
            self._judge,
            state,
            questions,
            what=f"statement typing (message {message['position'] + 1})",
        )

    async def _missed_details(
        self,
        message: dict[str, Any],
        facts: list[FactStatement],
        preferences: list[PreferenceStatement],
        decisions: list[DecisionStatement],
        *,
        max_chars: int = 6000,
        limit: int = 4,
    ) -> list[FactStatement]:
        """Facts for the numbers and dates of a message that nothing recorded.

        Code finds the details and checks which are missing; the judge says
        which of the missing ones the user states about their own situation;
        the LLM writes a fact for each of those.
        """
        judge = self._judge
        if judge is None:
            return []
        recorded = " ".join(
            [f"{f.statement} {f.value} {f.date or ''}" for f in facts]
            + [p.statement for p in preferences]
            + [f"{d.statement} {d.chosen} {d.date or ''}" for d in decisions]
        )
        missing = [
            detail
            for detail in stated_details(message["content"])
            if not mentions_detail(recorded, detail)
        ]
        if not missing:
            return []
        answers = await judge_safely(
            judge,
            {"user_message": message["content"][:max_chars], "details": missing},
            {
                f"v{i}": YesNo(
                    f"In `user_message`, does the user state `details[{i}]` as a fact about "
                    "their own situation: an amount, a count, a measurement, a date or a "
                    "deadline of theirs?",
                    yes="The user reports it as how things are, were or will be for them.",
                    no="It appears only in code or an example, in a label or reference "
                    "mark, or in a question about what to do.",
                )
                for i in range(len(missing))
            },
            what=f"missed details (message {message['position'] + 1})",
        )
        if answers is None:
            return []
        wanted = [d for i, d in enumerate(missing) if answers[f"v{i}"].probability >= 0.5][:limit]
        if not wanted:
            return []
        prompt = [
            ChatMessage(role="system", content=MISSED_DETAILS_PROMPT),
            ChatMessage(
                role="user",
                content=f"Date of the message: {_date_of(message['timestamp']) or 'unknown'}\n\n"
                f"DETAILS that were missed: {'; '.join(wanted)}\n\n"
                f"Message from the user:\n{message['content']}",
            ),
        ]
        found = await self._with_retries(
            lambda: self._require_llm().complete_structured(
                prompt, MessageStatements, temperature=0.0
            ),
            f"missed details (message {message['position'] + 1})",
        )
        if found is None:
            return []
        # Keep only facts that do carry one of the details asked for.
        return [
            fact
            for fact in found.facts
            if fact.statement.strip()
            and any(mentions_detail(f"{fact.statement} {fact.value}", d) for d in wanted)
        ][:limit]

    async def extract_statements(
        self,
        session_id: str,
        *,
        roles: Sequence[str] = ("user",),
        skip_existing: bool = True,
        reply_context: bool = False,
        reply_context_chars: int = 1500,
        decisions: bool = False,
        verify: bool = True,
        complete: bool = True,
        min_support: float = 0.2,
        max_concurrency: int = 8,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> dict[str, int]:
        """Store the facts, preferences, instructions and decisions stated in a session.

        Args:
            session_id: The session to read.
            roles: Roles whose messages are read. The default reads what the
                user wrote: what the assistant wrote is advice, not a record of
                the user's situation.
            skip_existing: Skip messages an earlier run already read.
            reply_context: Show the LLM the end of the message being replied
                to (its last ``reply_context_chars`` characters), so that "I'll
                go with that" can be recorded as what was chosen.
            decisions: Also record the choices the user makes, as ``:Decision``
                nodes with what was chosen, what was passed over and why.
            verify: With a judge configured, have it check every statement
                against the message and type it. A statement whose probability
                of being said by the user is under ``min_support`` is dropped;
                whether a fact is a denial, a preference a standing instruction
                and a decision a commitment is taken from the judge and stored
                with its probability.
            complete: With a judge configured, look for numbers, amounts and
                dates in the message that no statement records. The judge says
                which of them the user states about their own situation, and
                the LLM is asked once more for a fact about each of those.
            max_concurrency: LLM calls in flight.
            on_progress: Called with (messages done, messages to do).

        Returns:
            ``messages_processed``, ``facts``, ``preferences`` (of which
            ``instructions``), ``decisions``, ``dropped`` (statements the judge
            did not find in their message), ``details_recovered`` (facts added
            for details the first reading missed) and ``failures`` (messages the LLM
            never answered for; they stay unread and a later run picks them up).
        """
        llm = self._require_llm()
        everything = await self._session_messages(session_id)
        messages = [
            m
            for m in everything
            if m["role"] in roles and not (skip_existing and m["statements_extracted"])
        ]
        stats = {
            "messages_processed": 0,
            "facts": 0,
            "preferences": 0,
            "instructions": 0,
            "decisions": 0,
            "dropped": 0,
            "details_recovered": 0,
            "failures": 0,
        }
        system = STATEMENTS_PROMPT
        if decisions:
            system += DECISIONS_NOTE
        if reply_context:
            system += REPLY_CONTEXT_NOTE
        payload: type[BaseModel] = MessageStatementsAndDecisions if decisions else MessageStatements

        async def one(message: dict[str, Any]) -> None:
            said_at = _date_of(message["timestamp"])
            replied_to: str | None = None
            if reply_context and message["position"] > 0:
                before = everything[message["position"] - 1]
                if before["role"] != message["role"] and before["content"]:
                    replied_to = before["content"][-reply_context_chars:]
            content = f"Date of the message: {said_at or 'unknown'}\n\n"
            if replied_to:
                content += (
                    "End of the assistant's message the user replies to (context only):\n"
                    f"<assistant>\n{replied_to}\n</assistant>\n\n"
                )
            content += f"Message from the user:\n{message['content']}"
            prompt = [
                ChatMessage(role="system", content=system),
                ChatMessage(role="user", content=content),
            ]
            found = await self._with_retries(
                lambda: llm.complete_structured(prompt, payload, temperature=0.0),
                f"statement extraction (message {message['position'] + 1})",
            )
            if found is None:
                stats["failures"] += 1
                return
            facts: list[FactStatement] = [f for f in found.facts if f.statement.strip()][:8]
            preferences: list[PreferenceStatement] = [
                p for p in found.preferences if p.statement.strip()
            ][:4]
            chosen: list[DecisionStatement] = [
                d for d in getattr(found, "decisions", []) if d.statement.strip()
            ][:3]
            if not facts and not preferences and not chosen:
                await self._client.execute_write(_MARK_MESSAGE_DONE, {"message_id": message["id"]})
                stats["messages_processed"] += 1
                return

            if complete and self._judge is not None:
                missed = await self._missed_details(message, facts, preferences, chosen)
                if missed:
                    stats["details_recovered"] += len(missed)
                    facts = (facts + missed)[:12]

            judged = (
                await self._judge_statements(
                    message, replied_to, facts, preferences, chosen, max_chars=6000
                )
                if verify
                else None
            )

            def probability(name: str) -> float | None:
                return judged[name].probability if judged and name in judged else None

            fact_rows: list[dict[str, Any]] = []
            preference_rows: list[dict[str, Any]] = []
            decision_rows: list[dict[str, Any]] = []
            for i, fact in enumerate(facts):
                support = probability(f"supported{i}")
                if support is not None and support < min_support:
                    stats["dropped"] += 1
                    continue
                denial = probability(f"denial{i}")
                fact_rows.append(
                    {
                        "id": str(uuid4()),
                        "subject": fact.subject.strip(),
                        "predicate": fact.attribute.strip(),
                        "object": fact.value.strip(),
                        "text": _with_value(fact),
                        "event_date": _valid_date(fact.date),
                        "denial": bool(fact.denial) if denial is None else denial >= 0.5,
                        "denial_probability": denial,
                        "support": support,
                    }
                )
            for i, pref in enumerate(preferences, start=len(facts)):
                support = probability(f"supported{i}")
                if support is not None and support < min_support:
                    stats["dropped"] += 1
                    continue
                instruction = probability(f"instruction{i}")
                kind = pref.kind
                if instruction is not None:
                    kind = "instruction" if instruction >= 0.5 else "preference"
                preference_rows.append(
                    {
                        "id": str(uuid4()),
                        "category": pref.category.strip().lower(),
                        "preference": pref.statement.strip(),
                        "kind": kind,
                        "instruction_probability": instruction,
                        "support": support,
                    }
                )
            for i, decision in enumerate(chosen, start=len(facts) + len(preferences)):
                support = probability(f"supported{i}")
                if support is not None and support < min_support:
                    stats["dropped"] += 1
                    continue
                commit = probability(f"commit{i}")
                status = decision.status
                if commit is not None:
                    status = "decided" if commit >= 0.5 else "considering"
                decision_rows.append(
                    {
                        "id": str(uuid4()),
                        "text": decision.statement.strip(),
                        "chosen": decision.chosen.strip(),
                        "alternatives": [a.strip() for a in decision.alternatives if a.strip()],
                        "reason": (decision.reason or "").strip() or None,
                        "status": status,
                        "commit_probability": commit,
                        "support": support,
                        "effective_date": _valid_date(decision.date),
                    }
                )
            rows = fact_rows + preference_rows + decision_rows
            if not rows:
                await self._client.execute_write(_MARK_MESSAGE_DONE, {"message_id": message["id"]})
                stats["messages_processed"] += 1
                return

            texts = [row.get("text") or row["preference"] for row in rows]
            embeddings: list[list[float] | None] = [None] * len(texts)
            if self._embedder is not None:
                embeddings = list(await self._embedder.embed_batch(texts))
            for row, embedding in zip(rows, embeddings, strict=True):
                row["embedding"] = embedding
            await self._client.execute_write(
                _STORE_STATEMENTS,
                {
                    "message_id": message["id"],
                    "session_id": session_id,
                    "message_index": message["position"],
                    "said_at": said_at,
                    "timestamp": message["timestamp"],
                    "metadata": json.dumps({"source": "distillation"}),
                    "facts": fact_rows,
                    "preferences": preference_rows,
                    "decisions": decision_rows,
                },
            )
            stats["messages_processed"] += 1
            stats["facts"] += len(fact_rows)
            stats["preferences"] += len(preference_rows)
            stats["instructions"] += sum(p["kind"] == "instruction" for p in preference_rows)
            stats["decisions"] += len(decision_rows)

        await self._gather([partial(one, m) for m in messages], max_concurrency, on_progress)
        if stats["decisions"]:
            await self._link_decisions_to_entities(session_id)
        return stats

    async def _link_decisions_to_entities(self, session_id: str) -> int:
        """Link each decision to the session's entities it chose or passed over.

        An entity is linked when its name appears as whole words in what was
        chosen (``CHOSE``) or in an alternative (``REJECTED``). Names shorter
        than three characters are ignored.
        """
        decisions = await self._client.execute_read(_SESSION_DECISIONS, {"session_id": session_id})
        entities = await self._client.execute_read(
            _SESSION_ENTITY_NAMES, {"session_id": session_id}
        )
        patterns = [
            (e["id"], re.compile(rf"(?<!\w){re.escape(e['name'].strip().lower())}(?!\w)"))
            for e in entities
            if e.get("name") and len(e["name"].strip()) >= 3
        ]
        rows: list[dict[str, Any]] = []
        for decision in decisions:
            chosen = (decision.get("chosen") or "").lower()
            passed_over = " | ".join(decision.get("alternatives") or []).lower()
            for entity_id, pattern in patterns:
                if chosen and pattern.search(chosen):
                    rows.append({"decision": decision["id"], "entity": entity_id, "chosen": True})
                elif passed_over and pattern.search(passed_over):
                    rows.append({"decision": decision["id"], "entity": entity_id, "chosen": False})
        if rows:
            await self._client.execute_write(_LINK_DECISION_ENTITIES, {"rows": rows})
        return len(rows)

    # -- reconciliation -------------------------------------------------------

    async def reconcile(
        self,
        session_id: str,
        *,
        candidates: int | None = None,
        min_similarity: float | None = None,
        denial_candidates: int | None = None,
        denial_similarity: float | None = None,
        denial_messages: int | None = None,
        denial_message_chars: int = 1200,
        preference_similarity: float = 0.96,
        store_floor: float = 0.3,
        close_above: float = 0.8,
        max_concurrency: int = 8,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> dict[str, int]:
        """Find the statements that replace or contradict earlier ones.

        Each fact not yet reconciled is compared with up to ``candidates``
        earlier facts of the session whose embedding similarity is at least
        ``min_similarity`` (Neo4j's cosine score, between 0 and 1), and each
        pair is judged an update, a restatement or unrelated.

        Each denial among them ("has never ...") is compared with up to
        ``denial_candidates`` statements and ``denial_messages`` user messages
        (their first ``denial_message_chars`` characters) from up to and
        including the denial's own message, and linked to what it conflicts
        with. A conflict found in a message becomes a statement of its own. A
        statement made after a denial is not held against it: by then the user
        may have done the thing.

        Each decision is compared with the earlier decisions it resembles, and
        with those that named its choice as an alternative; a decision that
        settles the same question differently replaces the earlier one.

        **Without a judge** the LLM decides, for the facts of one message at a
        time, and its answers are final. **With a judge**
        (:class:`~neo4j_agent_memory.judge.Judge`) every pair is judged on its
        own and more candidates are considered (the defaults widen from 5 to 15
        facts and from 8 to 24 statements per denial). A relationship is stored
        with the judge's ``probability`` whenever that is at least
        ``store_floor``, so that a reader can apply its own threshold; the
        earlier statement is closed (``valid_until``, a decision's status
        ``reversed``) only at ``close_above``.

        Returns:
            ``facts_checked``, ``updates``, ``restatements``, ``denials`` and the
            ``contradictions`` found for them, ``decisions_checked``,
            ``decisions_reversed``, ``preferences_superseded`` and ``failures``.
            With a judge, ``updates`` and ``contradictions`` count the
            relationships at probability 0.5 or more.
        """
        judged = self._judge is not None

        def default(value: Any, with_judge: Any, without: Any) -> Any:
            return value if value is not None else (with_judge if judged else without)

        candidates = default(candidates, 15, 5)
        min_similarity = default(min_similarity, 0.72, 0.78)
        denial_candidates = default(denial_candidates, 24, 8)
        denial_similarity = default(denial_similarity, 0.62, 0.70)
        denial_messages = default(denial_messages, 6, 4)
        if judged:
            stats = await self._reconcile_facts_with_judge(
                session_id,
                candidates=candidates,
                min_similarity=min_similarity,
                denial_candidates=denial_candidates,
                denial_similarity=denial_similarity,
                denial_messages=denial_messages,
                denial_message_chars=denial_message_chars,
                store_floor=store_floor,
                close_above=close_above,
                max_concurrency=max_concurrency,
                on_progress=on_progress,
            )
        else:
            stats = await self._reconcile_facts_with_llm(
                session_id,
                candidates=candidates,
                min_similarity=min_similarity,
                denial_candidates=denial_candidates,
                denial_similarity=denial_similarity,
                denial_messages=denial_messages,
                denial_message_chars=denial_message_chars,
                max_concurrency=max_concurrency,
                on_progress=on_progress,
            )
        stats.update(
            await self._reconcile_decisions(
                session_id,
                store_floor=store_floor,
                close_above=close_above,
                max_concurrency=max_concurrency,
            )
        )
        superseded = await self._client.execute_write(
            _SUPERSEDE_RESTATED_PREFERENCES,
            {"session_id": session_id, "fetch": 24, "min_score": preference_similarity},
        )
        stats["preferences_superseded"] = superseded[0]["superseded"] if superseded else 0
        return stats

    async def _reconcile_facts_with_llm(
        self,
        session_id: str,
        *,
        candidates: int,
        min_similarity: float,
        denial_candidates: int,
        denial_similarity: float,
        denial_messages: int,
        denial_message_chars: int,
        max_concurrency: int,
        on_progress: Callable[[int, int], None] | None,
    ) -> dict[str, int]:
        """The facts of one message at a time, decided by the LLM."""
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
                        "probability": None,
                        "judge": None,
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
        return stats

    async def _reconcile_facts_with_judge(
        self,
        session_id: str,
        *,
        candidates: int,
        min_similarity: float,
        denial_candidates: int,
        denial_similarity: float,
        denial_messages: int,
        denial_message_chars: int,
        store_floor: float,
        close_above: float,
        max_concurrency: int,
        on_progress: Callable[[int, int], None] | None,
    ) -> dict[str, int]:
        """Every pair on its own, judged with a probability."""
        judge = self._judge
        assert judge is not None
        pending = await self._client.execute_read(_UNRECONCILED_FACTS, {"session_id": session_id})
        positions = {m["id"]: m for m in await self._session_messages(session_id)}
        stats = {
            "facts_checked": 0,
            "updates": 0,
            "denials": 0,
            "contradictions": 0,
            "restatements": 0,
            "failures": 0,
        }

        def shown(fact: dict[str, Any]) -> dict[str, Any]:
            return {
                "statement": fact["text"],
                "subject": fact.get("subject"),
                "attribute": fact.get("attribute"),
                "value": fact.get("value"),
                "said_on": fact.get("said_at"),
            }

        async def one(fact: dict[str, Any]) -> None:
            rows = (
                []
                # A denial is checked against what conflicts with it, above; it
                # is not a new value for anything.
                if fact["denial"]
                else await self._client.execute_read(
                    _EARLIER_SIMILAR_FACTS,
                    {
                        "ids": [fact["id"]],
                        "session_id": session_id,
                        "fetch": candidates * 8,
                        "limit": candidates,
                        "min_score": min_similarity,
                    },
                )
            )
            earlier = [c for c in (rows[0]["earlier"] if rows else []) if not c.get("denial")]
            if earlier:
                answers = await judge_safely(
                    judge,
                    {"new": shown(fact), "earlier": [shown(c) for c in earlier]},
                    {f"e{i}": _fact_relation_question(i) for i in range(len(earlier))},
                    what=f"fact reconciliation (message {fact['message_index'] + 1})",
                )
                if answers is None:
                    stats["failures"] += 1
                    return
                applied: list[dict[str, Any]] = []
                for i, candidate in enumerate(earlier):
                    answer = answers[f"e{i}"]
                    update, same = answer.of("update"), answer.of("same")
                    if update >= store_floor and update >= same:
                        relation, probability = "update", update
                    elif same >= 0.5:
                        relation, probability = "same", same
                    else:
                        continue
                    applied.append(
                        {
                            "new": fact["id"],
                            "earlier": candidate["id"],
                            "relation": relation,
                            "probability": probability,
                        }
                    )
                if applied:
                    await self._client.execute_write(
                        _APPLY_JUDGED_FACT_RELATIONS,
                        {"rows": applied, "judge": judge.name, "close_above": close_above},
                    )
                stats["updates"] += sum(
                    r["relation"] == "update" and r["probability"] >= 0.5 for r in applied
                )
                stats["restatements"] += sum(r["relation"] == "same" for r in applied)
            await self._client.execute_write(_MARK_FACTS_RECONCILED, {"ids": [fact["id"]]})
            stats["facts_checked"] += 1

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
            covered: set[int] = set()
            if statements:
                answers = await judge_safely(
                    judge,
                    {"denial": denial["text"], "statements": [s["text"] for s in statements]},
                    {
                        f"s{i}": YesNo(
                            f"Does `statements[{i}]` show the user doing, having done, using "
                            "or already working on the very thing that `denial` says they "
                            "have never done, had or used?",
                            yes="The user was implementing, integrating, configuring, using "
                            "or testing that very thing, also when asking for help with it.",
                            no="It is about something else, or the user only wonders "
                            "whether to do the thing, or plans to start on it.",
                        )
                        for i in range(len(statements))
                    },
                    what=f"denial check (message {denial['message_index'] + 1})",
                )
                if answers is None:
                    stats["failures"] += 1
                    return
                rows = [
                    {"id": s["id"], "probability": answers[f"s{i}"].probability}
                    for i, s in enumerate(statements)
                    if answers[f"s{i}"].probability >= store_floor
                ]
                if rows:
                    await self._client.execute_write(
                        _APPLY_JUDGED_CONFLICTS,
                        {"denial": denial["id"], "rows": rows, "judge": judge.name},
                    )
                by_id = {s["id"]: s for s in statements}
                covered = {by_id[r["id"]]["message_index"] for r in rows if r["probability"] >= 0.5}
                stats["contradictions"] += sum(r["probability"] >= 0.5 for r in rows)

            async def check_message(row: dict[str, Any]) -> None:
                message = positions[row["id"]]
                if message["position"] in covered:
                    return
                excerpt = row["content"][:denial_message_chars]
                answers = await judge_safely(
                    judge,
                    {"denial": denial["text"], "message": excerpt},
                    {
                        "conflict": YesNo(
                            "In `message`, is the user doing, or has the user done, used or "
                            "already started working on, the very thing that `denial` says "
                            "they have never done, had or used?",
                            yes="The user is implementing, integrating, configuring, using "
                            "or testing that very thing in the message, also when asking "
                            "for help with it.",
                            no="The message is about something else, only repeats the "
                            "denial, or the user only wonders whether to do the thing.",
                        )
                    },
                    what=f"denial check against a message (message {message['position'] + 1})",
                )
                if answers is None or answers["conflict"].probability < 0.5:
                    return
                written = await self._with_retries(
                    lambda: self._require_llm().complete(
                        [
                            ChatMessage(role="system", content=CONFLICT_SENTENCE_PROMPT),
                            ChatMessage(
                                role="user",
                                content=f"DENIAL: {denial['text']}\n\nMESSAGE:\n{excerpt}",
                            ),
                        ],
                        temperature=0.0,
                        max_tokens=80,
                    ),
                    f"conflict sentence (message {message['position'] + 1})",
                )
                said = written.content.strip() if written is not None else ""
                # A conflicting statement is something the user did, not
                # another denial: the model sometimes echoes the denial back.
                if not said or "never" in said.lower() or message["position"] in covered:
                    return
                covered.add(message["position"])
                embedding = await self._embedder.embed(said) if self._embedder is not None else None
                await self._client.execute_write(
                    _STORE_CONFLICTING_STATEMENT,
                    {
                        "denial": denial["id"],
                        "message_id": row["id"],
                        "id": str(uuid4()),
                        "text": said,
                        "said_at": _date_of(message["timestamp"]),
                        "session_id": session_id,
                        "message_index": message["position"],
                        "embedding": embedding,
                        "timestamp": message["timestamp"],
                        "metadata": json.dumps({"source": "distillation:denial-check"}),
                        "probability": answers["conflict"].probability,
                        "judge": judge.name,
                    },
                )
                stats["contradictions"] += 1

            await asyncio.gather(*(check_message(row) for row in messages))

        # Denials first: a conflict found in a message adds a statement, and
        # the general pass marks the facts as reconciled.
        denials = [row for row in pending if row["denial"]]
        stats["denials"] = len(denials)
        await self._gather([partial(check_denial, d) for d in denials], max_concurrency, None)
        await self._gather([partial(one, f) for f in pending], max_concurrency, on_progress)
        return stats

    async def _reconcile_decisions(
        self,
        session_id: str,
        *,
        candidates: int = 8,
        min_similarity: float = 0.5,
        store_floor: float,
        close_above: float,
        max_concurrency: int,
    ) -> dict[str, int]:
        """Find the decisions that replace earlier ones.

        A decision is compared with the earlier decisions whose embedding is
        within ``min_similarity`` (plain cosine) and with every earlier decision
        that named its choice among the alternatives, or whose choice it names
        among its own. Without a judge the LLM answers the same questions.
        """
        stats = {"decisions_checked": 0, "decisions_reversed": 0}
        decisions = await self._client.execute_read(_SESSION_DECISIONS, {"session_id": session_id})
        pending = [d for d in decisions if not d["reconciled"]]
        if not pending:
            return stats
        judge: Judge = self._judge if self._judge is not None else LLMJudge(self._require_llm())

        def named(decision: dict[str, Any]) -> tuple[str, set[str]]:
            return (
                (decision.get("chosen") or "").strip().lower(),
                {a.strip().lower() for a in decision.get("alternatives") or [] if a.strip()},
            )

        def shown(decision: dict[str, Any]) -> dict[str, Any]:
            return {
                "decision": decision["text"],
                "chosen": decision.get("chosen"),
                "passed_over": decision.get("alternatives") or [],
                "said_on": decision.get("decided_on"),
            }

        async def one(decision: dict[str, Any]) -> None:
            chosen, passed_over = named(decision)
            scored: list[tuple[float, dict[str, Any]]] = []
            for other in decisions:
                if other["message_index"] >= decision["message_index"]:
                    continue
                other_chosen, other_passed_over = named(other)
                flagged = bool(chosen and chosen in other_passed_over) or bool(
                    other_chosen and other_chosen in passed_over
                )
                similarity = _cosine(decision.get("embedding"), other.get("embedding"))
                if flagged or similarity >= min_similarity:
                    scored.append((2.0 if flagged else similarity, other))
            earlier = [other for _, other in sorted(scored, key=lambda x: -x[0])[:candidates]]
            if earlier:
                answers = await judge_safely(
                    judge,
                    {"new": shown(decision), "earlier": [shown(e) for e in earlier]},
                    {
                        f"e{i}": OneOf(
                            f"How does the decision in `new` relate to the one in `earlier[{i}]`?",
                            {
                                "unrelated": "They settle different questions.",
                                "same": "`new` repeats or confirms the choice made in "
                                f"`earlier[{i}]`.",
                                "replaces": "`new` settles the same question differently: the "
                                "user changed their mind, or moved on from the earlier "
                                "choice to another.",
                            },
                        )
                        for i in range(len(earlier))
                    },
                    what=f"decision reconciliation (message {decision['message_index'] + 1})",
                )
                if answers is None:
                    return
                rows = [
                    {
                        "new": decision["id"],
                        "earlier": e["id"],
                        "probability": answers[f"e{i}"].of("replaces"),
                    }
                    for i, e in enumerate(earlier)
                    if answers[f"e{i}"].of("replaces") >= store_floor
                    and answers[f"e{i}"].of("replaces") >= answers[f"e{i}"].of("same")
                ]
                if rows:
                    await self._client.execute_write(
                        _APPLY_DECISION_RELATIONS,
                        {"rows": rows, "judge": judge.name, "close_above": close_above},
                    )
                stats["decisions_reversed"] += sum(r["probability"] >= close_above for r in rows)
            await self._client.execute_write(_MARK_DECISIONS_RECONCILED, {"ids": [decision["id"]]})
            stats["decisions_checked"] += 1

        await self._gather([partial(one, d) for d in pending], max_concurrency, None)
        return stats

    # -- summaries ------------------------------------------------------------

    async def mark_topic_starts(
        self,
        session_id: str,
        *,
        role: str = "user",
        max_message_chars: int = 1500,
        max_concurrency: int = 8,
    ) -> int:
        """Judge, for each message of ``role``, whether it opens a new topic.

        The judge compares the message with the previous one of the same role,
        and its probability is stored on the message as ``topic_start``.
        Messages already judged are skipped. Needs a judge; returns the number
        of messages judged.
        """
        if self._judge is None:
            return 0
        judge = self._judge
        messages = [m for m in await self._session_messages(session_id) if m["role"] == role]
        rows: list[dict[str, Any]] = []

        async def one(previous: dict[str, Any], message: dict[str, Any]) -> None:
            answers = await judge_safely(
                judge,
                {
                    "previous_message": previous["content"][:max_message_chars],
                    "message": message["content"][:max_message_chars],
                },
                {
                    "new_topic": YesNo(
                        "Does `message` move the conversation to a different subject "
                        "from the one `previous_message` was about?",
                        yes="It takes up another subject or another part of the work.",
                        no="It continues with the same subject: a follow-up, a next "
                        "step, a detail or a problem with the same thing.",
                    )
                },
                what=f"topic start (message {message['position'] + 1})",
            )
            if answers is not None:
                rows.append({"id": message["id"], "probability": answers["new_topic"].probability})

        jobs = [
            partial(one, previous, message)
            for previous, message in zip(messages, messages[1:], strict=False)
            if message.get("topic_start") is None
        ]
        await self._gather(jobs, max_concurrency, None)
        if rows:
            await self._client.execute_write(_SET_TOPIC_STARTS, {"rows": rows})
        return len(rows)

    async def summarize_session(
        self,
        session_id: str,
        *,
        span: int = 10,
        by_topic: bool = False,
        topic_threshold: float = 0.5,
        skip_existing: bool = True,
        max_message_chars: int = 3000,
        max_concurrency: int = 8,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> dict[str, int]:
        """Write the outline of a session as ``:Summary`` nodes.

        By default there is one summary per ``span`` messages. A trailing
        stretch shorter than ``span`` is summarized too; run the job again
        after more messages arrive and that last summary is rewritten.

        With ``by_topic`` and a judge, the stretches follow the conversation
        instead: a stretch starts where a user message opens a new topic
        (``topic_start`` at ``topic_threshold`` or more, see
        :meth:`mark_topic_starts`), is at least ``span // 2`` messages long and
        at most ``2 * span``. Summaries of stretches that no longer exist are
        removed.

        Returns:
            ``summaries`` written and ``failures``.
        """
        llm = self._require_llm()
        stats = {"summaries": 0, "failures": 0}
        if by_topic and self._judge is not None:
            await self.mark_topic_starts(session_id, max_concurrency=max_concurrency)
            messages = await self._session_messages(session_id)
            spans = topic_spans(
                [m.get("topic_start") for m in messages],
                threshold=topic_threshold,
                shortest=max(2, span // 2),
                longest=2 * span,
            )
            stretches = [messages[first : last + 1] for first, last in spans]
            existing_spans = {
                (row["start_index"], row["end_index"])
                for row in await self._client.execute_read(
                    _EXISTING_SUMMARY_SPANS, {"session_id": session_id}
                )
            }
            wanted = set(spans)
            stale = sorted({start for start, end in existing_spans if (start, end) not in wanted})
            if stale:
                await self._client.execute_write(
                    _DELETE_SUMMARIES_AT, {"session_id": session_id, "starts": stale}
                )
            # The last stretch may still grow, so it is always rewritten.
            existing = {start for start, end in existing_spans & wanted if end != len(messages) - 1}
        else:
            messages = await self._session_messages(session_id)
            stretches = [messages[start : start + span] for start in range(0, len(messages), span)]
            existing = {
                row["start_index"]
                for row in await self._client.execute_read(
                    _EXISTING_SUMMARY_STARTS, {"session_id": session_id}
                )
                # A trailing stretch shorter than the span is rewritten.
                if row["start_index"] + span <= len(messages)
            }

        async def one(stretch: list[dict[str, Any]]) -> None:
            start = stretch[0]["position"]
            if skip_existing and start in existing:
                return
            # A long stretch is shown evenly cut, so that its end is not lost.
            chars = max(600, min(max_message_chars, int(span * max_message_chars / len(stretch))))
            transcript = (
                "<transcript>\n"
                + "\n\n".join(f"{m['role'].upper()}: {m['content'][:chars]}" for m in stretch)
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

        await self._gather([partial(one, s) for s in stretches], max_concurrency, on_progress)
        return stats

    # -- all three ------------------------------------------------------------

    async def distill_session(
        self,
        session_id: str,
        *,
        summary_span: int = 10,
        reply_context: bool = False,
        decisions: bool = False,
        verify: bool = True,
        complete: bool = True,
        topic_summaries: bool = False,
        max_concurrency: int = 8,
    ) -> dict[str, dict[str, int]]:
        """Extract statements, reconcile them and summarize the session.

        ``reply_context``, ``decisions``, ``verify`` and ``complete`` are those of
        :meth:`extract_statements`; ``topic_summaries`` is that of
        :meth:`summarize_session`.
        """
        statements = await self.extract_statements(
            session_id,
            reply_context=reply_context,
            decisions=decisions,
            verify=verify,
            complete=complete,
            max_concurrency=max_concurrency,
        )
        reconciliation = await self.reconcile(session_id, max_concurrency=max_concurrency)
        summaries = await self.summarize_session(
            session_id, span=summary_span, by_topic=topic_summaries, max_concurrency=max_concurrency
        )
        return {
            "statements": statements,
            "reconciliation": reconciliation,
            "summaries": summaries,
        }
