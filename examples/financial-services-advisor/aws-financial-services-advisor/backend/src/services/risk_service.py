"""Risk scoring service for customer assessment."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from ..models import CustomerRisk, RiskLevel

logger = logging.getLogger(__name__)

# High-risk jurisdictions (FATF gray/black list, common offshore)
HIGH_RISK_JURISDICTIONS = {
    "KP",  # North Korea
    "IR",  # Iran
    "MM",  # Myanmar
    "SY",  # Syria
    "YE",  # Yemen
    "AF",  # Afghanistan
}

MEDIUM_RISK_JURISDICTIONS = {
    "PA",  # Panama
    "BVI",  # British Virgin Islands
    "KY",  # Cayman Islands
    "JE",  # Jersey
    "GG",  # Guernsey
    "IM",  # Isle of Man
    "LI",  # Liechtenstein
    "MC",  # Monaco
    "AE",  # UAE
    "PK",  # Pakistan
    "NG",  # Nigeria
}

# High-risk industries
HIGH_RISK_INDUSTRIES = {
    "gambling",
    "casino",
    "cryptocurrency",
    "money_service_business",
    "arms_dealer",
    "precious_metals",
    "art_dealer",
}

MEDIUM_RISK_INDUSTRIES = {
    "real_estate",
    "construction",
    "cash_intensive",
    "import_export",
    "automotive",
}


class RiskService:
    """Service for calculating customer risk scores.

    Risk assessment methodology:
    - Geographic risk: Based on customer/counterparty jurisdictions
    - Customer type risk: Based on entity type and structure
    - Transaction risk: Based on patterns, volume, and counterparties
    - Network risk: Based on connections to high-risk entities
    """

    def __init__(self) -> None:
        """Initialize the risk service."""
        self.weights = {
            "geographic": 0.25,
            "customer_type": 0.20,
            "transaction": 0.30,
            "network": 0.25,
        }

    def calculate_geographic_risk(
        self,
        primary_jurisdiction: str,
        secondary_jurisdictions: list[str] | None = None,
        counterparty_jurisdictions: list[str] | None = None,
    ) -> tuple[float, list[str]]:
        """Calculate geographic risk score.

        Args:
            primary_jurisdiction: Customer's primary jurisdiction
            secondary_jurisdictions: Other related jurisdictions
            counterparty_jurisdictions: Jurisdictions of counterparties

        Returns:
            Tuple of (score 0-100, list of risk factors)
        """
        risk_factors = []
        base_score = 0.0

        all_jurisdictions = {primary_jurisdiction}
        if secondary_jurisdictions:
            all_jurisdictions.update(secondary_jurisdictions)
        if counterparty_jurisdictions:
            all_jurisdictions.update(counterparty_jurisdictions)

        # Check for high-risk jurisdictions
        high_risk_found = all_jurisdictions & HIGH_RISK_JURISDICTIONS
        medium_risk_found = all_jurisdictions & MEDIUM_RISK_JURISDICTIONS

        if high_risk_found:
            base_score += 60
            risk_factors.append(f"High-risk jurisdictions: {', '.join(high_risk_found)}")

        if medium_risk_found:
            base_score += 25
            risk_factors.append(f"Medium-risk jurisdictions: {', '.join(medium_risk_found)}")

        # Primary jurisdiction carries more weight
        if primary_jurisdiction in HIGH_RISK_JURISDICTIONS:
            base_score += 20
            risk_factors.append("Primary jurisdiction is high-risk")
        elif primary_jurisdiction in MEDIUM_RISK_JURISDICTIONS:
            base_score += 10
            risk_factors.append("Primary jurisdiction is medium-risk")

        return min(base_score, 100), risk_factors

    def calculate_customer_type_risk(
        self,
        customer_type: str,
        industry: str | None = None,
        has_complex_structure: bool = False,
        has_nominee_shareholders: bool = False,
        has_bearer_shares: bool = False,
    ) -> tuple[float, list[str]]:
        """Calculate customer type risk score.

        Args:
            customer_type: Type of customer
            industry: Industry sector
            has_complex_structure: Complex ownership structure
            has_nominee_shareholders: Uses nominee shareholders
            has_bearer_shares: Has bearer shares

        Returns:
            Tuple of (score 0-100, list of risk factors)
        """
        risk_factors = []
        base_score = 0.0

        # Customer type risk
        type_scores = {
            "individual": 10,
            "corporate": 25,
            "trust": 40,
            "partnership": 30,
        }
        base_score = type_scores.get(customer_type.lower(), 20)

        # Industry risk
        if industry:
            industry_lower = industry.lower().replace(" ", "_")
            if industry_lower in HIGH_RISK_INDUSTRIES:
                base_score += 35
                risk_factors.append(f"High-risk industry: {industry}")
            elif industry_lower in MEDIUM_RISK_INDUSTRIES:
                base_score += 15
                risk_factors.append(f"Medium-risk industry: {industry}")

        # Structure complexity
        if has_complex_structure:
            base_score += 20
            risk_factors.append("Complex ownership structure")

        if has_nominee_shareholders:
            base_score += 25
            risk_factors.append("Uses nominee shareholders")

        if has_bearer_shares:
            base_score += 30
            risk_factors.append("Bearer shares present")

        return min(base_score, 100), risk_factors

    def calculate_transaction_risk(
        self,
        avg_monthly_volume: float,
        expected_monthly_volume: float,
        cash_transaction_ratio: float = 0.0,
        high_risk_jurisdiction_ratio: float = 0.0,
        structuring_indicators: int = 0,
        rapid_movement_count: int = 0,
    ) -> tuple[float, list[str]]:
        """Calculate transaction pattern risk score.

        Args:
            avg_monthly_volume: Average monthly transaction volume
            expected_monthly_volume: Expected volume based on profile
            cash_transaction_ratio: Ratio of cash transactions
            high_risk_jurisdiction_ratio: Ratio of high-risk destination txns
            structuring_indicators: Count of structuring patterns detected
            rapid_movement_count: Count of rapid fund movements

        Returns:
            Tuple of (score 0-100, list of risk factors)
        """
        risk_factors = []
        base_score = 0.0

        # Volume deviation
        if expected_monthly_volume > 0:
            volume_ratio = avg_monthly_volume / expected_monthly_volume
            if volume_ratio > 3.0:
                base_score += 30
                risk_factors.append(f"Transaction volume {volume_ratio:.1f}x expected")
            elif volume_ratio > 2.0:
                base_score += 15
                risk_factors.append(f"Elevated transaction volume ({volume_ratio:.1f}x expected)")

        # Cash transaction risk
        if cash_transaction_ratio > 0.5:
            base_score += 25
            risk_factors.append(f"High cash ratio: {cash_transaction_ratio * 100:.0f}%")
        elif cash_transaction_ratio > 0.25:
            base_score += 10
            risk_factors.append(f"Elevated cash ratio: {cash_transaction_ratio * 100:.0f}%")

        # High-risk jurisdiction transactions
        if high_risk_jurisdiction_ratio > 0.3:
            base_score += 30
            risk_factors.append(
                f"High-risk jurisdiction exposure: {high_risk_jurisdiction_ratio * 100:.0f}%"
            )
        elif high_risk_jurisdiction_ratio > 0.1:
            base_score += 15
            risk_factors.append(
                f"Some high-risk jurisdiction exposure: {high_risk_jurisdiction_ratio * 100:.0f}%"
            )

        # Structuring indicators
        if structuring_indicators > 3:
            base_score += 35
            risk_factors.append(f"Multiple structuring indicators: {structuring_indicators}")
        elif structuring_indicators > 0:
            base_score += 15
            risk_factors.append(f"Structuring indicators detected: {structuring_indicators}")

        # Rapid movement
        if rapid_movement_count > 5:
            base_score += 25
            risk_factors.append(f"Frequent rapid fund movements: {rapid_movement_count}")
        elif rapid_movement_count > 0:
            base_score += 10
            risk_factors.append(f"Some rapid fund movements: {rapid_movement_count}")

        return min(base_score, 100), risk_factors

    def calculate_network_risk(
        self,
        high_risk_connections: int = 0,
        pep_connections: int = 0,
        sanctioned_connections: int = 0,
        shell_company_connections: int = 0,
        total_connections: int = 1,
    ) -> tuple[float, list[str]]:
        """Calculate network/relationship risk score.

        Args:
            high_risk_connections: Number of high-risk entity connections
            pep_connections: Number of PEP connections
            sanctioned_connections: Number of sanctioned entity connections
            shell_company_connections: Number of shell company connections
            total_connections: Total number of connections

        Returns:
            Tuple of (score 0-100, list of risk factors)
        """
        risk_factors = []
        base_score = 0.0

        # Sanctioned connections are critical
        if sanctioned_connections > 0:
            base_score += 80
            risk_factors.append(f"Connected to {sanctioned_connections} sanctioned entities")

        # PEP connections
        if pep_connections > 2:
            base_score += 35
            risk_factors.append(f"Multiple PEP connections: {pep_connections}")
        elif pep_connections > 0:
            base_score += 20
            risk_factors.append(f"PEP connection(s): {pep_connections}")

        # Shell company connections
        if shell_company_connections > 2:
            base_score += 40
            risk_factors.append(f"Multiple shell company connections: {shell_company_connections}")
        elif shell_company_connections > 0:
            base_score += 20
            risk_factors.append(f"Shell company connection(s): {shell_company_connections}")

        # High-risk connection ratio
        if total_connections > 0:
            high_risk_ratio = high_risk_connections / total_connections
            if high_risk_ratio > 0.3:
                base_score += 25
                risk_factors.append(
                    f"High proportion of risky connections: {high_risk_ratio * 100:.0f}%"
                )

        return min(base_score, 100), risk_factors

    def assess_customer_risk(
        self,
        customer_id: str,
        customer_type: str,
        jurisdiction: str,
        industry: str | None = None,
        transaction_data: dict[str, Any] | None = None,
        network_data: dict[str, Any] | None = None,
        structure_data: dict[str, Any] | None = None,
    ) -> CustomerRisk:
        """Perform comprehensive customer risk assessment.

        Args:
            customer_id: Customer identifier
            customer_type: Type of customer
            jurisdiction: Primary jurisdiction
            industry: Industry sector
            transaction_data: Transaction statistics
            network_data: Network analysis data
            structure_data: Ownership structure data

        Returns:
            Complete risk assessment
        """
        all_factors = []

        # Geographic risk
        geo_score, geo_factors = self.calculate_geographic_risk(
            primary_jurisdiction=jurisdiction,
            secondary_jurisdictions=transaction_data.get("counterparty_jurisdictions")
            if transaction_data
            else None,
        )
        all_factors.extend(geo_factors)

        # Customer type risk
        structure = structure_data or {}
        type_score, type_factors = self.calculate_customer_type_risk(
            customer_type=customer_type,
            industry=industry,
            has_complex_structure=structure.get("is_complex", False),
            has_nominee_shareholders=structure.get("has_nominees", False),
            has_bearer_shares=structure.get("has_bearer_shares", False),
        )
        all_factors.extend(type_factors)

        # Transaction risk
        txn = transaction_data or {}
        txn_score, txn_factors = self.calculate_transaction_risk(
            avg_monthly_volume=txn.get("avg_monthly_volume", 0),
            expected_monthly_volume=txn.get("expected_monthly_volume", 1),
            cash_transaction_ratio=txn.get("cash_ratio", 0),
            high_risk_jurisdiction_ratio=txn.get("high_risk_ratio", 0),
            structuring_indicators=txn.get("structuring_count", 0),
            rapid_movement_count=txn.get("rapid_movement_count", 0),
        )
        all_factors.extend(txn_factors)

        # Network risk
        network = network_data or {}
        net_score, net_factors = self.calculate_network_risk(
            high_risk_connections=network.get("high_risk_count", 0),
            pep_connections=network.get("pep_count", 0),
            sanctioned_connections=network.get("sanctioned_count", 0),
            shell_company_connections=network.get("shell_company_count", 0),
            total_connections=network.get("total_connections", 1),
        )
        all_factors.extend(net_factors)

        # Calculate weighted overall score
        overall_score = (
            geo_score * self.weights["geographic"]
            + type_score * self.weights["customer_type"]
            + txn_score * self.weights["transaction"]
            + net_score * self.weights["network"]
        )

        # Determine risk level
        if overall_score >= 75:
            risk_level = RiskLevel.CRITICAL
        elif overall_score >= 50:
            risk_level = RiskLevel.HIGH
        elif overall_score >= 25:
            risk_level = RiskLevel.MEDIUM
        else:
            risk_level = RiskLevel.LOW

        # Generate recommendations
        recommendations = self._generate_recommendations(
            risk_level=risk_level,
            risk_factors=all_factors,
            geo_score=geo_score,
            txn_score=txn_score,
            net_score=net_score,
        )

        return CustomerRisk(
            customer_id=customer_id,
            overall_risk=risk_level,
            risk_score=round(overall_score, 2),
            geographic_risk=round(geo_score, 2),
            customer_type_risk=round(type_score, 2),
            transaction_risk=round(txn_score, 2),
            network_risk=round(net_score, 2),
            risk_factors=all_factors,
            recommendations=recommendations,
            assessment_date=datetime.utcnow(),
        )

    def _generate_recommendations(
        self,
        risk_level: RiskLevel,
        risk_factors: list[str],
        geo_score: float,
        txn_score: float,
        net_score: float,
    ) -> list[str]:
        """Generate risk mitigation recommendations.

        Args:
            risk_level: Overall risk level
            risk_factors: List of identified risk factors
            geo_score: Geographic risk score
            txn_score: Transaction risk score
            net_score: Network risk score

        Returns:
            List of recommendations
        """
        recommendations = []

        if risk_level in (RiskLevel.CRITICAL, RiskLevel.HIGH):
            recommendations.append("Conduct Enhanced Due Diligence (EDD)")
            recommendations.append("Increase transaction monitoring frequency")
            recommendations.append("Require senior management approval for relationship")

        if geo_score > 50:
            recommendations.append("Verify source of funds documentation")
            recommendations.append("Obtain additional jurisdiction-specific compliance")

        if txn_score > 50:
            recommendations.append("Implement real-time transaction monitoring")
            recommendations.append("Review transaction limits and thresholds")

        if net_score > 50:
            recommendations.append("Conduct network analysis review")
            recommendations.append("Verify beneficial ownership chain")

        if any("sanctioned" in f.lower() for f in risk_factors):
            recommendations.append("IMMEDIATE: Escalate for sanctions review")
            recommendations.append("Consider relationship termination")

        if any("pep" in f.lower() for f in risk_factors):
            recommendations.append("Conduct PEP-specific enhanced due diligence")
            recommendations.append("Document source of wealth")

        if not recommendations:
            recommendations.append("Continue standard monitoring")
            recommendations.append("Schedule regular periodic review")

        return recommendations


# Global service instance
_risk_service: RiskService | None = None


def get_risk_service() -> RiskService:
    """Get the global risk service instance."""
    global _risk_service
    if _risk_service is None:
        _risk_service = RiskService()
    return _risk_service
