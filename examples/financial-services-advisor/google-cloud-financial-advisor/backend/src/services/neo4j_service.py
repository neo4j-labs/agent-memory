"""Neo4j domain data service for the Financial Advisor.

Provides async methods to query domain-specific data (customers, transactions,
organizations, alerts, sanctions, PEPs) from Neo4j. The service takes the
:class:`~neo4j_agent_memory.MemoryClient` itself so domain data and agent
memory share one driver, one database and one connection pool.

Reads and writes take different routes on purpose:

* **reads** go through ``client.query.cypher(...)`` — the portable, read-only
  validated accessor that works on both the bolt and the hosted (NAMS)
  backends;
* **writes** go through ``client.graph.execute_write(...)``, which is
  **bolt-only**. Arbitrary Cypher writes are not part of the hosted backend's
  surface, so a NAMS deployment of this app would keep the read paths and move
  these few writes behind its own API.
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from neo4j_agent_memory import MemoryClient

logger = logging.getLogger(__name__)

# Memory labels written by neo4j-agent-memory. Used by get_memory_graph() so
# the "context graph" view actually contains the context graph.
MEMORY_LABELS = (
    "Conversation",
    "Message",
    "Entity",
    "Preference",
    "Fact",
    "ReasoningTrace",
    "ReasoningStep",
    "ToolCall",
)

#: Domain labels loaded by ``data/load_sample_data.py``.
DOMAIN_LABELS = (
    "Customer",
    "Organization",
    "Transaction",
    "Alert",
    "SanctionedEntity",
    "PEP",
    "Investigation",
)


class Neo4jDomainService:
    """Queries domain data from Neo4j via the memory client."""

    def __init__(self, client: MemoryClient) -> None:
        """Initialize with the application's :class:`MemoryClient`."""
        self._client = client

    async def read(self, query: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Run a read-only Cypher query through the shared memory client.

        Public because a handful of tool functions in ``src/tools/`` own their
        own one-off queries. ``client.query.cypher`` validates read-only-ness,
        so this cannot be used to write.
        """
        return await self._client.query.cypher(query, params)

    #: Internal alias used by this module's own query methods.
    _read = read

    async def _write(
        self, query: str, params: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        """Run a write query. Bolt-only — see the module docstring."""
        return await self._client.graph.execute_write(query, params or {})

    # ── Customers ──────────────────────────────────────────────────────

    async def list_customers(
        self,
        *,
        customer_type: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List customers with optional type filter."""
        where = ""
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if customer_type:
            where = "WHERE c.type = $type"
            params["type"] = customer_type

        query = f"""
        MATCH (c:Customer)
        {where}
        OPTIONAL MATCH (c)-[:HAS_DOCUMENT]->(d:Document)
        WITH c, collect(d {{.type, .status, .expiry_date, .submission_date}}) AS docs
        RETURN c {{.*, documents: docs}} AS customer
        ORDER BY c.id
        SKIP $offset LIMIT $limit
        """
        results = await self._read(query, params)
        return [r["customer"] for r in results]

    async def get_customer(self, customer_id: str) -> dict[str, Any] | None:
        """Get a customer by ID with documents."""
        query = """
        MATCH (c:Customer {id: $id})
        OPTIONAL MATCH (c)-[:HAS_DOCUMENT]->(d:Document)
        WITH c, collect(d {.type, .status, .expiry_date, .submission_date}) AS docs
        RETURN c {.*, documents: docs} AS customer
        """
        results = await self._read(query, {"id": customer_id})
        return results[0]["customer"] if results else None

    async def get_customer_documents(
        self, customer_id: str, document_type: str | None = None
    ) -> list[dict[str, Any]]:
        """Get documents for a customer, optionally filtered by type."""
        where = "WHERE c.id = $id"
        params: dict[str, Any] = {"id": customer_id}
        if document_type:
            where += " AND d.type = $doc_type"
            params["doc_type"] = document_type

        query = f"""
        MATCH (c:Customer)-[:HAS_DOCUMENT]->(d:Document)
        {where}
        RETURN d {{.*}} AS document
        """
        results = await self._read(query, params)
        return [r["document"] for r in results]

    # ── Transactions ───────────────────────────────────────────────────

    async def get_transactions(
        self,
        customer_id: str,
        *,
        days: int = 90,
        min_amount: float | None = None,
        transaction_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """Get transactions for a customer with optional filters."""
        filters = ["t.date >= date() - duration({days: $days})"]
        params: dict[str, Any] = {"id": customer_id, "days": days}

        if min_amount is not None:
            filters.append("t.amount >= $min_amount")
            params["min_amount"] = min_amount
        if transaction_type:
            filters.append("t.type = $tx_type")
            params["tx_type"] = transaction_type

        where_extra = "WHERE " + " AND ".join(filters)

        query = f"""
        MATCH (c:Customer {{id: $id}})-[:HAS_TRANSACTION]->(t:Transaction)
        {where_extra}
        RETURN t {{.*}} AS transaction
        ORDER BY t.date DESC
        """
        results = await self._read(query, params)
        return [r["transaction"] for r in results]

    async def get_transaction_stats(self, customer_id: str) -> dict[str, Any]:
        """Get aggregated transaction statistics for a customer."""
        query = """
        MATCH (c:Customer {id: $id})-[:HAS_TRANSACTION]->(t:Transaction)
        WITH t,
             CASE WHEN t.type IN ['deposit', 'wire_in', 'cash_deposit'] THEN t.amount ELSE 0 END AS deposit,
             CASE WHEN t.type IN ['withdrawal', 'wire_out'] THEN t.amount ELSE 0 END AS withdrawal
        RETURN count(t) AS transaction_count,
               sum(t.amount) AS total_volume,
               sum(deposit) AS total_deposits,
               sum(withdrawal) AS total_withdrawals,
               avg(t.amount) AS average_transaction,
               collect(DISTINCT t.counterparty) AS counterparties,
               collect(DISTINCT t.type) AS transaction_types
        """
        results = await self._read(query, {"id": customer_id})
        if not results:
            return {
                "transaction_count": 0,
                "total_volume": 0,
                "total_deposits": 0,
                "total_withdrawals": 0,
                "average_transaction": 0,
                "counterparties": [],
                "transaction_types": [],
            }
        return results[0]

    async def detect_structuring(self, customer_id: str) -> list[dict[str, Any]]:
        """Detect cash deposits just under $10K reporting threshold."""
        query = """
        MATCH (c:Customer {id: $id})-[:HAS_TRANSACTION]->(t:Transaction)
        WHERE t.type = 'cash_deposit'
          AND t.amount >= 9000 AND t.amount < 10000
        RETURN t {.*} AS transaction
        ORDER BY t.date
        """
        results = await self._read(query, {"id": customer_id})
        return [r["transaction"] for r in results]

    async def detect_rapid_movement(self, customer_id: str) -> list[dict[str, Any]]:
        """Detect funds received and moved quickly with minimal loss."""
        query = """
        MATCH (c:Customer {id: $id})-[:HAS_TRANSACTION]->(t_in:Transaction)
        WHERE t_in.type IN ['wire_in', 'deposit']
        MATCH (c)-[:HAS_TRANSACTION]->(t_out:Transaction)
        WHERE t_out.type IN ['wire_out', 'withdrawal']
          AND t_out.date >= t_in.date
          AND date(t_out.date) <= date(t_in.date) + duration({days: 2})
          AND t_out.amount >= t_in.amount * 0.9
          AND t_out.amount <= t_in.amount
        RETURN t_in {.*} AS inbound, t_out {.*} AS outbound,
               t_in.amount - t_out.amount AS retained
        ORDER BY t_in.date
        """
        results = await self._read(query, {"id": customer_id})
        return results

    async def detect_layering(self, customer_id: str) -> list[dict[str, Any]]:
        """Detect layering: transactions involving multiple offshore jurisdictions."""
        query = """
        MATCH (c:Customer {id: $id})-[:HAS_TRANSACTION]->(t:Transaction)
        WHERE t.counterparty CONTAINS 'Offshore'
           OR t.counterparty CONTAINS 'Cayman'
           OR t.counterparty CONTAINS 'Seychelles'
           OR t.counterparty CONTAINS 'Panama'
           OR t.counterparty CONTAINS 'Shell Corp'
           OR t.counterparty CONTAINS 'Anonymous Trust'
        RETURN t {.*} AS transaction
        ORDER BY t.date
        """
        results = await self._read(query, {"id": customer_id})
        return [r["transaction"] for r in results]

    async def get_velocity_metrics(self, customer_id: str) -> dict[str, Any]:
        """Get transaction velocity metrics by type."""
        query = """
        MATCH (c:Customer {id: $id})-[:HAS_TRANSACTION]->(t:Transaction)
        WITH t.type AS tx_type, count(t) AS cnt, sum(t.amount) AS vol
        RETURN tx_type, cnt, vol
        ORDER BY tx_type
        """
        results = await self._read(query, {"id": customer_id})
        by_type = {r["tx_type"]: {"count": r["cnt"], "volume": r["vol"]} for r in results}
        total_txns = sum(r["cnt"] for r in results)
        total_vol = sum(r["vol"] for r in results)
        return {
            "total_transactions": total_txns,
            "total_volume": total_vol,
            "average_transaction": total_vol / total_txns if total_txns > 0 else 0,
            "transactions_by_type": {k: v["count"] for k, v in by_type.items()},
            "volume_by_type": {k: v["volume"] for k, v in by_type.items()},
        }

    # ── Network / Relationships ────────────────────────────────────────

    async def find_connections(self, entity_id: str, *, depth: int = 2) -> dict[str, Any]:
        """Find connected entities up to a given depth."""
        # Written without a CALL subquery on purpose: `client.query.cypher`
        # rejects `CALL {` because a subquery can contain writes.
        query = """
        MATCH path = (start {id: $id})-[*1..$depth]-(connected)
        WHERE connected <> start
        WITH DISTINCT connected,
             length(path) AS distance,
             [r IN relationships(path) | type(r)] AS rel_types
        RETURN connected {.id, .name, .type, .jurisdiction, .shell_indicators, .business_type} AS entity,
               distance,
               rel_types
        ORDER BY distance, connected.name
        """
        # depth can't be parameterized in Neo4j variable-length paths
        actual_query = query.replace("$depth", str(min(depth, 3)))
        results = await self._read(actual_query, {"id": entity_id})
        return {
            "entity_id": entity_id,
            "connections": results,
        }

    async def detect_shell_companies(self, entity_id: str) -> list[dict[str, Any]]:
        """Find connected organizations with shell company indicators."""
        query = """
        MATCH (start {id: $id})-[*1..2]-(o:Organization)
        WHERE size(o.shell_indicators) > 0
        RETURN DISTINCT o {.id, .name, .jurisdiction, .business_type, .shell_indicators} AS org
        """
        results = await self._read(query, {"id": entity_id})
        return [r["org"] for r in results]

    async def trace_ownership(self, entity_id: str) -> dict[str, Any]:
        """Trace beneficial ownership chains."""
        query = """
        MATCH (target {id: $id})
        OPTIONAL MATCH path = (owner)-[:OWNS|CONTROLS|DIRECTED_BY*1..3]->(target)
        WHERE owner:Customer OR owner:Organization
        WITH target, path, owner,
             [r IN relationships(path) | type(r)] AS rel_types,
             length(path) AS chain_length
        RETURN owner {.id, .name, .type, .jurisdiction} AS owner,
               rel_types,
               chain_length
        ORDER BY chain_length
        """
        results = await self._read(query, {"id": entity_id})
        owners = [r for r in results if r["owner"] is not None]
        return {
            "entity_id": entity_id,
            "ownership_chains": owners,
            "ubo_identified": any(
                r["owner"].get("type") in ("individual", "PERSON") for r in owners
            ),
        }

    async def get_network_risk(self, entity_id: str) -> dict[str, Any]:
        """Calculate network risk based on connected entities."""
        high_risk_jurisdictions = {"KY", "BVI", "SC", "PA"}

        query = """
        MATCH (start {id: $id})-[*1..2]-(connected)
        WHERE connected <> start
        RETURN DISTINCT connected {
            .id, .name, .type, .jurisdiction, .shell_indicators, .role
        } AS entity
        """
        results = await self._read(query, {"id": entity_id})

        risk_score = 0
        risk_factors = []

        for r in results:
            entity = r["entity"]
            jurisdiction = entity.get("jurisdiction") or ""
            indicators = entity.get("shell_indicators") or []
            role = entity.get("role") or ""

            if jurisdiction in high_risk_jurisdictions:
                risk_score += 15
                risk_factors.append(
                    f"HIGH_RISK_JURISDICTION: {entity.get('name')} ({jurisdiction})"
                )
            if len(indicators) > 0:
                risk_score += 20
                risk_factors.append(
                    f"SHELL_COMPANY: {entity.get('name')} ({', '.join(indicators)})"
                )
            if role == "nominee_services":
                risk_score += 15
                risk_factors.append(f"NOMINEE_SERVICES: {entity.get('name')}")

        if len(results) > 5:
            risk_score += 10
            risk_factors.append(f"COMPLEX_NETWORK: {len(results)} connections")

        risk_score = min(risk_score, 100)
        risk_level = (
            "CRITICAL"
            if risk_score >= 75
            else "HIGH"
            if risk_score >= 50
            else "MEDIUM"
            if risk_score >= 25
            else "LOW"
        )

        return {
            "entity_id": entity_id,
            "network_risk_score": risk_score,
            "risk_level": risk_level,
            "risk_factors": risk_factors,
            "total_connections": len(results),
        }

    # ── Alerts ─────────────────────────────────────────────────────────

    async def list_alerts(
        self,
        *,
        status: str | None = None,
        severity: str | None = None,
        alert_type: str | None = None,
        customer_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List alerts with optional filters."""
        filters = []
        params: dict[str, Any] = {"limit": limit, "offset": offset}

        if status:
            filters.append("a.status = $status")
            params["status"] = status
        if severity:
            filters.append("a.severity = $severity")
            params["severity"] = severity
        if alert_type:
            filters.append("a.type = $alert_type")
            params["alert_type"] = alert_type
        if customer_id:
            filters.append("c.id = $customer_id")
            params["customer_id"] = customer_id

        where = ("WHERE " + " AND ".join(filters)) if filters else ""

        query = f"""
        MATCH (c:Customer)-[:HAS_ALERT]->(a:Alert)
        {where}
        RETURN a {{.*, customer_id: c.id, customer_name: c.name}} AS alert
        ORDER BY
            CASE a.severity
                WHEN 'CRITICAL' THEN 0
                WHEN 'HIGH' THEN 1
                WHEN 'MEDIUM' THEN 2
                WHEN 'LOW' THEN 3
            END,
            a.created_at DESC
        SKIP $offset LIMIT $limit
        """
        results = await self._read(query, params)
        return [r["alert"] for r in results]

    async def get_alert(self, alert_id: str) -> dict[str, Any] | None:
        """Get an alert by ID."""
        query = """
        MATCH (c:Customer)-[:HAS_ALERT]->(a:Alert {id: $id})
        OPTIONAL MATCH (a)-[:RELATED_TO_TRANSACTION]->(t:Transaction)
        WITH a, c, collect(t {.*}) AS txns
        RETURN a {.*, customer_id: c.id, customer_name: c.name,
                   transactions: txns} AS alert
        """
        results = await self._read(query, {"id": alert_id})
        return results[0]["alert"] if results else None

    async def create_alert(self, alert: dict[str, Any]) -> dict[str, Any]:
        """Create a new alert linked to a customer."""
        query = """
        MATCH (c:Customer {id: $customer_id})
        MERGE (a:Alert {id: $id})
        ON CREATE SET
            a.type = $type,
            a.severity = $severity,
            a.status = $status,
            a.title = $title,
            a.description = $description,
            a.evidence = $evidence,
            a.requires_sar = $requires_sar,
            a.auto_generated = $auto_generated,
            a.created_at = datetime()
        MERGE (c)-[:HAS_ALERT]->(a)
        RETURN a {.*, customer_id: c.id, customer_name: c.name} AS alert
        """
        alert_id = alert.get("id", f"ALERT-{uuid.uuid4().hex[:8].upper()}")
        results = await self._write(
            query,
            {
                "customer_id": alert["customer_id"],
                "id": alert_id,
                "type": alert.get("type", "AML"),
                "severity": alert.get("severity", "MEDIUM"),
                "status": alert.get("status", "NEW"),
                "title": alert["title"],
                "description": alert.get("description", ""),
                "evidence": alert.get("evidence", []),
                "requires_sar": alert.get("requires_sar", False),
                "auto_generated": alert.get("auto_generated", False),
            },
        )
        return results[0]["alert"] if results else alert

    async def update_alert(self, alert_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
        """Update alert properties."""
        set_clauses = []
        params: dict[str, Any] = {"id": alert_id}

        for key, value in updates.items():
            if key in ("status", "severity", "assigned_to", "resolution_notes"):
                set_clauses.append(f"a.{key} = ${key}")
                params[key] = value

        if updates.get("status") == "ACKNOWLEDGED":
            set_clauses.append("a.acknowledged_at = datetime()")
        elif updates.get("status") in ("RESOLVED", "FALSE_POSITIVE"):
            set_clauses.append("a.resolved_at = datetime()")

        if not set_clauses:
            return await self.get_alert(alert_id)

        query = f"""
        MATCH (c:Customer)-[:HAS_ALERT]->(a:Alert {{id: $id}})
        SET {", ".join(set_clauses)}
        RETURN a {{.*, customer_id: c.id, customer_name: c.name}} AS alert
        """
        results = await self._write(query, params)
        return results[0]["alert"] if results else None

    async def get_alert_summary(self) -> dict[str, Any]:
        """Get aggregated alert statistics."""
        query = """
        MATCH (c:Customer)-[:HAS_ALERT]->(a:Alert)
        WITH a.severity AS sev, a.status AS stat
        RETURN
            count(*) AS total,
            sum(CASE WHEN sev = 'CRITICAL' THEN 1 ELSE 0 END) AS critical,
            sum(CASE WHEN sev = 'HIGH' THEN 1 ELSE 0 END) AS high,
            sum(CASE WHEN sev = 'MEDIUM' THEN 1 ELSE 0 END) AS medium,
            sum(CASE WHEN sev = 'LOW' THEN 1 ELSE 0 END) AS low,
            sum(CASE WHEN stat = 'NEW' THEN 1 ELSE 0 END) AS new_count,
            sum(CASE WHEN stat = 'ACKNOWLEDGED' THEN 1 ELSE 0 END) AS acknowledged,
            sum(CASE WHEN stat = 'INVESTIGATING' THEN 1 ELSE 0 END) AS investigating,
            sum(CASE WHEN stat = 'ESCALATED' THEN 1 ELSE 0 END) AS escalated,
            sum(CASE WHEN stat = 'RESOLVED' THEN 1 ELSE 0 END) AS resolved,
            sum(CASE WHEN sev = 'CRITICAL' AND NOT stat IN ['RESOLVED', 'FALSE_POSITIVE'] THEN 1 ELSE 0 END) AS critical_unresolved,
            sum(CASE WHEN sev = 'HIGH' AND NOT stat IN ['RESOLVED', 'FALSE_POSITIVE'] THEN 1 ELSE 0 END) AS high_unresolved
        """
        results = await self._read(query)
        if not results:
            return {
                "total": 0,
                "by_severity": {},
                "by_status": {},
                "critical_unresolved": 0,
                "high_unresolved": 0,
            }
        r = results[0]
        return {
            "total": r["total"],
            "by_severity": {
                "CRITICAL": r["critical"],
                "HIGH": r["high"],
                "MEDIUM": r["medium"],
                "LOW": r["low"],
            },
            "by_status": {
                "NEW": r["new_count"],
                "ACKNOWLEDGED": r["acknowledged"],
                "INVESTIGATING": r["investigating"],
                "ESCALATED": r["escalated"],
                "RESOLVED": r["resolved"],
            },
            "by_type": {},  # populated below
            "critical_unresolved": r["critical_unresolved"],
            "high_unresolved": r["high_unresolved"],
        }

    # ── Sanctions ──────────────────────────────────────────────────────

    async def check_sanctions(
        self, entity_name: str, *, include_aliases: bool = True
    ) -> list[dict[str, Any]]:
        """Check an entity name against the sanctions database."""
        name_lower = entity_name.lower()
        query = """
        MATCH (s:SanctionedEntity)
        OPTIONAL MATCH (alias:SanctionAlias)-[:ALIAS_OF]->(s)
        WITH s, collect(alias.name) AS aliases
        WHERE toLower(s.name) CONTAINS $name
           OR any(a IN aliases WHERE toLower(a) CONTAINS $name)
        RETURN s {.*, aliases: aliases} AS entity,
               CASE
                   WHEN toLower(s.name) = $name THEN 'EXACT'
                   WHEN any(a IN aliases WHERE toLower(a) = $name) THEN 'ALIAS'
                   ELSE 'PARTIAL'
               END AS match_type,
               CASE
                   WHEN toLower(s.name) = $name THEN 1.0
                   WHEN any(a IN aliases WHERE toLower(a) = $name) THEN 0.95
                   ELSE 0.7
               END AS confidence
        """
        results = await self._read(query, {"name": name_lower})
        return results

    # ── PEP ────────────────────────────────────────────────────────────

    async def check_pep(
        self, person_name: str, *, include_relatives: bool = True
    ) -> list[dict[str, Any]]:
        """Check a person name against the PEP database."""
        name_lower = person_name.lower()
        matches = []

        # Direct PEP match
        query = """
        MATCH (p:PEP)
        WHERE toLower(p.name) CONTAINS $name
        RETURN p {.*} AS pep,
               CASE WHEN toLower(p.name) = $name THEN 'DIRECT_PEP'
                    ELSE 'POTENTIAL_PEP' END AS match_type,
               CASE WHEN toLower(p.name) = $name THEN 1.0
                    ELSE 0.7 END AS confidence
        """
        results = await self._read(query, {"name": name_lower})
        matches.extend(results)

        # PEP relatives
        if include_relatives:
            query = """
            MATCH (r:PEPRelative)-[:RELATIVE_OF]->(p:PEP)
            WHERE toLower(r.name) CONTAINS $name
            RETURN r {.*, pep_name: p.name, pep_position: p.position} AS pep,
                   'PEP_RELATIVE' AS match_type,
                   0.95 AS confidence
            """
            results = await self._read(query, {"name": name_lower})
            matches.extend(results)

        return matches

    # ── Graph stats ────────────────────────────────────────────────────

    async def get_graph_stats(self) -> dict[str, Any]:
        """Get graph statistics (node and relationship counts)."""
        node_query = """
        MATCH (n)
        RETURN labels(n)[0] AS label, count(*) AS count
        ORDER BY label
        """
        rel_query = """
        MATCH ()-[r]->()
        RETURN type(r) AS type, count(*) AS count
        ORDER BY type
        """
        nodes = await self._read(node_query)
        rels = await self._read(rel_query)

        total_nodes = sum(r["count"] for r in nodes)
        total_rels = sum(r["count"] for r in rels)

        return {
            "total_nodes": total_nodes,
            "total_relationships": total_rels,
            "nodes_by_label": {r["label"]: r["count"] for r in nodes},
            "relationships_by_type": {r["type"]: r["count"] for r in rels},
        }

    async def get_neighbors(
        self,
        entity_id: str,
        *,
        depth: int = 1,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Get the neighbourhood of a node, shaped for graph visualisation.

        Matches on ``id`` or ``name`` so it works for both domain nodes
        (``Customer {id}``) and memory nodes (``Entity {name}``).
        """
        # Variable-length bounds cannot be parameterised; clamp then inline.
        hops = max(1, min(int(depth), 3))
        query = f"""
        MATCH path = (start)-[*1..{hops}]-(neighbor)
        WHERE start.id = $entity_id OR start.name = $entity_id
        WITH start, neighbor, relationships(path) AS rels
        LIMIT $limit
        RETURN
            coalesce(start.id, start.name) AS start_id,
            coalesce(start.name, start.id) AS start_name,
            labels(start) AS start_labels,
            coalesce(neighbor.id, neighbor.name) AS neighbor_id,
            coalesce(neighbor.name, neighbor.id) AS neighbor_name,
            labels(neighbor) AS neighbor_labels,
            [rel IN rels | type(rel)] AS relationship_types
        """
        records = await self._read(query, {"entity_id": entity_id, "limit": limit})

        nodes: dict[str, dict[str, Any]] = {}
        edges: list[dict[str, Any]] = []

        for record in records:
            start_id = record["start_id"]
            if start_id and start_id not in nodes:
                nodes[start_id] = {
                    "id": start_id,
                    "label": record["start_name"] or start_id,
                    "type": record["start_labels"][0] if record["start_labels"] else "Unknown",
                    "labels": record["start_labels"] or [],
                    "isRoot": True,
                }

            neighbor_id = record["neighbor_id"]
            if neighbor_id and neighbor_id not in nodes:
                nodes[neighbor_id] = {
                    "id": neighbor_id,
                    "label": record["neighbor_name"] or neighbor_id,
                    "type": record["neighbor_labels"][0]
                    if record["neighbor_labels"]
                    else "Unknown",
                    "labels": record["neighbor_labels"] or [],
                    "isRoot": False,
                }

            rel_types = record["relationship_types"] or []
            if start_id and neighbor_id and rel_types:
                edges.append(
                    {
                        "from": start_id,
                        "to": neighbor_id,
                        "relationship": rel_types[0],
                    }
                )

        return {
            "entity_id": entity_id,
            "depth": hops,
            "nodes": list(nodes.values()),
            "edges": edges,
            "total_nodes": len(nodes),
            "total_edges": len(edges),
        }

    # ── Investigations ─────────────────────────────────────────────────

    async def create_investigation(self, investigation: dict[str, Any]) -> dict[str, Any]:
        """Create an Investigation node linked to a Customer."""
        query = """
        MATCH (c:Customer {id: $customer_id})
        MERGE (i:Investigation {id: $id})
        ON CREATE SET
            i.title = $title, i.description = $description,
            i.status = $status, i.priority = $priority,
            i.trigger = $trigger, i.created_at = datetime(),
            i.customer_id = $customer_id, i.type = $type,
            i.reason = $reason, i.assigned_to = $assigned_to,
            i.session_id = $session_id
        MERGE (c)-[:HAS_INVESTIGATION]->(i)
        RETURN i {.*, customer_id: c.id, customer_name: c.name} AS investigation
        """
        inv_id = investigation.get("id", f"INV-{uuid.uuid4().hex[:8].upper()}")
        results = await self._write(
            query,
            {
                "customer_id": investigation["customer_id"],
                "id": inv_id,
                "title": investigation.get("title", ""),
                "description": investigation.get("description", ""),
                "status": investigation.get("status", "pending"),
                "priority": investigation.get("priority", "normal"),
                "trigger": investigation.get("trigger", ""),
                "type": investigation.get("type", "comprehensive"),
                "reason": investigation.get("reason", investigation.get("title", "")),
                "assigned_to": investigation.get("assigned_to"),
                "session_id": investigation.get("session_id"),
            },
        )
        return results[0]["investigation"] if results else {**investigation, "id": inv_id}

    async def get_investigation(self, investigation_id: str) -> dict[str, Any] | None:
        """Get an investigation by ID."""
        query = """
        MATCH (c:Customer)-[:HAS_INVESTIGATION]->(i:Investigation {id: $id})
        RETURN i {.*, customer_id: c.id, customer_name: c.name} AS investigation
        """
        results = await self._read(query, {"id": investigation_id})
        return results[0]["investigation"] if results else None

    async def list_investigations(
        self, *, status: str | None = None, customer_id: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        """List investigations with optional filters."""
        filters = []
        params: dict[str, Any] = {"limit": limit}
        if status:
            filters.append("i.status = $status")
            params["status"] = status
        if customer_id:
            filters.append("c.id = $customer_id")
            params["customer_id"] = customer_id
        where = ("WHERE " + " AND ".join(filters)) if filters else ""
        query = f"""
        MATCH (c:Customer)-[:HAS_INVESTIGATION]->(i:Investigation)
        {where}
        RETURN i {{.*, customer_id: c.id, customer_name: c.name}} AS investigation
        ORDER BY i.created_at DESC
        LIMIT $limit
        """
        results = await self._read(query, params)
        return [r["investigation"] for r in results]

    #: Properties ``update_investigation`` is allowed to set. An allowlist, not
    #: a blocklist: the keys are interpolated into the SET clause.
    UPDATABLE_INVESTIGATION_PROPERTIES = (
        "status",
        "priority",
        "conclusion",
        "summary",
        "overall_risk_level",
        "agents_consulted",
        "trace_id",
        "session_id",
        "assigned_to",
        "reviewed_by",
    )

    async def update_investigation(
        self, investigation_id: str, updates: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Update investigation properties."""
        set_clauses = []
        params: dict[str, Any] = {"id": investigation_id}
        for key, value in updates.items():
            if key in self.UPDATABLE_INVESTIGATION_PROPERTIES:
                set_clauses.append(f"i.{key} = ${key}")
                params[key] = value
        status = str(updates.get("status", "")).lower()
        if status == "in_progress":
            set_clauses.append("i.started_at = datetime()")
        elif status in ("completed", "closed"):
            set_clauses.append("i.completed_at = datetime()")
        if not set_clauses:
            return await self.get_investigation(investigation_id)
        query = f"""
        MATCH (c:Customer)-[:HAS_INVESTIGATION]->(i:Investigation {{id: $id}})
        SET {", ".join(set_clauses)}, i.updated_at = datetime()
        RETURN i {{.*, customer_id: c.id, customer_name: c.name}} AS investigation
        """
        results = await self._write(query, params)
        return results[0]["investigation"] if results else None

    # ── Memory Graph (for NVL visualization) ───────────────────────────

    #: Seeds the session-scoped context graph: the conversation and its
    #: messages, the entities those messages mention, and the reasoning chain
    #: recorded for the session (including the ``TOUCHED`` audit edges).
    _SESSION_GRAPH_SEEDS = """
    MATCH (c:Conversation {session_id: $session_id})
    OPTIONAL MATCH (c)-[:HAS_MESSAGE]->(m:Message)
    OPTIONAL MATCH (m)-[:MENTIONS]->(me:Entity)
    WITH c, collect(DISTINCT m) AS messages, collect(DISTINCT me) AS mentioned
    WITH [c] + messages + mentioned AS conversation_nodes
    OPTIONAL MATCH (rt:ReasoningTrace {session_id: $session_id})
    OPTIONAL MATCH (rt)-[:HAS_STEP]->(rs:ReasoningStep)
    OPTIONAL MATCH (rs)-[:USES_TOOL]->(tc:ToolCall)
    OPTIONAL MATCH (rs)-[:TOUCHED]->(te:Entity)
    WITH conversation_nodes,
         collect(DISTINCT rt) AS traces,
         collect(DISTINCT rs) AS steps,
         collect(DISTINCT tc) AS tool_calls,
         collect(DISTINCT te) AS touched
    WITH conversation_nodes + traces + steps + tool_calls + touched AS seeds
    UNWIND seeds AS n
    WITH DISTINCT n
    WHERE n IS NOT NULL
    RETURN elementId(n) AS element_id,
           coalesce(n.id, n.name, elementId(n)) AS id,
           coalesce(n.name, n.title, n.content, n.task, n.tool_name, n.id) AS label,
           labels(n) AS labels,
           properties(n) AS properties
    LIMIT $limit
    """

    #: Unscoped fallback: a capped sample across both the domain labels and the
    #: memory labels, so the view is never all-domain or all-memory.
    _SAMPLE_GRAPH_SEEDS = """
    MATCH (n)
    WHERE any(l IN labels(n) WHERE l IN $labels)
    WITH n, CASE WHEN any(l IN labels(n) WHERE l IN $memory_labels) THEN 0 ELSE 1 END AS bucket
    ORDER BY bucket
    LIMIT $limit
    RETURN elementId(n) AS element_id,
           coalesce(n.id, n.name, elementId(n)) AS id,
           coalesce(n.name, n.title, n.content, n.task, n.tool_name, n.id) AS label,
           labels(n) AS labels,
           properties(n) AS properties
    """

    #: Relationships induced by a node set (both endpoints must be in it).
    _INDUCED_RELATIONSHIPS = """
    MATCH (a)-[r]->(b)
    WHERE elementId(a) IN $element_ids AND elementId(b) IN $element_ids
    RETURN elementId(r) AS id,
           elementId(a) AS from_element_id,
           elementId(b) AS to_element_id,
           type(r) AS type,
           properties(r) AS properties
    """

    async def get_memory_graph(
        self, *, session_id: str | None = None, limit: int = 500
    ) -> dict[str, Any]:
        """Get a subgraph of domain **and** memory data for visualisation.

        With ``session_id`` the result is scoped to one conversation: its
        messages, the entities they mention, and the reasoning trace / steps /
        tool calls recorded for that session. Without it, a capped sample
        across the memory and domain labels is returned.
        """
        if session_id:
            node_rows = await self._read(
                self._SESSION_GRAPH_SEEDS,
                {"session_id": session_id, "limit": limit},
            )
        else:
            node_rows = await self._read(
                self._SAMPLE_GRAPH_SEEDS,
                {
                    "labels": list(MEMORY_LABELS + DOMAIN_LABELS),
                    "memory_labels": list(MEMORY_LABELS),
                    "limit": limit,
                },
            )

        nodes: dict[str, dict[str, Any]] = {}
        for row in node_rows:
            element_id = row.get("element_id")
            if not element_id or element_id in nodes:
                continue
            raw_label = row.get("label") or row.get("id") or element_id
            nodes[element_id] = {
                "id": row.get("id") or element_id,
                "element_id": element_id,
                "label": str(raw_label)[:80],
                "labels": row.get("labels") or [],
                "properties": row.get("properties") or {},
            }

        if not nodes:
            return {"nodes": [], "relationships": []}

        rel_rows = await self._read(
            self._INDUCED_RELATIONSHIPS,
            {"element_ids": list(nodes)},
        )
        relationships = [
            {
                "id": row["id"],
                "from": nodes[row["from_element_id"]]["id"],
                "to": nodes[row["to_element_id"]]["id"],
                "type": row["type"],
                "properties": row.get("properties") or {},
            }
            for row in rel_rows
            if row.get("from_element_id") in nodes and row.get("to_element_id") in nodes
        ]

        return {"nodes": list(nodes.values()), "relationships": relationships}

    # ── Reasoning audit (the TOUCHED one-hop) ──────────────────────────

    async def get_entity_audit_trail(
        self, entity_name: str, *, limit: int = 50
    ) -> list[dict[str, Any]]:
        """Answer "which reasoning steps touched this entity, and via which tool?".

        This is the payoff of passing ``touched_entities=`` when recording tool
        calls: one hop from an :Entity to the reasoning that produced it.
        """
        query = """
        MATCH (e:Entity)<-[:TOUCHED]-(s:ReasoningStep)<-[:HAS_STEP]-(t:ReasoningTrace)
        WHERE toLower(e.name) = toLower($name)
        OPTIONAL MATCH (s)-[:USES_TOOL]->(tc:ToolCall)
        RETURN e.name AS entity_name,
               t.id AS trace_id,
               t.session_id AS session_id,
               t.task AS task,
               t.outcome AS outcome,
               t.started_at AS started_at,
               s.id AS step_id,
               s.step_number AS step_number,
               s.thought AS thought,
               s.action AS action,
               collect(DISTINCT tc.tool_name) AS tools
        ORDER BY started_at DESC, step_number
        LIMIT $limit
        """
        return await self._read(query, {"name": entity_name, "limit": limit})
