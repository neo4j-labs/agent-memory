"""Integration tests requiring a running Neo4j instance.

These load the shared sample data with the real loader and then run the real
Cypher, so they catch what the mocked unit tests cannot: a query that returns
nothing, a temporal comparison that evaluates to null, a label namespace that
has quietly merged with the library's.

Run against a throwaway database::

    NEO4J_URI=bolt://localhost:7688 NEO4J_USERNAME=neo4j \\
      NEO4J_PASSWORD=test-password uv run pytest tests/test_integration.py

``--reset`` scopes its delete to the demo labels, so agent memory in that
database survives.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import subprocess
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

DATA_DIR = Path(__file__).resolve().parents[3] / "data"

NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USERNAME = os.getenv("NEO4J_USERNAME") or os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "test-password")

#: Dimension of the memory vector indexes in the target database. The default
#: matches ``all-MiniLM-L6-v2``, which the repo's own test fixtures use; point
#: ``EMBEDDING_DIMENSIONS`` at your embedder's width if yours differs, or
#: ``MemoryClient.connect()`` raises ``EmbeddingDimensionMismatchError``.
EMBEDDING_DIMENSIONS = int(os.getenv("EMBEDDING_DIMENSIONS", "384"))


class DeterministicEmbedder:
    """Hash-based stand-in for a real embedder.

    Keeps these tests free of both API keys and a model download. Similarity
    scores are meaningless — nothing here asserts on semantic search, only on
    Cypher, the label namespace, and the reasoning-graph shape.
    """

    def __init__(self, dimensions: int = EMBEDDING_DIMENSIONS) -> None:
        self._dimensions = dimensions

    @property
    def dimensions(self) -> int:
        return self._dimensions

    async def embed(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        return [digest[i % len(digest)] / 255.0 for i in range(self._dimensions)]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [await self.embed(text) for text in texts]


try:
    from neo4j import GraphDatabase

    _driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USERNAME, NEO4J_PASSWORD))
    _driver.verify_connectivity()
    _driver.close()
    HAS_NEO4J = True
except Exception:
    HAS_NEO4J = False

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not HAS_NEO4J, reason=f"Neo4j not available at {NEO4J_URI}"),
]

SESSION_ID = "fsa-integration-test"


def _load_sample_data(*extra_args: str) -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(DATA_DIR / "load_sample_data.py"),
            "--uri",
            NEO4J_URI,
            "--username",
            NEO4J_USERNAME,
            "--password",
            NEO4J_PASSWORD,
            *extra_args,
        ],
        capture_output=True,
        text=True,
        cwd=str(DATA_DIR),
    )
    if result.returncode != 0:
        pytest.skip(f"Failed to load sample data: {result.stderr[-2000:]}")


@pytest.fixture(scope="module")
def loaded_data() -> None:
    """Load the fixture once, from a clean slate, for the whole module."""
    _load_sample_data("--reset", "--yes")


@pytest.fixture
async def memory_client(loaded_data) -> AsyncIterator[object]:
    from neo4j_agent_memory import MemoryClient, MemorySettings, Neo4jConfig
    from neo4j_agent_memory.config.settings import ExtractionConfig, ExtractorType
    from pydantic import SecretStr

    settings = MemorySettings(
        neo4j=Neo4jConfig(
            uri=NEO4J_URI,
            username=NEO4J_USERNAME,
            password=SecretStr(NEO4J_PASSWORD),
        ),
        llm=None,
        extraction=ExtractionConfig(extractor_type=ExtractorType.NONE),
    )
    client = MemoryClient(settings, embedder=DeterministicEmbedder())
    await client.connect()
    try:
        yield client
    finally:
        await client.close()


@pytest.fixture
async def svc(memory_client):
    from src.services.neo4j_service import Neo4jDomainService

    return Neo4jDomainService(memory_client)


class TestCustomerQueries:
    async def test_get_customer_john_smith(self, svc):
        customer = await svc.get_customer("CUST-001")
        assert customer is not None
        assert customer["name"] == "John Smith"
        assert customer["type"] == "individual"

    async def test_get_customer_global_holdings(self, svc):
        customer = await svc.get_customer("CUST-003")
        assert customer is not None
        assert customer["name"] == "Global Holdings Ltd"
        assert customer["type"] == "corporate"

    async def test_list_customers(self, svc):
        customers = await svc.list_customers()
        assert len(customers) == 3

    async def test_document_dates_serialize_as_iso_strings(self, svc):
        """Dates are Neo4j DATE values; the service must hand back JSON-safe text."""
        customer = await svc.get_customer("CUST-002")
        passport = next(d for d in customer["documents"] if d["type"] == "passport")
        assert isinstance(passport["expiry_date"], str)
        assert passport["expiry_date"].count("-") == 2


class TestTemporalQueries:
    async def test_transactions_fall_inside_the_90_day_window(self, svc):
        """The headline regression: string dates made this silently return [].

        ``t.date >= date() - duration({days: 90})`` compares a STRING to a DATE
        and yields null, so every row was filtered out — and the old fixture's
        2024 dates would have missed a present-day window anyway.
        """
        transactions = await svc.get_transactions("CUST-003", days=90)
        assert transactions, "no transactions in the 90-day window"
        assert all(t["date"] for t in transactions)

    async def test_transaction_window_is_respected(self, svc):
        narrow = await svc.get_transactions("CUST-003", days=20)
        wide = await svc.get_transactions("CUST-003", days=90)
        assert len(narrow) < len(wide)

    async def test_velocity_scan_sees_rows(self, svc):
        """``analyze_velocity``'s second caller of get_transactions."""
        stats = await svc.get_transaction_stats("CUST-003")
        assert stats["transaction_count"] == 7


class TestAMLPatterns:
    async def test_detect_structuring_cust003(self, svc):
        """CUST-003 has 4x $9,500 cash deposits (structuring)."""
        results = await svc.detect_structuring("CUST-003")
        assert len(results) == 4
        assert all(9000 <= r["amount"] < 10000 for r in results)

    async def test_no_structuring_cust001(self, svc):
        results = await svc.detect_structuring("CUST-001")
        assert results == []

    async def test_detect_rapid_movement_cust002(self, svc):
        """CUST-002 has wire_in/wire_out pairs within two days."""
        results = await svc.detect_rapid_movement("CUST-002")
        assert len(results) >= 1

    async def test_detect_layering_cust003(self, svc):
        results = await svc.detect_layering("CUST-003")
        assert len(results) >= 1


class TestNetworkAnalysis:
    async def test_find_connections_cust003(self, svc):
        result = await svc.find_connections("CUST-003")
        assert len(result["connections"]) >= 1

    async def test_detect_shell_companies_cust003(self, svc):
        shells = await svc.detect_shell_companies("CUST-003")
        assert len(shells) >= 1
        names = [s["name"] for s in shells]
        assert any("Shell Corp" in n or "Anonymous Trust" in n for n in names)

    async def test_network_risk_cust003_is_high(self, svc):
        result = await svc.get_network_risk("CUST-003")
        assert result["risk_level"] in ("HIGH", "CRITICAL")

    async def test_trace_ownership_finds_the_ubo(self, svc):
        result = await svc.trace_ownership("ORG-001")
        assert result["ownership_chains"]
        assert result["ubo_identified"] is True


class TestSanctionsAndPEP:
    async def test_check_sanctions_exact_match(self, svc):
        results = await svc.check_sanctions("Ivan Petrov")
        assert len(results) >= 1
        assert any(r["match_type"] == "EXACT" for r in results)

    async def test_check_sanctions_alias_match(self, svc):
        results = await svc.check_sanctions("SC Ltd")
        assert any(r["match_type"] in ("ALIAS", "PARTIAL") for r in results)

    async def test_check_sanctions_no_match(self, svc):
        assert await svc.check_sanctions("Clean Person XYZ") == []

    async def test_check_pep_direct(self, svc):
        results = await svc.check_pep("Carlos Rodriguez")
        assert len(results) >= 1

    async def test_check_pep_relative(self, svc):
        results = await svc.check_pep("Maria Rodriguez")
        assert any(r["match_type"] == "PEP_RELATIVE" for r in results)


class TestReadOnlyCypher:
    async def test_a_write_query_is_refused(self, svc):
        """The library validates read-only before any round-trip."""
        with pytest.raises(ValueError):
            await svc.read_only_cypher("CREATE (n:Nope) RETURN n")

    async def test_a_label_containing_set_is_allowed(self, svc):
        """The old keyword blocklist 400'd ``:Asset`` because it contains SET."""
        rows = await svc.read_only_cypher("MATCH (a:Asset) RETURN count(a) AS total")
        assert rows[0]["total"] == 0


class TestLabelNamespace:
    async def test_extraction_does_not_create_rival_organizations(self, memory_client, svc):
        """An extracted ``:Entity:Organization`` must not look like a compliance org.

        The library turns POLE+O types into PascalCase labels, so an extracted
        ORGANIZATION lands on ``:Organization`` — the same label the loader uses.
        The marker label ``:Compliance`` is what keeps the two namespaces apart.
        """
        before = await svc.read_only_cypher(
            "MATCH (o:Organization:Compliance) RETURN count(o) AS total"
        )
        await memory_client.long_term.add_entity(
            "Totally Unrelated Holdings GmbH", "ORGANIZATION", deduplicate=False
        )
        after = await svc.read_only_cypher(
            "MATCH (o:Organization:Compliance) RETURN count(o) AS total"
        )
        assert after[0]["total"] == before[0]["total"] == 6

        shells = await svc.detect_shell_companies("CUST-003")
        assert all("Unrelated" not in s["name"] for s in shells)


class TestLoaderSafety:
    async def test_a_conversation_survives_a_reload(self, memory_client, svc):
        """Re-running the loader must not touch agent memory (it used to wipe it)."""
        await memory_client.short_term.add_message(
            session_id=SESSION_ID, role="user", content="Investigate CUST-003"
        )
        _load_sample_data()
        conversation = await memory_client.short_term.get_conversation(SESSION_ID)
        assert len(conversation.messages) >= 1

    async def test_reload_is_idempotent(self, svc):
        before = await svc.get_graph_stats()
        _load_sample_data()
        after = await svc.get_graph_stats()
        assert after["nodes_by_label"].get("Customer") == before["nodes_by_label"].get("Customer")
        assert after["nodes_by_label"].get("Transaction") == before["nodes_by_label"].get(
            "Transaction"
        )


class TestReasoningAuditTrail:
    async def test_touched_edges_make_the_audit_query_one_hop(self, memory_client, svc):
        """``record_tool_call(touched_entities=...)`` is what earns the audit claim."""
        from neo4j_agent_memory.schema import EntityRef, TraceOutcome

        from src.services.memory_service import FinancialMemoryService

        memory = FinancialMemoryService.__new__(FinancialMemoryService)
        memory._client = memory_client
        memory._connected = True
        memory._init_lock = asyncio.Lock()

        message = await memory.add_conversation_message(
            SESSION_ID, "user", "Screen CUST-003 against OFAC"
        )
        trace_id = await memory.start_investigation_trace(
            SESSION_ID, "Screen CUST-003", triggered_by_message_id=message.id
        )
        step_id = await memory.add_reasoning_step(
            trace_id, agent="compliance", action="check_sanctions", reasoning="screening"
        )
        await memory.record_tool_call(
            step_id,
            tool_name="check_sanctions",
            arguments={"entity_name": "Global Holdings Ltd"},
            result="CLEAR",
            message_id=message.id,
            touched_entities=[EntityRef(name="Global Holdings Ltd", type="ORGANIZATION")],
        )
        await memory.complete_investigation_trace(
            trace_id,
            conclusion="No sanctions match",
            metrics={"tool_calls": 1.0},
        )

        trail = await memory.audit_trail(trace_id)
        assert trail, "no :TOUCHED edges - the audit trail would render empty"
        assert trail[0]["entity"] == "Global Holdings Ltd"
        assert "check_sanctions" in trail[0]["tools"]

        trace = await memory.get_investigation_trace(trace_id)
        assert trace is not None
        assert trace["steps"][0]["tool_calls"], "tool calls must be readable back"
        assert isinstance(TraceOutcome(success=True, summary="x"), TraceOutcome)

    async def test_screening_facts_round_trip(self, memory_client):
        """Long-term memory is used, not just advertised."""
        from src.services.memory_service import FinancialMemoryService

        memory = FinancialMemoryService.__new__(FinancialMemoryService)
        memory._client = memory_client
        memory._connected = True
        memory._init_lock = asyncio.Lock()

        await memory.record_screening(
            "Global Holdings Ltd", "OFAC SDN", result="CLEAR", confidence=0.9
        )
        prior = await memory.prior_screenings("Global Holdings Ltd")
        assert prior and prior[0]["list"] == "OFAC SDN"
        assert prior[0]["result"] == "CLEAR"
