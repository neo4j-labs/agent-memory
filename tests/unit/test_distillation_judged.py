"""Unit tests for distillation with a judge, decisions and reply context.

The LLM, the judge, the embedder and Neo4j are faked: these tests cover what is
asked, what is stored and with which probability.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from neo4j_agent_memory.judge import Judgement, OneOf, YesNo
from neo4j_agent_memory.llm.types import Completion
from neo4j_agent_memory.memory.distillation import (
    DecisionStatement,
    DistillationMemory,
    FactStatement,
    MessageStatements,
    MessageStatementsAndDecisions,
    PreferenceStatement,
    _with_value,
    entities_named_in_texts,
    mentions_detail,
    stated_details,
    topic_spans,
)


class FakeClient:
    """Reads answer from canned rows by a marker in the query; writes are recorded."""

    def __init__(self, rows: dict[str, list[dict[str, Any]]] | None = None) -> None:
        self.rows = rows or {}
        self.writes: list[tuple[str, dict[str, Any]]] = []

    async def execute_read(self, query: str, parameters: dict[str, Any] | None = None, **_: Any):
        for marker, rows in self.rows.items():
            if marker in query:
                return [dict(row) for row in rows]
        return []

    async def execute_write(self, query: str, parameters: dict[str, Any] | None = None, **_: Any):
        self.writes.append((query, parameters or {}))
        if "RETURN count(*) AS superseded" in query:
            return [{"superseded": 0}]
        return []

    def written(self, marker: str) -> list[dict[str, Any]]:
        return [params for query, params in self.writes if marker in query]


class FakeEmbedder:
    async def embed(self, text: str) -> list[float]:
        return [float(len(text))]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [[float(len(text))] for text in texts]


class FakeLLM:
    """Structured answers by response model (a value, or a function of the prompt)."""

    model = "fake/model"

    def __init__(self, structured: dict[type, Any] | None = None, text: str = "") -> None:
        self.structured = structured or {}
        self.text = text
        self.prompts: list[str] = []
        self.systems: list[str] = []

    async def complete_structured(self, messages, response_model, **_: Any):
        self.systems.append(messages[0].content)
        self.prompts.append(messages[-1].content)
        answer = self.structured[response_model]
        return answer(messages[-1].content) if callable(answer) else answer

    async def complete(self, messages, **_: Any) -> Completion:
        self.prompts.append(messages[-1].content)
        return Completion(content=self.text, model=self.model)


class FakeJudge:
    """Answers each question through ``answer(name, question, state)``.

    A yes/no answer is a probability; a choice is a mapping of option to
    probability. Unanswered yes/no questions are 0.0; unanswered choices pick
    their first option.
    """

    name = "fake-judge"

    def __init__(self, answer: Callable[[str, Any, Any], Any] | None = None) -> None:
        self.answer = answer or (lambda *_: None)
        self.calls: list[tuple[Any, dict[str, Any]]] = []

    async def judge(self, state: Any, questions: dict[str, Any]) -> dict[str, Judgement]:
        self.calls.append((state, dict(questions)))
        answers: dict[str, Judgement] = {}
        for name, question in questions.items():
            given = self.answer(name, question, state)
            if isinstance(question, YesNo):
                answers[name] = Judgement(float(given or 0.0))
            else:
                assert isinstance(question, OneOf)
                probabilities = given or {next(iter(question.options)): 1.0}
                choice = max(probabilities, key=probabilities.get)
                answers[name] = Judgement(probabilities[choice], choice, probabilities)
        return answers


def _messages(count: int = 4, contents: dict[int, str] | None = None) -> list[dict[str, Any]]:
    return [
        {
            "id": f"m{i}",
            "role": "user" if i % 2 == 0 else "assistant",
            "content": (contents or {}).get(i, f"message {i}"),
            "timestamp": "2024-03-15T00:00:00Z",
            "statements_extracted": False,
            "topic_start": None,
        }
        for i in range(count)
    ]


def _fact(statement: str, *, value: str = "v", denial: bool = False) -> FactStatement:
    return FactStatement(
        statement=statement, subject="s", attribute="a", value=value, date=None, denial=denial
    )


class TestDetails:
    def test_numbers_amounts_and_dates_are_found_outside_code(self) -> None:
        text = (
            "Response time improved to 250ms; budget is $6,200, 25% spent, due April 22, "
            "takes 5-7 months, 165 commits. ```limit = 4000``` and `port 5000`."
        )
        assert stated_details(text) == [
            "250ms",
            "$6,200",
            "25%",
            "April 22",
            "5-7 months",
            "165",
        ]

    def test_version_numbers_are_not_details(self) -> None:
        assert stated_details("Using Flask 2.3.1 with Flask-Login v0.6.2.") == []

    def test_a_detail_is_mentioned_when_its_numbers_are(self) -> None:
        assert mentions_detail("The budget is $6200 in total.", "$6,200")
        assert mentions_detail("It takes 5 to 7 months.", "5-7 months")
        assert not mentions_detail("It has 1650 commits.", "165")

    def test_a_value_left_out_of_the_sentence_is_added(self) -> None:
        fact = FactStatement(
            statement="The user is optimizing the dashboard API response time.",
            subject="dashboard API",
            attribute="response time",
            value="250ms",
            date=None,
            denial=False,
        )
        assert _with_value(fact) == (
            "The user is optimizing the dashboard API response time (response time: 250ms)."
        )

    def test_a_sentence_that_has_its_value_is_left_alone(self) -> None:
        fact = _fact("The user merged 165 commits.", value="165")
        assert _with_value(fact) == "The user merged 165 commits."


class TestReplyContextAndDecisions:
    @pytest.mark.asyncio
    async def test_the_reply_context_shows_the_end_of_the_message_replied_to(self) -> None:
        rows = _messages(
            4, {1: "x" * 50 + " Shall we use Argon2 or bcrypt?", 2: "I'll go with that."}
        )
        llm = FakeLLM({MessageStatements: MessageStatements(facts=[], preferences=[])})
        memory = DistillationMemory(FakeClient({"statements_extracted": rows}), FakeEmbedder(), llm)  # type: ignore[arg-type]
        await memory.extract_statements("s", reply_context=True, reply_context_chars=30)
        first, second = sorted(llm.prompts, key=len)
        assert "<assistant>" not in first  # the opening message replies to nothing
        assert "<assistant>\nShall we use Argon2 or bcrypt?\n</assistant>" in second
        assert second.endswith("Message from the user:\nI'll go with that.")
        assert "the assistant's message it" in llm.systems[0]

    @pytest.mark.asyncio
    async def test_a_decision_is_stored_with_what_was_chosen_and_passed_over(self) -> None:
        found = MessageStatementsAndDecisions(
            facts=[],
            preferences=[],
            decisions=[
                DecisionStatement(
                    statement="The user decided to hash passwords with Argon2.",
                    chosen="Argon2",
                    alternatives=["bcrypt", " "],
                    reason="faster logins",
                    status="decided",
                    date="2024-04-01",
                )
            ],
        )
        client = FakeClient({"statements_extracted": _messages(2)})
        llm = FakeLLM({MessageStatementsAndDecisions: found})
        stats = await DistillationMemory(client, FakeEmbedder(), llm).extract_statements(  # type: ignore[arg-type]
            "s", decisions=True
        )
        assert stats["decisions"] == 1 and stats["facts"] == 0
        stored = client.written("CREATE (d:Decision")[0]
        decision = stored["decisions"][0]
        assert decision["chosen"] == "Argon2"
        assert decision["alternatives"] == ["bcrypt"]
        assert decision["reason"] == "faster logins"
        assert decision["status"] == "decided"
        assert decision["effective_date"] == "2024-04-01"
        assert decision["commit_probability"] is None  # no judge
        assert stored["said_at"] == "2024-03-15"
        assert "decisions: choices the user makes" in llm.systems[0]

    @pytest.mark.asyncio
    async def test_decisions_are_linked_to_the_entities_they_name(self) -> None:
        found = MessageStatementsAndDecisions(
            facts=[],
            preferences=[],
            decisions=[
                DecisionStatement(
                    statement="The user chose Argon2.",
                    chosen="Argon2 hashing",
                    alternatives=["bcrypt"],
                    reason=None,
                    status="decided",
                    date=None,
                )
            ],
        )
        client = FakeClient(
            {
                "statements_extracted": _messages(2),
                "MATCH (d:Decision {session_id: $session_id})": [
                    {
                        "id": "d1",
                        "chosen": "Argon2 hashing",
                        "alternatives": ["bcrypt"],
                        "message_index": 0,
                    }
                ],
                "RETURN DISTINCT e.id AS id, e.name AS name": [
                    {"id": "e1", "name": "Argon2"},
                    {"id": "e2", "name": "bcrypt"},
                    {"id": "e3", "name": "Flask"},
                    {"id": "e4", "name": "Ar"},
                ],
            }
        )
        llm = FakeLLM({MessageStatementsAndDecisions: found})
        await DistillationMemory(client, FakeEmbedder(), llm).extract_statements(  # type: ignore[arg-type]
            "s", decisions=True
        )
        links = client.written("MERGE (d)-[:CHOSE]->(e)")[0]["rows"]
        assert links == [
            {"decision": "d1", "entity": "e1", "chosen": True},
            {"decision": "d1", "entity": "e2", "chosen": False},
        ]


class TestJudgedStatements:
    @pytest.mark.asyncio
    async def test_the_judge_types_and_drops_statements(self) -> None:
        found = MessageStatementsAndDecisions(
            facts=[
                _fact("The user has never used Docker.", denial=False),  # the LLM missed it
                _fact("The assistant suggested Kubernetes."),
            ],
            preferences=[
                PreferenceStatement(
                    kind="instruction", category="tools", statement="The user likes SQLite."
                )
            ],
            decisions=[
                DecisionStatement(
                    statement="The user decided to try gevent.",
                    chosen="gevent",
                    alternatives=[],
                    reason=None,
                    status="decided",
                    date=None,
                )
            ],
        )

        def answer(name: str, question: Any, state: Any) -> float:
            return {
                "supported0": 0.95,
                "supported1": 0.05,  # not something the user said
                "supported2": 0.9,
                "supported3": 0.8,
                "denial0": 0.97,
                "denial1": 0.01,
                "instruction2": 0.1,  # a liking, not a standing rule
                "commit3": 0.3,  # still weighing it
            }[name]

        judge = FakeJudge(answer)
        client = FakeClient({"statements_extracted": _messages(2)})
        llm = FakeLLM({MessageStatementsAndDecisions: found})
        memory = DistillationMemory(client, FakeEmbedder(), llm, judge)  # type: ignore[arg-type]
        stats = await memory.extract_statements("s", decisions=True, complete=False)

        assert stats["facts"] == 1 and stats["dropped"] == 1
        assert stats["instructions"] == 0 and stats["preferences"] == 1
        stored = client.written("CREATE (f:Fact")[0]
        assert stored["facts"][0]["denial"] is True
        assert stored["facts"][0]["denial_probability"] == 0.97
        assert stored["facts"][0]["support"] == 0.95
        assert stored["preferences"][0]["kind"] == "preference"
        assert stored["preferences"][0]["instruction_probability"] == 0.1
        assert stored["decisions"][0]["status"] == "considering"
        assert stored["decisions"][0]["commit_probability"] == 0.3
        state, questions = judge.calls[0]
        assert state["statements"][0] == "The user has never used Docker."
        assert set(questions) == {
            "supported0",
            "supported1",
            "supported2",
            "supported3",
            "denial0",
            "denial1",
            "instruction2",
            "commit3",
        }

    @pytest.mark.asyncio
    async def test_verification_can_be_switched_off(self) -> None:
        found = MessageStatements(facts=[_fact("The user uses Flask.")], preferences=[])
        judge = FakeJudge()
        client = FakeClient({"statements_extracted": _messages(2)})
        memory = DistillationMemory(  # type: ignore[arg-type]
            client, FakeEmbedder(), FakeLLM({MessageStatements: found}), judge
        )
        stats = await memory.extract_statements("s", verify=False, complete=False)
        assert stats["facts"] == 1 and judge.calls == []

    @pytest.mark.asyncio
    async def test_a_detail_no_statement_records_is_asked_for_again(self) -> None:
        rows = _messages(2, {0: "My budget is $75 now and port 5000 is in `app.run(port=5000)`."})

        def structured(prompt: str) -> MessageStatements:
            if "DETAILS that were missed: $75" in prompt:
                return MessageStatements(
                    facts=[
                        _fact("The user's snack budget is $75.", value="$75"),
                        _fact("The user likes snacks.", value="yes"),  # carries no detail
                    ],
                    preferences=[],
                )
            return MessageStatements(facts=[_fact("The user has a budget.")], preferences=[])

        def answer(name: str, question: Any, state: Any) -> float:
            if name.startswith("v"):
                return 0.9 if state["details"][int(name[1:])] == "$75" else 0.1
            return 0.9 if name.startswith("supported") else 0.0

        judge = FakeJudge(answer)
        client = FakeClient({"statements_extracted": rows})
        llm = FakeLLM({MessageStatements: structured})
        memory = DistillationMemory(client, FakeEmbedder(), llm, judge)  # type: ignore[arg-type]
        stats = await memory.extract_statements("s")

        assert stats["details_recovered"] == 1 and stats["facts"] == 2
        texts = [f["text"] for f in client.written("CREATE (f:Fact")[0]["facts"]]
        assert texts == ["The user has a budget.", "The user's snack budget is $75."]
        # The judge was shown the two details nothing recorded.
        assert judge.calls[0][0]["details"] == ["$75", "5000"]


def _pending(fact_id: str, index: int, text: str, *, denial: bool = False) -> dict[str, Any]:
    return {
        "id": fact_id,
        "text": text,
        "message_index": index,
        "said_at": "2024-03-15",
        "subject": "s",
        "attribute": "a",
        "value": "v",
        "denial": denial,
    }


def _earlier(fact_id: str, index: int, text: str, *, denial: bool = False) -> dict[str, Any]:
    return {**_pending(fact_id, index, text, denial=denial), "score": 0.9}


class TestJudgedReconciliation:
    @pytest.mark.asyncio
    async def test_relations_are_stored_with_their_probability(self) -> None:
        client = FakeClient(
            {
                "f.reconciled IS NULL": [_pending("new", 8, "Response time is 250ms.")],
                "node.message_index < f.message_index": [
                    {
                        "fact_id": "new",
                        "earlier": [
                            _earlier("old", 2, "Response time is 400ms."),
                            _earlier("weak", 3, "Response time matters."),
                            _earlier("again", 4, "Response time is 250ms."),
                            _earlier("other", 5, "The deadline is March 29."),
                            _earlier("denied", 6, "The user never measured it.", denial=True),
                        ],
                    }
                ],
                "statements_extracted": _messages(10),
            }
        )

        def answer(name: str, question: Any, state: Any) -> dict[str, float]:
            return {
                "e0": {"unrelated": 0.03, "same": 0.02, "update": 0.95},
                "e1": {"unrelated": 0.6, "same": 0.05, "update": 0.35},
                "e2": {"unrelated": 0.1, "same": 0.85, "update": 0.05},
                "e3": {"unrelated": 0.98, "same": 0.01, "update": 0.01},
            }[name]

        judge = FakeJudge(answer)
        memory = DistillationMemory(client, FakeEmbedder(), FakeLLM(), judge)  # type: ignore[arg-type]
        stats = await memory.reconcile("s")

        written = client.written("MERGE (e)-[r:SUPERSEDED_BY]->(n)")[0]
        assert written["judge"] == "fake-judge" and written["close_above"] == 0.8
        assert written["rows"] == [
            {"new": "new", "earlier": "old", "relation": "update", "probability": 0.95},
            {"new": "new", "earlier": "weak", "relation": "update", "probability": 0.35},
            {"new": "new", "earlier": "again", "relation": "same", "probability": 0.85},
        ]
        # Only the sure update counts; the denial was never offered as a candidate.
        assert stats["updates"] == 1 and stats["restatements"] == 1
        assert len(judge.calls[0][0]["earlier"]) == 4
        assert client.written("SET f.reconciled = true") == [{"ids": ["new"]}]

    @pytest.mark.asyncio
    async def test_the_judge_widens_the_search(self) -> None:
        client = FakeClient(
            {
                "f.reconciled IS NULL": [_pending("new", 8, "x")],
                "statements_extracted": _messages(10),
            }
        )
        seen: list[dict[str, Any]] = []
        original = client.execute_read

        async def spy(query: str, parameters: dict[str, Any] | None = None, **kw: Any):
            if "node.message_index < f.message_index" in query:
                seen.append(parameters or {})
            return await original(query, parameters, **kw)

        client.execute_read = spy  # type: ignore[method-assign]
        await DistillationMemory(client, FakeEmbedder(), FakeLLM(), FakeJudge()).reconcile("s")  # type: ignore[arg-type]
        assert seen[0]["limit"] == 15 and seen[0]["min_score"] == 0.72

    @pytest.mark.asyncio
    async def test_a_denial_is_checked_statement_by_statement(self) -> None:
        messages = _messages(8, {2: "I'm implementing the homepage route with Flask."})
        client = FakeClient(
            {
                "f.reconciled IS NULL": [
                    _pending("denial", 4, "The user has never written a Flask route.", denial=True)
                ],
                "node.message_index <= d.message_index": [
                    {"id": "a", "text": "The user set up Flask 2.3.1.", "message_index": 0},
                    {"id": "b", "text": "The user tested a route.", "message_index": 0},
                ],
                "message_embedding_idx": [
                    {"id": "m2", "content": messages[2]["content"], "score": 0.8},
                    {"id": "m6", "content": "later", "score": 0.7},
                ],
                "statements_extracted": messages,
            }
        )

        def answer(name: str, question: Any, state: Any) -> float:
            return {"s0": 0.1, "s1": 0.4, "conflict": 0.9}[name]

        judge = FakeJudge(answer)
        llm = FakeLLM(text="The user implemented a homepage route with Flask.")
        stats = await DistillationMemory(client, FakeEmbedder(), llm, judge).reconcile("s")  # type: ignore[arg-type]

        # The weak statement is stored as evidence but does not count.
        linked = client.written("MERGE (d)-[r:CONTRADICTS]->(o)")[0]
        assert linked["rows"] == [{"id": "b", "probability": 0.4}]
        # The message conflicts; the LLM words what the user said in it.
        created = client.written("MERGE (d)-[r:CONTRADICTS]->(f)")
        assert len(created) == 1
        assert created[0]["text"] == "The user implemented a homepage route with Flask."
        assert created[0]["probability"] == 0.9 and created[0]["judge"] == "fake-judge"
        assert created[0]["message_index"] == 2
        assert stats["denials"] == 1 and stats["contradictions"] == 1
        # Only the message said before the denial was judged.
        message_states = [state for state, _ in judge.calls if "message" in state]
        assert [state["message"] for state in message_states] == [messages[2]["content"]]


def _decision(
    decision_id: str, index: int, chosen: str, alternatives: list[str], embedding: list[float]
) -> dict[str, Any]:
    return {
        "id": decision_id,
        "text": f"The user chose {chosen}.",
        "chosen": chosen,
        "alternatives": alternatives,
        "reason": None,
        "status": "decided",
        "message_index": index,
        "decided_on": "2024-03-15",
        "effective_date": None,
        "embedding": embedding,
        "reconciled": False,
    }


class TestDecisionReconciliation:
    @pytest.mark.asyncio
    async def test_a_reversal_replaces_the_earlier_decision(self) -> None:
        decisions = [
            _decision("d1", 2, "bcrypt", ["Argon2"], [1.0, 0.0]),
            _decision("d2", 4, "Bootstrap", [], [0.0, 1.0]),
            # Unlike d1 in embedding, but it picks what d1 passed over.
            _decision("d3", 8, "Argon2", [], [0.1, 1.0]),
        ]
        client = FakeClient({"MATCH (d:Decision {session_id: $session_id})": decisions})

        def answer(name: str, question: Any, state: Any) -> dict[str, float]:
            earlier = state["earlier"][int(name[1:])]
            if state["new"]["chosen"] == "Argon2" and earlier["chosen"] == "bcrypt":
                return {"unrelated": 0.05, "same": 0.05, "replaces": 0.9}
            return {"unrelated": 0.9, "same": 0.05, "replaces": 0.05}

        judge = FakeJudge(answer)
        stats = await DistillationMemory(client, FakeEmbedder(), FakeLLM(), judge).reconcile("s")  # type: ignore[arg-type]

        applied = client.written("MERGE (e)-[r:SUPERSEDED_BY]->(n)")
        rows = [row for write in applied for row in write.get("rows", []) if "relation" not in row]
        assert rows == [{"new": "d3", "earlier": "d1", "probability": 0.9}]
        assert stats["decisions_checked"] == 3 and stats["decisions_reversed"] == 1
        # d3 was compared with d1 first: it names what d1 passed over.
        state = next(s for s, _ in judge.calls if s["new"]["chosen"] == "Argon2")
        assert state["earlier"][0]["chosen"] == "bcrypt"

    @pytest.mark.asyncio
    async def test_without_a_judge_the_llm_answers(self) -> None:
        decisions = [
            _decision("d1", 2, "bcrypt", ["Argon2"], [1.0, 0.0]),
            _decision("d2", 8, "Argon2", [], [1.0, 0.0]),
        ]
        client = FakeClient({"MATCH (d:Decision {session_id: $session_id})": decisions})

        class StructuredLLM(FakeLLM):
            async def complete_structured(self, messages, response_model, **_: Any):
                self.prompts.append(messages[-1].content)
                return response_model(q1="replaces")

        llm = StructuredLLM()
        stats = await DistillationMemory(client, FakeEmbedder(), llm).reconcile("s")  # type: ignore[arg-type]
        assert stats["decisions_reversed"] == 1
        assert "`earlier[0]`" in llm.prompts[0]


class TestTopics:
    def test_a_stretch_starts_where_the_topic_changes(self) -> None:
        starts = [None, None, 0.9, None, 0.1, None, 0.8, None, 0.9, None, 0.2, None]
        assert topic_spans(starts, shortest=4, longest=20) == [(0, 5), (6, 11)]

    def test_a_long_stretch_is_cut_evenly(self) -> None:
        assert topic_spans([0.0] * 50, shortest=4, longest=20) == [(0, 16), (17, 33), (34, 49)]

    def test_a_short_tail_joins_the_stretch_before_it(self) -> None:
        starts: list[float | None] = [0.0] * 10 + [0.9, 0.0]
        assert topic_spans(starts, shortest=4, longest=20) == [(0, 11)]

    def test_no_messages_no_stretches(self) -> None:
        assert topic_spans([]) == []

    @pytest.mark.asyncio
    async def test_summaries_follow_the_topics_and_stale_ones_are_removed(self) -> None:
        rows = _messages(12)
        for position, probability in ((2, 0.1), (4, 0.2), (6, 0.9), (8, 0.1), (10, 0.2)):
            rows[position]["topic_start"] = probability
        client = FakeClient(
            {
                "statements_extracted": rows,
                "RETURN s.start_index AS start_index, s.end_index AS end_index": [
                    {"start_index": 0, "end_index": 9},  # from a fixed-span run
                ],
            }
        )
        memory = DistillationMemory(  # type: ignore[arg-type]
            client, FakeEmbedder(), FakeLLM(text="- a bullet"), FakeJudge()
        )
        stats = await memory.summarize_session("s", span=10, by_topic=True)

        assert stats["summaries"] == 2
        spans = sorted(
            (w["start_index"], w["end_index"]) for w in client.written("MERGE (s:Summary")
        )
        assert spans == [(0, 5), (6, 11)]
        assert client.written("DETACH DELETE s") == [{"session_id": "s", "starts": [0]}]

    @pytest.mark.asyncio
    async def test_topic_starts_are_judged_once(self) -> None:
        rows = _messages(6)
        rows[2]["topic_start"] = 0.3  # judged by an earlier run
        client = FakeClient({"statements_extracted": rows})
        judge = FakeJudge(lambda *_: 0.8)
        memory = DistillationMemory(client, FakeEmbedder(), FakeLLM(), judge)  # type: ignore[arg-type]
        assert await memory.mark_topic_starts("s") == 1
        assert client.written("SET m.topic_start = row.probability") == [
            {"rows": [{"id": "m4", "probability": 0.8}]}
        ]
        assert judge.calls[0][0] == {"previous_message": "message 2", "message": "message 4"}


class TestEntityLinks:
    def test_whole_names_are_matched_whatever_the_case(self) -> None:
        pairs = entities_named_in_texts(
            {
                "a": "The user integrated Flask-Login into the flask app.",
                "b": "The user likes SQLite.",
            },
            {"e1": "Flask-Login", "e2": "SQL", "e3": "Flask App", "e4": "the user", "e5": "io"},
        )
        assert sorted(pairs) == [("a", "e1"), ("a", "e3")]

    @pytest.mark.asyncio
    async def test_statements_are_linked_to_the_entities_they_name(self) -> None:
        client = FakeClient(
            {
                "RETURN id, label, text": [
                    {"id": "f1", "label": "Fact", "text": "The user deployed with Docker."},
                    {"id": "d1", "label": "Decision", "text": "The user chose Docker over Podman."},
                    {"id": "p1", "label": "Preference", "text": "The user prefers short answers."},
                ],
                "RETURN DISTINCT e.id AS id, e.name AS name": [
                    {"id": "e1", "name": "Docker"},
                    {"id": "e2", "name": "Podman"},
                ],
            }
        )
        memory = DistillationMemory(client, FakeEmbedder(), FakeLLM())  # type: ignore[arg-type]
        assert await memory.link_statements_to_entities("s") == 3
        written = {
            query.split("MATCH (s:")[1].split(" ")[0]: params["rows"]
            for query, params in client.writes
            if "MERGE (s)-[:ABOUT]->(e)" in query
        }
        assert written == {
            "Fact": [{"statement": "f1", "entity": "e1"}],
            "Decision": [
                {"statement": "d1", "entity": "e1"},
                {"statement": "d1", "entity": "e2"},
            ],
        }


class TestJudgeUnavailable:
    @pytest.mark.asyncio
    async def test_an_ingest_stops_when_the_judge_is_unavailable(self) -> None:
        from neo4j_agent_memory.judge import JudgeUnavailable

        class NoCredits:
            name = "down"

            async def judge(self, state: Any, questions: Any) -> dict[str, Judgement]:
                raise JudgeUnavailable("no credits")

        found = MessageStatements(facts=[_fact("The user uses Flask.")], preferences=[])
        client = FakeClient({"statements_extracted": _messages(2)})
        memory = DistillationMemory(  # type: ignore[arg-type]
            client, FakeEmbedder(), FakeLLM({MessageStatements: found}), NoCredits()
        )
        with pytest.raises(JudgeUnavailable):
            await memory.extract_statements("s")
        assert client.written("CREATE (f:Fact") == []
