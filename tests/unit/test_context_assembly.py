"""Unit tests for context assembly (``neo4j_agent_memory.memory.context``).

The ranking and rendering logic is pure; the assembler itself is driven with a
fake Neo4j client that answers each query from canned rows.
"""

from __future__ import annotations

from typing import Any

import pytest

from neo4j_agent_memory.memory import context as ctx
from neo4j_agent_memory.memory.context import (
    ContextAssembler,
    clauses_of,
    entities_named_in,
    fuse_rankings,
    graph_message_scores,
    pair_of,
    select_pairs,
    to_pair_ranking,
)

ROLES = ["user", "assistant"] * 4


class TestRanking:
    def test_pair_of_groups_a_user_message_with_its_reply(self) -> None:
        assert pair_of(2, ROLES) == (2, 3)
        assert pair_of(3, ROLES) == (2, 3)

    def test_pair_of_leaves_an_unanswered_message_alone(self) -> None:
        assert pair_of(2, ["user", "assistant", "user"]) == (2, 2)

    def test_pair_ranking_keeps_each_exchange_at_its_best_rank(self) -> None:
        assert to_pair_ranking([3, 2, 0], ROLES) == [(2, 3), (0, 1)]

    def test_entity_named_in_question_matches_whole_words_only(self) -> None:
        names = {"a": "Flask", "b": "Fla", "c": "io"}
        assert entities_named_in("How do I use Flask routes?", names) == {"a"}

    def test_rare_entities_count_for_more(self) -> None:
        scores = graph_message_scores(
            {"rare": 1.0, "common": 1.0},
            {"rare": {0}, "common": {0, 1, 2, 3, 4, 5}},
            message_count=8,
        )
        # Message 0 is mentioned by both; message 1 only by the common entity.
        assert scores[0] > 2 * scores[1]

    def test_fusion_prefers_an_exchange_ranked_by_two_sources(self) -> None:
        fused = fuse_rankings({"vector": [(0, 1), (2, 3)], "graph": [(2, 3)]})
        assert fused[0]["pair"] == (2, 3)
        assert fused[0]["ranks"] == {"vector": 2, "graph": 1}

    def test_selection_always_keeps_the_best_vector_hits(self) -> None:
        # (6, 7) leads the vector ranking alone; three other exchanges are
        # ranked by both sources and would fill a top-3 without it.
        ranked = fuse_rankings(
            {
                "vector": [(6, 7), (0, 1), (2, 3), (4, 5)],
                "graph": [(0, 1), (2, 3), (4, 5)],
            }
        )
        assert (6, 7) not in [entry["pair"] for entry in ranked[:3]]
        chosen = select_pairs(ranked, [(6, 7)], [], lambda _pair: 10.0, top_k=3, budget=1000.0)
        assert (6, 7) in [entry["pair"] for entry in chosen]
        assert len(chosen) == 3

    def test_selection_skips_what_does_not_fit_and_adds_recent(self) -> None:
        ranked = fuse_rankings({"vector": [(0, 1), (2, 3)]})
        sizes = {(0, 1): 900.0, (2, 3): 50.0, (6, 7): 50.0}
        chosen = select_pairs(ranked, [], [(6, 7)], lambda pair: sizes[pair], top_k=2, budget=500.0)
        assert [entry["pair"] for entry in chosen] == [(2, 3), (6, 7)]
        assert chosen[-1]["recent"] is True


class TestClauses:
    def test_a_two_part_question_is_split(self) -> None:
        question = (
            "How many days passed between when I decided to reject the raise "
            "and when I rescheduled my final meeting?"
        )
        assert clauses_of(question) == [
            "How many days passed between when I decided to reject the raise",
            "when I rescheduled my final meeting",
        ]

    def test_a_single_part_question_has_no_clauses(self) -> None:
        assert clauses_of("When does my first sprint end?") == []

    def test_short_fragments_are_dropped(self) -> None:
        assert clauses_of("Is it red, blue or green?") == []


class TestRendering:
    def test_timeline_counts_days_from_the_first_entry(self) -> None:
        facts = [
            {
                "id": "b",
                "text": "Meeting rescheduled.",
                "event_date": "2024-03-30",
                "message_index": 64,
            },
            {"id": "a", "text": "Raise rejected.", "event_date": "2024-03-12", "message_index": 56},
        ]
        text = ContextAssembler._render_timeline(facts, {})
        lines = [line for line in text.splitlines() if line.startswith("- ")]
        assert lines == [
            "- 2024-03-12 (day 0): Raise rejected. (#57)",
            "- 2024-03-30 (day 18): Meeting rescheduled. (#65)",
        ]

    def test_timeline_needs_two_dated_statements(self) -> None:
        facts = [{"id": "a", "text": "x", "event_date": "2024-03-12", "message_index": 1}]
        assert ContextAssembler._render_timeline(facts, {}) == ""

    def test_a_replaced_statement_is_left_off_the_timeline(self) -> None:
        facts = [
            {"id": "old", "text": "Due March 29.", "event_date": "2024-03-29", "message_index": 1},
            {"id": "new", "text": "Due March 31.", "event_date": "2024-03-31", "message_index": 5},
            {"id": "x", "text": "Kickoff.", "event_date": "2024-03-01", "message_index": 0},
        ]
        text = ContextAssembler._render_timeline(facts, {"old": "new"})
        assert "March 29" not in text and "March 31" in text

    def test_statements_point_from_an_old_value_to_the_new_one(self) -> None:
        facts = {
            "old": {
                "id": "old",
                "text": "Response time is 400ms.",
                "message_index": 3,
                "said_at": "2024-03-15",
            },
            "new": {
                "id": "new",
                "text": "Response time is 250ms.",
                "message_index": 9,
                "said_at": None,
            },
        }
        text = ContextAssembler(None, None)._render_statements(facts, {"old": "new"})  # type: ignore[arg-type]
        assert "- [#4, 2024-03-15] Response time is 400ms. (later changed: see #10)" in text
        assert "- [#10] Response time is 250ms. (changes what was said at #4)" in text

    def test_contradictions_pair_a_denial_with_what_it_conflicts_with(self) -> None:
        facts = {
            "d": {
                "id": "d",
                "text": "The user has never written a Flask route.",
                "message_index": 58,
                "said_at": None,
                "score": 0.9,
            },
            "o": {
                "id": "o",
                "text": "The user implemented a homepage route.",
                "message_index": 24,
                "said_at": None,
                "score": 0.8,
            },
        }
        text = ContextAssembler(None, None)._render_conflicts(facts, {"d": ["o"]})  # type: ignore[arg-type]
        assert "## Contradictions in what the user has said" in text
        assert '(at #59): "The user has never written a Flask route."' in text
        assert '(at #25) "The user implemented a homepage route."' in text

    def test_no_contradiction_section_without_contradictions(self) -> None:
        assert ContextAssembler(None, None)._render_conflicts({}, {}) == ""  # type: ignore[arg-type]


class FakeClient:
    """Answers each read from canned rows, chosen by a marker in the query."""

    def __init__(self, rows: dict[str, list[dict[str, Any]]]) -> None:
        self.rows = rows
        self.queries: list[str] = []

    async def execute_read(self, query: str, parameters: dict[str, Any] | None = None, **_: Any):
        self.queries.append(query)
        for marker, rows in self.rows.items():
            if marker in query:
                return [dict(row) for row in rows]
        return []


class FakeEmbedder:
    async def embed(self, text: str) -> list[float]:
        return [0.0]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] for _ in texts]


def _session_rows() -> dict[str, list[dict[str, Any]]]:
    messages = [
        {
            "id": f"m{i}",
            "role": "user" if i % 2 == 0 else "assistant",
            "chars": 40,
            "timestamp": "2024-03-15T00:00:00Z",
        }
        for i in range(8)
    ]
    return {
        "RETURN count(m) AS messages": [{"messages": len(messages)}],
        "size(m.content) AS chars": messages,
        "message_embedding_idx": [{"id": "m4", "score": 0.9}, {"id": "m0", "score": 0.8}],
        "RETURN m.id AS id, m.content AS content": [
            {"id": f"m{i}", "content": f"content {i}"} for i in range(8)
        ],
        "fact_embedding_idx": [
            {
                "id": "f1",
                "text": "The user has never used Docker.",
                "message_index": 4,
                "said_at": "2024-03-15",
                "event_date": None,
                "score": 0.9,
            },
            {
                "id": "f2",
                "text": "The user deployed with Docker.",
                "message_index": 0,
                "said_at": "2024-03-15",
                "event_date": "2024-03-10",
                "score": 0.8,
            },
        ],
        "SUPERSEDED_BY|CONTRADICTS": [
            {
                "type": "CONTRADICTS",
                "source": "f1",
                "target": "f2",
                "id": "f2",
                "text": "The user deployed with Docker.",
                "message_index": 0,
                "said_at": "2024-03-15",
                "event_date": "2024-03-10",
            },
        ],
        "MATCH (p:Preference": [
            {
                "id": "p1",
                "text": "The user wants version numbers always included.",
                "kind": "instruction",
                "message_index": 2,
                "score": 0.7,
            },
        ],
        "MATCH (s:Summary": [
            {
                "start_index": 0,
                "end_index": 7,
                "text": "- Docker deployment.",
                "start_time": "2024-03-15T00:00:00Z",
            },
        ],
    }


class TestAssemble:
    @pytest.mark.asyncio
    async def test_the_distilled_memory_follows_the_excerpts(self) -> None:
        assembler = ContextAssembler(FakeClient(_session_rows()), FakeEmbedder())  # type: ignore[arg-type]
        result = await assembler.assemble("Have I used Docker?", session_id="s")
        text = result.text
        order = [
            text.index("## Conversation excerpts"),
            text.index("## Outline of the conversation"),
            text.index("## What the user has said"),
            text.index("## The user's standing instructions and preferences"),
            text.index("## Contradictions in what the user has said"),
            text.index("## How to answer from this memory"),
        ]
        assert order == sorted(order)
        assert text.rstrip().endswith("- The user wants version numbers always included.")
        assert result.details["conflicts"] == 1
        assert result.details["instructions"] == 1
        # The vector hits (#5 and #1) and the two most recent exchanges.
        assert [p["messages"] for p in result.details["pairs"]] == [[1, 2], [5, 6], [7, 8]]

    @pytest.mark.asyncio
    async def test_guidance_can_be_left_out(self) -> None:
        assembler = ContextAssembler(FakeClient(_session_rows()), FakeEmbedder())  # type: ignore[arg-type]
        result = await assembler.assemble(
            "Have I used Docker?", session_id="s", include_guidance=False
        )
        assert "## How to answer from this memory" not in result.text
        assert "## Contradictions in what the user has said" in result.text

    @pytest.mark.asyncio
    async def test_the_context_stays_within_the_budget(self) -> None:
        rows = _session_rows()
        for row in rows["size(m.content) AS chars"]:
            row["chars"] = 4000
        assembler = ContextAssembler(FakeClient(rows), FakeEmbedder())  # type: ignore[arg-type]
        result = await assembler.assemble(
            "Have I used Docker?", session_id="s", token_budget=1500, chars_per_token=4.0
        )
        assert len(result.details["pairs"]) < 3

    @pytest.mark.asyncio
    async def test_an_empty_session_gives_an_empty_context(self) -> None:
        client = FakeClient({"RETURN count(m) AS messages": [{"messages": 0}]})
        assembler = ContextAssembler(client, FakeEmbedder())  # type: ignore[arg-type]
        result = await assembler.assemble("anything", session_id="none")
        assert result.text == ""

    @pytest.mark.asyncio
    async def test_the_session_skeleton_is_read_once(self) -> None:
        client = FakeClient(_session_rows())
        assembler = ContextAssembler(client, FakeEmbedder())  # type: ignore[arg-type]
        await assembler.assemble("Have I used Docker?", session_id="s")
        await assembler.assemble("Which version?", session_id="s")
        assert sum("size(m.content) AS chars" in q for q in client.queries) == 1


def test_rrf_constant_is_the_published_one() -> None:
    assert ctx.RRF_K == 60
