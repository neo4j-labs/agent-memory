"""Neo4j domain data service for the Financial Services Advisor.

Queries the compliance domain graph (customers, documents, transactions,
organizations, alerts, sanctions, PEPs, investigations and reports) over the
same connection the memory client already owns.

Two access paths, deliberately:

* **reads** go through ``client.query.cypher(...)`` — validated read-only
  before any round-trip, and the same call works against a self-hosted bolt
  database and the hosted NAMS backend.
* **writes** go through ``client.graph.execute_write(...)``, which is bolt-only.
  The compliance domain graph is yours to own; the three memory tiers are what
  NAMS hosts.

Every node the loader writes carries the ``:Compliance`` marker label. Graph
traversals here are scoped to it, so an ``:Entity:Organization`` extracted from
a chat turn is never mistaken for a compliance organization — the two share the
``:Organization`` label.
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from neo4j_agent_memory import MemoryClient

logger = logging.getLogger(__name__)

#: Marker label the shared loader puts on every compliance node.
MARKER = "Compliance"


def _json_safe(value: Any) -> Any:
    """Convert Neo4j temporal values to ISO strings, recursively.

    ``Transaction.date`` and ``Document.expiry_date`` are real Neo4j ``DATE``
    values so the AML time windows work; ``neo4j.time.Date`` is not a
    ``datetime.date``, so FastAPI would serialise it as its private fields.
    """
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    to_native = getattr(value, "to_native", None)
    if callable(to_native):
        return to_native().isoformat()
    return value


class Neo4jDomainService:
    """Queries the compliance domain graph via the shared ``MemoryClient``."""

    def __init__(self, client: MemoryClient) -> None:
        self._client = client

    async def _read(self, query: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Portable read-only Cypher. Raises ``ValueError`` on a write query."""
        rows = await self._client.query.cypher(query, params)
        return [_json_safe(row) for row in rows]

    async def _write(
        self, query: str, params: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        """Bolt-only write. See the module docstring for why this is separate."""
        rows = await self._client.graph.execute_write(query, params)
        return [_json_safe(row) for row in rows]

    async def read_only_cypher(
        self, query: str, params: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        """Run a caller-supplied read-only query.

        Used by the ``/api/graph/query`` demo endpoint. Validation is the
        library's: ``client.query.cypher`` rejects writes before the query
        reaches the server, which a keyword blocklist cannot do correctly.
        """
        return await self._read(query, params)

    # ── Customers ──────────────────────────────────────────────────────

    async def list_customers(
        self, *, customer_type: str | None = None, limit: int = 100, offset: int = 0
    ) -> list[dict[str, Any]]:
        where = ""
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if customer_type:
            # ``customer_type`` rather than ``type``: the loader writes both, and
            # only ``customer_type`` survives `--adopt`, which sets ``type`` to
            # the library entity type.
            where = "WHERE coalesce(c.customer_type, c.type) = $type"
            params["type"] = customer_type
        query = f"""
        MATCH (c:Customer:{MARKER})
        {where}
        OPTIONAL MATCH (c)-[:HAS_DOCUMENT]->(d:Document)
        WITH c, collect(d {{.type, .status, .expiry_date, .submission_date}}) AS docs
        RETURN c {{.*, type: coalesce(c.customer_type, c.type), documents: docs}} AS customer
        ORDER BY c.id
        SKIP $offset LIMIT $limit
        """
        results = await self._read(query, params)
        return [r["customer"] for r in results]

    async def get_customer(self, customer_id: str) -> dict[str, Any] | None:
        query = f"""
        MATCH (c:Customer:{MARKER} {{id: $id}})
        OPTIONAL MATCH (c)-[:HAS_DOCUMENT]->(d:Document)
        WITH c, collect(d {{.type, .status, .expiry_date, .submission_date}}) AS docs
        RETURN c {{.*, type: coalesce(c.customer_type, c.type), documents: docs}} AS customer
        """
        results = await self._read(query, {"id": customer_id})
        return results[0]["customer"] if results else None

    async def get_customer_documents(
        self, customer_id: str, document_type: str | None = None
    ) -> list[dict[str, Any]]:
        where = "WHERE c.id = $id"
        params: dict[str, Any] = {"id": customer_id}
        if document_type:
            where += " AND d.type = $doc_type"
            params["doc_type"] = document_type
        query = f"""
        MATCH (c:Customer:{MARKER})-[:HAS_DOCUMENT]->(d:Document)
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
        """Transactions inside a look-back window.

        ``t.date`` is a Neo4j ``DATE``, so this comparison is date arithmetic.
        When it was a string the comparison evaluated to null and every
        time-windowed AML query silently returned nothing.
        """
        filters = ["t.date >= date() - duration({days: $days})"]
        params: dict[str, Any] = {"id": customer_id, "days": days}
        if min_amount is not None:
            filters.append("t.amount >= $min_amount")
            params["min_amount"] = min_amount
        if transaction_type:
            filters.append("t.type = $tx_type")
            params["tx_type"] = transaction_type
        query = f"""
        MATCH (c:Customer:{MARKER} {{id: $id}})-[:HAS_TRANSACTION]->(t:Transaction)
        WHERE {" AND ".join(filters)}
        RETURN t {{.*}} AS transaction
        ORDER BY t.date DESC
        """
        results = await self._read(query, params)
        return [r["transaction"] for r in results]

    async def get_transaction(self, transaction_id: str) -> dict[str, Any] | None:
        """One transaction plus the customer that owns it."""
        query = f"""
        MATCH (c:Customer:{MARKER})-[:HAS_TRANSACTION]->(t:Transaction {{id: $txn_id}})
        RETURN c.id AS customer_id, t {{.*}} AS transaction
        """
        results = await self._read(query, {"txn_id": transaction_id})
        return results[0] if results else None

    async def get_transaction_stats(self, customer_id: str) -> dict[str, Any]:
        query = f"""
        MATCH (c:Customer:{MARKER} {{id: $id}})-[:HAS_TRANSACTION]->(t:Transaction)
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
        """Cash deposits clustered just under the $10,000 CTR threshold."""
        query = f"""
        MATCH (c:Customer:{MARKER} {{id: $id}})-[:HAS_TRANSACTION]->(t:Transaction)
        WHERE t.type = 'cash_deposit' AND t.amount >= 9000 AND t.amount < 10000
        RETURN t {{.*}} AS transaction ORDER BY t.date
        """
        results = await self._read(query, {"id": customer_id})
        return [r["transaction"] for r in results]

    async def detect_rapid_movement(self, customer_id: str) -> list[dict[str, Any]]:
        """Funds in and almost all of them back out within two days."""
        query = f"""
        MATCH (c:Customer:{MARKER} {{id: $id}})-[:HAS_TRANSACTION]->(t_in:Transaction)
        WHERE t_in.type IN ['wire_in', 'deposit']
        MATCH (c)-[:HAS_TRANSACTION]->(t_out:Transaction)
        WHERE t_out.type IN ['wire_out', 'withdrawal']
          AND t_out.date >= t_in.date
          AND t_out.date <= t_in.date + duration({{days: 2}})
          AND t_out.amount >= t_in.amount * 0.9
          AND t_out.amount <= t_in.amount
        RETURN t_in {{.*}} AS inbound, t_out {{.*}} AS outbound,
               t_in.amount - t_out.amount AS retained
        ORDER BY t_in.date
        """
        return await self._read(query, {"id": customer_id})

    async def detect_layering(self, customer_id: str) -> list[dict[str, Any]]:
        """Transactions with counterparties in classic layering jurisdictions."""
        query = f"""
        MATCH (c:Customer:{MARKER} {{id: $id}})-[:HAS_TRANSACTION]->(t:Transaction)
        WHERE t.counterparty CONTAINS 'Offshore' OR t.counterparty CONTAINS 'Cayman'
           OR t.counterparty CONTAINS 'Seychelles' OR t.counterparty CONTAINS 'Panama'
           OR t.counterparty CONTAINS 'Shell Corp' OR t.counterparty CONTAINS 'Anonymous Trust'
        RETURN t {{.*}} AS transaction ORDER BY t.date
        """
        results = await self._read(query, {"id": customer_id})
        return [r["transaction"] for r in results]

    async def get_velocity_metrics(self, customer_id: str) -> dict[str, Any]:
        query = f"""
        MATCH (c:Customer:{MARKER} {{id: $id}})-[:HAS_TRANSACTION]->(t:Transaction)
        WITH t.type AS tx_type, count(t) AS cnt, sum(t.amount) AS vol
        RETURN tx_type, cnt, vol ORDER BY tx_type
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
        """Neighbourhood of a compliance node, out to ``depth`` hops."""
        # The hop range cannot be parameterised, so it is clamped to 1-3 and
        # interpolated as an integer.
        hops = max(1, min(int(depth), 3))
        query = f"""
        MATCH (start:{MARKER} {{id: $id}})
        MATCH path = (start)-[*1..{hops}]-(connected:{MARKER})
        WHERE connected <> start
        WITH DISTINCT
             connected {{.id, .name, .customer_type, .type, .jurisdiction,
                        .shell_indicators, .business_type}} AS entity,
             length(path) AS distance,
             [r IN relationships(path) | type(r)] AS rel_types,
             connected.name AS connected_name
        RETURN entity, distance, rel_types
        ORDER BY distance, connected_name
        """
        results = await self._read(query, {"id": entity_id})
        return {"entity_id": entity_id, "connections": results}

    async def detect_shell_companies(self, entity_id: str) -> list[dict[str, Any]]:
        query = f"""
        MATCH (start:{MARKER} {{id: $id}})-[*1..2]-(o:Organization:{MARKER})
        WHERE o.shell_indicators IS NOT NULL AND size(o.shell_indicators) > 0
        RETURN DISTINCT o {{.id, .name, .jurisdiction, .business_type, .shell_indicators}} AS org
        """
        results = await self._read(query, {"id": entity_id})
        return [r["org"] for r in results]

    async def trace_ownership(self, entity_id: str) -> dict[str, Any]:
        query = f"""
        MATCH (target:{MARKER} {{id: $id}})
        OPTIONAL MATCH path = (owner:{MARKER})-[:OWNS|CONTROLS|DIRECTED_BY*1..3]->(target)
        WHERE owner:Customer OR owner:Organization
        WITH target, path, owner,
             [r IN relationships(path) | type(r)] AS rel_types,
             length(path) AS chain_length
        RETURN owner {{.id, .name, .customer_type, .type, .jurisdiction}} AS owner,
               rel_types, chain_length
        ORDER BY chain_length
        """
        results = await self._read(query, {"id": entity_id})
        owners = [r for r in results if r["owner"] is not None]
        return {
            "entity_id": entity_id,
            "ownership_chains": owners,
            "ubo_identified": any(
                (r["owner"].get("customer_type") or r["owner"].get("type"))
                in ("individual", "PERSON")
                for r in owners
            ),
        }

    async def get_ownership_graph(self, entity_id: str) -> list[dict[str, Any]]:
        """Raw ownership/control edges around a node, for ownership mapping."""
        query = f"""
        MATCH (n:{MARKER}) WHERE n.id = $id OR n.name = $id
        OPTIONAL MATCH (owner:{MARKER})-[r:OWNS|CONTROLS|DIRECTED_BY]->(n)
        RETURN n {{.id, .name, .customer_type, .type, .jurisdiction}} AS target,
               owner {{.id, .name, .customer_type, .type, .jurisdiction}} AS owner,
               type(r) AS relationship,
               r.percentage AS percentage
        """
        return await self._read(query, {"id": entity_id})

    async def get_network_risk(self, entity_id: str) -> dict[str, Any]:
        high_risk_jurisdictions = {"KY", "BVI", "SC", "PA"}
        query = f"""
        MATCH (start:{MARKER} {{id: $id}})-[*1..2]-(connected:{MARKER})
        WHERE connected <> start
        RETURN DISTINCT connected {{.id, .name, .customer_type, .type, .jurisdiction,
                                   .shell_indicators, .role}} AS entity
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

    async def search_nodes(self, query_text: str, *, limit: int = 10) -> list[dict[str, Any]]:
        """Name substring search across compliance nodes."""
        query = f"""
        MATCH (n:{MARKER})
        WHERE n.name IS NOT NULL AND toLower(n.name) CONTAINS toLower($query)
        RETURN n.id AS id, n.name AS name, labels(n)[0] AS type,
               n.jurisdiction AS jurisdiction
        LIMIT $limit
        """
        return await self._read(query, {"query": query_text, "limit": limit})

    async def get_node_summary(self, entity_id: str) -> dict[str, Any] | None:
        """Resolve a compliance node by ``id`` or ``name``.

        The tools accept either, so this is the shared "does it exist, and what
        is it called?" lookup they all start from.
        """
        query = f"""
        MATCH (n:{MARKER}) WHERE n.id = $id OR n.name = $id
        RETURN n.id AS id, n.name AS name, labels(n) AS labels,
               coalesce(n.customer_type, n.type) AS type,
               n.shell_indicators AS shell_indicators,
               n.jurisdiction AS jurisdiction
        LIMIT 1
        """
        results = await self._read(query, {"id": entity_id})
        return results[0] if results else None

    async def get_large_transactions(
        self, customer_id: str, *, threshold: float = 50000
    ) -> list[str]:
        """Ids of a customer's transactions above ``threshold``."""
        results = await self._read(
            f"""
            MATCH (c:Customer:{MARKER} {{id: $id}})-[:HAS_TRANSACTION]->(t:Transaction)
            WHERE t.amount > $threshold
            RETURN t.id AS id ORDER BY t.amount DESC
            """,
            {"id": customer_id, "threshold": threshold},
        )
        return [r["id"] for r in results]

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
        MATCH (c:Customer:{MARKER})-[:HAS_ALERT]->(a:Alert)
        {where}
        RETURN a {{.*, customer_id: c.id, customer_name: c.name}} AS alert
        ORDER BY CASE a.severity WHEN 'CRITICAL' THEN 0 WHEN 'HIGH' THEN 1
                                WHEN 'MEDIUM' THEN 2 WHEN 'LOW' THEN 3 END,
                 a.created_at DESC
        SKIP $offset LIMIT $limit
        """
        results = await self._read(query, params)
        return [r["alert"] for r in results]

    async def get_alert(self, alert_id: str) -> dict[str, Any] | None:
        query = f"""
        MATCH (c:Customer:{MARKER})-[:HAS_ALERT]->(a:Alert {{id: $id}})
        OPTIONAL MATCH (a)-[:RELATED_TO_TRANSACTION]->(t:Transaction)
        WITH a, c, collect(t {{.*}}) AS txns
        RETURN a {{.*, customer_id: c.id, customer_name: c.name, transactions: txns}} AS alert
        """
        results = await self._read(query, {"id": alert_id})
        return results[0]["alert"] if results else None

    async def create_alert(self, alert: dict[str, Any]) -> dict[str, Any]:
        query = f"""
        MATCH (c:Customer:{MARKER} {{id: $customer_id}})
        MERGE (a:Alert:{MARKER} {{id: $id}})
        ON CREATE SET a.type = $type, a.severity = $severity, a.status = $status,
            a.title = $title, a.description = $description, a.evidence = $evidence,
            a.requires_sar = $requires_sar, a.auto_generated = $auto_generated,
            a.created_at = datetime()
        MERGE (c)-[:HAS_ALERT]->(a)
        RETURN a {{.*, customer_id: c.id, customer_name: c.name}} AS alert
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
        MATCH (c:Customer:{MARKER})-[:HAS_ALERT]->(a:Alert {{id: $id}})
        SET {", ".join(set_clauses)}
        RETURN a {{.*, customer_id: c.id, customer_name: c.name}} AS alert
        """
        results = await self._write(query, params)
        return results[0]["alert"] if results else None

    async def get_alert_summary(self) -> dict[str, Any]:
        query = f"""
        MATCH (c:Customer:{MARKER})-[:HAS_ALERT]->(a:Alert)
        WITH a.severity AS sev, a.status AS stat
        RETURN count(*) AS total,
            sum(CASE WHEN sev = 'CRITICAL' THEN 1 ELSE 0 END) AS critical,
            sum(CASE WHEN sev = 'HIGH' THEN 1 ELSE 0 END) AS high,
            sum(CASE WHEN sev = 'MEDIUM' THEN 1 ELSE 0 END) AS medium,
            sum(CASE WHEN sev = 'LOW' THEN 1 ELSE 0 END) AS low,
            sum(CASE WHEN stat = 'NEW' THEN 1 ELSE 0 END) AS new_count,
            sum(CASE WHEN stat = 'ACKNOWLEDGED' THEN 1 ELSE 0 END) AS acknowledged,
            sum(CASE WHEN stat = 'INVESTIGATING' THEN 1 ELSE 0 END) AS investigating,
            sum(CASE WHEN stat = 'ESCALATED' THEN 1 ELSE 0 END) AS escalated,
            sum(CASE WHEN stat = 'RESOLVED' THEN 1 ELSE 0 END) AS resolved,
            sum(CASE WHEN sev = 'CRITICAL' AND NOT stat IN ['RESOLVED', 'FALSE_POSITIVE']
                     THEN 1 ELSE 0 END) AS critical_unresolved,
            sum(CASE WHEN sev = 'HIGH' AND NOT stat IN ['RESOLVED', 'FALSE_POSITIVE']
                     THEN 1 ELSE 0 END) AS high_unresolved
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
            "critical_unresolved": r["critical_unresolved"],
            "high_unresolved": r["high_unresolved"],
        }

    # ── Sanctions ──────────────────────────────────────────────────────

    async def check_sanctions(
        self, entity_name: str, *, include_aliases: bool = True
    ) -> list[dict[str, Any]]:
        query = f"""
        MATCH (s:SanctionedEntity:{MARKER})
        OPTIONAL MATCH (alias:SanctionAlias)-[:ALIAS_OF]->(s)
        WITH s, collect(alias.name) AS aliases
        WHERE toLower(s.name) CONTAINS $name
           OR ($include_aliases AND any(a IN aliases WHERE toLower(a) CONTAINS $name))
        RETURN s {{.*, aliases: aliases}} AS entity,
               CASE WHEN toLower(s.name) = $name THEN 'EXACT'
                    WHEN any(a IN aliases WHERE toLower(a) = $name) THEN 'ALIAS'
                    ELSE 'PARTIAL' END AS match_type,
               CASE WHEN toLower(s.name) = $name THEN 1.0
                    WHEN any(a IN aliases WHERE toLower(a) = $name) THEN 0.95
                    ELSE 0.7 END AS confidence
        """
        return await self._read(
            query, {"name": entity_name.lower(), "include_aliases": include_aliases}
        )

    # ── PEP ────────────────────────────────────────────────────────────

    async def check_pep(
        self, person_name: str, *, include_relatives: bool = True
    ) -> list[dict[str, Any]]:
        name_lower = person_name.lower()
        matches = await self._read(
            f"""
            MATCH (p:PEP:{MARKER})
            WHERE toLower(p.name) CONTAINS $name
            RETURN p {{.*}} AS pep,
                   CASE WHEN toLower(p.name) = $name THEN 'DIRECT_PEP'
                        ELSE 'POTENTIAL_PEP' END AS match_type,
                   CASE WHEN toLower(p.name) = $name THEN 1.0 ELSE 0.7 END AS confidence
            """,
            {"name": name_lower},
        )
        if include_relatives:
            matches += await self._read(
                f"""
                MATCH (r:PEPRelative:{MARKER})-[:RELATIVE_OF]->(p:PEP)
                WHERE toLower(r.name) CONTAINS $name
                RETURN r {{.*, pep_name: p.name, pep_position: p.position}} AS pep,
                       'PEP_RELATIVE' AS match_type, 0.95 AS confidence
                """,
                {"name": name_lower},
            )
        return matches

    # ── Graph stats ────────────────────────────────────────────────────

    async def get_graph_stats(self) -> dict[str, Any]:
        nodes = await self._read(
            "MATCH (n) RETURN labels(n)[0] AS label, count(*) AS count ORDER BY label"
        )
        rels = await self._read(
            "MATCH ()-[r]->() RETURN type(r) AS type, count(*) AS count ORDER BY type"
        )
        return {
            "total_nodes": sum(r["count"] for r in nodes),
            "total_relationships": sum(r["count"] for r in rels),
            "nodes_by_label": {r["label"]: r["count"] for r in nodes},
            "relationships_by_type": {r["type"]: r["count"] for r in rels},
        }

    # ── Investigations ─────────────────────────────────────────────────

    async def create_investigation(self, investigation: dict[str, Any]) -> dict[str, Any]:
        query = f"""
        MATCH (c:Customer:{MARKER} {{id: $customer_id}})
        MERGE (i:Investigation:{MARKER} {{id: $id}})
        ON CREATE SET
            i.title = $title, i.description = $description,
            i.status = $status, i.priority = $priority,
            i.trigger = $trigger, i.created_at = datetime(),
            i.customer_id = $customer_id
        MERGE (c)-[:HAS_INVESTIGATION]->(i)
        RETURN i {{.*, customer_id: c.id, customer_name: c.name}} AS investigation
        """
        inv_id = investigation.get("id", f"INV-{uuid.uuid4().hex[:8].upper()}")
        results = await self._write(
            query,
            {
                "customer_id": investigation["customer_id"],
                "id": inv_id,
                "title": investigation.get("title", ""),
                "description": investigation.get("description", ""),
                "status": investigation.get("status", "PENDING"),
                "priority": investigation.get("priority", "MEDIUM"),
                "trigger": investigation.get("trigger", ""),
            },
        )
        return results[0]["investigation"] if results else {**investigation, "id": inv_id}

    async def get_investigation(self, investigation_id: str) -> dict[str, Any] | None:
        # The collect() needs its own WITH: mixing an aggregation with the
        # non-aggregated `i`/`c` projections in one RETURN is an implicit
        # grouping error in Neo4j 5+.
        query = f"""
        MATCH (c:Customer:{MARKER})-[:HAS_INVESTIGATION]->(i:Investigation {{id: $id}})
        OPTIONAL MATCH (i)-[:HAS_TRACE]->(rt:ReasoningTrace)
        WITH i, c, collect(rt.id) AS trace_ids
        RETURN i {{.*, customer_id: c.id, customer_name: c.name,
                   trace_ids: trace_ids}} AS investigation
        """
        results = await self._read(query, {"id": investigation_id})
        return results[0]["investigation"] if results else None

    async def list_investigations(
        self, *, status: str | None = None, customer_id: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
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
        MATCH (c:Customer:{MARKER})-[:HAS_INVESTIGATION]->(i:Investigation)
        {where}
        RETURN i {{.*, customer_id: c.id, customer_name: c.name}} AS investigation
        ORDER BY i.created_at DESC
        LIMIT $limit
        """
        results = await self._read(query, params)
        return [r["investigation"] for r in results]

    async def update_investigation(
        self, investigation_id: str, updates: dict[str, Any]
    ) -> dict[str, Any] | None:
        set_clauses = []
        params: dict[str, Any] = {"id": investigation_id}
        for key, value in updates.items():
            if key in ("status", "priority", "conclusion", "summary", "overall_risk_level"):
                set_clauses.append(f"i.{key} = ${key}")
                params[key] = value
        if updates.get("status") == "IN_PROGRESS":
            set_clauses.append("i.started_at = datetime()")
        elif updates.get("status") == "COMPLETED":
            set_clauses.append("i.completed_at = datetime()")
        if not set_clauses:
            return await self.get_investigation(investigation_id)
        query = f"""
        MATCH (c:Customer:{MARKER})-[:HAS_INVESTIGATION]->(i:Investigation {{id: $id}})
        SET {", ".join(set_clauses)}, i.updated_at = datetime()
        RETURN i {{.*, customer_id: c.id, customer_name: c.name}} AS investigation
        """
        results = await self._write(query, params)
        return results[0]["investigation"] if results else None

    async def link_investigation_to_trace(self, investigation_id: str, trace_id: str) -> bool:
        """Link an investigation to the reasoning trace that produced it.

        Replaces a process-local ``dict`` of investigation → trace mappings, so
        the audit trail survives a restart and is visible to every instance.
        """
        results = await self._write(
            """
            MATCH (i:Investigation {id: $investigation_id})
            MATCH (rt:ReasoningTrace {id: $trace_id})
            MERGE (i)-[:HAS_TRACE]->(rt)
            RETURN rt.id AS trace_id
            """,
            {"investigation_id": investigation_id, "trace_id": trace_id},
        )
        return bool(results)

    # ── Reports ────────────────────────────────────────────────────────

    async def save_report(
        self,
        report_id: str,
        report_kind: str,
        customer_id: str,
        payload: str,
        *,
        status: str = "DRAFT",
        investigation_id: str | None = None,
    ) -> None:
        """Persist a report as ``(:Report)-[:ABOUT]->(:Customer)``.

        ``payload`` is the JSON-serialised Pydantic model: the demo keeps the
        full document so the read path can return it verbatim, while the
        indexed properties (kind, customer, status) support the list endpoints.
        """
        await self._write(
            f"""
            MATCH (c:Customer:{MARKER} {{id: $customer_id}})
            MERGE (r:Report:{MARKER} {{id: $id}})
            SET r.kind = $kind, r.status = $status, r.payload = $payload,
                r.investigation_id = $investigation_id,
                r.created_at = coalesce(r.created_at, datetime()),
                r.updated_at = datetime()
            MERGE (r)-[:ABOUT]->(c)
            """,
            {
                "id": report_id,
                "kind": report_kind,
                "customer_id": customer_id,
                "status": status,
                "payload": payload,
                "investigation_id": investigation_id,
            },
        )

    async def get_report(self, report_id: str, report_kind: str) -> str | None:
        results = await self._read(
            "MATCH (r:Report {id: $id, kind: $kind}) RETURN r.payload AS payload",
            {"id": report_id, "kind": report_kind},
        )
        return results[0]["payload"] if results else None

    async def list_reports(
        self,
        report_kind: str,
        *,
        status: str | None = None,
        customer_id: str | None = None,
    ) -> list[str]:
        filters = ["r.kind = $kind"]
        params: dict[str, Any] = {"kind": report_kind}
        if status:
            filters.append("r.status = $status")
            params["status"] = status
        if customer_id:
            filters.append("c.id = $customer_id")
            params["customer_id"] = customer_id
        results = await self._read(
            f"""
            MATCH (r:Report)-[:ABOUT]->(c:Customer)
            WHERE {" AND ".join(filters)}
            RETURN r.payload AS payload
            ORDER BY r.created_at DESC
            """,
            params,
        )
        return [r["payload"] for r in results]

    # ── Memory Graph (for NVL visualization) ───────────────────────────

    async def get_memory_graph(
        self, *, session_id: str | None = None, limit: int = 500
    ) -> dict[str, Any]:
        """Compliance subgraph plus its edges, shaped for the NVL viewer."""
        query = f"""
        MATCH (n:{MARKER})
        WITH n LIMIT $limit
        OPTIONAL MATCH (n)-[r]-(m)
        RETURN
            collect(DISTINCT {{
                id: coalesce(n.id, n.name, toString(elementId(n))),
                label: coalesce(n.name, n.id, n.title),
                labels: labels(n),
                properties: properties(n)
            }}) AS nodes,
            collect(DISTINCT {{
                id: elementId(r),
                from: coalesce(startNode(r).id, startNode(r).name,
                               toString(elementId(startNode(r)))),
                to: coalesce(endNode(r).id, endNode(r).name,
                             toString(elementId(endNode(r)))),
                type: type(r),
                properties: properties(r)
            }}) AS relationships
        """
        results = await self._read(query, {"limit": limit})
        if not results:
            return {"nodes": [], "relationships": []}
        row = results[0]
        seen_nodes: dict[str, Any] = {}
        for node in row.get("nodes", []):
            node_id = node.get("id")
            if node_id and node_id not in seen_nodes:
                seen_nodes[node_id] = node
        seen_rels: dict[str, Any] = {}
        for rel in row.get("relationships", []):
            rel_id = rel.get("id")
            if rel_id and rel_id not in seen_rels and rel.get("from") and rel.get("to"):
                seen_rels[rel_id] = rel
        return {"nodes": list(seen_nodes.values()), "relationships": list(seen_rels.values())}
