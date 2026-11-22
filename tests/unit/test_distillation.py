"""Unit tests for session distillation (``neo4j_agent_memory.memory.distillation``).

The LLM, the embedder and Neo4j are faked: these tests cover what the jobs ask
for, what they write, and what they skip.
"""

from __future__ import annotations

from typing import Any

import pytest

from neo4j_agent_memory.llm.types import Completion
from neo4j_agent_memory.memory.distillation import (
    DenialConflict,
    DenialConflicts,
    DistillationMemory,
    FactRelation,
    FactRelations,
    FactStatement,
    MessageStatements,
    PreferenceStatement,
    _date_of,
    _valid_date,
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
    """Returns queued structured answers by response model; records the prompts."""

    model = "fake/model"

    def __init__(self, structured: dict[type, Any] | None = None, text: str = "") -> None:
        self.structured = structured or {}
        self.text = text
        self.prompts: list[str] = []

    async def complete_structured(self, messages, response_model, **_: Any):
        self.prompts.append(messages[-1].content)
        answer = self.structured[response_model]
        if isinstance(answer, Exception):
            raise answer
        return answer(messages[-1].content) if callable(answer) else answer

    async def complete(self, messages, **_: Any) -> Completion:
        self.prompts.append(messages[-1].content)
        return Completion(content=self.text, model=self.model)


def _messages(count: int = 4) -> list[dict[str, Any]]:
    return [
        {
            "id": f"m{i}",
            "role": "user" if i % 2 == 0 else "assistant",
            "content": f"message {i}",
            "timestamp": "2024-03-15T00:00:00Z",
            "statements_extracted": False,
        }
        for i in range(count)
    ]


class TestDates:
    def test_date_of_a_timestamp(self) -> None:
        assert _date_of("2024-03-15T10:00:00Z") == "2024-03-15"
        assert _date_of(None) is None

    @pytest.mark.parametrize("value", ["2024-13-01", "2024-3-1", "March 29", "", None])
    def test_implausible_dates_are_dropped(self, value: str | None) -> None:
        assert _valid_date(value) is None

    def test_a_plausible_date_is_kept(self) -> None:
        assert _valid_date("2024-03-29") == "2024-03-29"


class TestExtractStatements:
    @pytest.mark.asyncio
    async def test_facts_and_preferences_are_stored_with_their_source(self) -> None:
        found = MessageStatements(
            facts=[
                FactStatement(
                    statement="The user's first sprint ends on March 29.",
                    subject="first sprint",
                    attribute="end date",
                    value="March 29",
                    date="2024-03-29",
                    denial=False,
                ),
                FactStatement(
                    statement="The user has never used Docker.",
                    subject="Docker",
                    attribute="experience",
                    value="none",
                    date="not a date",
                    denial=True,
                ),
            ],
            preferences=[
                PreferenceStatement(
                    kind="instruction",
                    category="Response Format",
                    statement="The user wants version numbers included.",
                ),
            ],
        )
        client = FakeClient({"statements_extracted": _messages()})
        memory = DistillationMemory(client, FakeEmbedder(), FakeLLM({MessageStatements: found}))  # type: ignore[arg-type]

        stats = await memory.extract_statements("s", max_concurrency=2)

        # Only the two user messages are read.
        assert stats == {
            "messages_processed": 2,
            "facts": 4,
            "preferences": 2,
            "instructions": 2,
            "decisions": 0,
            "dropped": 0,
            "details_recovered": 0,
            "failures": 0,
        }
        stored = client.written("CREATE (f:Fact")
        assert {params["message_index"] for params in stored} == {0, 2}
        first = next(params for params in stored if params["message_index"] == 0)
        assert first["said_at"] == "2024-03-15"
        assert [fact["event_date"] for fact in first["facts"]] == ["2024-03-29", None]
        assert [fact["denial"] for fact in first["facts"]] == [False, True]
        assert first["facts"][0]["predicate"] == "end date"
        assert first["preferences"][0]["kind"] == "instruction"
        assert first["preferences"][0]["category"] == "response format"
        assert all(fact["embedding"] for fact in first["facts"])

    @pytest.mark.asyncio
    async def test_the_prompt_carries_the_date_of_the_message(self) -> None:
        llm = FakeLLM({MessageStatements: MessageStatements(facts=[], preferences=[])})
        client = FakeClient({"statements_extracted": _messages(2)})
        await DistillationMemory(client, FakeEmbedder(), llm).extract_statements("s")  # type: ignore[arg-type]
        assert llm.prompts[0].startswith("Date of the message: 2024-03-15")

    @pytest.mark.asyncio
    async def test_a_message_with_nothing_to_remember_is_still_marked_read(self) -> None:
        llm = FakeLLM({MessageStatements: MessageStatements(facts=[], preferences=[])})
        client = FakeClient({"statements_extracted": _messages(2)})
        stats = await DistillationMemory(client, FakeEmbedder(), llm).extract_statements("s")  # type: ignore[arg-type]
        assert stats["messages_processed"] == 1 and stats["facts"] == 0
        assert client.written("SET m.statements_extracted = true") == [{"message_id": "m0"}]

    @pytest.mark.asyncio
    async def test_messages_already_read_are_skipped(self) -> None:
        rows = _messages(2)
        rows[0]["statements_extracted"] = True
        llm = FakeLLM({MessageStatements: MessageStatements(facts=[], preferences=[])})
        stats = await DistillationMemory(
            FakeClient({"statements_extracted": rows}),
            FakeEmbedder(),
            llm,  # type: ignore[arg-type]
        ).extract_statements("s")
        assert stats["messages_processed"] == 0 and llm.prompts == []

    @pytest.mark.asyncio
    async def test_a_failing_llm_is_counted_and_leaves_the_message_unread(self) -> None:
        client = FakeClient({"statements_extracted": _messages(2)})
        memory = DistillationMemory(
            client,
            FakeEmbedder(),
            FakeLLM({MessageStatements: RuntimeError("down")}),
            retries=1,  # type: ignore[arg-type]
        )
        stats = await memory.extract_statements("s")
        assert stats["failures"] == 1 and stats["messages_processed"] == 0
        assert client.writes == []

    @pytest.mark.asyncio
    async def test_distillation_needs_an_llm(self) -> None:
        with pytest.raises(RuntimeError, match="needs an LLM"):
            await DistillationMemory(FakeClient(), FakeEmbedder(), None).extract_statements("s")  # type: ignore[arg-type]


def _pending_fact(fact_id: str, index: int, text: str, *, denial: bool = False) -> dict[str, Any]:
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


class TestReconcile:
    @pytest.mark.asyncio
    async def test_an_update_closes_the_earlier_fact(self) -> None:
        client = FakeClient(
            {
                "f.reconciled IS NULL": [_pending_fact("new", 8, "Response time is 250ms.")],
                "node.message_index < f.message_index": [
                    {
                        "fact_id": "new",
                        "earlier": [
                            {
                                "id": "old",
                                "text": "Response time is 400ms.",
                                "message_index": 2,
                                "said_at": "2024-03-15",
                                "subject": "API",
                                "attribute": "response time",
                                "value": "400ms",
                                "score": 0.9,
                            }
                        ],
                    }
                ],
                "statements_extracted": _messages(10),
            }
        )
        llm = FakeLLM(
            {
                FactRelations: FactRelations(
                    relations=[
                        FactRelation(new=1, earlier=1, relation="update"),
                        FactRelation(new=7, earlier=1, relation="update"),  # out of range: ignored
                    ]
                )
            }
        )
        stats = await DistillationMemory(client, FakeEmbedder(), llm).reconcile("s")  # type: ignore[arg-type]

        assert stats["updates"] == 1 and stats["facts_checked"] == 1
        applied = client.written("MERGE (e)-[:SUPERSEDED_BY]->(n)")
        assert applied == [{"rows": [{"new": "new", "earlier": "old", "relation": "update"}]}]
        assert "E1. (message 3, 2024-03-15) Response time is 400ms." in llm.prompts[0]
        assert "attribute: response time" in llm.prompts[0]
        assert client.written("SET f.reconciled = true") == [{"ids": ["new"]}]

    @pytest.mark.asyncio
    async def test_a_fact_without_similar_earlier_facts_needs_no_llm(self) -> None:
        client = FakeClient(
            {
                "f.reconciled IS NULL": [_pending_fact("a", 0, "The user is a producer.")],
                "statements_extracted": _messages(2),
            }
        )
        llm = FakeLLM()
        stats = await DistillationMemory(client, FakeEmbedder(), llm).reconcile("s")  # type: ignore[arg-type]
        assert stats["facts_checked"] == 1 and llm.prompts == []

    @pytest.mark.asyncio
    async def test_a_denial_is_linked_to_what_it_conflicts_with(self) -> None:
        messages = _messages(8)
        messages[2]["content"] = "I'm implementing the homepage route with Flask."
        client = FakeClient(
            {
                "f.reconciled IS NULL": [
                    _pending_fact(
                        "denial", 6, "The user has never written a Flask route.", denial=True
                    )
                ],
                "node.message_index <= d.message_index": [
                    {
                        "id": "setup",
                        "text": "The user set up Flask 2.3.1.",
                        "message_index": 0,
                        "said_at": "2024-03-15",
                        "score": 0.8,
                    },
                ],
                "message_embedding_idx": [
                    {
                        "id": "m2",
                        "content": messages[2]["content"],
                        "timestamp": "2024-03-15T00:00:02Z",
                        "score": 0.8,
                    },
                    # Said after the denial: not held against it.
                    {
                        "id": "m7",
                        "content": "later",
                        "timestamp": "2024-03-16T00:00:00Z",
                        "score": 0.7,
                    },
                ],
                "statements_extracted": messages,
            }
        )
        llm = FakeLLM(
            {
                DenialConflicts: DenialConflicts(
                    conflicts=[
                        DenialConflict(
                            source="message",
                            number=1,
                            said="The user implemented a homepage route with Flask.",
                        ),
                        DenialConflict(source="message", number=2, said="The user never did."),
                    ]
                ),
                FactRelations: FactRelations(relations=[]),
            }
        )
        stats = await DistillationMemory(client, FakeEmbedder(), llm).reconcile("s")  # type: ignore[arg-type]

        assert stats["denials"] == 1 and stats["contradictions"] == 1
        prompt = llm.prompts[0]
        assert "DENIAL: The user has never written a Flask route." in prompt
        assert "S1. The user set up Flask 2.3.1." in prompt
        assert "M1. I'm implementing the homepage route with Flask." in prompt
        assert "M2." not in prompt  # the later message was not offered
        created = client.written("MERGE (d)-[r:CONTRADICTS]->(f)")
        assert len(created) == 1
        assert created[0]["text"] == "The user implemented a homepage route with Flask."
        assert created[0]["message_index"] == 2 and created[0]["denial"] == "denial"

    @pytest.mark.asyncio
    async def test_a_conflicting_statement_is_linked_without_a_new_one(self) -> None:
        client = FakeClient(
            {
                "f.reconciled IS NULL": [
                    _pending_fact("denial", 6, "The user has never used Docker.", denial=True)
                ],
                "node.message_index <= d.message_index": [
                    {
                        "id": "used",
                        "text": "The user deployed with Docker.",
                        "message_index": 2,
                        "said_at": "2024-03-15",
                        "score": 0.9,
                    },
                ],
                "statements_extracted": _messages(8),
            }
        )
        llm = FakeLLM(
            {
                DenialConflicts: DenialConflicts(
                    conflicts=[
                        DenialConflict(source="statement", number=1, said="The user used Docker."),
                    ]
                ),
                FactRelations: FactRelations(relations=[]),
            }
        )
        stats = await DistillationMemory(client, FakeEmbedder(), llm).reconcile("s")  # type: ignore[arg-type]
        assert stats["contradictions"] == 1
        assert client.written("MERGE (d)-[:CONTRADICTS]->(o)") == [
            {"denial": "denial", "ids": ["used"]}
        ]
        assert client.written("MERGE (d)-[r:CONTRADICTS]->(f)") == []


class TestSummaries:
    @pytest.mark.asyncio
    async def test_one_summary_per_stretch(self) -> None:
        client = FakeClient({"statements_extracted": _messages(5)})
        llm = FakeLLM(text="- The user asked about sprints.")
        stats = await DistillationMemory(client, FakeEmbedder(), llm).summarize_session(  # type: ignore[arg-type]
            "s", span=2
        )
        assert stats == {"summaries": 3, "failures": 0}
        spans = sorted(
            (params["start_index"], params["end_index"])
            for params in client.written("MERGE (s:Summary")
        )
        assert spans == [(0, 1), (2, 3), (4, 4)]
        assert llm.prompts[0].startswith("<transcript>\nUSER: message")

    @pytest.mark.asyncio
    async def test_complete_stretches_already_summarized_are_skipped(self) -> None:
        client = FakeClient(
            {
                "statements_extracted": _messages(5),
                "RETURN s.start_index AS start_index": [{"start_index": 0}, {"start_index": 4}],
            }
        )
        llm = FakeLLM(text="- Something.")
        stats = await DistillationMemory(client, FakeEmbedder(), llm).summarize_session(  # type: ignore[arg-type]
            "s", span=2
        )
        # The stretch at 2 is new; the trailing short one at 4 is rewritten.
        assert stats["summaries"] == 2

    @pytest.mark.asyncio
    async def test_an_empty_summary_counts_as_a_failure(self) -> None:
        client = FakeClient({"statements_extracted": _messages(2)})
        stats = await DistillationMemory(
            client, FakeEmbedder(), FakeLLM(text="  ")
        ).summarize_session(  # type: ignore[arg-type]
            "s", span=2
        )
        assert stats == {"summaries": 0, "failures": 1}
