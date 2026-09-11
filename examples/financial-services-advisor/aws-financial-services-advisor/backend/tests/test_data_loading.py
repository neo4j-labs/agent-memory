"""Validation tests for sample data files and load script.

These tests verify data structure and content without requiring Neo4j.
"""

from __future__ import annotations

import ast
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[3] / "data"


class TestCustomersData:
    def test_customers_file_exists(self):
        assert (DATA_DIR / "customers.json").exists()

    def test_customers_has_three_entries(self, sample_customers):
        assert len(sample_customers) == 3

    def test_customers_have_required_fields(self, sample_customers):
        required = {"id", "name", "type", "documents", "kyc_status"}
        for c in sample_customers:
            assert required.issubset(c.keys()), f"Customer {c.get('id')} missing fields"

    def test_customer_ids_are_unique(self, sample_customers):
        ids = [c["id"] for c in sample_customers]
        assert len(ids) == len(set(ids))

    def test_customer_types_valid(self, sample_customers):
        valid_types = {"individual", "corporate"}
        for c in sample_customers:
            assert c["type"] in valid_types

    def test_cust001_is_low_risk(self, sample_customers):
        cust = next(c for c in sample_customers if c["id"] == "CUST-001")
        assert cust["risk_factors"] == []
        assert cust["kyc_status"] == "approved"

    def test_cust003_is_high_risk_corporate(self, sample_customers):
        cust = next(c for c in sample_customers if c["id"] == "CUST-003")
        assert cust["type"] == "corporate"
        assert "offshore_jurisdiction" in cust["risk_factors"]
        assert "nominee_directors" in cust["risk_factors"]
        assert cust["kyc_status"] == "under_review"


class TestTransactionsData:
    def test_transactions_file_exists(self):
        assert (DATA_DIR / "transactions.json").exists()

    def test_transactions_count(self, sample_transactions):
        assert len(sample_transactions) == 16

    def test_structuring_pattern_exists(self, sample_transactions):
        """CUST-003 should have 4x $9,500 cash deposits (structuring)."""
        structuring = [
            t
            for t in sample_transactions
            if t["customer_id"] == "CUST-003"
            and t["type"] == "cash_deposit"
            and t["amount"] == 9500
        ]
        assert len(structuring) == 4

    def test_transactions_have_required_fields(self, sample_transactions):
        required = {"id", "customer_id", "days_ago", "type", "amount"}
        for t in sample_transactions:
            assert required.issubset(t.keys()), f"Transaction {t.get('id')} missing fields"

    def test_transaction_ids_unique(self, sample_transactions):
        ids = [t["id"] for t in sample_transactions]
        assert len(ids) == len(set(ids))


class TestOrganizationsData:
    def test_organizations_file_exists(self):
        assert (DATA_DIR / "organizations.json").exists()

    def test_shell_companies_have_indicators(self, sample_organizations):
        shell_orgs = [o for o in sample_organizations if o.get("shell_indicators")]
        assert len(shell_orgs) >= 2, "Should have at least 2 orgs with shell indicators"

    def test_org_ids_unique(self, sample_organizations):
        ids = [o["id"] for o in sample_organizations]
        assert len(ids) == len(set(ids))


class TestAlertsData:
    def test_alerts_file_exists(self):
        assert (DATA_DIR / "alerts.json").exists()

    def test_alerts_have_required_fields(self, sample_alerts):
        required = {"id", "customer_id", "type", "severity", "status", "title"}
        for a in sample_alerts:
            assert required.issubset(a.keys()), f"Alert {a.get('id')} missing fields"

    def test_critical_alert_exists(self, sample_alerts):
        critical = [a for a in sample_alerts if a["severity"] == "CRITICAL"]
        assert len(critical) >= 1

    def test_structuring_alert_links_to_transactions(self, sample_alerts):
        structuring_alert = next(
            (a for a in sample_alerts if "Structuring" in a.get("title", "")), None
        )
        assert structuring_alert is not None
        assert len(structuring_alert["transaction_ids"]) == 4


class TestSanctionsAndPEPData:
    def test_sanctions_file_exists(self):
        assert (DATA_DIR / "sanctions.json").exists()

    def test_pep_file_exists(self):
        assert (DATA_DIR / "pep.json").exists()

    def test_sanctions_have_aliases(self, sample_sanctions):
        with_aliases = [s for s in sample_sanctions if s.get("aliases")]
        assert len(with_aliases) >= 1

    def test_pep_data_structure(self, sample_pep_data):
        assert "peps" in sample_pep_data
        assert "pep_relatives" in sample_pep_data
        assert len(sample_pep_data["peps"]) >= 3


class TestLoadScript:
    def test_load_script_exists(self):
        assert (DATA_DIR / "load_sample_data.py").exists()

    def test_load_script_has_no_syntax_errors(self):
        script = (DATA_DIR / "load_sample_data.py").read_text()
        ast.parse(script)  # Raises SyntaxError if invalid

    def test_load_script_has_main_function(self):
        script = (DATA_DIR / "load_sample_data.py").read_text()
        assert "def main(" in script

    def test_load_script_creates_constraints(self):
        script = (DATA_DIR / "load_sample_data.py").read_text()
        assert "CREATE CONSTRAINT" in script
