"""Smoke tests for the basic_usage.py example (the guided tour).

Three layers of coverage:

* structure/content checks (no Neo4j, no imports) — marked ``syntax``
* import checks mirroring the example's import block — marked ``imports``
* behavioural checks against Neo4j using the mock-backed ``memory_client``
  fixture, plus one ``slow`` end-to-end subprocess run of the real script
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest

EXAMPLES_DIR = Path(__file__).parent.parent.parent / "examples"
EXAMPLE_PATH = EXAMPLES_DIR / "basic_usage.py"
ENV_EXAMPLE_PATH = EXAMPLES_DIR / ".env.example"

# Vector indexes are dimension-specific. The example uses a real
# sentence-transformers embedder (384 dims) while the rest of this suite uses the
# 1536-dim MockEmbedder, so the end-to-end run drops the vector indexes before
# and after itself and lets whoever connects next recreate them.
VECTOR_INDEXES = (
    "entity_embedding_idx",
    "fact_embedding_idx",
    "message_embedding_idx",
    "preference_embedding_idx",
    "step_embedding_idx",
    "task_embedding_idx",
)


def _drop_vector_indexes(connection: dict) -> None:
    from neo4j import GraphDatabase

    driver = GraphDatabase.driver(
        connection["uri"], auth=(connection["username"], connection["password"])
    )
    try:
        with driver.session() as session:
            for index in VECTOR_INDEXES:
                session.run(f"DROP INDEX {index} IF EXISTS").consume()
    finally:
        driver.close()


@pytest.mark.syntax
class TestBasicUsageStructure:
    """Structure and content checks — no Neo4j, no imports."""

    def test_example_file_exists(self, examples_dir):
        assert (examples_dir / "basic_usage.py").exists()

    def test_env_example_is_tracked_with_working_defaults(self):
        """The documented `cp examples/.env.example examples/.env` must work."""
        assert ENV_EXAMPLE_PATH.exists(), "examples/.env.example is missing"
        content = ENV_EXAMPLE_PATH.read_text(encoding="utf-8")
        # The default must match docker-compose.test.yml / `make neo4j-start`.
        assert "NEO4J_PASSWORD=test-password" in content
        assert "NEO4J_URI=bolt://localhost:7687" in content
        for key in ("MEMORY_API_KEY", "OPENAI_API_KEY", "EMBEDDING_MODEL", "ENABLE_GEOCODING"):
            assert key in content, f"{key} is undocumented in .env.example"

    def test_env_helper_defaults_to_the_docker_password(self):
        """examples/_env.py is the single .env loader shared by the scripts."""
        import _env

        assert _env.DEFAULT_NEO4J_PASSWORD == "test-password"
        assert _env.DEFAULT_NEO4J_URI == "bolt://localhost:7687"
        assert _env.NEO4J_PASSWORD  # resolved value, env override or default
        assert callable(_env.load_example_env)

    def test_example_uses_the_shared_env_helper(self):
        source = EXAMPLE_PATH.read_text(encoding="utf-8")
        assert "from _env import" in source
        # No copy-pasted loader, and no hard-coded fallback password.
        assert "def load_env_files" not in source
        assert '"password"' not in source

    def test_all_sections_are_numbered_and_present(self):
        source = EXAMPLE_PATH.read_text(encoding="utf-8")
        for number in range(1, 13):
            assert f"section({number}," in source or f"        {number}." in source, (
                f"section {number} is missing from the tour"
            )
        for title in (
            "SHORT-TERM MEMORY",
            "LONG-TERM MEMORY",
            "GEOCODING",
            "REASONING MEMORY",
            "COMBINED CONTEXT",
            "BATCH MESSAGE LOADING",
            "SESSION LISTING",
            "METADATA-FILTERED MESSAGE SEARCH",
            "STREAMING TRACE RECORDER",
            "TRACE LISTING AND TOOL STATISTICS",
            "SEQUENTIAL MESSAGE LINKS",
            "GRAPH EXPORT AND MEMORY STATISTICS",
        ):
            assert title in source, f"missing section: {title}"

    def test_optional_sections_are_skippable(self):
        """Geocoding must be opt-in, not a live network call on every run."""
        source = EXAMPLE_PATH.read_text(encoding="utf-8")
        assert 'os.getenv("ENABLE_GEOCODING") == "1"' in source
        assert "skipped(" in source
        # OSM's usage policy requires an identifying User-Agent.
        assert "GEOCODING_USER_AGENT" in source

    def test_model_ids_come_from_the_environment(self):
        source = EXAMPLE_PATH.read_text(encoding="utf-8")
        assert '"EMBEDDING_MODEL"' in source
        assert '"LOCAL_EMBEDDING_MODEL"' in source
        assert "os.getenv(" in source
        # No retired model ids.
        for retired in ("gpt-4o", "text-embedding-004", "textembedding-gecko"):
            assert retired not in source

    def test_uses_the_structured_reasoning_surface(self):
        source = EXAMPLE_PATH.read_text(encoding="utf-8")
        assert "TraceOutcome(" in source
        assert "touched_entities=" in source
        assert "EntityRef(" in source
        # The DeduplicationResult half of add_entity is unpacked at least once.
        assert "dedup.action" in source

    def test_points_at_the_hosted_quickstart(self):
        source = EXAMPLE_PATH.read_text(encoding="utf-8")
        assert "nams-quickstart" in source


@pytest.mark.imports
class TestBasicUsageImports:
    """Every name the example imports must resolve."""

    def test_package_root_imports_resolve(self):
        from neo4j_agent_memory import (  # noqa: F401
            BoltMemoryClient,
            Conversation,
            Entity,
            ExtractionConfig,
            ExtractorType,
            GeocodingConfig,
            GeocodingProvider,
            Neo4jConfig,
            StreamingTraceRecorder,
            ToolCallStatus,
            connect,
        )
        from neo4j_agent_memory.config.settings import BoltSettings  # noqa: F401

        assert callable(connect)
        assert BoltSettings is not None

    def test_reasoning_models_are_imported_from_schema_models(self):
        """EntityRef/TraceOutcome are not re-exported from the package root."""
        import neo4j_agent_memory
        from neo4j_agent_memory.schema.models import EntityRef, TraceOutcome

        assert EntityRef(name="Downtown", type="LOCATION").name == "Downtown"
        assert TraceOutcome(success=True, summary="ok").success is True
        assert not hasattr(neo4j_agent_memory, "TraceOutcome")


@pytest.mark.requires_neo4j
class TestBasicUsageOperations:
    """The calls the example makes, against Neo4j with mock components."""

    @pytest.mark.asyncio
    async def test_short_term_memory_operations(self, memory_client):
        session_id = f"test-basic-{uuid4()}"

        from neo4j_agent_memory import MessageRole

        # The examples pass plain string roles; they read back as MessageRole.
        await memory_client.short_term.add_message(
            session_id,
            "user",
            "Hi! I'm looking for restaurant recommendations.",
        )
        await memory_client.short_term.add_message(
            session_id,
            "assistant",
            "I'd be happy to help you find restaurants!",
        )

        conversation = await memory_client.short_term.get_conversation(session_id)
        assert len(conversation.messages) == 2
        assert conversation.messages[0].role == MessageRole.USER
        assert conversation.messages[1].role == MessageRole.ASSISTANT

    @pytest.mark.asyncio
    async def test_long_term_memory_operations(self, memory_client):
        await memory_client.long_term.add_preference(
            category="food",
            preference="Loves Italian cuisine",
            context="Restaurant recommendations",
        )
        await memory_client.long_term.add_preference(
            category="dietary",
            preference="Vegetarian diet",
        )

        # add_entity returns (entity, dedup_result), as the example unpacks it.
        entity, dedup = await memory_client.long_term.add_entity(
            name="Downtown",
            entity_type="LOCATION",
            subtype="LANDMARK",
            description="User's preferred dining area",
        )
        assert entity.type == "LOCATION"
        assert dedup.action in {"none", "merged", "flagged"}

        await memory_client.long_term.add_fact(
            subject="User",
            predicate="dietary_restriction",
            obj="vegetarian",
        )

        food_prefs = await memory_client.long_term.search_preferences("food", limit=5)
        assert len(food_prefs) >= 1

    @pytest.mark.asyncio
    async def test_custom_entity_types_become_labels(self, memory_client):
        """The example claims custom types become PascalCase labels."""
        entity, _ = await memory_client.long_term.add_entity(
            name=f"Italian Food {uuid4()}",
            entity_type="CUISINE",
            subtype="ITALIAN",
        )
        rows = await memory_client.query.cypher(
            "MATCH (e:Entity {id: $id}) RETURN labels(e) AS labels",
            {"id": str(entity.id)},
        )
        assert rows, "entity not found by id"
        assert {"Entity", "Cuisine", "Italian"} <= set(rows[0]["labels"])

    @pytest.mark.asyncio
    async def test_reasoning_memory_operations(self, memory_client):
        session_id = f"test-reasoning-{uuid4()}"

        from neo4j_agent_memory import ToolCallStatus
        from neo4j_agent_memory.schema.models import EntityRef, TraceOutcome

        entity, _ = await memory_client.long_term.add_entity(
            name=f"Downtown {uuid4()}",
            entity_type="LOCATION",
        )

        message = await memory_client.short_term.add_message(
            session_id, "user", "Find me a vegetarian restaurant downtown"
        )

        trace = await memory_client.reasoning.start_trace(
            session_id,
            task="Find vegetarian restaurant",
            triggered_by_message_id=message.id,
        )
        assert trace.id is not None

        step = await memory_client.reasoning.add_step(
            trace.id,
            thought="Need to search for restaurants",
            action="search_restaurants",
        )

        tool_call = await memory_client.reasoning.record_tool_call(
            step.id,
            tool_name="restaurant_api",
            arguments={"cuisine": "vegetarian"},
            result=[{"name": "Green Garden"}],
            status=ToolCallStatus.SUCCESS,
            duration_ms=100,
            message_id=message.id,
            touched_entities=[EntityRef(name=entity.name, type=entity.type)],
        )
        assert tool_call.id is not None

        completed = await memory_client.reasoning.complete_trace(
            trace.id,
            outcome=TraceOutcome(
                success=True,
                summary="Found restaurant",
                related_entities=[EntityRef(name=entity.name, type=entity.type)],
                metrics={"tools_called": 1.0},
            ),
        )
        assert completed.success is True
        assert completed.outcome == "Found restaurant"

    @pytest.mark.asyncio
    async def test_cross_memory_edges_are_one_hop(self, memory_client):
        """INITIATED_BY / TRIGGERED_BY / TOUCHED, as section 12 counts them."""
        session_id = f"test-audit-{uuid4()}"

        from neo4j_agent_memory.schema.models import EntityRef

        entity, _ = await memory_client.long_term.add_entity(
            name=f"Audit Entity {uuid4()}",
            entity_type="LOCATION",
        )
        message = await memory_client.short_term.add_message(session_id, "user", "do the thing")
        trace = await memory_client.reasoning.start_trace(
            session_id, task="do the thing", triggered_by_message_id=message.id
        )
        step = await memory_client.reasoning.add_step(trace.id, action="act")
        await memory_client.reasoning.record_tool_call(
            step.id,
            tool_name="thing_api",
            arguments={},
            result="ok",
            message_id=message.id,
            touched_entities=[EntityRef(name=entity.name, type=entity.type)],
        )
        await memory_client.reasoning.complete_trace(trace.id, success=True)

        rows = await memory_client.query.cypher(
            """
            MATCH (rt:ReasoningTrace {session_id: $session_id})
            OPTIONAL MATCH (rt)-[initiated:INITIATED_BY]->(:Message)
            OPTIONAL MATCH (rt)-[:HAS_STEP]->(:ReasoningStep)-[:USES_TOOL]->
                           (:ToolCall)-[triggered:TRIGGERED_BY]->(:Message)
            OPTIONAL MATCH (rt)-[:HAS_STEP]->(:ReasoningStep)-[touched:TOUCHED]->(e:Entity)
            RETURN count(DISTINCT initiated) AS initiated_by,
                   count(DISTINCT triggered) AS triggered_by,
                   collect(DISTINCT e.name) AS touched
            """,
            {"session_id": session_id},
        )
        assert rows[0]["initiated_by"] == 1
        assert rows[0]["triggered_by"] == 1
        assert entity.name in rows[0]["touched"]

    @pytest.mark.asyncio
    async def test_batch_loading(self, memory_client):
        session_id = f"test-batch-{uuid4()}"

        messages = [
            {"role": "user", "content": "What's the weather?", "metadata": {"topic": "weather"}},
            {"role": "assistant", "content": "It's sunny!", "metadata": {"topic": "weather"}},
            {"role": "user", "content": "Thanks!"},
        ]

        loaded = await memory_client.short_term.add_messages_batch(
            session_id,
            messages,
            batch_size=2,
            generate_embeddings=True,
            extract_entities=False,
        )
        assert len(loaded) == 3

    @pytest.mark.asyncio
    async def test_message_linking_chain(self, memory_client):
        """Section 11: FIRST_MESSAGE plus a NEXT_MESSAGE chain."""
        session_id = f"test-links-{uuid4()}"

        await memory_client.short_term.add_messages_batch(
            session_id,
            [{"role": "user", "content": f"message {i}"} for i in range(4)],
            generate_embeddings=False,
        )

        rows = await memory_client.query.cypher(
            """
            MATCH (c:Conversation {session_id: $session_id})-[:FIRST_MESSAGE]->(first:Message)
            OPTIONAL MATCH (c)-[:HAS_MESSAGE]->(:Message)-[link:NEXT_MESSAGE]->(:Message)
            RETURN first.content AS first_message, count(DISTINCT link) AS next_links
            """,
            {"session_id": session_id},
        )
        assert rows[0]["first_message"] == "message 0"
        assert rows[0]["next_links"] == 3

    @pytest.mark.asyncio
    async def test_session_listing(self, memory_client):
        for i in range(3):
            session_id = f"test-list-session-{i}-{uuid4()}"
            await memory_client.short_term.add_message(
                session_id, "user", f"Message in session {i}"
            )

        sessions = await memory_client.short_term.list_sessions(
            limit=10,
            order_by="updated_at",
            order_dir="desc",
        )
        assert len(sessions) >= 3

    @pytest.mark.asyncio
    async def test_metadata_search(self, memory_client):
        session_id = f"test-metadata-{uuid4()}"

        messages = [
            {"role": "user", "content": "Weather question", "metadata": {"topic": "weather"}},
            {"role": "user", "content": "Food question", "metadata": {"topic": "food"}},
        ]

        await memory_client.short_term.add_messages_batch(session_id, messages)

        results = await memory_client.short_term.search_messages(
            "question",
            session_id=session_id,
            metadata_filters={"topic": "weather"},
            limit=5,
        )
        # Every hit must respect the filter (the count depends on similarity).
        for message in results:
            assert (message.metadata or {}).get("topic") == "weather"

    @pytest.mark.asyncio
    async def test_streaming_trace_recorder(self, memory_client):
        session_id = f"test-streaming-{uuid4()}"

        from neo4j_agent_memory import StreamingTraceRecorder

        async with StreamingTraceRecorder(
            memory_client.reasoning, session_id, "Process request"
        ) as recorder:
            step = await recorder.start_step(
                thought="Analyzing request",
                action="analyze",
            )
            assert step is not None

            await recorder.record_tool_call(
                "analyze_text",
                {"text": "Hello"},
                result={"result": "greeting"},
            )

            await recorder.add_observation("User is greeting")

        traces = await memory_client.reasoning.list_traces(session_id=session_id)
        assert len(traces) >= 1

    @pytest.mark.asyncio
    async def test_list_traces(self, memory_client):
        session_id = f"test-list-traces-{uuid4()}"

        trace = await memory_client.reasoning.start_trace(session_id, task="Test task")
        await memory_client.reasoning.complete_trace(trace.id, success=True)

        traces = await memory_client.reasoning.list_traces(
            session_id=session_id,
            success_only=True,
            limit=5,
        )
        assert len(traces) >= 1

    @pytest.mark.asyncio
    async def test_tool_stats(self, memory_client):
        session_id = f"test-tool-stats-{uuid4()}"

        from neo4j_agent_memory import ToolCallStatus

        trace = await memory_client.reasoning.start_trace(session_id, task="Test")
        step = await memory_client.reasoning.add_step(trace.id, action="test")
        await memory_client.reasoning.record_tool_call(
            step.id,
            tool_name="test_tool",
            arguments={},
            result="ok",
            status=ToolCallStatus.SUCCESS,
            duration_ms=50,
        )
        await memory_client.reasoning.complete_trace(trace.id)

        stats = await memory_client.reasoning.get_tool_stats()
        assert isinstance(stats, list)

    @pytest.mark.asyncio
    async def test_graph_export(self, memory_client):
        session_id = f"test-graph-{uuid4()}"

        await memory_client.short_term.add_message(session_id, "user", "Test message")
        await memory_client.long_term.add_preference("test", "Test preference")

        graph = await memory_client.get_graph(
            memory_types=["short_term", "long_term", "reasoning"],
            session_id=session_id,
            include_embeddings=False,
            limit=100,
        )

        assert hasattr(graph, "nodes")
        assert hasattr(graph, "relationships")

    @pytest.mark.asyncio
    async def test_get_context(self, memory_client):
        session_id = f"test-context-{uuid4()}"

        await memory_client.short_term.add_message(session_id, "user", "I love pizza")
        await memory_client.long_term.add_preference("food", "Loves pizza")

        context = await memory_client.get_context(
            "pizza recommendation",
            session_id=session_id,
        )

        assert isinstance(context, str)

    @pytest.mark.asyncio
    async def test_get_stats(self, memory_client):
        stats = await memory_client.get_stats()

        assert isinstance(stats, dict)
        assert "messages" in stats or "conversations" in stats


@pytest.mark.requires_neo4j
@pytest.mark.slow
class TestBasicUsageEndToEnd:
    """Run the real script: the only test that proves the documented path works."""

    def test_example_runs_to_completion(self, neo4j_connection):
        pytest.importorskip(
            "sentence_transformers",
            reason="the keyless path needs the [sentence-transformers] extra",
        )

        env = {
            **os.environ,
            "NEO4J_URI": neo4j_connection["uri"],
            "NEO4J_USERNAME": neo4j_connection["username"],
            "NEO4J_PASSWORD": neo4j_connection["password"],
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
        }
        # Force the deterministic, keyless path: local embedder, no LLM
        # extraction, and no live Nominatim calls.
        env.pop("OPENAI_API_KEY", None)
        env.pop("ENABLE_GEOCODING", None)

        _drop_vector_indexes(neo4j_connection)
        try:
            result = subprocess.run(
                [sys.executable, str(EXAMPLE_PATH)],
                capture_output=True,
                text=True,
                timeout=600,
                cwd=str(EXAMPLES_DIR),
                env=env,
                encoding="utf-8",
            )
            assert result.returncode == 0, (
                f"example exited {result.returncode}:\n{result.stdout}\n{result.stderr}"
            )
            assert "Demo complete!" in result.stdout, (
                f"example did not finish:\n{result.stdout}\n{result.stderr}"
            )
            # Section 3 is opt-in and must announce that it was skipped.
            assert "SKIPPED" in result.stdout
            # Section 12 proves the cross-memory edges were written.
            assert "TOUCHED (step -> entity): ['Downtown']" in result.stdout
        finally:
            # Leave no dimension-specific vector indexes behind for the
            # mock-embedder tests that share this database.
            _drop_vector_indexes(neo4j_connection)
