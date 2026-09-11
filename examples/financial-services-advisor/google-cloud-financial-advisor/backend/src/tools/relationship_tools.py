"""Relationship analysis tools for network investigation and beneficial ownership.

These tools are used by the Relationship Agent to analyze entity networks,
trace ownership structures, and detect shell companies via Neo4j graph queries.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from ..services.neo4j_service import Neo4jDomainService

logger = logging.getLogger(__name__)


async def find_connections(
    entity_id: str,
    depth: int = 2,
    relationship_types: list[str] | None = None,
    *,
    neo4j_service: Neo4jDomainService,
) -> dict[str, Any]:
    """Find all connections for an entity in the graph."""
    logger.info(f"Finding connections for entity {entity_id}, depth={depth}")

    # Get the starting entity
    query = """
    MATCH (n)
    WHERE n.id = $id OR n.name = $id
    RETURN n {.id, .name, .type} AS entity
    LIMIT 1
    """
    results = await neo4j_service.read(query, {"id": entity_id})
    if not results:
        return {
            "entity_id": entity_id,
            "status": "NOT_FOUND",
            "message": f"Entity {entity_id} not found in network",
            "timestamp": datetime.now().isoformat(),
        }

    start = results[0]["entity"]

    conn_data = await neo4j_service.find_connections(start["id"] or entity_id, depth=depth)
    connections = []
    for c in conn_data.get("connections", []):
        entity = c.get("entity", {})
        connections.append(
            {
                "entity_id": entity.get("id"),
                "name": entity.get("name"),
                "type": entity.get("type"),
                "relationship": c.get("rel_types", ["CONNECTED_TO"])[0]
                if c.get("rel_types")
                else "CONNECTED_TO",
                "distance": c.get("distance", 1),
                "jurisdiction": entity.get("jurisdiction"),
            }
        )

    return {
        "entity_id": entity_id,
        "entity_name": start.get("name"),
        "entity_type": start.get("type"),
        "depth_searched": depth,
        "connections_found": len(connections),
        "connections": connections,
        "timestamp": datetime.now().isoformat(),
    }


async def analyze_network_risk(
    entity_id: str,
    include_indirect: bool = True,
    *,
    neo4j_service: Neo4jDomainService,
) -> dict[str, Any]:
    """Analyze network-level risk for an entity."""
    logger.info(f"Analyzing network risk for {entity_id}")

    risk_data = await neo4j_service.get_network_risk(entity_id)

    # Get entity name
    query = """
    MATCH (n)
    WHERE n.id = $id OR n.name = $id
    RETURN n.name AS name
    LIMIT 1
    """
    results = await neo4j_service.read(query, {"id": entity_id})
    entity_name = results[0]["name"] if results else entity_id

    return {
        "entity_id": entity_id,
        "entity_name": entity_name,
        "network_risk_score": risk_data["network_risk_score"],
        "risk_level": risk_data["risk_level"],
        "risk_factors": risk_data["risk_factors"],
        "total_connections": risk_data["total_connections"],
        "include_indirect": include_indirect,
        "timestamp": datetime.now().isoformat(),
    }


async def detect_shell_companies(
    entity_id: str,
    *,
    neo4j_service: Neo4jDomainService,
) -> dict[str, Any]:
    """Detect potential shell companies in an entity's network."""
    logger.info(f"Detecting shell companies for {entity_id}")

    shell_indicator_descriptions = {
        "no_employees": "No employees on record",
        "po_box_address": "PO Box or virtual address only",
        "nominee_directors": "Directors are nominee services",
        "opaque_structure": "Ownership structure not transparent",
        "high_risk_jurisdiction": "Registered in secrecy jurisdiction",
        "minimal_activity": "Little to no business activity",
    }

    # Get entity name
    query = """
    MATCH (n)
    WHERE n.id = $id OR n.name = $id
    RETURN n {.id, .name, .type, .shell_indicators} AS entity
    LIMIT 1
    """
    results = await neo4j_service.read(query, {"id": entity_id})
    if not results:
        return {
            "entity_id": entity_id,
            "status": "NOT_FOUND",
            "message": f"Entity {entity_id} not found in network",
            "timestamp": datetime.now().isoformat(),
        }

    start = results[0]["entity"]
    shells = await neo4j_service.detect_shell_companies(start["id"] or entity_id)

    detected_shells = []
    for org in shells:
        indicators = org.get("shell_indicators", [])
        detected_shells.append(
            {
                "entity_id": org.get("id"),
                "name": org.get("name"),
                "jurisdiction": org.get("jurisdiction"),
                "indicators": [
                    {
                        "code": ind,
                        "description": shell_indicator_descriptions.get(ind, ind),
                    }
                    for ind in indicators
                ],
                "indicator_count": len(indicators),
                "confidence": min(0.5 + len(indicators) * 0.15, 0.95),
            }
        )

    # Check subject entity itself
    subject_indicators = start.get("shell_indicators") or []
    if subject_indicators:
        detected_shells.insert(
            0,
            {
                "entity_id": start.get("id"),
                "name": start.get("name"),
                "jurisdiction": None,
                "indicators": [
                    {
                        "code": ind,
                        "description": shell_indicator_descriptions.get(ind, ind),
                    }
                    for ind in subject_indicators
                ],
                "indicator_count": len(subject_indicators),
                "confidence": min(0.5 + len(subject_indicators) * 0.15, 0.95),
                "is_subject": True,
            },
        )

    return {
        "entity_id": entity_id,
        "entity_name": start.get("name"),
        "shell_companies_detected": len(detected_shells),
        "shell_companies": detected_shells,
        "risk_level": "CRITICAL" if detected_shells else "LOW",
        "timestamp": datetime.now().isoformat(),
    }


async def map_beneficial_ownership(
    entity_id: str,
    threshold_percentage: float = 25.0,
    *,
    neo4j_service: Neo4jDomainService,
) -> dict[str, Any]:
    """Map beneficial ownership structure for an entity."""
    logger.info(f"Mapping beneficial ownership for {entity_id}")

    # Get entity
    query = """
    MATCH (n)
    WHERE n.id = $id OR n.name = $id
    RETURN n {.id, .name, .type} AS entity
    LIMIT 1
    """
    results = await neo4j_service.read(query, {"id": entity_id})
    if not results:
        return {
            "entity_id": entity_id,
            "status": "NOT_FOUND",
            "timestamp": datetime.now().isoformat(),
        }

    entity = results[0]["entity"]
    ownership = await neo4j_service.trace_ownership(entity["id"] or entity_id)

    ownership_chains = []
    ubos = []

    for chain in ownership.get("ownership_chains", []):
        owner = chain.get("owner", {})
        rel_types = chain.get("rel_types", [])
        ownership_chains.append(
            {
                "owner_id": owner.get("id"),
                "owner_name": owner.get("name"),
                "owner_type": owner.get("type"),
                "relationship": rel_types[0] if rel_types else "UNKNOWN",
                "percentage": None,
            }
        )

        if owner.get("type") in ("individual", "PERSON"):
            ubos.append(
                {
                    "id": owner.get("id"),
                    "name": owner.get("name"),
                    "ownership_type": "DIRECT",
                    "percentage": None,
                }
            )

    opaque_indicators = []
    if not ubos:
        opaque_indicators.append(
            {
                "entity": entity.get("name"),
                "reason": "No identifiable ultimate beneficial owner",
            }
        )

    # Check if any owners are shell companies
    for chain in ownership_chains:
        shell_check = await neo4j_service.detect_shell_companies(chain.get("owner_id", ""))
        if shell_check:
            opaque_indicators.append(
                {
                    "entity": chain["owner_name"],
                    "reason": "Owner has shell company indicators",
                }
            )

    return {
        "entity_id": entity_id,
        "entity_name": entity.get("name"),
        "ownership_threshold": threshold_percentage,
        "ownership_chains": ownership_chains,
        "ultimate_beneficial_owners": ubos,
        "ubo_identified": len(ubos) > 0,
        "opaque_indicators": opaque_indicators,
        "transparency_risk": "HIGH" if opaque_indicators else "LOW",
        "timestamp": datetime.now().isoformat(),
    }
