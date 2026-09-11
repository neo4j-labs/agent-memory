"""Unit tests for tool functions (KYC, AML, Relationship, Compliance).

All tools are tested with a mocked Neo4jDomainService.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.services.neo4j_service import Neo4jDomainService


@pytest.fixture
def mock_neo4j() -> AsyncMock:
    """Mocked Neo4jDomainService with common methods."""
    svc = AsyncMock(spec=Neo4jDomainService)
    svc._graph = AsyncMock()
    return svc


# ── KYC Tools ─────────────────────────────────────────────────────────


class TestKYCTools:
    @pytest.mark.asyncio
    async def test_verify_identity_found_verified(self, mock_neo4j, customer_john_smith):
        from src.tools.kyc_tools import verify_identity

        mock_neo4j.get_customer.return_value = customer_john_smith
        result = await verify_identity("CUST-001", neo4j_service=mock_neo4j)
        assert result["status"] == "VERIFIED"
        assert result["verified"] is True
        assert result["missing_documents"] == []

    @pytest.mark.asyncio
    async def test_verify_identity_not_found(self, mock_neo4j):
        from src.tools.kyc_tools import verify_identity

        mock_neo4j.get_customer.return_value = None
        result = await verify_identity("CUST-999", neo4j_service=mock_neo4j)
        assert result["status"] == "NOT_FOUND"
        assert result["verified"] is False

    @pytest.mark.asyncio
    async def test_verify_identity_pending_docs(self, mock_neo4j, customer_global_holdings):
        from src.tools.kyc_tools import verify_identity

        mock_neo4j.get_customer.return_value = customer_global_holdings
        result = await verify_identity("CUST-003", neo4j_service=mock_neo4j)
        assert result["status"] == "PENDING"
        assert "register_of_directors" in result["missing_documents"]
        assert "proof_of_address" in result["missing_documents"]

    @pytest.mark.asyncio
    async def test_check_documents_all(self, mock_neo4j, customer_john_smith):
        from src.tools.kyc_tools import check_documents

        mock_neo4j.get_customer.return_value = customer_john_smith
        result = await check_documents("CUST-001", neo4j_service=mock_neo4j)
        assert result["total_documents"] == 2

    @pytest.mark.asyncio
    async def test_check_documents_specific_type(self, mock_neo4j, customer_john_smith):
        from src.tools.kyc_tools import check_documents

        mock_neo4j.get_customer.return_value = customer_john_smith
        result = await check_documents(
            "CUST-001", document_type="passport", neo4j_service=mock_neo4j
        )
        assert result["document_type"] == "passport"
        assert result["status"] == "VERIFIED"

    @pytest.mark.asyncio
    async def test_assess_customer_risk_low(self, mock_neo4j, customer_john_smith):
        from src.tools.kyc_tools import assess_customer_risk

        mock_neo4j.get_customer.return_value = customer_john_smith
        result = await assess_customer_risk("CUST-001", neo4j_service=mock_neo4j)
        assert result["risk_level"] == "LOW"
        assert result["risk_score"] == 20  # base score only

    @pytest.mark.asyncio
    async def test_assess_customer_risk_high(self, mock_neo4j, customer_global_holdings):
        from src.tools.kyc_tools import assess_customer_risk

        mock_neo4j.get_customer.return_value = customer_global_holdings
        result = await assess_customer_risk("CUST-003", neo4j_service=mock_neo4j)
        assert result["risk_level"] in ("HIGH", "CRITICAL")
        assert result["risk_score"] > 50

    @pytest.mark.asyncio
    async def test_check_adverse_media_clean(self, mock_neo4j, customer_john_smith):
        from src.tools.kyc_tools import check_adverse_media

        mock_neo4j.get_customer.return_value = customer_john_smith
        result = await check_adverse_media("CUST-001", neo4j_service=mock_neo4j)
        assert result["hits_found"] == 0
        assert result["risk_indicator"] == "LOW"

    @pytest.mark.asyncio
    async def test_check_adverse_media_hit(self, mock_neo4j, customer_global_holdings):
        from src.tools.kyc_tools import check_adverse_media

        mock_neo4j.get_customer.return_value = customer_global_holdings
        result = await check_adverse_media("CUST-003", neo4j_service=mock_neo4j)
        assert result["hits_found"] == 1
        assert result["risk_indicator"] == "HIGH"


# ── AML Tools ─────────────────────────────────────────────────────────


class TestAMLTools:
    @pytest.mark.asyncio
    async def test_scan_transactions_found(self, mock_neo4j):
        from src.tools.aml_tools import scan_transactions

        mock_neo4j.get_transactions.return_value = [
            {"id": "TXN-001", "amount": 5000, "type": "deposit", "counterparty": "Employer"},
            {"id": "TXN-002", "amount": 1500, "type": "withdrawal", "counterparty": "Landlord"},
        ]
        result = await scan_transactions("CUST-001", neo4j_service=mock_neo4j)
        assert result["transaction_count"] == 2
        assert result["total_volume"] == 6500

    @pytest.mark.asyncio
    async def test_scan_transactions_empty(self, mock_neo4j):
        from src.tools.aml_tools import scan_transactions

        mock_neo4j.get_transactions.return_value = []
        result = await scan_transactions("CUST-999", neo4j_service=mock_neo4j)
        assert result["status"] == "NO_TRANSACTIONS"

    @pytest.mark.asyncio
    async def test_detect_patterns_structuring(self, mock_neo4j):
        from src.tools.aml_tools import detect_patterns

        mock_neo4j.get_transactions.return_value = [{"id": "TXN-203"}]
        mock_neo4j.detect_structuring.return_value = [
            {"id": "TXN-203", "amount": 9500},
            {"id": "TXN-204", "amount": 9500},
            {"id": "TXN-205", "amount": 9500},
            {"id": "TXN-206", "amount": 9500},
        ]
        mock_neo4j.detect_rapid_movement.return_value = []
        mock_neo4j.detect_layering.return_value = []

        result = await detect_patterns("CUST-003", neo4j_service=mock_neo4j)
        patterns = result["patterns_detected"]
        assert any(p["pattern"] == "STRUCTURING" for p in patterns)
        assert result["overall_risk"] == "HIGH"

    @pytest.mark.asyncio
    async def test_detect_patterns_clean(self, mock_neo4j):
        from src.tools.aml_tools import detect_patterns

        mock_neo4j.get_transactions.return_value = [{"id": "TXN-001"}]
        mock_neo4j.detect_structuring.return_value = []
        mock_neo4j.detect_rapid_movement.return_value = []
        mock_neo4j.detect_layering.return_value = []

        result = await detect_patterns("CUST-001", neo4j_service=mock_neo4j)
        assert result["patterns_detected"] == []
        assert result["overall_risk"] == "LOW"

    @pytest.mark.asyncio
    async def test_flag_suspicious_transaction(self, mock_neo4j):
        from src.tools.aml_tools import flag_suspicious_transaction

        mock_neo4j.get_transaction.return_value = {
            "customer_id": "CUST-003",
            "transaction": {"id": "TXN-201", "amount": 250000, "type": "wire_in"},
        }
        mock_neo4j.create_alert.return_value = {"id": "ALERT-NEW", "severity": "HIGH"}

        result = await flag_suspicious_transaction(
            "TXN-201", "Large offshore wire", severity="HIGH", neo4j_service=mock_neo4j
        )
        assert result["status"] == "FLAGGED"
        assert result["customer_id"] == "CUST-003"

    @pytest.mark.asyncio
    async def test_analyze_velocity_anomalies(self, mock_neo4j):
        from src.tools.aml_tools import analyze_velocity

        mock_neo4j.get_velocity_metrics.return_value = {
            "total_transactions": 7,
            "total_volume": 400000,
            "average_transaction": 57142,
            "transactions_by_type": {"cash_deposit": 4, "wire_in": 1, "wire_out": 2},
            "volume_by_type": {"cash_deposit": 38000, "wire_in": 250000, "wire_out": 283000},
        }
        mock_neo4j.get_large_transactions.return_value = ["TXN-201"]

        result = await analyze_velocity("CUST-003", neo4j_service=mock_neo4j)
        assert len(result["anomalies_detected"]) >= 1
        assert result["velocity_risk"] in ("MEDIUM", "HIGH")


# ── Relationship Tools ────────────────────────────────────────────────


class TestRelationshipTools:
    @pytest.mark.asyncio
    async def test_find_connections_found(self, mock_neo4j):
        from src.tools.relationship_tools import find_connections

        mock_neo4j.get_node_summary.return_value = {
            "id": "CUST-003",
            "name": "Global Holdings",
            "type": "corporate",
            "labels": ["Customer"],
        }
        mock_neo4j.find_connections.return_value = {
            "entity_id": "CUST-003",
            "connections": [
                {
                    "entity": {"id": "ORG-002", "name": "Shell Corp", "jurisdiction": "KY"},
                    "distance": 1,
                    "rel_types": ["CONNECTED_TO"],
                },
            ],
        }

        result = await find_connections("CUST-003", neo4j_service=mock_neo4j)
        assert result["connections_found"] == 1
        assert result["connections"][0]["name"] == "Shell Corp"

    @pytest.mark.asyncio
    async def test_find_connections_not_found(self, mock_neo4j):
        from src.tools.relationship_tools import find_connections

        mock_neo4j.get_node_summary.return_value = None
        result = await find_connections("UNKNOWN", neo4j_service=mock_neo4j)
        assert result["status"] == "NOT_FOUND"

    @pytest.mark.asyncio
    async def test_analyze_network_risk(self, mock_neo4j):
        from src.tools.relationship_tools import analyze_network_risk

        mock_neo4j.get_network_risk.return_value = {
            "network_risk_score": 65,
            "risk_level": "HIGH",
            "risk_factors": ["HIGH_RISK_JURISDICTION: Shell Corp (KY)"],
            "total_connections": 3,
        }
        mock_neo4j.get_node_summary.return_value = {"id": "CUST-003", "name": "Global Holdings"}

        result = await analyze_network_risk("CUST-003", neo4j_service=mock_neo4j)
        assert result["risk_level"] == "HIGH"
        assert result["network_risk_score"] == 65

    @pytest.mark.asyncio
    async def test_detect_shell_companies(self, mock_neo4j):
        from src.tools.relationship_tools import detect_shell_companies

        mock_neo4j.get_node_summary.return_value = {
            "id": "CUST-003",
            "name": "Global Holdings",
            "type": "corporate",
            "shell_indicators": None,
        }
        mock_neo4j.detect_shell_companies.return_value = [
            {
                "id": "ORG-002",
                "name": "Shell Corp",
                "jurisdiction": "KY",
                "shell_indicators": ["no_employees", "po_box_address"],
            },
        ]

        result = await detect_shell_companies("CUST-003", neo4j_service=mock_neo4j)
        assert result["shell_companies_detected"] == 1
        assert result["risk_level"] == "CRITICAL"

    @pytest.mark.asyncio
    async def test_map_beneficial_ownership_no_ubo(self, mock_neo4j):
        from src.tools.relationship_tools import map_beneficial_ownership

        mock_neo4j.get_node_summary.return_value = {
            "id": "CUST-003",
            "name": "Global Holdings",
            "type": "corporate",
        }
        mock_neo4j.trace_ownership.return_value = {
            "entity_id": "CUST-003",
            "ownership_chains": [],
            "ubo_identified": False,
        }

        result = await map_beneficial_ownership("CUST-003", neo4j_service=mock_neo4j)
        assert result["ubo_identified"] is False
        assert result["transparency_risk"] == "HIGH"
        assert len(result["opaque_indicators"]) > 0


# ── Compliance Tools ──────────────────────────────────────────────────


class TestComplianceTools:
    @pytest.mark.asyncio
    async def test_check_sanctions_clear(self, mock_neo4j):
        from src.tools.compliance_tools import check_sanctions

        mock_neo4j.check_sanctions.return_value = []
        result = await check_sanctions("John Smith", neo4j_service=mock_neo4j)
        assert result["screening_status"] == "CLEAR"
        assert result["risk_level"] == "LOW"

    @pytest.mark.asyncio
    async def test_check_sanctions_hit(self, mock_neo4j):
        from src.tools.compliance_tools import check_sanctions

        mock_neo4j.check_sanctions.return_value = [
            {
                "entity": {
                    "name": "Ivan Petrov",
                    "list": "OFAC SDN",
                    "reason": "Russian sanctions",
                },
                "match_type": "EXACT",
                "confidence": 1.0,
            },
        ]
        result = await check_sanctions("Ivan Petrov", neo4j_service=mock_neo4j)
        assert result["screening_status"] == "HIT"
        assert result["risk_level"] == "CRITICAL"
        assert result["requires_escalation"] is True

    @pytest.mark.asyncio
    async def test_verify_pep_status_clear(self, mock_neo4j):
        from src.tools.compliance_tools import verify_pep_status

        mock_neo4j.check_pep.return_value = []
        result = await verify_pep_status("John Smith", neo4j_service=mock_neo4j)
        assert result["pep_status"] == "CLEAR"
        assert result["is_pep"] is False

    @pytest.mark.asyncio
    async def test_verify_pep_status_confirmed(self, mock_neo4j):
        from src.tools.compliance_tools import verify_pep_status

        mock_neo4j.check_pep.return_value = [
            {
                "pep": {
                    "name": "Carlos Rodriguez",
                    "position": "Minister",
                    "country": "MX",
                    "tier": 1,
                },
                "match_type": "DIRECT_PEP",
                "confidence": 1.0,
            },
        ]
        result = await verify_pep_status("Carlos Rodriguez", neo4j_service=mock_neo4j)
        assert result["pep_status"] == "PEP_CONFIRMED"
        assert result["is_pep"] is True
        assert result["enhanced_due_diligence_required"] is True

    @pytest.mark.asyncio
    async def test_generate_sar_report(self, mock_neo4j):
        from src.tools.compliance_tools import generate_sar_report

        mock_neo4j.get_customer.return_value = {"name": "Global Holdings Ltd"}
        result = await generate_sar_report(
            "CUST-003",
            "structuring",
            transaction_ids=["TXN-203", "TXN-204"],
            neo4j_service=mock_neo4j,
        )
        assert result["status"] == "SAR_DRAFT_CREATED"
        assert result["sar_document"]["suspicious_activity"]["activity_code"] == "31"
        assert (
            result["sar_document"]["subject_information"]["customer_name"] == "Global Holdings Ltd"
        )

    @pytest.mark.asyncio
    async def test_assess_regulatory_requirements_us(self, mock_neo4j):
        from src.tools.compliance_tools import assess_regulatory_requirements

        mock_neo4j.get_customer.return_value = {"nationality": "US"}
        mock_neo4j.get_transactions.return_value = [{"type": "cash_deposit"}, {"type": "wire_out"}]

        result = await assess_regulatory_requirements("CUST-001", neo4j_service=mock_neo4j)
        reg_names = [r["regulation"] for r in result["applicable_regulations"]]
        assert "Bank Secrecy Act (BSA)" in reg_names
        assert "FATF Recommendations" in reg_names

    @pytest.mark.asyncio
    async def test_assess_regulatory_requirements_high_risk(self, mock_neo4j):
        from src.tools.compliance_tools import assess_regulatory_requirements

        mock_neo4j.get_customer.return_value = {"jurisdiction": "BVI"}
        mock_neo4j.get_transactions.return_value = [{"type": "wire_out"}]

        result = await assess_regulatory_requirements(
            "CUST-003",
            jurisdictions=["BVI"],
            neo4j_service=mock_neo4j,
        )
        reg_names = [r["regulation"] for r in result["applicable_regulations"]]
        assert "Enhanced Due Diligence" in reg_names
