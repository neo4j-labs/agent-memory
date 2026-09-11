"""Shared test fixtures for the Financial Services Advisor test suite."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

# Set test environment variables before any app imports
os.environ.setdefault("NEO4J_URI", "bolt://localhost:7687")
os.environ.setdefault("NEO4J_USER", "neo4j")
os.environ.setdefault("NEO4J_PASSWORD", "test-password")
os.environ.setdefault("NEO4J_DATABASE", "neo4j")
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-6")
os.environ.setdefault("BEDROCK_EMBEDDING_MODEL_ID", "amazon.titan-embed-text-v2:0")

DATA_DIR = Path(__file__).resolve().parents[3] / "data"


# ── Sample Data Fixtures ──────────────────────────────────────────────


@pytest.fixture
def sample_customers() -> list[dict[str, Any]]:
    with open(DATA_DIR / "customers.json") as f:
        return json.load(f)


@pytest.fixture
def sample_organizations() -> list[dict[str, Any]]:
    with open(DATA_DIR / "organizations.json") as f:
        return json.load(f)


@pytest.fixture
def sample_transactions() -> list[dict[str, Any]]:
    with open(DATA_DIR / "transactions.json") as f:
        return json.load(f)


@pytest.fixture
def sample_sanctions() -> list[dict[str, Any]]:
    with open(DATA_DIR / "sanctions.json") as f:
        return json.load(f)


@pytest.fixture
def sample_pep_data() -> dict[str, Any]:
    with open(DATA_DIR / "pep.json") as f:
        return json.load(f)


@pytest.fixture
def sample_alerts() -> list[dict[str, Any]]:
    with open(DATA_DIR / "alerts.json") as f:
        return json.load(f)


# ── Customer Fixture Helpers ──────────────────────────────────────────


@pytest.fixture
def customer_john_smith() -> dict[str, Any]:
    """Low-risk individual customer with all docs verified."""
    return {
        "id": "CUST-001",
        "name": "John Smith",
        "type": "individual",
        "nationality": "US",
        "risk_factors": [],
        "kyc_status": "approved",
        "documents": [
            {
                "type": "passport",
                "status": "verified",
                "expiry_date": "2028-03-15",
                "submission_date": None,
            },
            {
                "type": "utility_bill",
                "status": "verified",
                "expiry_date": None,
                "submission_date": "2024-01-01",
            },
        ],
    }


@pytest.fixture
def customer_global_holdings() -> dict[str, Any]:
    """High-risk corporate customer with missing docs and shell indicators."""
    return {
        "id": "CUST-003",
        "name": "Global Holdings Ltd",
        "type": "corporate",
        "jurisdiction": "BVI",
        "risk_factors": ["offshore_jurisdiction", "nominee_directors", "shell_company_indicators"],
        "kyc_status": "under_review",
        "documents": [
            {
                "type": "certificate_of_incorporation",
                "status": "verified",
                "expiry_date": None,
                "submission_date": "2015-09-10",
            },
            {
                "type": "register_of_directors",
                "status": "pending",
                "expiry_date": None,
                "submission_date": None,
            },
            {
                "type": "proof_of_address",
                "status": "missing",
                "expiry_date": None,
                "submission_date": None,
            },
        ],
    }


# ── Mock MemoryClient ────────────────────────────────────────────────


@pytest.fixture
def mock_memory_client() -> MagicMock:
    """Mock ``MemoryClient`` shaped the way ``Neo4jDomainService`` uses it.

    Reads go through ``client.query.cypher`` (portable, read-only validated) and
    writes through ``client.graph.execute_write`` (bolt-only). Both are also
    exposed as ``execute_read`` / ``execute_write`` so a test can set a return
    value or assert a call without caring which path the method under test took.
    """
    client = MagicMock()
    client.execute_read = AsyncMock(return_value=[])
    client.execute_write = AsyncMock(return_value=[])
    client.query = MagicMock()
    client.query.cypher = client.execute_read
    client.graph = MagicMock()
    client.graph.execute_write = client.execute_write
    return client


@pytest.fixture
def neo4j_service(mock_memory_client):
    """Neo4jDomainService with a mocked MemoryClient."""
    from src.services.neo4j_service import Neo4jDomainService

    return Neo4jDomainService(mock_memory_client)


# ── Mock Memory Service ───────────────────────────────────────────────


@pytest.fixture
def mock_memory_service():
    """Mock FinancialMemoryService with correct API signatures."""
    service = AsyncMock()
    service.connected = True
    service.initialize = AsyncMock()
    service.ping = AsyncMock(return_value=True)
    service.close = AsyncMock()

    # client property
    mock_client = MagicMock()
    mock_client.graph = AsyncMock()
    service.client = mock_client

    # Short-term memory
    service.add_conversation_message = AsyncMock()
    service.get_conversation_history = AsyncMock(return_value=[])
    service.search_conversations = AsyncMock(return_value=[])

    # Reasoning memory
    service.start_investigation_trace = AsyncMock(return_value=str(uuid4()))
    service.add_reasoning_step = AsyncMock(return_value=str(uuid4()))
    service.complete_investigation_trace = AsyncMock()
    service.get_investigation_trace = AsyncMock(return_value=None)
    service.record_tool_call = AsyncMock()
    service.audit_trail = AsyncMock(return_value=[])

    # Long-term memory
    service.record_screening = AsyncMock()
    service.prior_screenings = AsyncMock(return_value=[])
    service.set_analyst_preference = AsyncMock()
    service.analyst_preferences = AsyncMock(return_value=[])

    return service
