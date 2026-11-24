"""Keyword, summary and community search during context assembly, against Neo4j.

Community detection needs the Graph Data Science plugin; its tests are skipped
on a server without it.
"""

from __future__ import annotations

from typing import Any

import pytest

from neo4j_agent_memory.llm.types import Completion
from neo4j_agent_memory.memory.distillation import DistillationMemory

pytestmark = pytest.mark.integration


class ScriptedLLM:
    """Answers every completion with the same text."""

    model = "scripted/model"

    def __init__(self, text: str) -> None:
        self.text = text

    async def complete(self, messages: Any, **_: Any) -> Completion:
        return Completion(content=self.text, model=self.model)


EXCHANGES = [
    ("I am planning the pilot episode and need a schedule.", "Here is a three phase plan."),
    ("My registration code is ZX4471, keep it for later.", "Noted, I will keep the code."),
    ("The pilot script is half written now.", "Good progress on the script."),
    ("For our anniversary Stephen booked a restaurant.", "That sounds like a lovely evening."),
    ("Casting for the pilot finished last week.", "Then filming can start."),
    (
        "Stephen wants to plan the anniversary trip together.",
        "Planning it together is a good idea.",
    ),
]


async def _store(client: Any, session_id: str) -> list[str]:
    """Store the exchanges and return the message ids in order."""
    messages = await client.short_term.add_messages_batch(
        session_id,
        [
            {"role": role, "content": content}
            for user, assistant in EXCHANGES
            for role, content in (("user", user), ("assistant", assistant))
        ],
    )
    return [str(message.id) for message in messages]


async def _mention(client: Any, mentions: dict[str, list[str]]) -> None:
    """Create one entity per name, mentioned by the given messages."""
    await client._client.execute_write(
        """
        UNWIND $rows AS row
        MERGE (e:Entity {id: 'test-' + row.name})
        SET e.name = row.name, e.type = 'OBJECT'
        WITH e, row
        UNWIND row.messages AS message_id
        MATCH (m:Message {id: message_id})
        MERGE (m)-[:MENTIONS]->(e)
        """,
        {"rows": [{"name": name, "messages": ids} for name, ids in mentions.items()]},
    )


class TestSchema:
    async def test_the_search_indexes_are_created(self, clean_memory_client: Any) -> None:
        rows = await clean_memory_client._client.execute_read(
            "SHOW INDEXES YIELD name, type RETURN name, type"
        )
        kinds = {row["name"]: row["type"] for row in rows}
        assert kinds["message_content_fulltext_idx"] == "FULLTEXT"
        assert kinds["fact_text_fulltext_idx"] == "FULLTEXT"
        assert kinds["community_summary_fulltext_idx"] == "FULLTEXT"
        assert kinds["summary_embedding_idx"] == "VECTOR"
        assert kinds["community_embedding_idx"] == "VECTOR"


class TestKeywordSearch:
    async def test_the_message_with_the_exact_word_is_ranked(
        self, clean_memory_client: Any, session_id: str
    ) -> None:
        await _store(clean_memory_client, session_id)
        await clean_memory_client._client.execute_read("CALL db.awaitIndexes(60)")
        result = await clean_memory_client.assemble_context(
            "What is the code ZX4471?", session_id=session_id, keyword=True
        )
        assert result.details["keyword"] == '"code" "ZX4471"'
        by_keyword = [p["messages"] for p in result.details["pairs"] if "keyword" in p["ranks"]]
        assert [3, 4] in by_keyword

    async def test_another_sessions_messages_are_not_found(
        self, clean_memory_client: Any, session_id: str
    ) -> None:
        await _store(clean_memory_client, session_id)
        other = f"{session_id}-other"
        await clean_memory_client.short_term.add_messages_batch(
            other, [{"role": "user", "content": "Nothing to see here."}]
        )
        await clean_memory_client._client.execute_read("CALL db.awaitIndexes(60)")
        result = await clean_memory_client.assemble_context(
            "What is the code ZX4471?", session_id=other, keyword=True
        )
        assert not any("keyword" in p["ranks"] for p in result.details["pairs"])


class TestSummarySearch:
    async def test_summaries_are_embedded_and_their_stretches_ranked(
        self, clean_memory_client: Any, mock_embedder: Any, session_id: str
    ) -> None:
        await _store(clean_memory_client, session_id)
        distillation = DistillationMemory(
            clean_memory_client._client,
            mock_embedder,
            ScriptedLLM("- The user planned the pilot episode and its script."),  # type: ignore[arg-type]
        )
        stats = await distillation.summarize_session(session_id, span=4)
        assert stats == {"summaries": 3, "failures": 0}
        rows = await clean_memory_client._client.execute_read(
            "MATCH (s:Summary {session_id: $s}) RETURN s.detail AS detail, "
            "s.embedding IS NOT NULL AS embedded",
            {"s": session_id},
        )
        assert [(row["detail"], row["embedded"]) for row in rows] == [("stretch", True)] * 3

        await clean_memory_client._client.execute_read("CALL db.awaitIndexes(60)")
        result = await clean_memory_client.assemble_context(
            "How is the pilot episode going?", session_id=session_id, summary_search=2
        )
        assert len(result.details["summary_stretches"]) == 2
        assert any("summaries" in p["ranks"] for p in result.details["pairs"])

    async def test_two_outlines_are_kept_side_by_side(
        self, clean_memory_client: Any, mock_embedder: Any, session_id: str
    ) -> None:
        await _store(clean_memory_client, session_id)
        client = clean_memory_client._client
        llm = ScriptedLLM("- #1: Asked for a schedule; the assistant gave three phases.")
        distillation = DistillationMemory(client, mock_embedder, llm)  # type: ignore[arg-type]
        await distillation.summarize_session(session_id, span=6)
        await distillation.summarize_session(session_id, span=6, detail="exchange")
        rows = await client.execute_read(
            "MATCH (s:Summary {session_id: $s}) RETURN s.detail AS detail, count(*) AS n",
            {"s": session_id},
        )
        assert {row["detail"]: row["n"] for row in rows} == {"stretch": 2, "exchange": 2}


@pytest.fixture
async def gds(clean_memory_client: Any) -> None:
    try:
        await clean_memory_client._client.execute_read("RETURN gds.version() AS version")
    except Exception:  # noqa: BLE001 - any failure means the plugin is not usable
        pytest.skip("Neo4j Graph Data Science is not installed on the test server")


class TestCommunities:
    async def _prepare(self, client: Any, session_id: str) -> None:
        ids = await _store(client, session_id)
        # Two groups of things that never come up in the same exchange.
        await _mention(
            client,
            {
                "pilot": [ids[0], ids[4]],
                "script": [ids[0], ids[4]],
                "casting": [ids[4], ids[8]],
                "Stephen": [ids[6], ids[10]],
                "anniversary": [ids[6], ids[10]],
            },
        )

    async def test_entities_that_occur_together_form_a_community(
        self, clean_memory_client: Any, mock_embedder: Any, session_id: str, gds: None
    ) -> None:
        await self._prepare(clean_memory_client, session_id)
        distillation = DistillationMemory(
            clean_memory_client._client,
            mock_embedder,
            ScriptedLLM("The thread\n- #1: Asked for a plan; got three phases."),  # type: ignore[arg-type]
        )
        stats = await distillation.detect_communities(session_id)
        assert stats["entities"] == 5
        assert stats["communities"] == 2
        assert stats["summaries"] == 2

        rows = await clean_memory_client._client.execute_read(
            """
            MATCH (:Conversation {session_id: $s})-[:HAS_COMMUNITY]->(c:Community)
            MATCH (e:Entity)-[:IN_COMMUNITY]->(c)
            WITH c, e ORDER BY e.name
            RETURN collect(e.name) AS names, c.messages AS messages, c.title AS title,
                   c.embedding IS NOT NULL AS embedded, c.first_index AS first_index
            ORDER BY first_index
            """,
            {"s": session_id},
        )
        assert [row["names"] for row in rows] == [
            ["casting", "pilot", "script"],
            ["Stephen", "anniversary"],
        ]
        assert [row["messages"] for row in rows] == [[0, 4, 8], [6, 10]]
        assert all(row["title"] == "The thread" and row["embedded"] for row in rows)

        # The projection is dropped again.
        graphs = await clean_memory_client._client.execute_read(
            "CALL gds.graph.list() YIELD graphName RETURN graphName"
        )
        assert not [g for g in graphs if g["graphName"].startswith("agent-memory-communities")]

    async def test_detecting_again_replaces_the_communities(
        self, clean_memory_client: Any, mock_embedder: Any, session_id: str, gds: None
    ) -> None:
        await self._prepare(clean_memory_client, session_id)
        distillation = DistillationMemory(clean_memory_client._client, mock_embedder, None)
        await distillation.detect_communities(session_id, summarize=False)
        await distillation.detect_communities(session_id, summarize=False)
        rows = await clean_memory_client._client.execute_read(
            "MATCH (c:Community {session_id: $s}) RETURN count(c) AS n", {"s": session_id}
        )
        assert rows[0]["n"] == 2

    async def test_a_summary_question_is_shown_the_threads(
        self, clean_memory_client: Any, mock_embedder: Any, session_id: str, gds: None
    ) -> None:
        await self._prepare(clean_memory_client, session_id)
        distillation = DistillationMemory(
            clean_memory_client._client,
            mock_embedder,
            ScriptedLLM("Pilot episode\n- #1: Asked for a schedule; got three phases."),  # type: ignore[arg-type]
        )
        await distillation.detect_communities(session_id)
        await clean_memory_client._client.execute_read("CALL db.awaitIndexes(60)")

        broad = await clean_memory_client.assemble_context(
            "Can you give me a summary of how the pilot episode went?",
            session_id=session_id,
            communities=2,
        )
        assert "## Threads of the conversation" in broad.text
        assert "### Pilot episode (messages #" in broad.text
        assert len(broad.details["threads"]) == 2

        narrow = await clean_memory_client.assemble_context(
            "Who booked the restaurant?", session_id=session_id, communities=2
        )
        assert "## Threads of the conversation" not in narrow.text
