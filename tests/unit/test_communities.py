"""Unit tests for community detection (``neo4j_agent_memory.memory.communities``).

Building the graph to cluster and reading the result are pure; the detector is
driven with a fake Neo4j client that answers each query from canned rows.
"""

from __future__ import annotations

from typing import Any

import pytest

from neo4j_agent_memory.llm.types import Completion
from neo4j_agent_memory.memory.communities import (
    CommunityDetector,
    GraphDataScienceUnavailable,
    assign_units,
    community_levels,
    cooccurrence,
    is_value_like,
    parse_thread,
    positions_in,
    unit_of,
)


class TestGraphToCluster:
    @pytest.mark.parametrize("name", ["$85,000", "15%", "3.2", "10", "2024-03-15", ""])
    def test_values_are_not_things(self, name: str) -> None:
        assert is_value_like(name)

    @pytest.mark.parametrize("name", ["Flask 2.3.1", "Python 3", "Stephen", "April 8", "HBO Max"])
    def test_things_with_a_number_in_their_name_are(self, name: str) -> None:
        assert not is_value_like(name)

    def test_an_exchange_is_named_by_its_first_message(self) -> None:
        roles = ["user", "assistant", "user", "user", "assistant"]
        assert [unit_of(p, roles) for p in range(5)] == [0, 0, 2, 3, 3]

    def test_entities_that_occur_together_are_linked_by_their_overlap(self) -> None:
        occurrences = {"a": {0, 2, 4}, "b": {0, 2}, "c": {4, 6}, "d": {6, 8}}
        links = cooccurrence(occurrences, unit_count=10)
        assert links == [("a", "b", 0.816497), ("a", "c", 0.408248), ("c", "d", 0.5)]

    def test_an_entity_seen_once_or_everywhere_is_left_out(self) -> None:
        occurrences = {
            "a": {0, 2},
            "b": {0, 2},
            "once": {2},
            "everywhere": {0, 2, 4, 6, 8},
        }
        assert cooccurrence(occurrences, unit_count=10, max_share=0.4) == [("a", "b", 1.0)]

    def test_a_relationship_adds_to_a_link_and_can_make_one(self) -> None:
        occurrences = {"a": {0, 2}, "b": {0, 2}, "c": {4, 6}}
        links = cooccurrence(
            occurrences, unit_count=10, related=[("b", "a"), ("c", "a"), ("a", "missing")]
        )
        assert links == [("a", "b", 1.5), ("a", "c", 0.5)]


class TestLevels:
    def test_each_level_groups_the_one_below(self) -> None:
        levels = community_levels(
            {"a": [1, 7], "b": [1, 7], "c": [2, 7], "d": [2, 7], "e": [3, 8], "f": [3, 8]}
        )
        by_key = {c["key"]: c for c in levels}
        assert by_key[(0, 1)]["entities"] == {"a", "b"}
        assert by_key[(0, 1)]["parent"] == (1, 7)
        assert by_key[(1, 7)]["entities"] == {"a", "b", "c", "d"}
        assert by_key[(1, 7)]["parent"] is None

    def test_a_parent_with_the_members_of_its_child_is_dropped(self) -> None:
        levels = community_levels(
            {"a": [1, 7], "b": [1, 7], "c": [2, 7], "d": [2, 7], "e": [3, 8], "f": [3, 8]}
        )
        keys = {c["key"] for c in levels}
        # Community 8 of the upper level is community 3 of the lower one again.
        assert (1, 8) not in keys
        assert next(c for c in levels if c["key"] == (0, 3))["parent"] is None

    def test_a_level_that_merges_nothing_is_dropped(self) -> None:
        levels = community_levels({"a": [1, 5, 9], "b": [1, 5, 9], "c": [2, 6, 9], "d": [2, 6, 9]})
        assert sorted(c["key"] for c in levels) == [(0, 1), (0, 2), (1, 9)]

    def test_an_exchange_goes_to_the_community_of_its_rarest_entity(self) -> None:
        communities = [
            {"key": (0, 1), "level": 0, "entities": {"common"}, "parent": None},
            {"key": (0, 2), "level": 0, "entities": {"rare"}, "parent": None},
        ]
        occurrences = {"common": {0, 2, 4, 6}, "rare": {4}}
        assign_units(communities, occurrences, unit_count=8)
        assert communities[0]["units"] == [0, 2, 6]
        assert communities[1]["units"] == [4]


class TestThreadText:
    def test_the_first_line_is_the_title_and_the_rest_are_bullets(self) -> None:
        title, summary = parse_thread("**Pilot planning**\n- #12: A plan.\n\n* #40: A delay.")
        assert title == "Pilot planning"
        assert summary == "- #12: A plan.\n- #40: A delay."

    def test_nothing_written_gives_nothing(self) -> None:
        assert parse_thread("  ") == ("", "")

    def test_bullets_point_at_messages(self) -> None:
        assert positions_in("- #12: A plan.\n- #40 (2024-03-20): A delay.") == [11, 39]


# --- the detector, against a fake client -----------------------------------


class FakeClient:
    """Reads answer from canned rows by a marker in the query; writes are recorded."""

    def __init__(self, rows: dict[str, list[dict[str, Any]]]) -> None:
        self.rows = rows
        self.writes: list[tuple[str, dict[str, Any]]] = []

    async def execute_read(self, query: str, parameters: dict[str, Any] | None = None, **_: Any):
        for marker, rows in self.rows.items():
            if marker in query:
                if rows and isinstance(rows[0], Exception):
                    raise rows[0]
                return [dict(row) for row in rows]
        return []

    async def execute_write(self, query: str, parameters: dict[str, Any] | None = None, **_: Any):
        self.writes.append((query, parameters or {}))
        return []

    def written(self, marker: str) -> list[dict[str, Any]]:
        return [params for query, params in self.writes if marker in query]


class FakeEmbedder:
    async def embed(self, text: str) -> list[float]:
        return [float(len(text))]


class FakeLLM:
    def __init__(self, text: str) -> None:
        self.text = text
        self.systems: list[str] = []
        self.prompts: list[str] = []

    async def complete(self, messages: Any, **_: Any) -> Completion:
        self.systems.append(messages[0].content)
        self.prompts.append(messages[-1].content)
        return Completion(content=self.text, model="fake/model")


def _session() -> dict[str, list[dict[str, Any]]]:
    """Eight messages; two pairs of entities that never meet."""
    messages = [
        {
            "id": f"m{i}",
            "role": "user" if i % 2 == 0 else "assistant",
            "content": f"message {i} " + "x" * 30,
            "timestamp": "2024-03-15T00:00:00Z",
        }
        for i in range(8)
    ]
    return {
        "RETURN gds.version()": [{"version": "2.13.0"}],
        "RETURN m.id AS id, m.role AS role": messages,
        "collect(DISTINCT m.id) AS message_ids": [
            {"id": "e1", "name": "script", "message_ids": ["m0", "m2"]},
            {"id": "e2", "name": "casting", "message_ids": ["m0", "m3"]},
            {"id": "e3", "name": "Stephen", "message_ids": ["m4", "m6"]},
            {"id": "e5", "name": "$120,000", "message_ids": ["m0", "m2", "m4"]},
        ],
        "collect(DISTINCT s.message_index) AS positions": [
            {"id": "e4", "name": "anniversary", "positions": [4, 6]},
        ],
        "gds.louvain.stream": [
            {"id": "e1", "communityId": 9, "intermediateCommunityIds": [1, 9]},
            {"id": "e2", "communityId": 9, "intermediateCommunityIds": [1, 9]},
            {"id": "e3", "communityId": 8, "intermediateCommunityIds": [2, 8]},
            {"id": "e4", "communityId": 8, "intermediateCommunityIds": [2, 8]},
        ],
    }


class TestDetect:
    @pytest.mark.asyncio
    async def test_without_the_plugin_detection_says_so(self) -> None:
        rows = _session()
        rows["RETURN gds.version()"] = [RuntimeError("Unknown function 'gds.version'")]  # type: ignore[list-item]
        detector = CommunityDetector(FakeClient(rows), None, None)  # type: ignore[arg-type]
        with pytest.raises(GraphDataScienceUnavailable):
            await detector.detect("s")

    @pytest.mark.asyncio
    async def test_the_weighted_graph_is_projected_clustered_and_dropped(self) -> None:
        client = FakeClient(_session())
        detector = CommunityDetector(client, None, None)  # type: ignore[arg-type]
        stats = await detector.detect("s")

        pairs = client.written("gds.graph.project")[0]["pairs"]
        # The amount is not an entity to cluster; the two pairs never share an exchange.
        assert [(p["a"], p["b"]) for p in pairs] == [("e1", "e2"), ("e3", "e4")]
        assert client.written("gds.graph.drop")
        assert stats == {"entities": 4, "links": 2, "communities": 2, "levels": 1}

    @pytest.mark.asyncio
    async def test_communities_are_written_with_their_exchanges(self) -> None:
        client = FakeClient(_session())
        await CommunityDetector(client, None, None).detect("s")  # type: ignore[arg-type]

        # The communities the session had are replaced.
        assert client.written("DETACH DELETE c") == [{"session_id": "s"}]
        stored = sorted(
            client.written("CREATE (c:Community")[0]["rows"], key=lambda r: r["messages"]
        )
        assert [row["entity_names"] for row in stored] == [
            ["casting", "script"],
            ["Stephen", "anniversary"],
        ]
        assert [row["messages"] for row in stored] == [[0, 2], [4, 6]]
        assert [(row["first_index"], row["last_index"]) for row in stored] == [(0, 2), (4, 6)]
        links = client.written("MERGE (e)-[:IN_COMMUNITY]->(c)")[0]["rows"]
        assert sorted(link["entity"] for link in links) == ["e1", "e2", "e3", "e4"]

    @pytest.mark.asyncio
    async def test_a_session_with_nothing_to_link_has_no_communities(self) -> None:
        rows = _session()
        rows["collect(DISTINCT m.id) AS message_ids"] = [
            {"id": "e1", "name": "script", "message_ids": ["m0"]}
        ]
        rows["collect(DISTINCT s.message_index) AS positions"] = []
        client = FakeClient(rows)
        stats = await CommunityDetector(client, None, None).detect("s")  # type: ignore[arg-type]
        assert stats == {"entities": 0, "links": 0, "communities": 0, "levels": 0}
        assert not client.written("gds.graph.project")


class TestSummarize:
    def _rows(self) -> dict[str, list[dict[str, Any]]]:
        rows = _session()
        rows["RETURN m.id AS id, m.role AS role, m.content AS content"] = rows.pop(
            "RETURN m.id AS id, m.role AS role"
        )
        rows["MATCH (c:Community {session_id: $session_id})"] = [
            {
                "id": "c1",
                "level": 0,
                "messages": [0, 2],
                "entity_names": ["script", "casting"],
                "summary": None,
            },
            {
                "id": "c2",
                "level": 0,
                "messages": [4],
                "entity_names": ["Stephen"],
                "summary": None,
            },
        ]
        return rows

    @pytest.mark.asyncio
    async def test_a_thread_is_written_from_its_exchanges_and_embedded(self) -> None:
        client = FakeClient(self._rows())
        llm = FakeLLM("Pilot planning\n- #1: Asked for a plan; got three phases.")
        detector = CommunityDetector(client, FakeEmbedder(), llm)  # type: ignore[arg-type]
        stats = await detector.summarize("s")

        # One thread has two exchanges; the other has one, too little to summarize.
        assert stats == {"summaries": 1, "skipped": 1, "failures": 0}
        assert "[#1 | 2024-03-15]\nUSER: message 0" in llm.prompts[0]
        assert "ASSISTANT: message 3" in llm.prompts[0]
        assert "what the assistant gave in reply" in llm.systems[0]
        written = client.written("SET c.title = $title")[0]
        assert written["id"] == "c1"
        assert written["title"] == "Pilot planning"
        assert written["summary"] == "- #1: Asked for a plan; got three phases."
        assert written["embedding"] == [float(len("Pilot planning\n" + written["summary"]))]

    @pytest.mark.asyncio
    async def test_long_messages_are_cut_short(self) -> None:
        client = FakeClient(self._rows())
        llm = FakeLLM("Title\n- #1: A step.")
        detector = CommunityDetector(client, FakeEmbedder(), llm)  # type: ignore[arg-type]
        await detector.summarize("s", message_chars=12)
        assert "USER: message 0 xx [...]" in llm.prompts[0]

    @pytest.mark.asyncio
    async def test_a_thread_can_be_written_from_the_users_statements(self) -> None:
        rows = self._rows()
        rows["RETURN text, message_index, said_at, replaced"] = [
            {
                "text": "The budget is $120,000.",
                "message_index": 0,
                "said_at": "2024-03-15",
                "replaced": True,
            },
            {
                "text": "The script is half done.",
                "message_index": 2,
                "said_at": None,
                "replaced": False,
            },
            {
                "text": "Casting is finished.",
                "message_index": 2,
                "said_at": None,
                "replaced": False,
            },
        ]
        client = FakeClient(rows)
        llm = FakeLLM("Pilot planning\n- #1: The budget.")
        detector = CommunityDetector(client, FakeEmbedder(), llm)  # type: ignore[arg-type]
        stats = await detector.summarize("s", source="statements")
        assert stats["summaries"] == 1
        assert "(#1, 2024-03-15) The budget is $120,000. (later changed)" in llm.prompts[0]
        assert "(#3) Casting is finished." in llm.prompts[0]

    @pytest.mark.asyncio
    async def test_a_summarized_thread_is_left_alone(self) -> None:
        rows = self._rows()
        rows["MATCH (c:Community {session_id: $session_id})"][0]["summary"] = "- #1: Done."
        client = FakeClient(rows)
        llm = FakeLLM("Title\n- #1: A step.")
        stats = await CommunityDetector(client, FakeEmbedder(), llm).summarize("s")  # type: ignore[arg-type]
        assert stats["summaries"] == 0
        assert not llm.prompts

    @pytest.mark.asyncio
    async def test_an_answer_without_bullets_is_a_failure(self) -> None:
        client = FakeClient(self._rows())
        detector = CommunityDetector(client, FakeEmbedder(), FakeLLM("Only a title"))  # type: ignore[arg-type]
        stats = await detector.summarize("s")
        assert stats == {"summaries": 0, "skipped": 1, "failures": 1}
        assert not client.written("SET c.title = $title")

    @pytest.mark.asyncio
    async def test_summaries_need_an_llm(self) -> None:
        detector = CommunityDetector(FakeClient(self._rows()), FakeEmbedder(), None)  # type: ignore[arg-type]
        with pytest.raises(RuntimeError, match="need an LLM"):
            await detector.summarize("s")
