"""Unit tests for Neo4jDomainService.

Tests all domain data query methods with a mocked Neo4j graph client.
"""

from __future__ import annotations

import pytest

from src.services.neo4j_service import Neo4jDomainService


@pytest.fixture
def svc(mock_memory_client) -> Neo4jDomainService:
    return Neo4jDomainService(mock_memory_client)


# ── Customers ─────────────────────────────────────────────────────────


class TestCustomerQueries:
    @pytest.mark.asyncio
    async def test_list_customers(self, svc, mock_memory_client):
        mock_memory_client.execute_read.return_value = [
            {
                "customer": {
                    "id": "CUST-001",
                    "name": "John Smith",
                    "type": "individual",
                    "documents": [],
                }
            },
        ]
        result = await svc.list_customers()
        assert len(result) == 1
        assert result[0]["id"] == "CUST-001"
        mock_memory_client.execute_read.assert_called_once()

    @pytest.mark.asyncio
    async def test_list_customers_with_type_filter(self, svc, mock_memory_client):
        mock_memory_client.execute_read.return_value = []
        await svc.list_customers(customer_type="corporate")
        call_args = mock_memory_client.execute_read.call_args
        assert call_args[0][1]["type"] == "corporate"

    @pytest.mark.asyncio
    async def test_get_customer_found(self, svc, mock_memory_client, customer_john_smith):
        mock_memory_client.execute_read.return_value = [{"customer": customer_john_smith}]
        result = await svc.get_customer("CUST-001")
        assert result is not None
        assert result["name"] == "John Smith"

    @pytest.mark.asyncio
    async def test_get_customer_not_found(self, svc, mock_memory_client):
        mock_memory_client.execute_read.return_value = []
        result = await svc.get_customer("CUST-999")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_customer_documents(self, svc, mock_memory_client):
        mock_memory_client.execute_read.return_value = [
            {"document": {"type": "passport", "status": "verified"}},
        ]
        result = await svc.get_customer_documents("CUST-001")
        assert len(result) == 1
        assert result[0]["type"] == "passport"


# ── Transactions ──────────────────────────────────────────────────────


class TestTransactionQueries:
    @pytest.mark.asyncio
    async def test_get_transactions(self, svc, mock_memory_client):
        mock_memory_client.execute_read.return_value = [
            {"transaction": {"id": "TXN-001", "amount": 5000, "type": "deposit"}},
        ]
        result = await svc.get_transactions("CUST-001")
        assert len(result) == 1
        assert result[0]["amount"] == 5000

    @pytest.mark.asyncio
    async def test_get_transactions_with_filters(self, svc, mock_memory_client):
        mock_memory_client.execute_read.return_value = []
        await svc.get_transactions("CUST-001", min_amount=1000, transaction_type="wire_in")
        call_args = mock_memory_client.execute_read.call_args
        params = call_args[0][1]
        assert params["min_amount"] == 1000
        assert params["tx_type"] == "wire_in"

    @pytest.mark.asyncio
    async def test_get_transaction_stats_empty(self, svc, mock_memory_client):
        mock_memory_client.execute_read.return_value = []
        result = await svc.get_transaction_stats("CUST-999")
        assert result["transaction_count"] == 0
        assert result["total_volume"] == 0

    @pytest.mark.asyncio
    async def test_get_transaction_stats(self, svc, mock_memory_client):
        mock_memory_client.execute_read.return_value = [
            {
                "transaction_count": 5,
                "total_volume": 25000,
                "total_deposits": 15000,
                "total_withdrawals": 10000,
                "average_transaction": 5000,
                "counterparties": ["Employer Payroll"],
                "transaction_types": ["deposit", "withdrawal"],
            }
        ]
        result = await svc.get_transaction_stats("CUST-001")
        assert result["transaction_count"] == 5

    @pytest.mark.asyncio
    async def test_detect_structuring(self, svc, mock_memory_client):
        mock_memory_client.execute_read.return_value = [
            {"transaction": {"id": "TXN-203", "amount": 9500, "type": "cash_deposit"}},
            {"transaction": {"id": "TXN-204", "amount": 9500, "type": "cash_deposit"}},
        ]
        result = await svc.detect_structuring("CUST-003")
        assert len(result) == 2
        assert all(t["amount"] == 9500 for t in result)

    @pytest.mark.asyncio
    async def test_detect_rapid_movement(self, svc, mock_memory_client):
        mock_memory_client.execute_read.return_value = [
            {
                "inbound": {"id": "TXN-101", "amount": 45000},
                "outbound": {"id": "TXN-102", "amount": 43000},
                "retained": 2000,
            }
        ]
        result = await svc.detect_rapid_movement("CUST-002")
        assert len(result) == 1
        assert result[0]["retained"] == 2000

    @pytest.mark.asyncio
    async def test_detect_layering(self, svc, mock_memory_client):
        mock_memory_client.execute_read.return_value = [
            {"transaction": {"id": "TXN-201", "counterparty": "Unknown Offshore Entity"}},
        ]
        result = await svc.detect_layering("CUST-003")
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_get_velocity_metrics(self, svc, mock_memory_client):
        mock_memory_client.execute_read.return_value = [
            {"tx_type": "deposit", "cnt": 3, "vol": 15000},
            {"tx_type": "withdrawal", "cnt": 1, "vol": 1500},
        ]
        result = await svc.get_velocity_metrics("CUST-001")
        assert result["total_transactions"] == 4
        assert result["total_volume"] == 16500
        assert "deposit" in result["transactions_by_type"]


# ── Network ───────────────────────────────────────────────────────────


class TestNetworkQueries:
    @pytest.mark.asyncio
    async def test_find_connections(self, svc, mock_memory_client):
        mock_memory_client.execute_read.return_value = [
            {
                "entity": {"id": "ORG-002", "name": "Shell Corp"},
                "distance": 1,
                "rel_types": ["CONNECTED_TO"],
            },
        ]
        result = await svc.find_connections("CUST-003")
        assert result["entity_id"] == "CUST-003"
        assert len(result["connections"]) == 1

    @pytest.mark.asyncio
    async def test_detect_shell_companies(self, svc, mock_memory_client):
        mock_memory_client.execute_read.return_value = [
            {
                "org": {
                    "id": "ORG-002",
                    "name": "Shell Corp",
                    "shell_indicators": ["no_employees", "po_box_address"],
                }
            },
        ]
        result = await svc.detect_shell_companies("CUST-003")
        assert len(result) == 1
        assert "no_employees" in result[0]["shell_indicators"]

    @pytest.mark.asyncio
    async def test_trace_ownership_with_owners(self, svc, mock_memory_client):
        mock_memory_client.execute_read.return_value = [
            {
                "owner": {"id": "CUST-002", "name": "Maria", "type": "individual"},
                "rel_types": ["OWNS"],
                "chain_length": 1,
            },
        ]
        result = await svc.trace_ownership("ORG-001")
        assert result["ubo_identified"] is True

    @pytest.mark.asyncio
    async def test_trace_ownership_no_owners(self, svc, mock_memory_client):
        mock_memory_client.execute_read.return_value = [
            {"owner": None, "rel_types": None, "chain_length": None}
        ]
        result = await svc.trace_ownership("ORG-003")
        assert result["ubo_identified"] is False

    @pytest.mark.asyncio
    async def test_network_risk_low(self, svc, mock_memory_client):
        mock_memory_client.execute_read.return_value = [
            {
                "entity": {
                    "id": "ORG-005",
                    "name": "Tech Corp",
                    "jurisdiction": "US-NY",
                    "shell_indicators": None,
                    "role": None,
                }
            },
        ]
        result = await svc.get_network_risk("CUST-001")
        assert result["risk_level"] == "LOW"
        assert result["network_risk_score"] == 0

    @pytest.mark.asyncio
    async def test_network_risk_high(self, svc, mock_memory_client):
        mock_memory_client.execute_read.return_value = [
            {
                "entity": {
                    "id": "ORG-002",
                    "name": "Shell Corp",
                    "jurisdiction": "KY",
                    "shell_indicators": ["no_employees"],
                    "role": None,
                }
            },
            {
                "entity": {
                    "id": "ORG-004",
                    "name": "Nominee",
                    "jurisdiction": "BVI",
                    "shell_indicators": None,
                    "role": "nominee_services",
                }
            },
        ]
        result = await svc.get_network_risk("CUST-003")
        assert result["risk_level"] in ("HIGH", "CRITICAL")
        assert result["network_risk_score"] >= 50


# ── Alerts ────────────────────────────────────────────────────────────


class TestAlertQueries:
    @pytest.mark.asyncio
    async def test_list_alerts(self, svc, mock_memory_client):
        mock_memory_client.execute_read.return_value = [
            {"alert": {"id": "ALERT-001", "severity": "CRITICAL", "status": "NEW"}},
        ]
        result = await svc.list_alerts()
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_list_alerts_with_filters(self, svc, mock_memory_client):
        mock_memory_client.execute_read.return_value = []
        await svc.list_alerts(severity="HIGH", customer_id="CUST-003")
        call_args = mock_memory_client.execute_read.call_args
        params = call_args[0][1]
        assert params["severity"] == "HIGH"
        assert params["customer_id"] == "CUST-003"

    @pytest.mark.asyncio
    async def test_create_alert(self, svc, mock_memory_client):
        mock_memory_client.execute_write.return_value = [
            {"alert": {"id": "ALERT-NEW", "severity": "HIGH", "customer_id": "CUST-003"}},
        ]
        result = await svc.create_alert(
            {
                "customer_id": "CUST-003",
                "title": "Suspicious Pattern",
                "severity": "HIGH",
            }
        )
        assert result["severity"] == "HIGH"
        mock_memory_client.execute_write.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_alert_summary_empty(self, svc, mock_memory_client):
        mock_memory_client.execute_read.return_value = []
        result = await svc.get_alert_summary()
        assert result["total"] == 0


# ── Sanctions & PEP ───────────────────────────────────────────────────


class TestSanctionsAndPEP:
    @pytest.mark.asyncio
    async def test_check_sanctions(self, svc, mock_memory_client):
        mock_memory_client.execute_read.return_value = [
            {
                "entity": {"name": "Ivan Petrov", "list": "OFAC SDN"},
                "match_type": "EXACT",
                "confidence": 1.0,
            },
        ]
        result = await svc.check_sanctions("Ivan Petrov")
        assert len(result) == 1
        assert result[0]["match_type"] == "EXACT"

    @pytest.mark.asyncio
    async def test_check_sanctions_no_match(self, svc, mock_memory_client):
        mock_memory_client.execute_read.return_value = []
        result = await svc.check_sanctions("Clean Person")
        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_check_pep(self, svc, mock_memory_client):
        mock_memory_client.execute_read.side_effect = [
            [
                {
                    "pep": {"name": "Carlos Rodriguez", "position": "Minister"},
                    "match_type": "DIRECT_PEP",
                    "confidence": 1.0,
                }
            ],
            [],  # relatives query
        ]
        result = await svc.check_pep("Carlos Rodriguez")
        assert len(result) == 1
        assert result[0]["match_type"] == "DIRECT_PEP"

    @pytest.mark.asyncio
    async def test_check_pep_relative(self, svc, mock_memory_client):
        mock_memory_client.execute_read.side_effect = [
            [],  # direct PEP
            [
                {
                    "pep": {"name": "Maria Rodriguez", "pep_name": "Carlos"},
                    "match_type": "PEP_RELATIVE",
                    "confidence": 0.95,
                }
            ],
        ]
        result = await svc.check_pep("Maria Rodriguez")
        assert len(result) == 1
        assert result[0]["match_type"] == "PEP_RELATIVE"


# ── Graph Stats ───────────────────────────────────────────────────────


class TestGraphStats:
    @pytest.mark.asyncio
    async def test_get_graph_stats(self, svc, mock_memory_client):
        mock_memory_client.execute_read.side_effect = [
            [{"label": "Customer", "count": 3}, {"label": "Transaction", "count": 16}],
            [
                {"label": "HAS_TRANSACTION", "count": 16}
            ],  # rels query returns "label" key but that's fine
        ]
        # Fix: the rel query returns "type" not "label"
        mock_memory_client.execute_read.side_effect = [
            [{"label": "Customer", "count": 3}, {"label": "Transaction", "count": 16}],
            [{"type": "HAS_TRANSACTION", "count": 16}],
        ]
        result = await svc.get_graph_stats()
        assert result["total_nodes"] == 19
        assert result["total_relationships"] == 16
        assert result["nodes_by_label"]["Customer"] == 3
