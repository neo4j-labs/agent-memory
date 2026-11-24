"""Unit tests for context assembly (``neo4j_agent_memory.memory.context``).

The ranking and rendering logic is pure; the assembler itself is driven with a
fake Neo4j client that answers each query from canned rows.
"""

from __future__ import annotations

from typing import Any

import pytest

from neo4j_agent_memory.judge import Judgement, YesNo
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

    def test_a_replaced_statement_stays_on_the_timeline_marked(self) -> None:
        facts = [
            {"id": "old", "text": "Due March 29.", "event_date": "2024-03-29", "message_index": 1},
            {"id": "new", "text": "Due March 31.", "event_date": "2024-03-31", "message_index": 5},
            {"id": "x", "text": "Kickoff.", "event_date": "2024-03-01", "message_index": 0},
        ]
        text = ContextAssembler._render_timeline(facts, {"old": "new"})
        assert "Due March 29. (#2) (a later statement changed this)" in text
        assert "Due March 31. (#6)" in text and "Due March 31. (#6) (a later" not in text

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


class TestLongConversations:
    @staticmethod
    def summaries(count: int) -> list[dict[str, Any]]:
        return [
            {
                "start_index": 10 * i,
                "end_index": 10 * i + 9,
                "text": f"- stretch {i} " + "x" * 300,
                "start_time": "2024-03-15T00:00:00Z",
            }
            for i in range(count)
        ]

    def test_a_short_conversation_is_outlined_in_full(self) -> None:
        text = ContextAssembler._render_outline(self.summaries(5), 50, max_chars=4000)
        assert "The whole conversation (50 messages)" in text
        assert text.count("Messages #") == 5

    def test_a_long_one_shows_the_stretches_near_the_question(self) -> None:
        # Room for 10 of the 100 stretches; the rankings point into two of them.
        text = ContextAssembler._render_outline(
            self.summaries(100), 1000, max_chars=2600, near=[455, 457, 871]
        )
        assert "1000 messages in 100 stretches. These 10 are" in text
        assert text.count("Messages #") == 10
        assert "Messages #451-460" in text and "Messages #871-880" in text
        # The rest are spread over the conversation, in order, from its start.
        starts = [int(line.split("#")[1].split("-")[0]) for line in text.splitlines()[2::2]]
        assert starts == sorted(starts) and starts[0] == 1
        assert len(text) <= 2600 * 1.2

    def test_without_hints_the_stretches_are_spread_evenly(self) -> None:
        text = ContextAssembler._render_outline(self.summaries(100), 1000, max_chars=1300)
        assert [line for line in text.splitlines() if line.startswith("Messages #")] == [
            "Messages #1-10 (2024-03-15):",
            "Messages #201-210 (2024-03-15):",
            "Messages #401-410 (2024-03-15):",
            "Messages #601-610 (2024-03-15):",
            "Messages #801-810 (2024-03-15):",
        ]

    @pytest.mark.asyncio
    async def test_only_the_nearest_instructions_are_kept_beyond_the_limit(self) -> None:
        rows = _session_rows()
        rows["MATCH (p:Preference"] = [
            {
                "id": f"p{i}",
                "text": f"Instruction {i}.",
                "kind": "instruction",
                "message_index": i,
                "score": i / 10,
            }
            for i in range(6)
        ]
        assembler = ContextAssembler(FakeClient(rows), FakeEmbedder())  # type: ignore[arg-type]
        result = await assembler.assemble("Have I used Docker?", session_id="s", max_instructions=2)
        assert result.details["instructions"] == 2
        assert "Instruction 5." in result.text and "Instruction 4." in result.text
        assert "Instruction 3." not in result.text


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


# --- with a judge -------------------------------------------------------------


class FakeJudge:
    """Answers each question through ``answer(name, question, state)``."""

    name = "fake-judge"

    def __init__(self, answer: Any = None) -> None:
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
                probabilities = given or {next(iter(question.options)): 1.0}
                choice = max(probabilities, key=probabilities.get)
                answers[name] = Judgement(probabilities[choice], choice, probabilities)
        return answers


def _decision_rows() -> list[dict[str, Any]]:
    return [
        {
            "id": "d1",
            "text": "The user decided to hash passwords with Argon2.",
            "chosen": "Argon2",
            "alternatives": ["bcrypt"],
            "reason": "faster logins",
            "status": "decided",
            "message_index": 2,
            "said_at": "2024-03-15",
            "event_date": None,
            "changed_at": None,
            "score": 0.8,
        },
        {
            "id": "d2",
            "text": "The user decided to use Bootstrap.",
            "chosen": "Bootstrap",
            "alternatives": [],
            "reason": None,
            "status": "considering",
            "message_index": 4,
            "said_at": "2024-03-15",
            "event_date": None,
            "changed_at": 6,
            "score": 0.6,
        },
    ]


class TestFusionWeights:
    def test_a_weighted_ranking_counts_for_more(self) -> None:
        rankings = {"vector": [(0, 1), (2, 3)], "judge": [(2, 3), (0, 1)]}
        assert fuse_rankings(rankings)[0]["pair"] == (0, 1)  # a tie, earlier first
        assert fuse_rankings(rankings, {"judge": 3.0})[0]["pair"] == (2, 3)


class TestElapsedTime:
    @pytest.mark.parametrize(
        "question",
        [
            "How many days passed between the kickoff and the launch?",
            "How long after the review did I file the patent?",
            "How much time do I have from the sprint end to the deadline?",
        ],
    )
    def test_questions_about_elapsed_time_are_recognised(self, question: str) -> None:
        assert ctx.asks_for_elapsed_time(question)

    def test_other_questions_are_not(self) -> None:
        assert not ctx.asks_for_elapsed_time("Which libraries does my project use?")

    def test_a_span_is_described_in_days_and_weeks(self) -> None:
        assert ctx.describe_span(1) == "1 day"
        assert ctx.describe_span(18) == "18 days (2 weeks and 4 days)"
        assert ctx.describe_span(-21) == "21 days (3 weeks)"


class TestDecisionsAndProbabilities:
    def test_decisions_show_what_was_passed_over_and_what_changed(self) -> None:
        text = ContextAssembler(None, None)._render_decisions(_decision_rows())  # type: ignore[arg-type]
        assert "## What the user has decided" in text
        assert (
            "- [#3, 2024-03-15] The user decided to hash passwords with Argon2. "
            "(passed over: bcrypt; because: faster logins)" in text
        )
        assert "The user decided to use Bootstrap. (later changed: see #7)" in text

    @pytest.mark.asyncio
    async def test_without_a_judge_only_decisions_near_the_question_are_shown(self) -> None:
        rows = _session_rows()
        rows["MATCH (d:Decision"] = _decision_rows()
        assembler = ContextAssembler(FakeClient(rows), FakeEmbedder())  # type: ignore[arg-type]
        result = await assembler.assemble("Which hashing did I pick?", session_id="s")
        assert "hash passwords with Argon2" in result.text
        assert "Bootstrap" not in result.text
        assert result.details["decisions"] == 1

    @pytest.mark.asyncio
    async def test_a_weak_relationship_is_not_reported(self) -> None:
        rows = _session_rows()
        rows["SUPERSEDED_BY|CONTRADICTS"][0]["probability"] = 0.35
        assembler = ContextAssembler(FakeClient(rows), FakeEmbedder())  # type: ignore[arg-type]
        result = await assembler.assemble("Have I used Docker?", session_id="s")
        assert "## Contradictions in what the user has said" not in result.text
        assert result.details["conflicts"] == 0

    def test_the_surest_conflict_is_shown_first(self) -> None:
        facts = {
            "d": {"id": "d", "text": "Never used Docker.", "message_index": 9, "said_at": None},
            "a": {
                "id": "a",
                "text": "Asked about Docker.",
                "message_index": 1,
                "said_at": None,
                "score": 0.9,
                "conflict_probability": 0.55,
            },
            "b": {
                "id": "b",
                "text": "Deployed with Docker.",
                "message_index": 3,
                "said_at": None,
                "score": 0.2,
                "conflict_probability": 0.97,
            },
        }
        text = ContextAssembler(None, None)._render_conflicts(  # type: ignore[arg-type]
            facts, {"d": ["a", "b"]}, per_denial=1
        )
        assert "Deployed with Docker." in text and "Asked about Docker." not in text


class TestAssembleWithAJudge:
    @pytest.mark.asyncio
    async def test_the_gate_decides_what_is_reported(self) -> None:
        rows = _session_rows()
        rows["MATCH (d:Decision"] = _decision_rows()

        def answer(name: str, question: Any, state: Any) -> Any:
            return {"c0": 0.1, "i0": 0.9, "d0": 0.2, "d1": 0.8}.get(name)

        judge = FakeJudge(answer)
        assembler = ContextAssembler(FakeClient(rows), FakeEmbedder(), judge)  # type: ignore[arg-type]
        result = await assembler.assemble("How do I speed up my Docker build?", session_id="s")

        # The question is not about whether the user ever used Docker.
        assert "## Contradictions in what the user has said" not in result.text
        assert "that apply to this question. Follow each of them in the answer:" in result.text
        assert "The user decided to use Bootstrap." in result.text
        assert "hash passwords with Argon2" not in result.text
        gate = next(state for state, _ in judge.calls if "instructions" in state)
        assert gate["contradictions"] == [
            {
                "user_said": "The user has never used Docker.",
                "but_also_said": ["The user deployed with Docker."],
            }
        ]
        # The shape of the question is asked for only on request.
        assert result.details["judge"]["shape"] == "lookup"
        assert "shape" not in next(q for state, q in judge.calls if "instructions" in state)

    @pytest.mark.asyncio
    async def test_a_contradiction_the_question_asks_about_is_reported(self) -> None:
        judge = FakeJudge(lambda name, *_: 0.9 if name == "c0" else None)
        assembler = ContextAssembler(FakeClient(_session_rows()), FakeEmbedder(), judge)  # type: ignore[arg-type]
        result = await assembler.assemble("Have I used Docker?", session_id="s")
        assert "## Contradictions in what the user has said" in result.text
        # No instruction applies, so none is repeated at the end.
        assert "that apply to this question" not in result.text

    @pytest.mark.asyncio
    async def test_the_judges_ranking_reorders_the_exchanges(self) -> None:
        def answer(name: str, question: Any, state: Any) -> Any:
            if name == "needed":
                return 0.95 if state["exchange"]["user"] == "content 0" else 0.05
            return None

        judge = FakeJudge(answer)
        assembler = ContextAssembler(FakeClient(_session_rows()), FakeEmbedder(), judge)  # type: ignore[arg-type]
        result = await assembler.assemble(
            "Have I used Docker?", session_id="s", top_k=1, vector_floor=0, recent_pairs=0
        )
        # Vector search put #5 first; the judge found what is needed in #1-2.
        assert [p["messages"] for p in result.details["pairs"]] == [[1, 2]]
        assert result.details["pairs"][0]["judged"] == 0.95
        assert result.details["judge"]["pairs_judged"] == 2

    @pytest.mark.asyncio
    async def test_reranking_can_be_switched_off(self) -> None:
        judge = FakeJudge()
        assembler = ContextAssembler(FakeClient(_session_rows()), FakeEmbedder(), judge)  # type: ignore[arg-type]
        await assembler.assemble("Have I used Docker?", session_id="s", rerank=False)
        assert not any("exchange" in state for state, _ in judge.calls)

    @pytest.mark.asyncio
    async def test_the_span_between_two_events_is_worked_out(self) -> None:
        rows = _session_rows()
        rows["fact_embedding_idx"] = [
            {
                "id": "f1",
                "text": "The first sprint ends on March 29.",
                "message_index": 0,
                "said_at": "2024-03-15",
                "event_date": "2024-03-29",
                "score": 0.9,
            },
            {
                "id": "f2",
                "text": "Analytics are due April 19.",
                "message_index": 4,
                "said_at": "2024-03-15",
                "event_date": "2024-04-19",
                "score": 0.8,
            },
        ]
        rows["SUPERSEDED_BY|CONTRADICTS"] = []

        def answer(name: str, question: Any, state: Any) -> Any:
            # The first thing is the sprint's end, the second the deadline.
            return {"a0": 0.9, "a1": 0.1, "b0": 0.0, "b1": 0.8}.get(name)

        judge = FakeJudge(answer)
        assembler = ContextAssembler(FakeClient(rows), FakeEmbedder(), judge)  # type: ignore[arg-type]
        result = await assembler.assemble(
            "How many days are there between the end of my first sprint and the analytics deadline?",
            session_id="s",
        )
        assert (
            'Worked out for this question: from 2024-03-29 ("The first sprint ends on '
            'March 29.") to 2024-04-19 ("Analytics are due April 19.") is 21 days (3 weeks).'
        ) in result.text
        assert result.details["judge"]["span"] == {
            "days": 21,
            "from": "2024-03-29",
            "to": "2024-04-19",
            "probabilities": [0.9, 0.8],
        }

    @pytest.mark.asyncio
    async def test_an_unsure_judge_works_out_no_span(self) -> None:
        rows = _session_rows()
        rows["fact_embedding_idx"][0]["event_date"] = "2024-03-12"

        def answer(name: str, question: Any, state: Any) -> Any:
            return 0.3 if name[:1] in ("a", "b") and name[1:].isdigit() else None

        assembler = ContextAssembler(FakeClient(rows), FakeEmbedder(), FakeJudge(answer))  # type: ignore[arg-type]
        result = await assembler.assemble(
            "How many days between my first Docker use and the deploy?", session_id="s"
        )
        assert "Worked out for this question" not in result.text
        assert result.details["judge"]["span"] is None

    def test_without_a_span_the_days_between_the_dates_are_listed(self) -> None:
        facts = [
            {"id": "a", "text": "Meeting.", "event_date": "2024-03-25", "message_index": 1},
            {"id": "b", "text": "Accepted.", "event_date": "2024-04-15", "message_index": 5},
            {"id": "c", "text": "Signed.", "event_date": "2024-05-01", "message_index": 9},
        ]
        text = ContextAssembler._render_timeline(facts, {}, None, between=True)
        assert text.splitlines()[-1] == (
            "Days between these dates, worked out: "
            "2024-03-25 to 2024-04-15: 21 days (3 weeks); "
            "2024-03-25 to 2024-05-01: 37 days (5 weeks and 2 days); "
            "2024-04-15 to 2024-05-01: 16 days (2 weeks and 2 days)."
        )

    def test_date_candidates_take_turns_between_the_parts_of_a_question(self) -> None:
        first = [{"id": "a", "event_date": "2024-03-22"}, {"id": "b", "event_date": "2024-03-25"}]
        second = [{"id": "c", "event_date": "2024-05-01"}, {"id": "a", "event_date": "2024-03-22"}]
        decisions = [
            {"id": "d", "event_date": "2024-06-01", "changed_at": None, "score": 0.8},
            {"id": "e", "event_date": None, "changed_at": None, "score": 0.9},
        ]
        chosen = ContextAssembler._date_candidates([first, second], decisions)
        assert [c["id"] for c in chosen] == ["a", "c", "b", "d"]

    @pytest.mark.asyncio
    async def test_a_summary_question_gets_more_exchanges_cut_short(self) -> None:
        rows = _session_rows()
        rows["RETURN m.id AS id, m.content AS content"] = [
            {"id": f"m{i}", "content": "long " * 300} for i in range(8)
        ]

        def answer(name: str, question: Any, state: Any) -> Any:
            if name == "shape":
                return {"lookup": 0.1, "elapsed": 0.0, "order": 0.1, "summary": 0.8}
            return None

        judge = FakeJudge(answer)
        assembler = ContextAssembler(FakeClient(rows), FakeEmbedder(), judge)  # type: ignore[arg-type]
        result = await assembler.assemble(
            "Summarize how my deployment went.",
            session_id="s",
            route=True,
            broad_message_chars=100,
        )
        assert result.details["judge"]["shape"] == "summary"
        assert "Long messages are cut short, marked [...]." in result.text
        assert "long long long [...]" in result.text
        assert ("long " * 30) not in result.text

    @pytest.mark.asyncio
    async def test_a_judge_that_fails_leaves_the_context_as_without_one(self) -> None:
        class Broken:
            name = "broken"

            async def judge(self, state: Any, questions: Any) -> dict[str, Any]:
                raise RuntimeError("unreachable")

        async def no_sleep(_: float) -> None:
            return None

        import neo4j_agent_memory.judge as judge_module

        original = judge_module.asyncio.sleep
        judge_module.asyncio.sleep = no_sleep  # type: ignore[assignment]
        try:
            assembler = ContextAssembler(FakeClient(_session_rows()), FakeEmbedder(), Broken())  # type: ignore[arg-type]
            result = await assembler.assemble("Have I used Docker?", session_id="s")
        finally:
            judge_module.asyncio.sleep = original  # type: ignore[assignment]
        plain = await ContextAssembler(FakeClient(_session_rows()), FakeEmbedder()).assemble(  # type: ignore[arg-type]
            "Have I used Docker?", session_id="s"
        )
        assert result.text == plain.text


# --- the entity graph ---------------------------------------------------------


class TestWalk:
    def test_the_walk_reaches_messages_that_mention_no_seed(self) -> None:
        # "argon2" is mentioned in messages 0 and 2; message 2 also mentions
        # "login", which message 5 mentions too. Message 7 shares nothing.
        mentions = {"argon2": {0, 2}, "login": {2, 5}, "docker": {7}}
        scores = ctx.walk_message_scores({"argon2": 1.0}, mentions, {})
        assert scores[0] > scores[5] > 0
        assert 7 not in scores

    def test_a_relationship_carries_the_walk_to_another_entity(self) -> None:
        mentions = {"flask": {0}, "gunicorn": {4}}
        without = ctx.walk_message_scores({"flask": 1.0}, mentions, {})
        with_relation = ctx.walk_message_scores(
            {"flask": 1.0}, mentions, {"flask": {"gunicorn"}, "gunicorn": {"flask"}}
        )
        assert 4 not in without and with_relation[4] > 0

    def test_an_entity_mentioned_everywhere_spreads_thin(self) -> None:
        mentions = {"rare": {0}, "common": set(range(20))}
        scores = ctx.walk_message_scores({"rare": 1.0, "common": 1.0}, mentions, {})
        assert scores[0] > 5 * scores[10]

    def test_no_seeds_no_scores(self) -> None:
        assert ctx.walk_message_scores({}, {"a": {0}}, {}) == {}

    def test_kind_of_a_typed_entity(self) -> None:
        assert ctx.kind_of("OBJECT:CODE_ARTIFACT") == "code artifact"
        assert ctx.kind_of("PERSON") == "person"


def _typed_rows() -> dict[str, list[dict[str, Any]]]:
    """A session whose entities carry domain types, mentioned by the user."""
    rows = _session_rows()
    rows["MENTIONS]->(e:Entity)"] = [
        {
            "id": "e1",
            "name": "Todoist",
            "type": "OBJECT",
            "subtype": "SOFTWARE",
            "message_ids": ["m0", "m4"],
        },
        {
            "id": "e2",
            "name": "Asana",
            "type": "OBJECT",
            "subtype": "SOFTWARE",
            "message_ids": ["m2"],
        },
        {
            "id": "e3",
            "name": "Excel",
            "type": "OBJECT",
            "subtype": "SOFTWARE",
            "message_ids": ["m1"],
        },
        {
            "id": "e4",
            "name": "Joshua",
            "type": "PERSON",
            "subtype": None,
            "message_ids": ["m0", "m2"],
        },
        {"id": "e5", "name": "Linda", "type": "PERSON", "subtype": None, "message_ids": ["m6"]},
    ]
    rows["entity_embedding_idx"] = [{"id": "e1", "score": 0.9}, {"id": "e4", "score": 0.6}]
    return rows


class TestEntityOptions:
    @pytest.mark.asyncio
    async def test_statements_are_reached_through_the_questions_entities(self) -> None:
        rows = _typed_rows()
        rows["<-[:ABOUT]-(f:Fact"] = [
            {
                "entity": "e1",
                "id": "f9",
                "text": "The user moved weekend plans into Todoist.",
                "message_index": 6,
                "said_at": "2024-03-15",
                "event_date": None,
                "changed_at": None,
                "score": 0.4,
            },
            {
                "entity": "e1",
                "id": "f1",  # already among the statements that match the question
                "text": "The user has never used Docker.",
                "message_index": 4,
                "said_at": "2024-03-15",
                "event_date": None,
                "changed_at": None,
                "score": 0.9,
            },
        ]
        assembler = ContextAssembler(FakeClient(rows), FakeEmbedder())  # type: ignore[arg-type]
        result = await assembler.assemble(
            "What do I use Todoist for?", session_id="s", entity_statements=4
        )
        section = result.text.split("## What else the user has said")[1].split("\n## ")[0]
        assert "About Todoist:" in section
        assert "- [#7, 2024-03-15] The user moved weekend plans into Todoist." in section
        assert "never used Docker" not in section
        assert result.details["entity_statements"] == {"Todoist": 1}
        # The statement's exchange is ranked through it.
        assert any("entity_statements" in p["ranks"] for p in result.details["pairs"])

    @pytest.mark.asyncio
    async def test_the_option_is_off_by_default(self) -> None:
        assembler = ContextAssembler(FakeClient(_typed_rows()), FakeEmbedder())  # type: ignore[arg-type]
        result = await assembler.assemble("What do I use Todoist for?", session_id="s")
        assert "## What else the user has said" not in result.text
        assert result.details["graph_walk"] == "hop"

    @pytest.mark.asyncio
    async def test_things_of_one_kind_are_listed_when_the_question_counts_them(self) -> None:
        def answer(name: str, question: Any, state: Any) -> Any:
            if name == "kind":
                return {"none": 0.1, "software": 0.85, "person": 0.05}
            if name.startswith("t"):
                return 0.9 if state["things"][int(name[1:])] in ("Todoist", "Asana") else 0.1
            return None

        judge = FakeJudge(answer)
        assembler = ContextAssembler(FakeClient(_typed_rows()), FakeEmbedder(), judge)  # type: ignore[arg-type]
        result = await assembler.assemble(
            "How many different tools have I mentioned for planning?",
            session_id="s",
            enumerate_entities=True,
            rerank=False,
        )
        section = result.text.split("## Things of the kind the question asks about (software)")[1]
        section = section.split("\n## ")[0]
        assert "- Todoist: #1, #5" in section and "- Asana: #3" in section
        # Excel was only mentioned by the assistant (#2), so it was never a candidate.
        assert "Excel" not in section
        assert result.details["enumerated"] == {
            "kind": "software",
            "judged": 2,
            "listed": ["Todoist", "Asana"],
        }
        kinds = next(q["kind"] for _, q in judge.calls if "kind" in q).options
        assert list(kinds) == ["none", "software", "person"]
        assert kinds["software"].startswith("2 in the conversation, such as Todoist")

    @pytest.mark.asyncio
    async def test_nothing_is_listed_when_the_question_counts_nothing(self) -> None:
        judge = FakeJudge(
            lambda name, *_: {"none": 0.9, "software": 0.1} if name == "kind" else None
        )
        assembler = ContextAssembler(FakeClient(_typed_rows()), FakeEmbedder(), judge)  # type: ignore[arg-type]
        result = await assembler.assemble(
            "Which tools have I mentioned?", session_id="s", enumerate_entities=True, rerank=False
        )
        assert "## Things of the kind" not in result.text
        assert result.details["enumerated"] is None

    @pytest.mark.asyncio
    async def test_a_question_that_counts_nothing_is_not_put_to_the_judge(self) -> None:
        judge = FakeJudge()
        assembler = ContextAssembler(FakeClient(_typed_rows()), FakeEmbedder(), judge)  # type: ignore[arg-type]
        await assembler.assemble(
            "What do I use Todoist for?", session_id="s", enumerate_entities=True, rerank=False
        )
        assert not any("kind" in questions for _, questions in judge.calls)

    @pytest.mark.parametrize(
        ("question", "expected"),
        [
            ("How many different tools have I mentioned for planning?", True),
            ("Which libraries have I used in this project?", True),
            ("List all the movies I planned to watch.", True),
            ("How many days passed between the kickoff and the launch?", False),
            ("Have I ever used Docker?", False),
            ("When does my first sprint end?", False),
        ],
    )
    def test_counting_questions_are_recognised(self, question: str, expected: bool) -> None:
        assert ctx.asks_to_count_or_list(question) is expected

    @pytest.mark.asyncio
    async def test_the_judge_narrows_the_questions_entities(self) -> None:
        def answer(name: str, question: Any, state: Any) -> Any:
            if name.startswith("e") and name[1:].isdigit():
                return 0.9 if state["entities"][int(name[1:])]["name"] == "Todoist" else 0.1
            return None

        assembler = ContextAssembler(FakeClient(_typed_rows()), FakeEmbedder(), FakeJudge(answer))  # type: ignore[arg-type]
        result = await assembler.assemble(
            "What do I use Todoist for?", session_id="s", judge_entities=True, rerank=False
        )
        assert [e["name"] for e in result.details["question_entities"]] == ["Todoist"]

    @pytest.mark.asyncio
    async def test_an_unavailable_judge_does_not_stop_the_assembly(self) -> None:
        from neo4j_agent_memory.judge import JudgeUnavailable

        class NoCredits:
            name = "down"

            async def judge(self, state: Any, questions: Any) -> dict[str, Any]:
                raise JudgeUnavailable("no credits")

        assembler = ContextAssembler(FakeClient(_typed_rows()), FakeEmbedder(), NoCredits())  # type: ignore[arg-type]
        result = await assembler.assemble(
            "How many tools?", session_id="s", enumerate_entities=True, judge_entities=True
        )
        assert "## Conversation excerpts" in result.text
        assert result.details["enumerated"] is None


# --- keyword, summary and community search ---------------------------------


class RecordingClient(FakeClient):
    """A fake client that also keeps the parameters of each read."""

    def __init__(self, rows: dict[str, list[dict[str, Any]]]) -> None:
        super().__init__(rows)
        self.parameters: list[tuple[str, dict[str, Any]]] = []

    async def execute_read(self, query: str, parameters: dict[str, Any] | None = None, **_: Any):
        self.parameters.append((query, parameters or {}))
        return await super().execute_read(query, parameters)

    def asked(self, marker: str) -> list[dict[str, Any]]:
        return [params for query, params in self.parameters if marker in query]


def _thread(identifier: str, title: str, summary: str, score: float, **more: Any) -> dict[str, Any]:
    return {
        "id": identifier,
        "level": 0,
        "title": title,
        "summary": summary,
        "messages": [0, 2, 6],
        "first_index": 0,
        "last_index": 6,
        "ancestors": [],
        "score": score,
        **more,
    }


class TestKeywordQuery:
    def test_content_words_are_quoted_and_the_rest_dropped(self) -> None:
        query = ctx.keyword_query('What did I say about "Paddington 2" and the $85,000 budget?')
        assert query == '"Paddington" "85,000" "budget"'

    def test_a_word_is_taken_once(self) -> None:
        assert ctx.keyword_query("Docker or docker, which Docker?") == '"Docker"'

    def test_nothing_in_a_word_is_read_as_query_syntax(self) -> None:
        # Version numbers and paths keep their punctuation inside the quotes.
        assert ctx.keyword_query("Flask v2.3.1 on src/app") == '"Flask" "v2.3.1" "src/app"'

    def test_a_question_without_content_words_gives_no_query(self) -> None:
        assert ctx.keyword_query("What did I do?") == ""


class TestInterleave:
    def test_rankings_take_turns_and_repeats_are_dropped(self) -> None:
        assert ctx.interleave([[1, 2, 3], [2, 9], [7]], limit=5) == [1, 2, 7, 9, 3]

    def test_the_limit_holds(self) -> None:
        assert ctx.interleave([[1, 2, 3], [4, 5, 6]], limit=3) == [1, 4, 2]


class TestBreadth:
    @pytest.mark.parametrize(
        ("question", "expected"),
        [
            ("Can you list the order in which I brought up the parts of my project?", "order"),
            ("Walk me through the order in which I raised these ideas.", "order"),
            ("Can you list in order how I brought up my writing projects?", "order"),
            ("Can you give me a summary of how I planned the family events?", "summary"),
            ("Can you summarize how my resume developed over the past months?", "summary"),
            ("How has my estate plan changed over time?", "summary"),
            ("What is the deadline for the pilot episode?", None),
            ("How can I best sequence my resume updates?", None),
            ("How did you recommend keeping steady progress throughout the weeks?", None),
        ],
    )
    def test_questions_about_a_whole_thread_are_recognised(
        self, question: str, expected: str | None
    ) -> None:
        assert ctx.breadth_of(question) == expected


class TestSearchOptions:
    @pytest.mark.asyncio
    async def test_keyword_search_ranks_messages_and_statements(self) -> None:
        rows = _session_rows()
        rows["message_content_fulltext_idx"] = [{"id": "m2", "score": 3.1}]
        rows["fact_text_fulltext_idx"] = [
            {
                "id": "f7",
                "text": "The user set the budget at $85,000.",
                "message_index": 2,
                "said_at": "2024-03-15",
                "event_date": None,
                "score": 4.0,
            }
        ]
        client = RecordingClient(rows)
        assembler = ContextAssembler(client, FakeEmbedder())  # type: ignore[arg-type]
        result = await assembler.assemble(
            "What was the $85,000 budget for?", session_id="s", keyword=True
        )
        assert result.details["keyword"] == '"85,000" "budget"'
        assert result.details["keyword_statements"] == 1
        assert "The user set the budget at $85,000." in result.text
        ranks = {tuple(p["messages"]): p["ranks"] for p in result.details["pairs"]}
        assert ranks[(3, 4)]["keyword"] == 1
        assert client.asked("message_content_fulltext_idx")[0]["query"] == '"85,000" "budget"'

    @pytest.mark.asyncio
    async def test_keyword_search_is_off_by_default(self) -> None:
        client = RecordingClient(_session_rows())
        assembler = ContextAssembler(client, FakeEmbedder())  # type: ignore[arg-type]
        result = await assembler.assemble("What was the $85,000 budget for?", session_id="s")
        assert result.details["keyword"] == ""
        assert not client.asked("fulltext")

    @pytest.mark.asyncio
    async def test_summary_search_ranks_the_exchanges_of_the_nearest_stretches(self) -> None:
        rows = _session_rows()
        rows["summary_embedding_idx"] = [{"start_index": 2, "end_index": 3, "score": 0.9}]
        rows["vector.similarity.cosine(m.embedding"] = [
            {"id": "m2", "score": 0.4},
            {"id": "m3", "score": 0.7},
        ]
        client = RecordingClient(rows)
        assembler = ContextAssembler(client, FakeEmbedder())  # type: ignore[arg-type]
        result = await assembler.assemble("Docker?", session_id="s", summary_search=2)
        assert result.details["summary_stretches"] == [[3, 4]]
        ranks = {tuple(p["messages"]): p["ranks"] for p in result.details["pairs"]}
        assert ranks[(3, 4)] == {"summaries": 1}
        assert client.asked("summary_embedding_idx")[0]["detail"] == "stretch"

    @pytest.mark.asyncio
    async def test_the_nearest_preferences_rank_their_messages(self) -> None:
        rows = _session_rows()
        rows["MATCH (p:Preference"] = [
            {
                "id": "p1",
                "text": "The user prefers few dependencies.",
                "kind": "preference",
                "message_index": 2,
                "score": 0.9,
            },
            {
                "id": "p2",
                "text": "The user likes tea.",
                "kind": "preference",
                "message_index": 6,
                "score": 0.3,  # too far from the question to rank its message
            },
        ]
        assembler = ContextAssembler(FakeClient(rows), FakeEmbedder())  # type: ignore[arg-type]
        result = await assembler.assemble("Which library?", session_id="s", preference_search=5)
        ranks = {tuple(p["messages"]): p["ranks"] for p in result.details["pairs"]}
        assert ranks[(3, 4)] == {"preferences": 1}
        assert "preferences" not in ranks[(7, 8)]

    @pytest.mark.asyncio
    async def test_a_search_over_a_missing_index_is_done_without(self) -> None:
        class NoKeywordIndex(FakeClient):
            async def execute_read(self, query: str, parameters: Any = None, **_: Any):
                if "fulltext" in query:
                    raise RuntimeError("There is no such fulltext schema index")
                return await super().execute_read(query, parameters)

        assembler = ContextAssembler(NoKeywordIndex(_session_rows()), FakeEmbedder())  # type: ignore[arg-type]
        result = await assembler.assemble("What about Docker?", session_id="s", keyword=True)
        assert "## Conversation excerpts" in result.text
        assert result.details["keyword_statements"] == 0

    @pytest.mark.asyncio
    async def test_the_outline_can_be_the_one_with_every_exchange(self) -> None:
        client = RecordingClient(_session_rows())
        assembler = ContextAssembler(client, FakeEmbedder())  # type: ignore[arg-type]
        result = await assembler.assemble(
            "Can you summarize how my Docker setup went?",
            session_id="s",
            outline_detail="exchange",
        )
        assert client.asked("MATCH (s:Summary")[0]["detail"] == "exchange"
        # A summary question is pointed at the outline when no threads are shown.
        assert '"Outline of the conversation" has one line per exchange' in result.text

    @pytest.mark.asyncio
    async def test_a_summary_question_is_shown_the_outline_per_exchange_when_there_is_one(
        self,
    ) -> None:
        class TwoOutlines(RecordingClient):
            async def execute_read(self, query: str, parameters: Any = None, **_: Any):
                if "MATCH (s:Summary" in query and (parameters or {}).get("detail") == "exchange":
                    self.parameters.append((query, parameters or {}))
                    return [
                        {
                            "start_index": 0,
                            "end_index": 7,
                            "text": "- #1: Asked about Docker; the assistant gave three steps.",
                            "start_time": None,
                        }
                    ]
                return await super().execute_read(query, parameters)

        client = TwoOutlines(_session_rows())
        assembler = ContextAssembler(client, FakeEmbedder())  # type: ignore[arg-type]
        summary = await assembler.assemble(
            "Can you summarize how my Docker setup went?", session_id="s"
        )
        assert "the assistant gave three steps" in summary.text
        assert summary.details["outline_detail"] == "exchange"
        assert "This question asks for a summary." in summary.text

        other = await assembler.assemble("Have I used Docker?", session_id="s")
        assert "- Docker deployment." in other.text
        assert other.details["outline_detail"] == "stretch"
        assert "This question asks for a summary." not in other.text

    @pytest.mark.asyncio
    async def test_without_an_outline_per_exchange_a_summary_question_gets_the_other(
        self,
    ) -> None:
        client = RecordingClient(_session_rows())
        assembler = ContextAssembler(client, FakeEmbedder())  # type: ignore[arg-type]

        # The canned rows answer every summary read alike; tell the two apart by detail.
        async def only_stretch(query: str, parameters: Any = None, **_: Any):
            if "MATCH (s:Summary" in query and (parameters or {}).get("detail") == "exchange":
                return []
            return await RecordingClient.execute_read(client, query, parameters)

        client.execute_read = only_stretch  # type: ignore[method-assign]
        result = await assembler.assemble(
            "Can you summarize how my Docker setup went?", session_id="s"
        )
        assert result.details["outline_detail"] == "stretch"
        assert "- Docker deployment." in result.text

    def test_an_outline_entry_is_cut_at_the_end_of_a_bullet(self) -> None:
        summaries = [
            {
                "start_index": i * 10,
                "end_index": i * 10 + 9,
                "text": "- " + "a" * 150 + "\n- " + "b" * 150,
                "start_time": None,
            }
            for i in range(2)
        ]
        text = ContextAssembler._render_outline(summaries, 20, max_chars=520)
        assert "a" * 150 in text
        assert "bbb" not in text


class TestThreads:
    SUMMARY_QUESTION = "Can you give me a summary of how my pilot episode went?"

    def _rows(self) -> dict[str, list[dict[str, Any]]]:
        rows = _session_rows()
        rows["community_embedding_idx"] = [
            _thread(
                "c1",
                "Pilot episode planning",
                "- #1: Asked for a plan; the assistant laid out three phases.\n"
                "- #7: Pushed delivery back to July 15.",
                0.9,
            ),
            _thread("c2", "Pilot episode and editing", "- #3: Chose an editor.", 0.8),
            _thread("c3", "Family plans", "- #5: Planned a weekend.", 0.5),
        ]
        return rows

    @pytest.mark.asyncio
    async def test_a_summary_question_is_shown_the_nearest_threads(self) -> None:
        assembler = ContextAssembler(FakeClient(self._rows()), FakeEmbedder())  # type: ignore[arg-type]
        result = await assembler.assemble(self.SUMMARY_QUESTION, session_id="s", communities=2)
        section = result.text.split("## Threads of the conversation")[1].split("\n## ")[0]
        assert "### Pilot episode planning (messages #1 to #8)" in section
        assert "the assistant laid out three phases" in section
        assert "Family plans" not in section
        assert result.details["breadth"] == "summary"
        assert [t["title"] for t in result.details["threads"]] == [
            "Pilot episode planning",
            "Pilot episode and editing",
        ]
        assert result.details["threads"][0]["messages"] == [1, 7]
        # The threads come last of the memory, and the guidance says how to use them.
        assert result.text.index("## Threads of the conversation") > result.text.index(
            "## What the user has said"
        )
        assert "This question asks for a summary." in result.text
        # The messages the bullets point at are ranked.
        ranks = {tuple(p["messages"]): p["ranks"] for p in result.details["pairs"]}
        assert ranks[(1, 2)]["communities"] == 1
        assert ranks[(3, 4)]["communities"] == 2

    @pytest.mark.asyncio
    async def test_a_question_about_one_thing_is_not(self) -> None:
        client = RecordingClient(self._rows())
        assembler = ContextAssembler(client, FakeEmbedder())  # type: ignore[arg-type]
        result = await assembler.assemble(
            "What is the pilot's deadline?", session_id="s", communities=2
        )
        assert "## Threads of the conversation" not in result.text
        assert result.details["threads"] == []
        assert not client.asked("community_embedding_idx")

    @pytest.mark.asyncio
    async def test_threads_can_be_shown_for_every_question(self) -> None:
        assembler = ContextAssembler(FakeClient(self._rows()), FakeEmbedder())  # type: ignore[arg-type]
        result = await assembler.assemble(
            "What is the pilot's deadline?", session_id="s", communities=1, thread_scope="all"
        )
        assert "### Pilot episode planning" in result.text
        assert "This question asks for" not in result.text

    @pytest.mark.asyncio
    async def test_an_order_question_gets_its_own_guidance(self) -> None:
        assembler = ContextAssembler(FakeClient(self._rows()), FakeEmbedder())  # type: ignore[arg-type]
        result = await assembler.assemble(
            "Can you list the order in which I brought up the parts of the pilot?",
            session_id="s",
            communities=1,
            thread_scope="broad",
        )
        assert "This question asks for an order." in result.text

    @pytest.mark.asyncio
    async def test_an_order_question_is_not_shown_threads_unless_asked(self) -> None:
        assembler = ContextAssembler(FakeClient(self._rows()), FakeEmbedder())  # type: ignore[arg-type]
        result = await assembler.assemble(
            "Can you list the order in which I brought up the parts of the pilot?",
            session_id="s",
            communities=1,
        )
        assert result.details["breadth"] == "order"
        assert "## Threads of the conversation" not in result.text

    @pytest.mark.asyncio
    async def test_a_community_and_its_parent_are_not_both_shown(self) -> None:
        rows = self._rows()
        rows["community_embedding_idx"] = [
            _thread("c1", "Pilot planning", "- #1: A plan.", 0.9, ancestors=["p1"]),
            _thread("p1", "Pilot episode", "- #1: Everything.", 0.85, level=1),
            _thread("c2", "Pilot editing", "- #3: An editor.", 0.8, ancestors=["p1"]),
            _thread("c3", "Family plans", "- #5: A weekend.", 0.5),
        ]
        assembler = ContextAssembler(FakeClient(rows), FakeEmbedder())  # type: ignore[arg-type]
        result = await assembler.assemble(self.SUMMARY_QUESTION, session_id="s", communities=3)
        # The parent is skipped for its child; the sibling is another line of the story.
        assert [t["title"] for t in result.details["threads"]] == [
            "Pilot planning",
            "Pilot editing",
            "Family plans",
        ]

    @pytest.mark.asyncio
    async def test_threads_are_off_by_default(self) -> None:
        assembler = ContextAssembler(FakeClient(self._rows()), FakeEmbedder())  # type: ignore[arg-type]
        result = await assembler.assemble(self.SUMMARY_QUESTION, session_id="s")
        assert "## Threads of the conversation" not in result.text

    @pytest.mark.asyncio
    async def test_keyword_search_joins_the_search_for_threads(self) -> None:
        rows = self._rows()
        rows["community_summary_fulltext_idx"] = [
            _thread("c3", "Family plans", "- #5: Planned a weekend.", 7.0),
            _thread("c1", "Pilot episode planning", "- #1: A plan.", 5.0),
        ]
        assembler = ContextAssembler(FakeClient(rows), FakeEmbedder())  # type: ignore[arg-type]
        result = await assembler.assemble(
            self.SUMMARY_QUESTION, session_id="s", communities=1, keyword=True
        )
        # First by embedding and second by keyword beats third and first.
        assert [t["title"] for t in result.details["threads"]] == ["Pilot episode planning"]


class TestFeedbackEntities:
    @pytest.mark.asyncio
    async def test_entities_of_the_best_exchanges_lead_to_other_exchanges(self) -> None:
        rows = _typed_rows()
        rows["entity_embedding_idx"] = []
        rows["vector.similarity.cosine(e.embedding"] = [{"id": "e1", "score": 0.7}]
        assembler = ContextAssembler(FakeClient(rows), FakeEmbedder())  # type: ignore[arg-type]
        # The best exchange (#5) mentions Todoist, which message #1 mentions too.
        result = await assembler.assemble(
            "What about the budget?", session_id="s", feedback_entities=3, feedback_pairs=1
        )
        assert result.details["feedback_entities"] == {"Todoist": 0.7}
        ranks = {tuple(p["messages"]): p["ranks"] for p in result.details["pairs"]}
        assert "feedback" in ranks[(1, 2)]

    @pytest.mark.asyncio
    async def test_an_entity_only_the_best_exchanges_mention_leads_nowhere(self) -> None:
        rows = _typed_rows()
        rows["entity_embedding_idx"] = []
        assembler = ContextAssembler(FakeClient(rows), FakeEmbedder())  # type: ignore[arg-type]
        # The two best exchanges (#1 and #5) hold every mention of Todoist, so it
        # points nowhere new; Joshua is also mentioned in #3.
        result = await assembler.assemble(
            "What about the budget?", session_id="s", feedback_entities=3, feedback_pairs=2
        )
        assert set(result.details["feedback_entities"]) == {"Joshua"}
        ranks = {tuple(p["messages"]): p["ranks"] for p in result.details["pairs"]}
        assert "feedback" in ranks[(3, 4)]

    @pytest.mark.asyncio
    async def test_the_option_is_off_by_default(self) -> None:
        assembler = ContextAssembler(FakeClient(_typed_rows()), FakeEmbedder())  # type: ignore[arg-type]
        result = await assembler.assemble("What about the budget?", session_id="s")
        assert result.details["feedback_entities"] == {}


# --- the graph around the retrieved exchanges and the question's entities ----


def _related_rows() -> dict[str, list[dict[str, Any]]]:
    """The typed session, with relationships between its entities."""
    rows = _typed_rows()
    rows["coalesce(r.source_message_ids, []) AS message_ids"] = [
        {"a": "e4", "b": "e1", "type": "USES", "support": 3, "message_ids": ["m2"]},
        {"a": "e1", "b": "e2", "type": "REPLACES", "support": 1, "message_ids": ["m6"]},
        {"a": "e5", "b": "e3", "type": "USES", "support": 1, "message_ids": []},
    ]
    return rows


class TestExpandEntities:
    RELATIONS = [
        {"a": "a", "b": "b", "type": "USES", "support": 2},
        {"a": "b", "b": "c", "type": "PART_OF", "support": 1},
        {"a": "d", "b": "a", "type": "KNOWS", "support": 1},
    ]

    def test_an_entity_is_followed_as_far_as_its_hops_allow(self) -> None:
        entities, walked = ctx.expand_entities({"a": 1}, self.RELATIONS)
        assert entities == ["a", "b", "d"]
        assert [(r["a"], r["type"], r["b"]) for r in walked] == [
            ("a", "USES", "b"),
            ("d", "KNOWS", "a"),
        ]
        entities, walked = ctx.expand_entities({"a": 2}, self.RELATIONS)
        assert entities == ["a", "b", "d", "c"]
        assert ("b", "PART_OF", "c") in [(r["a"], r["type"], r["b"]) for r in walked]

    def test_an_entity_with_no_hops_is_kept_alone(self) -> None:
        assert ctx.expand_entities({"a": 0}, self.RELATIONS) == (["a"], [])

    def test_a_relationship_reached_twice_is_walked_once(self) -> None:
        _, walked = ctx.expand_entities({"a": 1, "b": 1}, self.RELATIONS)
        assert len(walked) == 3

    def test_the_paths_of_an_entity_are_limited(self) -> None:
        _, walked = ctx.expand_entities({"a": 1}, self.RELATIONS, one_hop_paths=1)
        # The better supported relationship is the one kept.
        assert [(r["a"], r["b"]) for r in walked] == [("a", "b")]


class TestGraphAroundTheExcerpts:
    @pytest.mark.asyncio
    async def test_entities_of_the_best_exchanges_are_shown_with_their_relationships(
        self,
    ) -> None:
        rows = _related_rows()
        rows["entity_embedding_idx"] = []
        rows["vector.similarity.cosine(e.embedding"] = [
            {"id": "e1", "score": 0.95},  # matches the question: two relationships out
            {"id": "e4", "score": 0.6},
        ]
        assembler = ContextAssembler(FakeClient(rows), FakeEmbedder())  # type: ignore[arg-type]
        result = await assembler.assemble(
            "What about the budget?", session_id="s", chunk_entities=10, feedback_pairs=1
        )
        section = result.text.split("## How the things in the excerpts are related")[1].split(
            "\n## "
        )[0]
        # The best exchange (#5) mentions Todoist; from it, one and two relationships out.
        assert "- Joshua (person) -[USES]-> Todoist (software)" in section
        assert "- Todoist (software) -[REPLACES]-> Asana (software)" in section
        assert "Excel" not in section
        assert result.details["graph_relations"] == 2
        # The messages the relationships were taken from are ranked.
        ranks = {tuple(p["messages"]): p["ranks"] for p in result.details["pairs"]}
        assert "relations" in ranks[(3, 4)]

    @pytest.mark.asyncio
    async def test_an_entity_far_from_the_question_is_not_followed(self) -> None:
        rows = _related_rows()
        rows["entity_embedding_idx"] = []
        rows["vector.similarity.cosine(e.embedding"] = [{"id": "e1", "score": 0.1}]
        assembler = ContextAssembler(FakeClient(rows), FakeEmbedder())  # type: ignore[arg-type]
        result = await assembler.assemble(
            "What about the budget?", session_id="s", chunk_entities=10, feedback_pairs=1
        )
        assert "## How the things in the excerpts are related" not in result.text
        assert result.details["graph_relations"] == 0

    @pytest.mark.asyncio
    async def test_the_option_is_off_by_default(self) -> None:
        assembler = ContextAssembler(FakeClient(_related_rows()), FakeEmbedder())  # type: ignore[arg-type]
        result = await assembler.assemble("What about the budget?", session_id="s")
        assert "## How the things in the excerpts are related" not in result.text


class TestAroundTheQuestionsEntities:
    @pytest.mark.asyncio
    async def test_the_most_connected_entities_are_named_with_what_connects_them(self) -> None:
        rows = _related_rows()
        rows["entity_embedding_idx"] = [{"id": "e1", "score": 0.9}]
        assembler = ContextAssembler(FakeClient(rows), FakeEmbedder())  # type: ignore[arg-type]
        result = await assembler.assemble(
            "What do I use Todoist for?", session_id="s", entity_neighbours=5
        )
        section = result.text.split("## Entities the question mentions")[1].split("\n## ")[0]
        assert "Most connected to them: Asana (software), Joshua (person)." in section
        assert "- Joshua -[USES]-> Todoist" in section
        assert "- Todoist -[REPLACES]-> Asana" in section
        assert result.details["entity_neighbours"] == ["Asana", "Joshua"]

    @pytest.mark.asyncio
    async def test_the_communities_of_the_questions_entities_are_shown_as_threads(self) -> None:
        rows = _related_rows()
        rows["entity_embedding_idx"] = [{"id": "e1", "score": 0.9}]
        rows["count(DISTINCT e) AS members"] = [
            _thread("c9", "Task tools", "- #1: Moved plans into Todoist.", 1.0, members=1)
        ]
        client = RecordingClient(rows)
        assembler = ContextAssembler(client, FakeEmbedder())  # type: ignore[arg-type]
        # Not a summary question: these threads come through the entities.
        result = await assembler.assemble(
            "What do I use Todoist for?", session_id="s", entity_communities=3
        )
        assert "### Task tools (messages #1 to #8)" in result.text
        assert [t["title"] for t in result.details["threads"]] == ["Task tools"]
        assert client.asked("count(DISTINCT e) AS members")[0]["ids"] == ["e1"]

    @pytest.mark.asyncio
    async def test_a_thread_found_both_ways_is_shown_once(self) -> None:
        rows = _related_rows()
        rows["entity_embedding_idx"] = [{"id": "e1", "score": 0.9}]
        thread = _thread("c9", "Task tools", "- #1: Moved plans into Todoist.", 0.9)
        rows["community_embedding_idx"] = [thread]
        rows["count(DISTINCT e) AS members"] = [dict(thread, members=1)]
        assembler = ContextAssembler(FakeClient(rows), FakeEmbedder())  # type: ignore[arg-type]
        result = await assembler.assemble(
            "Can you summarize how I used Todoist?",
            session_id="s",
            communities=2,
            entity_communities=2,
        )
        assert [t["title"] for t in result.details["threads"]] == ["Task tools"]
