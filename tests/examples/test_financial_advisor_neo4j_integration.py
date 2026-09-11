"""Tests for the Financial Advisor Neo4j integration.

Covers:
- Neo4jDomainService (unit tests with mocked graph client)
- Tool functions (kyc, aml, relationship, compliance) with mocked Neo4jDomainService
- API route helpers (customers, alerts)
- Agent wiring (_bind_tool, agent creation)
- MemoryClient.graph property
- Structure validation (file existence, method signatures)

These are unit tests that do NOT require a running Neo4j instance.
All example app modules are loaded via importlib to avoid relative import issues.
"""

from __future__ import annotations

import ast
import importlib.util
import inspect
import json
import sys
import types
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

EXAMPLES_DIR = Path(__file__).parent.parent.parent / "examples"
APP_DIR = EXAMPLES_DIR / "financial-services-advisor" / "google-cloud-financial-advisor"
BACKEND_SRC = APP_DIR / "backend" / "src"

# Synthetic package name for the backend modules loaded below. Deliberately
# NOT "src": the repository root, every full-stack backend and the lennys
# backend each have a ``src`` directory, so squatting on that name in
# ``sys.modules`` at import time shadowed it for the whole session and broke
# ``import src.agent.tools`` in tests/examples/test_lennys_memory_example.py.
PKG = "gcp_fsa_backend"


# ============================================================================
# Module loader — loads individual .py files without triggering __init__.py
# ============================================================================


def _setup_backend_package_hierarchy():
    """Register the backend src directory as a proper package hierarchy.

    This enables relative imports (e.g. `from ..services.neo4j_service import ...`)
    to resolve when loading individual modules from the example app.
    """
    src_dir = BACKEND_SRC
    # Register the synthetic top-level package
    if PKG not in sys.modules:
        src_pkg = types.ModuleType(PKG)
        src_pkg.__path__ = [str(src_dir)]
        src_pkg.__package__ = PKG
        sys.modules[PKG] = src_pkg

    # Register sub-packages
    sub_packages = [
        "services",
        "tools",
        "models",
        "agents",
        "api",
        "api.routes",
    ]
    for sub in sub_packages:
        full_name = f"{PKG}.{sub}"
        if full_name not in sys.modules:
            pkg = types.ModuleType(full_name)
            pkg.__path__ = [str(src_dir / sub.replace(".", "/"))]
            pkg.__package__ = full_name
            sys.modules[full_name] = pkg


def _load_module(name: str, filepath: Path, package: str | None = None) -> types.ModuleType:
    """Load a single Python file as a module with proper package context."""
    spec = importlib.util.spec_from_file_location(name, filepath, submodule_search_locations=[])
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {filepath}")
    mod = importlib.util.module_from_spec(spec)
    if package:
        mod.__package__ = package
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# Set up the package hierarchy so relative imports work
_setup_backend_package_hierarchy()

# Pre-load neo4j_service.py (no problematic relative imports — only uses stdlib)
_neo4j_service_mod = _load_module(
    f"{PKG}.services.neo4j_service",
    BACKEND_SRC / "services" / "neo4j_service.py",
    package=f"{PKG}.services",
)
Neo4jDomainService = _neo4j_service_mod.Neo4jDomainService


# ============================================================================
# Helpers
# ============================================================================


def _make_graph_client(**overrides) -> AsyncMock:
    """Create a mock ``MemoryClient`` for ``Neo4jDomainService``.

    The service takes the ``MemoryClient`` itself and splits reads from writes:
    reads go through ``client.query.cypher`` (portable, read-only validated),
    writes through ``client.graph.execute_write`` (bolt-only). Both are wired to
    the same mocks here, and ``execute_read``/``execute_write`` are kept as
    aliases so the assertions in this module read the same either way.
    """
    client = AsyncMock()
    read = AsyncMock(return_value=[])
    write = AsyncMock(return_value=[])
    client.query.cypher = read
    client.graph.execute_read = read
    client.graph.execute_write = write
    client.execute_read = read
    client.execute_write = write
    for key, val in overrides.items():
        setattr(client, key, val)
    return client


def _load_tool_module(filename: str) -> types.ModuleType:
    """Load a tool module with proper package context for relative imports."""
    basename = filename.replace(".py", "")
    mod_name = f"{PKG}.tools.{basename}"
    # Remove cached version if reloading
    sys.modules.pop(mod_name, None)
    return _load_module(mod_name, BACKEND_SRC / "tools" / filename, package=f"{PKG}.tools")


SAMPLE_CUSTOMER = {
    "id": "CUST-001",
    "name": "Alice Johnson",
    "type": "individual",
    "nationality": "US",
    "address": "123 Main St, New York, NY",
    "occupation": "Software Engineer",
    "employer": "Tech Corp",
    "jurisdiction": "US",
    "kyc_status": "verified",
    "risk_factors": [],
    "documents": [
        {"type": "passport", "status": "verified", "expiry_date": "2028-01-01"},
        {"type": "utility_bill", "status": "verified", "expiry_date": None},
    ],
}

SAMPLE_CUSTOMER_HIGH_RISK = {
    "id": "CUST-003",
    "name": "Global Holdings Ltd",
    "type": "corporate",
    "nationality": None,
    "jurisdiction": "KY",
    "business_type": "investment_holding",
    "kyc_status": "enhanced_review",
    "risk_factors": [
        "offshore_jurisdiction",
        "nominee_directors",
        "shell_company_indicators",
    ],
    "documents": [
        {"type": "certificate_of_incorporation", "status": "verified", "expiry_date": None},
        {"type": "register_of_directors", "status": "pending", "expiry_date": None},
    ],
}

SAMPLE_TRANSACTION = {
    "id": "TXN-001",
    "amount": 15000.0,
    "type": "wire_in",
    "counterparty": "Overseas Corp",
    "date": "2024-01-15",
    "description": "Wire transfer from overseas",
}

SAMPLE_ALERT = {
    "id": "ALERT-001",
    "type": "AML",
    "severity": "CRITICAL",
    "status": "NEW",
    "title": "Structuring Pattern Detected",
    "description": "Multiple cash deposits near threshold",
    "customer_id": "CUST-003",
    "customer_name": "Global Holdings Ltd",
    "evidence": ["TXN-010", "TXN-011"],
    "requires_sar": True,
    "auto_generated": True,
    "created_at": datetime(2024, 1, 15, 10, 30),
}


# ============================================================================
# Neo4jDomainService Tests
# ============================================================================


class TestNeo4jDomainService:
    """Unit tests for Neo4jDomainService with mocked graph client."""

    @pytest.fixture
    def graph(self):
        return _make_graph_client()

    @pytest.fixture
    def service(self, graph):
        return Neo4jDomainService(graph)

    # -- Customers -----------------------------------------------------------

    @pytest.mark.asyncio
    async def test_list_customers_empty(self, service, graph):
        graph.execute_read.return_value = []
        result = await service.list_customers()
        assert result == []
        graph.execute_read.assert_called_once()

    @pytest.mark.asyncio
    async def test_list_customers_returns_data(self, service, graph):
        graph.execute_read.return_value = [
            {"customer": SAMPLE_CUSTOMER},
            {"customer": SAMPLE_CUSTOMER_HIGH_RISK},
        ]
        result = await service.list_customers()
        assert len(result) == 2
        assert result[0]["name"] == "Alice Johnson"

    @pytest.mark.asyncio
    async def test_list_customers_with_type_filter(self, service, graph):
        graph.execute_read.return_value = [{"customer": SAMPLE_CUSTOMER}]
        await service.list_customers(customer_type="individual")
        call_args = graph.execute_read.call_args
        assert call_args[0][1]["type"] == "individual"

    @pytest.mark.asyncio
    async def test_get_customer_found(self, service, graph):
        graph.execute_read.return_value = [{"customer": SAMPLE_CUSTOMER}]
        result = await service.get_customer("CUST-001")
        assert result is not None
        assert result["name"] == "Alice Johnson"

    @pytest.mark.asyncio
    async def test_get_customer_not_found(self, service, graph):
        graph.execute_read.return_value = []
        result = await service.get_customer("CUST-999")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_customer_documents(self, service, graph):
        graph.execute_read.return_value = [
            {"document": {"type": "passport", "status": "verified"}},
        ]
        result = await service.get_customer_documents("CUST-001")
        assert len(result) == 1
        assert result[0]["type"] == "passport"

    # -- Transactions --------------------------------------------------------

    @pytest.mark.asyncio
    async def test_get_transactions(self, service, graph):
        graph.execute_read.return_value = [{"transaction": SAMPLE_TRANSACTION}]
        result = await service.get_transactions("CUST-001")
        assert len(result) == 1
        assert result[0]["amount"] == 15000.0

    @pytest.mark.asyncio
    async def test_get_transactions_with_filters(self, service, graph):
        graph.execute_read.return_value = []
        await service.get_transactions("CUST-001", min_amount=5000.0, transaction_type="wire_in")
        call_args = graph.execute_read.call_args
        params = call_args[0][1]
        assert params["min_amount"] == 5000.0
        assert params["tx_type"] == "wire_in"

    @pytest.mark.asyncio
    async def test_get_transaction_stats_empty(self, service, graph):
        graph.execute_read.return_value = []
        result = await service.get_transaction_stats("CUST-001")
        assert result["transaction_count"] == 0
        assert result["total_volume"] == 0

    @pytest.mark.asyncio
    async def test_get_transaction_stats(self, service, graph):
        graph.execute_read.return_value = [
            {
                "transaction_count": 5,
                "total_volume": 50000.0,
                "total_deposits": 30000.0,
                "total_withdrawals": 20000.0,
                "average_transaction": 10000.0,
                "counterparties": ["Corp A", "Corp B"],
                "transaction_types": ["wire_in", "wire_out"],
            }
        ]
        result = await service.get_transaction_stats("CUST-001")
        assert result["transaction_count"] == 5
        assert result["total_volume"] == 50000.0

    @pytest.mark.asyncio
    async def test_detect_structuring(self, service, graph):
        graph.execute_read.return_value = [
            {"transaction": {"id": "TXN-010", "amount": 9500}},
            {"transaction": {"id": "TXN-011", "amount": 9800}},
        ]
        result = await service.detect_structuring("CUST-003")
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_detect_rapid_movement(self, service, graph):
        graph.execute_read.return_value = [
            {
                "inbound": {"id": "TXN-001", "amount": 50000},
                "outbound": {"id": "TXN-002", "amount": 48000},
                "retained": 2000,
            }
        ]
        result = await service.detect_rapid_movement("CUST-003")
        assert len(result) == 1
        assert result[0]["retained"] == 2000

    @pytest.mark.asyncio
    async def test_detect_layering(self, service, graph):
        graph.execute_read.return_value = [
            {"transaction": {"id": "TXN-005", "counterparty": "Cayman Fund"}},
        ]
        result = await service.detect_layering("CUST-003")
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_get_velocity_metrics_empty(self, service, graph):
        graph.execute_read.return_value = []
        result = await service.get_velocity_metrics("CUST-001")
        assert result["total_transactions"] == 0
        assert result["average_transaction"] == 0

    @pytest.mark.asyncio
    async def test_get_velocity_metrics(self, service, graph):
        graph.execute_read.return_value = [
            {"tx_type": "wire_in", "cnt": 3, "vol": 30000},
            {"tx_type": "cash_deposit", "cnt": 2, "vol": 19000},
        ]
        result = await service.get_velocity_metrics("CUST-001")
        assert result["total_transactions"] == 5
        assert result["total_volume"] == 49000
        assert result["transactions_by_type"]["wire_in"] == 3
        assert result["volume_by_type"]["cash_deposit"] == 19000

    # -- Network / Relationships ---------------------------------------------

    @pytest.mark.asyncio
    async def test_find_connections(self, service, graph):
        graph.execute_read.return_value = [
            {
                "entity": {"id": "ORG-001", "name": "Shell Corp"},
                "distance": 1,
                "rel_types": ["CONNECTED_TO"],
            }
        ]
        result = await service.find_connections("CUST-003")
        assert result["entity_id"] == "CUST-003"
        assert len(result["connections"]) == 1

    @pytest.mark.asyncio
    async def test_detect_shell_companies(self, service, graph):
        graph.execute_read.return_value = [
            {
                "org": {
                    "id": "ORG-001",
                    "name": "Shell Corp",
                    "jurisdiction": "KY",
                    "shell_indicators": ["no_employees", "po_box_address"],
                }
            }
        ]
        result = await service.detect_shell_companies("CUST-003")
        assert len(result) == 1
        assert result[0]["name"] == "Shell Corp"

    @pytest.mark.asyncio
    async def test_trace_ownership(self, service, graph):
        graph.execute_read.return_value = [
            {
                "owner": {"id": "CUST-003", "name": "Global Holdings", "type": "corporate"},
                "rel_types": ["OWNS"],
                "chain_length": 1,
            }
        ]
        result = await service.trace_ownership("ORG-001")
        assert result["entity_id"] == "ORG-001"
        assert len(result["ownership_chains"]) == 1
        assert result["ubo_identified"] is False

    @pytest.mark.asyncio
    async def test_trace_ownership_with_individual_ubo(self, service, graph):
        graph.execute_read.return_value = [
            {
                "owner": {"id": "CUST-001", "name": "Alice Johnson", "type": "individual"},
                "rel_types": ["OWNS"],
                "chain_length": 2,
            }
        ]
        result = await service.trace_ownership("ORG-001")
        assert result["ubo_identified"] is True

    @pytest.mark.asyncio
    async def test_get_network_risk_low(self, service, graph):
        graph.execute_read.return_value = []
        result = await service.get_network_risk("CUST-001")
        assert result["network_risk_score"] == 0
        assert result["risk_level"] == "LOW"

    @pytest.mark.asyncio
    async def test_get_network_risk_high(self, service, graph):
        graph.execute_read.return_value = [
            {
                "entity": {
                    "id": "ORG-001",
                    "name": "Shell Corp KY",
                    "jurisdiction": "KY",
                    "shell_indicators": ["no_employees"],
                    "role": "nominee_services",
                }
            },
            {
                "entity": {
                    "id": "ORG-002",
                    "name": "BVI Holdings",
                    "jurisdiction": "BVI",
                    "shell_indicators": [],
                    "role": None,
                }
            },
        ]
        result = await service.get_network_risk("CUST-003")
        assert result["risk_level"] == "HIGH"
        assert result["network_risk_score"] == 65

    # -- Alerts --------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_list_alerts_empty(self, service, graph):
        graph.execute_read.return_value = []
        result = await service.list_alerts()
        assert result == []

    @pytest.mark.asyncio
    async def test_list_alerts(self, service, graph):
        graph.execute_read.return_value = [{"alert": SAMPLE_ALERT}]
        result = await service.list_alerts()
        assert len(result) == 1
        assert result[0]["severity"] == "CRITICAL"

    @pytest.mark.asyncio
    async def test_list_alerts_with_filters(self, service, graph):
        graph.execute_read.return_value = []
        await service.list_alerts(status="NEW", severity="CRITICAL", customer_id="CUST-003")
        call_args = graph.execute_read.call_args
        params = call_args[0][1]
        assert params["status"] == "NEW"
        assert params["severity"] == "CRITICAL"
        assert params["customer_id"] == "CUST-003"

    @pytest.mark.asyncio
    async def test_get_alert_found(self, service, graph):
        graph.execute_read.return_value = [{"alert": SAMPLE_ALERT}]
        result = await service.get_alert("ALERT-001")
        assert result is not None
        assert result["title"] == "Structuring Pattern Detected"

    @pytest.mark.asyncio
    async def test_get_alert_not_found(self, service, graph):
        graph.execute_read.return_value = []
        result = await service.get_alert("ALERT-999")
        assert result is None

    @pytest.mark.asyncio
    async def test_create_alert(self, service, graph):
        created = {**SAMPLE_ALERT, "id": "ALERT-NEW"}
        graph.execute_write.return_value = [{"alert": created}]
        result = await service.create_alert(
            {
                "id": "ALERT-NEW",
                "customer_id": "CUST-003",
                "type": "AML",
                "severity": "HIGH",
                "status": "NEW",
                "title": "Test Alert",
                "description": "Test",
            }
        )
        assert result["id"] == "ALERT-NEW"
        graph.execute_write.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_alert(self, service, graph):
        updated = {**SAMPLE_ALERT, "status": "ACKNOWLEDGED"}
        graph.execute_write.return_value = [{"alert": updated}]
        result = await service.update_alert("ALERT-001", {"status": "ACKNOWLEDGED"})
        assert result is not None
        assert result["status"] == "ACKNOWLEDGED"

    @pytest.mark.asyncio
    async def test_update_alert_no_changes(self, service, graph):
        graph.execute_read.return_value = [{"alert": SAMPLE_ALERT}]
        result = await service.update_alert("ALERT-001", {"unknown_field": "value"})
        graph.execute_read.assert_called_once()
        graph.execute_write.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_alert_summary_empty(self, service, graph):
        graph.execute_read.return_value = []
        result = await service.get_alert_summary()
        assert result["total"] == 0

    @pytest.mark.asyncio
    async def test_get_alert_summary(self, service, graph):
        graph.execute_read.return_value = [
            {
                "total": 5,
                "critical": 2,
                "high": 1,
                "medium": 1,
                "low": 1,
                "new_count": 3,
                "acknowledged": 1,
                "investigating": 0,
                "escalated": 0,
                "resolved": 1,
                "critical_unresolved": 2,
                "high_unresolved": 1,
            }
        ]
        result = await service.get_alert_summary()
        assert result["total"] == 5
        assert result["by_severity"]["CRITICAL"] == 2
        assert result["by_status"]["NEW"] == 3
        assert result["critical_unresolved"] == 2

    # -- Sanctions -----------------------------------------------------------

    @pytest.mark.asyncio
    async def test_check_sanctions_no_match(self, service, graph):
        graph.execute_read.return_value = []
        result = await service.check_sanctions("John Smith")
        assert result == []

    @pytest.mark.asyncio
    async def test_check_sanctions_match(self, service, graph):
        graph.execute_read.return_value = [
            {
                "entity": {
                    "name": "Viktor Petrov",
                    "list": "OFAC SDN",
                    "reason": "Sanctions evasion",
                    "aliases": ["V. Petrov"],
                },
                "match_type": "EXACT",
                "confidence": 1.0,
            }
        ]
        result = await service.check_sanctions("Viktor Petrov")
        assert len(result) == 1
        assert result[0]["match_type"] == "EXACT"

    # -- PEP -----------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_check_pep_no_match(self, service, graph):
        graph.execute_read.return_value = []
        result = await service.check_pep("Random Person")
        assert result == []

    @pytest.mark.asyncio
    async def test_check_pep_direct_match(self, service, graph):
        graph.execute_read.return_value = [
            {
                "pep": {
                    "name": "Elena Rodriguez",
                    "position": "Finance Minister",
                    "country": "ES",
                    "tier": 1,
                },
                "match_type": "DIRECT_PEP",
                "confidence": 1.0,
            }
        ]
        result = await service.check_pep("Elena Rodriguez")
        assert len(result) >= 1
        assert result[0]["match_type"] == "DIRECT_PEP"

    # -- Graph stats ---------------------------------------------------------

    @pytest.mark.asyncio
    async def test_get_graph_stats(self, service, graph):
        graph.execute_read.side_effect = [
            [{"label": "Customer", "count": 3}, {"label": "Organization", "count": 6}],
            [{"type": "HAS_TRANSACTION", "count": 16}],
        ]
        result = await service.get_graph_stats()
        assert result["total_nodes"] == 9
        assert result["total_relationships"] == 16
        assert result["nodes_by_label"]["Customer"] == 3


# ============================================================================
# Tool function tests via source file validation
# ============================================================================


class TestToolFunctions:
    """Test tool function logic by loading modules directly with importlib.

    Each tool module does `from ..services.neo4j_service import Neo4jDomainService`
    We pre-register a stub so that import resolves.
    """

    @pytest.fixture
    def neo4j_service(self):
        svc = AsyncMock()
        svc.get_customer = AsyncMock(return_value=None)
        svc.get_customer_documents = AsyncMock(return_value=[])
        svc.get_transactions = AsyncMock(return_value=[])
        svc.detect_structuring = AsyncMock(return_value=[])
        svc.detect_rapid_movement = AsyncMock(return_value=[])
        svc.detect_layering = AsyncMock(return_value=[])
        svc.get_velocity_metrics = AsyncMock(
            return_value={
                "total_transactions": 0,
                "total_volume": 0,
                "average_transaction": 0,
                "transactions_by_type": {},
                "volume_by_type": {},
            }
        )
        svc.create_alert = AsyncMock(return_value={"id": "ALERT-NEW"})
        svc.check_sanctions = AsyncMock(return_value=[])
        svc.check_pep = AsyncMock(return_value=[])
        svc.find_connections = AsyncMock(return_value={"entity_id": "E1", "connections": []})
        svc.get_network_risk = AsyncMock(
            return_value={
                "network_risk_score": 0,
                "risk_level": "LOW",
                "risk_factors": [],
                "total_connections": 0,
            }
        )
        svc.detect_shell_companies = AsyncMock(return_value=[])
        svc.trace_ownership = AsyncMock(
            return_value={"entity_id": "E1", "ownership_chains": [], "ubo_identified": False}
        )
        # A few tool functions own one-off queries and call the service's public
        # read() helper (which forwards to client.query.cypher).
        svc.read = AsyncMock(return_value=[])
        return svc

    # -- KYC tools -----------------------------------------------------------

    @pytest.mark.asyncio
    async def test_verify_identity_not_found(self, neo4j_service):
        mod = _load_tool_module("kyc_tools.py")
        result = await mod.verify_identity("CUST-999", neo4j_service=neo4j_service)
        assert result["status"] == "NOT_FOUND"
        assert result["verified"] is False

    @pytest.mark.asyncio
    async def test_verify_identity_verified(self, neo4j_service):
        mod = _load_tool_module("kyc_tools.py")
        neo4j_service.get_customer.return_value = SAMPLE_CUSTOMER
        result = await mod.verify_identity("CUST-001", neo4j_service=neo4j_service)
        assert result["status"] == "VERIFIED"
        assert result["verified"] is True

    @pytest.mark.asyncio
    async def test_verify_identity_pending_corporate(self, neo4j_service):
        mod = _load_tool_module("kyc_tools.py")
        neo4j_service.get_customer.return_value = SAMPLE_CUSTOMER_HIGH_RISK
        result = await mod.verify_identity("CUST-003", neo4j_service=neo4j_service)
        assert result["status"] == "PENDING"
        assert "register_of_directors" in result["missing_documents"]

    @pytest.mark.asyncio
    async def test_check_documents_all(self, neo4j_service):
        mod = _load_tool_module("kyc_tools.py")
        neo4j_service.get_customer.return_value = SAMPLE_CUSTOMER
        result = await mod.check_documents("CUST-001", neo4j_service=neo4j_service)
        assert result["total_documents"] == 2

    @pytest.mark.asyncio
    async def test_check_documents_specific_type(self, neo4j_service):
        mod = _load_tool_module("kyc_tools.py")
        neo4j_service.get_customer.return_value = SAMPLE_CUSTOMER
        result = await mod.check_documents(
            "CUST-001", document_type="passport", neo4j_service=neo4j_service
        )
        assert result["status"] == "VERIFIED"

    @pytest.mark.asyncio
    async def test_assess_customer_risk_low(self, neo4j_service):
        mod = _load_tool_module("kyc_tools.py")
        neo4j_service.get_customer.return_value = SAMPLE_CUSTOMER
        result = await mod.assess_customer_risk("CUST-001", neo4j_service=neo4j_service)
        assert result["risk_level"] == "LOW"
        assert result["risk_score"] == 20

    @pytest.mark.asyncio
    async def test_assess_customer_risk_critical(self, neo4j_service):
        mod = _load_tool_module("kyc_tools.py")
        neo4j_service.get_customer.return_value = SAMPLE_CUSTOMER_HIGH_RISK
        result = await mod.assess_customer_risk("CUST-003", neo4j_service=neo4j_service)
        assert result["risk_level"] == "CRITICAL"
        assert result["risk_score"] == 100

    @pytest.mark.asyncio
    async def test_check_adverse_media_no_hits(self, neo4j_service):
        mod = _load_tool_module("kyc_tools.py")
        neo4j_service.get_customer.return_value = SAMPLE_CUSTOMER
        result = await mod.check_adverse_media("CUST-001", neo4j_service=neo4j_service)
        assert result["hits_found"] == 0

    @pytest.mark.asyncio
    async def test_check_adverse_media_with_hits(self, neo4j_service):
        mod = _load_tool_module("kyc_tools.py")
        neo4j_service.get_customer.return_value = SAMPLE_CUSTOMER_HIGH_RISK
        result = await mod.check_adverse_media("CUST-003", neo4j_service=neo4j_service)
        assert result["hits_found"] == 1
        assert result["risk_indicator"] == "HIGH"

    # -- AML tools -----------------------------------------------------------

    @pytest.mark.asyncio
    async def test_scan_transactions_no_data(self, neo4j_service):
        mod = _load_tool_module("aml_tools.py")
        result = await mod.scan_transactions("CUST-001", neo4j_service=neo4j_service)
        assert result["status"] == "NO_TRANSACTIONS"

    @pytest.mark.asyncio
    async def test_scan_transactions_with_data(self, neo4j_service):
        mod = _load_tool_module("aml_tools.py")
        neo4j_service.get_transactions.return_value = [
            {"id": "TXN-001", "amount": 10000, "type": "wire_in", "counterparty": "Corp A"},
            {"id": "TXN-002", "amount": 5000, "type": "wire_out", "counterparty": "Corp B"},
        ]
        result = await mod.scan_transactions("CUST-001", neo4j_service=neo4j_service)
        assert result["transaction_count"] == 2
        assert result["total_volume"] == 15000

    @pytest.mark.asyncio
    async def test_detect_patterns_structuring(self, neo4j_service):
        mod = _load_tool_module("aml_tools.py")
        neo4j_service.get_transactions.return_value = [
            {"id": "T1", "amount": 9500, "type": "cash_deposit"}
        ]
        neo4j_service.detect_structuring.return_value = [
            {"id": "TXN-010", "amount": 9500},
            {"id": "TXN-011", "amount": 9800},
        ]
        result = await mod.detect_patterns("CUST-003", neo4j_service=neo4j_service)
        assert any(p["pattern"] == "STRUCTURING" for p in result["patterns_detected"])

    @pytest.mark.asyncio
    async def test_flag_suspicious_transaction_success(self, neo4j_service):
        mod = _load_tool_module("aml_tools.py")
        neo4j_service.read.return_value = [
            {
                "customer_id": "CUST-003",
                "transaction": {"id": "TXN-010", "amount": 9500, "type": "cash_deposit"},
            }
        ]
        result = await mod.flag_suspicious_transaction(
            "TXN-010", "Structuring", severity="HIGH", neo4j_service=neo4j_service
        )
        assert result["status"] == "FLAGGED"

    @pytest.mark.asyncio
    async def test_analyze_velocity_no_data(self, neo4j_service):
        mod = _load_tool_module("aml_tools.py")
        result = await mod.analyze_velocity("CUST-001", neo4j_service=neo4j_service)
        assert result["status"] == "NO_DATA"

    # -- Compliance tools ----------------------------------------------------

    @pytest.mark.asyncio
    async def test_check_sanctions_clear(self, neo4j_service):
        mod = _load_tool_module("compliance_tools.py")
        result = await mod.check_sanctions("John Smith", neo4j_service=neo4j_service)
        assert result["screening_status"] == "CLEAR"

    @pytest.mark.asyncio
    async def test_check_sanctions_hit(self, neo4j_service):
        mod = _load_tool_module("compliance_tools.py")
        neo4j_service.check_sanctions.return_value = [
            {
                "entity": {"name": "Viktor Petrov", "list": "OFAC SDN", "reason": "test"},
                "match_type": "EXACT",
                "confidence": 1.0,
            }
        ]
        result = await mod.check_sanctions("Viktor Petrov", neo4j_service=neo4j_service)
        assert result["screening_status"] == "HIT"
        assert result["requires_escalation"] is True

    @pytest.mark.asyncio
    async def test_verify_pep_status_clear(self, neo4j_service):
        mod = _load_tool_module("compliance_tools.py")
        result = await mod.verify_pep_status("Random Person", neo4j_service=neo4j_service)
        assert result["pep_status"] == "CLEAR"

    @pytest.mark.asyncio
    async def test_generate_sar_report(self, neo4j_service):
        mod = _load_tool_module("compliance_tools.py")
        neo4j_service.get_customer.return_value = SAMPLE_CUSTOMER
        result = await mod.generate_sar_report(
            "CUST-001", "structuring", transaction_ids=["TXN-010"], neo4j_service=neo4j_service
        )
        assert result["status"] == "SAR_DRAFT_CREATED"

    @pytest.mark.asyncio
    async def test_assess_regulatory_requirements(self, neo4j_service):
        mod = _load_tool_module("compliance_tools.py")
        neo4j_service.get_customer.return_value = {**SAMPLE_CUSTOMER, "jurisdiction": "KY"}
        neo4j_service.get_transactions.return_value = [{"type": "cash_deposit"}]
        result = await mod.assess_regulatory_requirements("CUST-001", neo4j_service=neo4j_service)
        assert "US" in result["jurisdictions_analyzed"]

    # -- Relationship tools --------------------------------------------------

    @pytest.mark.asyncio
    async def test_find_connections_not_found(self, neo4j_service):
        mod = _load_tool_module("relationship_tools.py")
        result = await mod.find_connections("UNKNOWN", neo4j_service=neo4j_service)
        assert result["status"] == "NOT_FOUND"

    @pytest.mark.asyncio
    async def test_find_connections_found(self, neo4j_service):
        mod = _load_tool_module("relationship_tools.py")
        neo4j_service.read.return_value = [
            {"entity": {"id": "CUST-003", "name": "Global Holdings", "type": "corporate"}}
        ]
        neo4j_service.find_connections.return_value = {
            "entity_id": "CUST-003",
            "connections": [
                {
                    "entity": {
                        "id": "ORG-001",
                        "name": "Shell Corp",
                        "type": "org",
                        "jurisdiction": "KY",
                    },
                    "rel_types": ["CONNECTED_TO"],
                    "distance": 1,
                }
            ],
        }
        result = await mod.find_connections("CUST-003", neo4j_service=neo4j_service)
        assert result["connections_found"] == 1

    @pytest.mark.asyncio
    async def test_detect_shell_companies_entity_not_found(self, neo4j_service):
        mod = _load_tool_module("relationship_tools.py")
        result = await mod.detect_shell_companies("UNKNOWN", neo4j_service=neo4j_service)
        assert result["status"] == "NOT_FOUND"

    @pytest.mark.asyncio
    async def test_map_beneficial_ownership_not_found(self, neo4j_service):
        mod = _load_tool_module("relationship_tools.py")
        result = await mod.map_beneficial_ownership("UNKNOWN", neo4j_service=neo4j_service)
        assert result["status"] == "NOT_FOUND"


# ============================================================================
# Agent Wiring Tests (_bind_tool, agent creation)
# ============================================================================


class TestAgentWiring:
    """Test ``bind_tool``, imported from the module that actually defines it.

    Previously this class re-``exec``'d the function body extracted from the
    source text, which cannot catch an import-time or attribute error. Now the
    real ``src/agents/_base.py`` is loaded (it imports google-adk, which the
    repo's dev environment provides via the ``[google-adk]`` extra).
    """

    def _get_bind_tool(self):
        """Import the real ``bind_tool`` from ``src/agents/_base.py``."""
        pytest.importorskip("google.adk", reason="needs the [google-adk] extra")
        module = sys.modules.get(f"{PKG}.agents._base") or _load_module(
            f"{PKG}.agents._base",
            BACKEND_SRC / "agents" / "_base.py",
            package=f"{PKG}.agents",
        )
        return module.bind_tool

    def test_bind_tool_removes_neo4j_service_from_signature(self):
        _bind_tool = self._get_bind_tool()

        async def sample_tool(customer_id: str, *, neo4j_service: Any) -> dict:
            return {"customer_id": customer_id}

        bound = _bind_tool(sample_tool, MagicMock())
        sig = inspect.signature(bound)
        assert "neo4j_service" not in sig.parameters
        assert "customer_id" in sig.parameters

    def test_bind_tool_preserves_function_name(self):
        _bind_tool = self._get_bind_tool()

        async def verify_identity(customer_id: str, *, neo4j_service: Any) -> dict:
            return {}

        bound = _bind_tool(verify_identity, MagicMock())
        assert bound.__name__ == "verify_identity"

    @pytest.mark.asyncio
    async def test_bind_tool_passes_neo4j_service(self):
        _bind_tool = self._get_bind_tool()
        received = {}

        async def sample_tool(customer_id: str, *, neo4j_service: Any) -> dict:
            received["neo4j_service"] = neo4j_service
            received["customer_id"] = customer_id
            return {}

        mock_service = MagicMock()
        bound = _bind_tool(sample_tool, mock_service)
        await bound("CUST-001")

        assert received["neo4j_service"] is mock_service
        assert received["customer_id"] == "CUST-001"

    def test_bind_tool_handles_multiple_params(self):
        _bind_tool = self._get_bind_tool()

        async def multi_param(a: str, b: int = 5, c: bool = False, *, neo4j_service: Any) -> dict:
            return {}

        bound = _bind_tool(multi_param, MagicMock())
        sig = inspect.signature(bound)
        param_names = list(sig.parameters.keys())
        assert param_names == ["a", "b", "c"]


# ============================================================================
# API Route Helper Tests — loaded by parsing source directly
# ============================================================================


class TestCustomerRouteHelpers:
    """Test _compute_risk and _customer_from_dict from customers.py."""

    @pytest.fixture(autouse=True)
    def _load(self):
        """Load the helpers by exec'ing relevant code from customers.py."""
        # _compute_risk is a standalone function with no imports needed
        source = (BACKEND_SRC / "api" / "routes" / "customers.py").read_text(encoding="utf-8")

        # Extract _compute_risk function
        lines = source.split("\n")
        func_lines = []
        in_func = False
        for line in lines:
            if line.startswith("def _compute_risk("):
                in_func = True
            elif in_func and line and not line[0].isspace() and line.strip():
                break
            if in_func:
                func_lines.append(line)

        code = "\n".join(func_lines)
        ns: dict[str, Any] = {}
        exec(code, ns)
        self._compute_risk = ns["_compute_risk"]

    def test_compute_risk_low(self):
        result = self._compute_risk(SAMPLE_CUSTOMER)
        assert result["risk_level"] == "LOW"
        assert result["risk_score"] == 20

    def test_compute_risk_critical(self):
        result = self._compute_risk(SAMPLE_CUSTOMER_HIGH_RISK)
        assert result["risk_level"] == "CRITICAL"
        assert result["risk_score"] == 100

    def test_compute_risk_no_risk_factors(self):
        cust = {"risk_factors": None, "documents": []}
        result = self._compute_risk(cust)
        assert result["risk_score"] == 20
        assert result["risk_level"] == "LOW"


class TestAlertRouteHelpers:
    """Test _to_python_datetime and _alert_from_dict from alerts.py."""

    @pytest.fixture(autouse=True)
    def _load(self):
        """Load the helpers by exec'ing relevant code from alerts.py."""
        source = (BACKEND_SRC / "api" / "routes" / "alerts.py").read_text(encoding="utf-8")

        # Extract _to_python_datetime function
        lines = source.split("\n")

        # Find and extract _to_python_datetime
        to_dt_lines = []
        in_func = False
        for line in lines:
            if line.startswith("def _to_python_datetime("):
                in_func = True
            elif in_func and line and not line[0].isspace() and line.strip():
                break
            if in_func:
                to_dt_lines.append(line)

        code = "from datetime import datetime\n\n" + "\n".join(to_dt_lines)
        ns: dict[str, Any] = {}
        exec(code, ns)
        self._to_python_datetime = ns["_to_python_datetime"]

    def test_to_python_datetime_none(self):
        assert self._to_python_datetime(None) is None

    def test_to_python_datetime_with_python_datetime(self):
        dt = datetime(2024, 1, 15, 10, 30)
        assert self._to_python_datetime(dt) == dt

    def test_to_python_datetime_with_neo4j_datetime(self):
        mock_neo4j_dt = MagicMock()
        expected = datetime(2024, 1, 15, 10, 30)
        mock_neo4j_dt.to_native.return_value = expected
        assert self._to_python_datetime(mock_neo4j_dt) == expected


# ============================================================================
# MemoryClient.graph Property Test
# ============================================================================


class TestMemoryClientGraphProperty:
    """Test the MemoryClient.graph property."""

    def test_graph_property_exists(self):
        from neo4j_agent_memory import MemoryClient

        assert hasattr(MemoryClient, "graph")

    def test_graph_property_raises_when_not_connected(self):
        from pydantic import SecretStr

        from neo4j_agent_memory import MemoryClient, MemorySettings
        from neo4j_agent_memory.config.settings import Neo4jConfig

        settings = MemorySettings(
            neo4j=Neo4jConfig(
                uri="bolt://localhost:7687",
                password=SecretStr("test"),
            ),
        )
        client = MemoryClient(settings)
        with pytest.raises(Exception, match="not connected"):
            _ = client.graph


# ============================================================================
# SSE Streaming Helper Tests
# ============================================================================


class TestSSEHelpers:
    """Test the SSE frame formatter and the tool-result renderer.

    ``truncate_result`` now lives in ``src/services/adk_events.py`` (shared by
    the streaming and non-streaming routes) and is imported as a real module.
    ``_sse_event`` is genuinely local to ``chat.py`` and is still extracted from
    its source, since importing that module requires google-adk *and* the
    library's optional extras.
    """

    @pytest.fixture(autouse=True)
    def _load(self):
        module = sys.modules.get(f"{PKG}.services.adk_events") or _load_module(
            f"{PKG}.services.adk_events",
            BACKEND_SRC / "services" / "adk_events.py",
            package=f"{PKG}.services",
        )
        self._truncate_result = module.truncate_result

        source = (BACKEND_SRC / "api" / "routes" / "chat.py").read_text(encoding="utf-8")
        lines = source.split("\n")
        code = "import json\n\n"
        in_func = False
        func_lines: list[str] = []
        for line in lines:
            if line.startswith("def _sse_event("):
                in_func = True
            elif in_func and line and not line[0].isspace() and line.strip():
                break
            if in_func:
                func_lines.append(line)
        code += "\n".join(func_lines) + "\n"

        ns: dict[str, Any] = {}
        exec(code, ns)
        self._sse_event = ns["_sse_event"]

    def test_sse_event_format(self):
        result = self._sse_event("agent_start", {"agent": "kyc_agent"})
        assert result.startswith("event: agent_start\n")
        assert "data: " in result
        assert result.endswith("\n\n")
        import json

        data_line = result.split("\n")[1]
        data = json.loads(data_line.replace("data: ", ""))
        assert data["agent"] == "kyc_agent"

    def test_sse_event_types(self):
        for event_type in [
            "agent_start",
            "agent_complete",
            "agent_delegate",
            "tool_call",
            "tool_result",
            "memory_access",
            "thinking",
            "response",
            "trace_saved",
            "done",
            "error",
        ]:
            result = self._sse_event(event_type, {"test": True})
            assert result.startswith(f"event: {event_type}\n")

    def test_truncate_result_none(self):
        assert self._truncate_result(None) is None

    def test_truncate_result_string(self):
        result = self._truncate_result("hello")
        assert result == "hello"
        assert isinstance(result, str)

    def test_truncate_result_dict(self):
        result = self._truncate_result({"key": "value"})
        assert isinstance(result, str)
        assert "key" in result
        assert "value" in result

    def test_truncate_result_list(self):
        result = self._truncate_result([1, 2, 3])
        assert isinstance(result, str)
        assert result == "[1, 2, 3]"

    def test_truncate_result_long_string(self):
        long_str = "x" * 600
        result = self._truncate_result(long_str, max_len=500)
        assert len(result) == 503  # 500 + "..."
        assert result.endswith("...")

    def test_truncate_result_long_dict(self):
        big_dict = {"key": "v" * 600}
        result = self._truncate_result(big_dict, max_len=500)
        assert isinstance(result, str)
        assert result.endswith("...")


# ============================================================================
# Structure Validation
# ============================================================================


class TestNewFileStructure:
    """Validate that all new files exist and have expected content."""

    @pytest.fixture
    def app_dir(self):
        return APP_DIR

    def test_neo4j_service_exists(self, app_dir):
        assert (app_dir / "backend" / "src" / "services" / "neo4j_service.py").exists()

    def test_sanctions_json_exists(self, app_dir):
        assert (app_dir.parent / "data" / "sanctions.json").exists()

    def test_pep_json_exists(self, app_dir):
        assert (app_dir.parent / "data" / "pep.json").exists()

    def test_alerts_json_exists(self, app_dir):
        assert (app_dir.parent / "data" / "alerts.json").exists()

    def test_neo4j_service_has_expected_methods(self, app_dir):
        content = (app_dir / "backend" / "src" / "services" / "neo4j_service.py").read_text(
            encoding="utf-8"
        )
        expected = [
            "list_customers",
            "get_customer",
            "get_customer_documents",
            "get_transactions",
            "get_transaction_stats",
            "detect_structuring",
            "detect_rapid_movement",
            "detect_layering",
            "get_velocity_metrics",
            "find_connections",
            "detect_shell_companies",
            "trace_ownership",
            "get_network_risk",
            "list_alerts",
            "get_alert",
            "create_alert",
            "update_alert",
            "get_alert_summary",
            "check_sanctions",
            "check_pep",
            "get_graph_stats",
        ]
        for method in expected:
            assert f"async def {method}" in content, f"Missing method: {method}"

    def test_tools_use_neo4j_service_parameter(self, app_dir):
        for tool_file in [
            "kyc_tools.py",
            "aml_tools.py",
            "relationship_tools.py",
            "compliance_tools.py",
        ]:
            content = (app_dir / "backend" / "src" / "tools" / tool_file).read_text(
                encoding="utf-8"
            )
            assert "neo4j_service: Neo4jDomainService" in content, (
                f"{tool_file} missing neo4j_service param"
            )

    def test_agent_files_have_bind_tool(self, app_dir):
        # bind_tool lives in agents/_base.py and is re-exported from the package.
        init_content = (app_dir / "backend" / "src" / "agents" / "__init__.py").read_text(
            encoding="utf-8"
        )
        assert "bind_tool" in init_content, "__init__.py should re-export bind_tool"

        for agent_file in [
            "kyc_agent.py",
            "aml_agent.py",
            "relationship_agent.py",
            "compliance_agent.py",
        ]:
            content = (app_dir / "backend" / "src" / "agents" / agent_file).read_text(
                encoding="utf-8"
            )
            assert "create_specialist_agent" in content, (
                f"{agent_file} should build its agent through the shared factory"
            )
        base = (app_dir / "backend" / "src" / "agents" / "_base.py").read_text(encoding="utf-8")
        assert "def bind_tool(" in base, "_base.py should define bind_tool"
        assert "load_memory" in base, "specialists should use ADK's load_memory for reads"

    def test_main_initializes_neo4j_service(self, app_dir):
        content = (app_dir / "backend" / "src" / "main.py").read_text(encoding="utf-8")
        assert "Neo4jDomainService" in content
        assert "neo4j_service" in content

    def test_main_registers_traces_router(self, app_dir):
        content = (app_dir / "backend" / "src" / "main.py").read_text(encoding="utf-8")
        assert "traces" in content, "main.py should import traces router"
        assert "traces.router" in content, "main.py should register traces.router"

    def test_routes_use_app_state(self, app_dir):
        for route_file in ["customers.py", "alerts.py"]:
            content = (app_dir / "backend" / "src" / "api" / "routes" / route_file).read_text(
                encoding="utf-8"
            )
            assert "_get_neo4j_service" in content, f"{route_file} missing _get_neo4j_service"

    def test_chat_has_both_endpoints(self, app_dir):
        content = (app_dir / "backend" / "src" / "api" / "routes" / "chat.py").read_text(
            encoding="utf-8"
        )
        assert "async def chat_stream" in content, "chat.py should have chat_stream"
        assert "async def chat(" in content, "chat.py should have original chat endpoint"
        assert "StreamingResponse" in content, "chat.py should use StreamingResponse"
        assert "text/event-stream" in content, "chat.py should use SSE content type"

    def test_chat_records_reasoning_traces(self, app_dir):
        """Trace recording moved into services/trace_writer.py, shared by both routes."""
        chat = (app_dir / "backend" / "src" / "api" / "routes" / "chat.py").read_text(
            encoding="utf-8"
        )
        assert "TraceWriter" in chat, "chat.py should record a trace"

        writer = (app_dir / "backend" / "src" / "services" / "trace_writer.py").read_text(
            encoding="utf-8"
        )
        for expected in (
            "start_trace",
            "add_step",
            "record_tool_call",
            "complete_trace",
            "ToolCallStatus",
            # audit-grade additions
            "triggered_by_message_id",
            "touched_entities",
            "TraceOutcome",
            "EntityRef",
        ):
            assert expected in writer, f"trace_writer.py should use {expected}"

    def test_chat_filters_internal_adk_functions(self, app_dir):
        """Delegation functions are surfaced as agent_delegate, never as tool calls."""
        events = (app_dir / "backend" / "src" / "services" / "adk_events.py").read_text(
            encoding="utf-8"
        )
        assert "INTERNAL_FUNCTIONS" in events, "adk_events.py should define INTERNAL_FUNCTIONS"
        assert '"transfer_to_agent"' in events, "Should filter transfer_to_agent"
        assert '"transfer"' in events, "Should filter transfer"

    def test_adk_event_shape_lives_in_one_module(self, app_dir):
        """Only services/adk_events.py may read the ADK Event surface."""
        routes = app_dir / "backend" / "src" / "api" / "routes"
        for module in sorted(routes.glob("*.py")):
            content = module.read_text(encoding="utf-8")
            assert "get_function_calls" not in content, module.name
            assert "get_function_responses" not in content, module.name
            # The attributes the old loops guessed at do not exist on Event.
            assert "event.tool_calls" not in content, module.name
            assert "event.agent_name" not in content, module.name

    def test_runners_receive_the_memory_service(self, app_dir):
        """Runner(memory_service=...) is what gives the agents ADK's load_memory."""
        for route_file in ("chat.py", "investigations.py"):
            content = (app_dir / "backend" / "src" / "api" / "routes" / route_file).read_text(
                encoding="utf-8"
            )
            assert "memory_service=memory_service.adk_memory_service" in content, route_file

    def test_investigations_are_persisted_not_in_a_dict(self, app_dir):
        content = (app_dir / "backend" / "src" / "api" / "routes" / "investigations.py").read_text(
            encoding="utf-8"
        )
        assert "_investigations: dict" not in content, "process-local investigation store removed"
        assert "neo4j_service.list_investigations" in content
        assert "neo4j_service.update_investigation" in content
        assert "get_trace_with_steps" in content, "audit trail comes from the reasoning trace"

    def test_domain_reads_use_the_portable_query_accessor(self, app_dir):
        content = (app_dir / "backend" / "src" / "services" / "neo4j_service.py").read_text(
            encoding="utf-8"
        )
        assert "self._client.query.cypher" in content, "reads should use client.query.cypher"
        assert "execute_read" not in content, "client.graph.execute_read is deprecated"

    def test_memory_graph_includes_memory_labels(self, app_dir):
        content = (app_dir / "backend" / "src" / "services" / "neo4j_service.py").read_text(
            encoding="utf-8"
        )
        for label in ("Conversation", "Message", "Entity", "ReasoningTrace", "ToolCall"):
            assert label in content, f"get_memory_graph should be able to return :{label}"
        assert "$session_id" in content, "get_memory_graph should honour session_id"
        assert "TOUCHED" in content, "the audit edge should be queryable"

    def test_traces_route_has_endpoints(self, app_dir):
        content = (app_dir / "backend" / "src" / "api" / "routes" / "traces.py").read_text(
            encoding="utf-8"
        )
        assert "get_session_traces" in content, "traces.py should have get_session_traces"
        assert "get_trace_detail" in content, "traces.py should have get_trace_detail"
        assert "reasoning" in content, "traces.py should use reasoning layer"
        assert "list_traces" in content, "traces.py should call list_traces"
        assert "get_trace" in content, "traces.py should call get_trace"

    def test_routes_init_exports_traces(self, app_dir):
        content = (app_dir / "backend" / "src" / "api" / "routes" / "__init__.py").read_text(
            encoding="utf-8"
        )
        assert "traces" in content, "__init__.py should export traces module"

    def test_neo4j_service_uses_merge_for_alerts(self, app_dir):
        content = (app_dir / "backend" / "src" / "services" / "neo4j_service.py").read_text(
            encoding="utf-8"
        )
        assert "MERGE (a:Alert" in content, "create_alert should use MERGE"
        assert "ON CREATE SET" in content, "create_alert should use ON CREATE SET"
        assert "import uuid" in content, "Should import uuid for alert IDs"

    def test_neo4j_service_uses_where_not_and(self, app_dir):
        """Verify get_transactions uses WHERE (not AND) for optional filters."""
        content = (app_dir / "backend" / "src" / "services" / "neo4j_service.py").read_text(
            encoding="utf-8"
        )
        # The dynamic filter should use WHERE with AND-joined filter list
        assert 'where_extra = "WHERE "' in content, "get_transactions should use WHERE"

    def test_no_hardcoded_sample_data_in_tools(self, app_dir):
        for tool_file in [
            "kyc_tools.py",
            "aml_tools.py",
            "relationship_tools.py",
            "compliance_tools.py",
        ]:
            content = (app_dir / "backend" / "src" / "tools" / tool_file).read_text(
                encoding="utf-8"
            )
            assert "SAMPLE_CUSTOMERS" not in content, f"{tool_file} still has SAMPLE_CUSTOMERS"
            assert "SAMPLE_TRANSACTIONS" not in content, (
                f"{tool_file} still has SAMPLE_TRANSACTIONS"
            )
            assert "SAMPLE_NETWORK" not in content, f"{tool_file} still has SAMPLE_NETWORK"

    def test_no_hardcoded_sample_data_in_routes(self, app_dir):
        for route_file in ["customers.py", "alerts.py", "investigations.py"]:
            content = (app_dir / "backend" / "src" / "api" / "routes" / route_file).read_text(
                encoding="utf-8"
            )
            assert "SAMPLE_CUSTOMERS" not in content, f"{route_file} still has SAMPLE_CUSTOMERS"

    # -- Frontend structure --------------------------------------------------

    def test_frontend_has_agent_stream_hook(self):
        hook = APP_DIR / "frontend" / "src" / "hooks" / "useAgentStream.ts"
        assert hook.exists(), "useAgentStream.ts should exist"
        content = hook.read_text(encoding="utf-8")
        assert "useAgentStream" in content, "Should export useAgentStream"
        assert "agentStates" in content, "Should track agentStates"
        assert "streamChatMessage" in content, "Should use streamChatMessage from api"

    def test_frontend_has_orchestration_view(self):
        comp = APP_DIR / "frontend" / "src" / "components" / "Chat" / "AgentOrchestrationView.tsx"
        assert comp.exists()
        content = comp.read_text(encoding="utf-8")
        assert "framer-motion" in content or "motion" in content, "Should use framer-motion"
        assert "AnimatePresence" in content, "Should use AnimatePresence"

    def test_frontend_has_activity_timeline(self):
        comp = APP_DIR / "frontend" / "src" / "components" / "Chat" / "AgentActivityTimeline.tsx"
        assert comp.exists()
        content = comp.read_text(encoding="utf-8")
        assert "Timeline" in content, "Should use Chakra Timeline"

    def test_frontend_has_tool_call_card(self):
        comp = APP_DIR / "frontend" / "src" / "components" / "Chat" / "ToolCallCard.tsx"
        assert comp.exists()
        content = comp.read_text(encoding="utf-8")
        assert "formatValue" in content, "Should have formatValue helper"

    def test_frontend_has_memory_access_indicator(self):
        comp = APP_DIR / "frontend" / "src" / "components" / "Chat" / "MemoryAccessIndicator.tsx"
        assert comp.exists()

    def test_frontend_api_has_streaming(self):
        api = APP_DIR / "frontend" / "src" / "lib" / "api.ts"
        content = api.read_text(encoding="utf-8")
        assert "streamChatMessage" in content, "api.ts should have streamChatMessage"
        assert "getSessionTraces" in content, "api.ts should have getSessionTraces"
        assert "AgentEvent" in content, "api.ts should define AgentEvent type"

    def test_frontend_package_has_an_animation_library(self):
        """`motion` is the current package name; `framer-motion` was its predecessor."""
        pkg = APP_DIR / "frontend" / "package.json"
        content = pkg.read_text(encoding="utf-8")
        assert '"motion"' in content or '"framer-motion"' in content, (
            "package.json should include motion (or legacy framer-motion)"
        )


# ============================================================================
# Shared sample data (consumed by BOTH the AWS and GCP apps)
#
# One dataset, one loader, two backends. These assertions pin the invariants
# the demo's narrative depends on — remove the 4x $9,500 cash deposits and the
# structuring walkthrough has nothing to find — plus the two loader properties
# that were outright bugs: an unconditional `MATCH (n) DETACH DELETE n`, and
# transaction dates stored as strings so every 90-day AML window came back
# empty.
# ============================================================================

SHARED_DATA_DIR = EXAMPLES_DIR / "financial-services-advisor" / "data"
LOADER = SHARED_DATA_DIR / "load_sample_data.py"


def _shared_json(name: str):
    return json.loads((SHARED_DATA_DIR / name).read_text(encoding="utf-8"))


class TestSharedSampleDataShape:
    """Every fixture file parses and carries the fields the loader reads."""

    @pytest.mark.parametrize(
        "name",
        [
            "customers.json",
            "organizations.json",
            "transactions.json",
            "sanctions.json",
            "pep.json",
            "alerts.json",
        ],
    )
    def test_fixture_parses(self, name):
        assert _shared_json(name)

    def test_customers(self):
        customers = _shared_json("customers.json")
        assert len(customers) == 3
        assert {c["id"] for c in customers} == {"CUST-001", "CUST-002", "CUST-003"}
        for customer in customers:
            assert {"id", "name", "type", "documents", "kyc_status"} <= customer.keys()
            assert customer["type"] in {"individual", "corporate"}

    def test_document_dates_are_relative_not_absolute(self):
        """A shipped fixture with absolute expiry dates ages into "everything
        expired". Expiries are offsets from the load date."""
        for customer in _shared_json("customers.json"):
            for doc_type, info in customer["documents"].items():
                assert "expiry" not in info, f"{customer['id']}/{doc_type} has an absolute expiry"
                if "expiry_days" in info:
                    assert info["expiry_days"] > 0

    def test_transactions_use_relative_offsets(self):
        """``days_ago`` rather than a 2024 date, so the AML windows keep matching."""
        transactions = _shared_json("transactions.json")
        assert len(transactions) == 16
        for txn in transactions:
            assert "days_ago" in txn, f"{txn['id']} still carries an absolute date"
            assert isinstance(txn["days_ago"], int)

    def test_newest_transaction_is_inside_a_90_day_window(self):
        """The acceptance criterion for the temporal fix: ``get_transactions``
        defaults to ``days=90``, so the fixture must have rows inside it."""
        offsets = [txn["days_ago"] for txn in _shared_json("transactions.json")]
        assert min(offsets) < 90
        assert max(offsets) < 90, "some transactions fall outside every default window"

    def test_structuring_pattern_survives(self):
        """Four cash deposits in [9000, 10000) for CUST-003 — the sub-$10K CTR
        threshold pattern ``detect_structuring`` looks for."""
        deposits = [
            txn
            for txn in _shared_json("transactions.json")
            if txn["customer_id"] == "CUST-003"
            and txn["type"] == "cash_deposit"
            and 9000 <= txn["amount"] < 10000
        ]
        assert len(deposits) == 4
        # Consecutive days, which is what makes it a pattern rather than a coincidence.
        offsets = sorted(txn["days_ago"] for txn in deposits)
        assert offsets == list(range(offsets[0], offsets[0] + 4))

    def test_rapid_movement_pair_within_two_days(self):
        """``detect_rapid_movement`` correlates a wire_in with a wire_out of
        90-100% of the amount inside two days."""
        transactions = _shared_json("transactions.json")
        pairs = [
            (wire_in, wire_out)
            for wire_in in transactions
            for wire_out in transactions
            if wire_in["customer_id"] == wire_out["customer_id"]
            and wire_in["type"] == "wire_in"
            and wire_out["type"] == "wire_out"
            and 0 <= wire_in["days_ago"] - wire_out["days_ago"] <= 2
            and wire_in["amount"] * 0.9 <= wire_out["amount"] <= wire_in["amount"]
        ]
        assert pairs, "no wire-in/wire-out pair inside the two-day window"

    def test_offshore_counterparties_exist_for_layering(self):
        counterparties = " ".join(
            str(txn.get("counterparty", "")) for txn in _shared_json("transactions.json")
        )
        assert any(
            token in counterparties
            for token in ("Cayman", "Seychelles", "Panama", "Offshore", "Shell Corp")
        )

    def test_at_least_one_organization_has_shell_indicators(self):
        orgs = _shared_json("organizations.json")
        assert [org for org in orgs if org.get("shell_indicators")]

    def test_alert_transaction_ids_resolve(self):
        """A dangling transaction_id means an alert renders with no evidence."""
        known = {txn["id"] for txn in _shared_json("transactions.json")}
        for alert in _shared_json("alerts.json"):
            for txn_id in alert.get("transaction_ids", []):
                assert txn_id in known, f"{alert['id']} references unknown {txn_id}"

    def test_alert_customer_ids_resolve(self):
        known = {customer["id"] for customer in _shared_json("customers.json")}
        for alert in _shared_json("alerts.json"):
            assert alert["customer_id"] in known

    def test_alert_evidence_has_no_absolute_dates(self):
        """Evidence strings used to quote 2024 dates the loader no longer writes."""
        for alert in _shared_json("alerts.json"):
            for line in alert.get("evidence", []):
                assert "2024-" not in line, f"{alert['id']} evidence quotes a stale date: {line}"

    def test_pep_relatives_point_at_a_known_pep(self):
        data = _shared_json("pep.json")
        names = {pep["name"] for pep in data["peps"]}
        assert len(names) >= 3
        for relative in data.get("pep_relatives", []):
            assert relative["pep"] in names


class TestSharedLoaderSafety:
    """Static guards on the loader. The behavioural versions live in the AWS
    backend's ``tests/test_integration.py``, which runs it against Neo4j."""

    def test_loader_exists_and_parses(self):
        ast.parse(LOADER.read_text(encoding="utf-8"))

    def test_loader_never_deletes_the_whole_database(self):
        """It used to run ``MATCH (n) DETACH DELETE n`` unconditionally, on every
        load, destroying every :Conversation / :Message / :Entity /
        :ReasoningTrace the library had written."""
        source = LOADER.read_text(encoding="utf-8")
        assert "MATCH (n) DETACH DELETE n" not in source
        assert "--reset" in source, "the destructive path must be behind an explicit flag"

    def test_reset_is_scoped_to_the_demo_labels(self):
        source = LOADER.read_text(encoding="utf-8")
        assert "DEMO_LABELS" in source
        for label in ("Customer", "Transaction", "Alert", "Organization"):
            assert f'"{label}"' in source
        # ...and nothing the library owns.
        for label in ("Conversation", "Message", "ReasoningTrace", "ToolCall"):
            assert f'"{label}"' not in source, f"{label} must never be in the delete scope"

    def test_loader_is_idempotent_by_construction(self):
        """Every write is a MERGE, so a second run changes nothing. The old
        loader used CREATE and silently duplicated on a re-run."""
        source = LOADER.read_text(encoding="utf-8")
        assert "MERGE (c:Customer" in source
        assert "MERGE (t:Transaction" in source
        assert "MERGE (a:Alert" in source
        assert "CREATE (t:Transaction" not in source
        assert "CREATE (s:SanctionedEntity" not in source

    def test_loader_stores_dates_as_dates(self):
        """String dates made ``t.date >= date() - duration(...)`` evaluate to null."""
        source = LOADER.read_text(encoding="utf-8")
        assert "t.date = date(row.date)" in source

    def test_loader_batches_instead_of_looping_round_trips(self):
        source = LOADER.read_text(encoding="utf-8")
        assert source.count("UNWIND $rows AS row") >= 8
        assert "execute_write" in source, "the load should be one transaction, not autocommit"

    def test_loader_does_not_interpolate_labels_from_data(self):
        """``CREATE (c:Customer:{label} ...)`` took its label from a JSON field."""
        source = LOADER.read_text(encoding="utf-8")
        assert "Customer:{label}" not in source

    def test_loader_accepts_both_credential_spellings(self):
        source = LOADER.read_text(encoding="utf-8")
        assert "NEO4J_USERNAME" in source and "NEO4J_USER" in source

    def test_loader_refuses_to_guess_a_password(self):
        source = LOADER.read_text(encoding="utf-8")
        assert 'os.environ.get("NEO4J_PASSWORD", "password")' not in source
        assert "No Neo4j password" in source

    def test_loader_offers_the_adoption_phase(self):
        """``adopt_existing_graph`` is the feature this dataset exists to show:
        a pre-existing domain graph becoming long-term memory."""
        source = LOADER.read_text(encoding="utf-8")
        assert "adopt_existing_graph" in source
        assert "SchemaModel.CUSTOM" in source
        assert "AdoptionReport" in source or "report.total_migrated" in source

    def test_loader_marks_every_node_with_the_compliance_label(self):
        """The marker keeps the domain namespace apart from the ``:Entity``
        nodes extraction writes, which land on ``:Person`` / ``:Organization``
        too."""
        source = LOADER.read_text(encoding="utf-8")
        assert 'MARKER = "Compliance"' in source
        assert source.count("{MARKER}") >= 8

    def test_loader_is_annotated(self):
        """It sits in the mypy/ty target list now, so `main()` needs a return type."""
        source = LOADER.read_text(encoding="utf-8")
        assert "def main() -> None:" in source
