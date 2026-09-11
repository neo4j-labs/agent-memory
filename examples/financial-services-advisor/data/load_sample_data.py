#!/usr/bin/env python3
"""Load the shared compliance sample graph into Neo4j.

Both Financial Services Advisor implementations (AWS Strands + Bedrock and
Google ADK + Gemini) read the same domain graph: customers, documents,
organizations, transactions, sanctions, PEPs and alerts.

Safety properties this loader guarantees
----------------------------------------
* **It never wipes your database.** Every write is a ``MERGE``, so re-running
  is idempotent. ``--reset`` exists for a clean slate, and even then the
  delete is scoped to the demo labels listed in :data:`DEMO_LABELS` — agent
  memory (``:Conversation`` / ``:Message`` / ``:Entity`` / ``:ReasoningTrace``)
  and anything else in the database survive.
* **Every node carries the ``:Compliance`` marker label.** That keeps the
  compliance domain namespace separate from the ``:Entity`` nodes the agents
  extract from conversations, which also land on ``:Person`` /
  ``:Organization`` labels (see ``graph/query_builder.py`` in the library).
* **Transaction and document dates are real Neo4j ``DATE`` values**, generated
  relative to the run date, so the 90-day AML windows the agents query
  actually contain rows.

Usage
-----
::

    # From either example's backend directory (reads that backend's .env):
    cd backend && uv run python ../../data/load_sample_data.py

    # Clean slate (demo labels only), no prompt:
    uv run python ../../data/load_sample_data.py --reset --yes

    # Second phase: adopt the domain graph as long-term memory entities
    uv run python ../../data/load_sample_data.py --adopt

Environment: ``NEO4J_URI``, ``NEO4J_USERNAME`` (or ``NEO4J_USER``),
``NEO4J_PASSWORD``, ``NEO4J_DATABASE``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from dotenv import load_dotenv
from neo4j import AsyncGraphDatabase, AsyncManagedTransaction

if TYPE_CHECKING:
    # The driver types `run(query: LiteralString)`; `from __future__ import
    # annotations` keeps this a type-checking-only import.
    from typing_extensions import LiteralString

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("fsa.load")

DATA_DIR = Path(__file__).resolve().parent
EXAMPLE_ROOT = DATA_DIR.parent

#: Labels this loader owns. ``--reset`` deletes nothing else, so library
#: memory nodes and any unrelated data in the target database are safe.
DEMO_LABELS: list[str] = [
    "Alert",
    "Compliance",
    "CorporateCustomer",
    "Customer",
    "Document",
    "IndividualCustomer",
    "Investigation",
    "Organization",
    "PEP",
    "PEPRelative",
    "Report",
    "SanctionAlias",
    "SanctionedEntity",
    "Transaction",
]

#: Marker label every demo node carries (see module docstring).
MARKER = "Compliance"

#: Domain label → library entity type, for the optional ``--adopt`` phase.
#:
#: Customers are split by secondary label rather than by the ``customer_type``
#: property because ``adopt_existing_graph`` assigns one entity type per label —
#: and because ``:IndividualCustomer`` / ``:CorporateCustomer`` cannot collide
#: with the ``:Person`` / ``:Organization`` labels the library derives from
#: POLE+O types when it extracts entities from conversations.
#:
#: ``:Transaction`` and ``:Document`` are deliberately absent. Adoption sets
#: ``n.type`` to the library entity type, and in this domain graph ``type``
#: already means something else — ``'cash_deposit'`` on a transaction,
#: ``'passport'`` on a document — which the AML and KYC tools match on. The
#: general rule when adopting a graph you did not design for the library:
#: any label whose nodes use ``id``, ``type`` or ``name`` for domain meaning
#: needs that property renamed before it joins the entity namespace. Customers
#: carry both ``type`` (domain, overwritten by adoption) and ``customer_type``
#: (stable) so the tools keep working either way.
LABEL_TO_TYPE: dict[str, str] = {
    "IndividualCustomer": "PERSON",
    "CorporateCustomer": "ORGANIZATION",
    "Organization": "ORGANIZATION",
    "SanctionedEntity": "ORGANIZATION",
    "PEP": "PERSON",
}

#: Labels whose display name is not in a ``name`` property. Every label in
#: :data:`LABEL_TO_TYPE` uses ``name``, so this is empty — it is kept because
#: it is the argument real graphs almost always need.
NAME_PROPERTY_PER_LABEL: dict[str, str] = {}

CONSTRAINTS: list[LiteralString] = [
    "CREATE CONSTRAINT customer_id IF NOT EXISTS FOR (c:Customer) REQUIRE c.id IS UNIQUE",
    "CREATE CONSTRAINT organization_id IF NOT EXISTS FOR (o:Organization) REQUIRE o.id IS UNIQUE",
    "CREATE CONSTRAINT transaction_id IF NOT EXISTS FOR (t:Transaction) REQUIRE t.id IS UNIQUE",
    "CREATE CONSTRAINT document_id IF NOT EXISTS FOR (d:Document) REQUIRE d.id IS UNIQUE",
    "CREATE CONSTRAINT alert_id IF NOT EXISTS FOR (a:Alert) REQUIRE a.id IS UNIQUE",
    "CREATE CONSTRAINT sanctioned_entity_name IF NOT EXISTS "
    "FOR (s:SanctionedEntity) REQUIRE s.name IS UNIQUE",
    "CREATE CONSTRAINT pep_name IF NOT EXISTS FOR (p:PEP) REQUIRE p.name IS UNIQUE",
    "CREATE CONSTRAINT investigation_id IF NOT EXISTS FOR (i:Investigation) REQUIRE i.id IS UNIQUE",
]

# Curated relationships that express the investigation storyline. MERGE-only,
# so a re-run is a no-op.
STORYLINE_RELATIONSHIPS: list[LiteralString] = [
    """
    MATCH (c:Customer {id: 'CUST-003'}), (o:Organization)
    WHERE o.name IN ['Shell Corp - Cayman', 'Anonymous Trust - Seychelles']
    MERGE (c)-[:CONTROLS]->(o)
    """,
    """
    MATCH (c:Customer {id: 'CUST-003'}),
          (o:Organization {name: 'Nominee Director Services Ltd'})
    MERGE (c)-[:DIRECTED_BY]->(o)
    """,
    """
    MATCH (o1:Organization {name: 'Shell Corp - Cayman'}),
          (o2:Organization {name: 'Anonymous Trust - Seychelles'})
    MERGE (o1)-[:LINKED_TO]->(o2)
    """,
    """
    MATCH (g:Organization {name: 'Garcia Trading LLC'}),
          (s:Organization {name: 'Supplier Co - Panama'})
    MERGE (g)-[:TRADES_WITH]->(s)
    """,
    """
    MATCH (c:Customer {id: 'CUST-001'}), (o:Organization {name: 'Tech Corp Inc'})
    MERGE (c)-[:EMPLOYED_BY]->(o)
    """,
]


# ---------------------------------------------------------------------------
# Environment and credentials
# ---------------------------------------------------------------------------


def load_env_files() -> None:
    """Load ``.env`` files, most specific first, and log each one.

    ``load_dotenv`` does not override variables that are already set, so the
    first file that defines a key wins. The invoking directory comes first —
    ``make load-data`` runs this from a backend directory, so that backend's
    ``.env`` takes precedence over the shared one.
    """
    candidates = [
        Path.cwd() / ".env",
        Path.cwd().parent / ".env",
        EXAMPLE_ROOT / "aws-financial-services-advisor" / "backend" / ".env",
        EXAMPLE_ROOT / "google-cloud-financial-advisor" / "backend" / ".env",
        EXAMPLE_ROOT / ".env",
    ]
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in seen or not resolved.exists():
            continue
        seen.add(resolved)
        load_dotenv(resolved)
        logger.info("Loaded environment from %s", resolved)


def resolve_credentials(args: argparse.Namespace) -> tuple[str, str, str, str]:
    """Resolve (uri, username, password, database), erroring on a missing password.

    Accepts both ``NEO4J_USERNAME`` (the name the library and the rest of this
    repo use) and ``NEO4J_USER`` (the name the two backends' settings classes
    use), so either spelling works.
    """
    uri = args.uri or os.environ.get("NEO4J_URI") or "bolt://localhost:7687"
    username = (
        args.username or os.environ.get("NEO4J_USERNAME") or os.environ.get("NEO4J_USER") or "neo4j"
    )
    database = args.database or os.environ.get("NEO4J_DATABASE") or "neo4j"
    password = args.password or os.environ.get("NEO4J_PASSWORD")
    if not password:
        raise SystemExit(
            "No Neo4j password. Set NEO4J_PASSWORD (in your backend .env) or pass "
            "--password. Refusing to guess a default."
        )
    return uri, username, password, database


# ---------------------------------------------------------------------------
# Fixture shaping: relative dates
# ---------------------------------------------------------------------------


def _iso_days_ago(days: int, *, today: date) -> str:
    return (today - timedelta(days=days)).isoformat()


def _iso_days_ahead(days: int, *, today: date) -> str:
    return (today + timedelta(days=days)).isoformat()


def read_json(name: str) -> Any:
    with open(DATA_DIR / name, encoding="utf-8") as handle:
        return json.load(handle)


def customer_rows(
    customers: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split customers into individual and corporate rows with flat props.

    Two literal queries instead of one query with an f-string-interpolated
    label: node labels cannot be parameterised, and interpolating one straight
    from input data is an injection-shaped pattern. When a dynamic label really
    is required, validate it first the way
    ``neo4j_agent_memory.graph.query_builder`` does.
    """
    individuals: list[dict[str, Any]] = []
    corporates: list[dict[str, Any]] = []
    for customer in customers:
        shared = {
            "name": customer["name"],
            # ``type`` is what both backends filter on today; ``customer_type``
            # is the same value under a name ``adopt_existing_graph`` will not
            # overwrite. See LABEL_TO_TYPE for why.
            "type": customer.get("type", "individual"),
            "customer_type": customer.get("type", "individual"),
            "account_opened": customer.get("account_opened"),
            "risk_factors": customer.get("risk_factors", []),
            "kyc_status": customer.get("kyc_status", "pending"),
        }
        if customer.get("type", "individual") == "individual":
            individuals.append(
                {
                    "id": customer["id"],
                    "props": {
                        **shared,
                        "date_of_birth": customer.get("date_of_birth"),
                        "nationality": customer.get("nationality"),
                        "address": customer.get("address"),
                        "occupation": customer.get("occupation"),
                        "employer": customer.get("employer"),
                    },
                }
            )
        else:
            corporates.append(
                {
                    "id": customer["id"],
                    "props": {
                        **shared,
                        "incorporation_date": customer.get("incorporation_date"),
                        "jurisdiction": customer.get("jurisdiction"),
                        "registered_address": customer.get("registered_address"),
                        "business_type": customer.get("business_type"),
                        "directors": customer.get("directors", []),
                    },
                }
            )
    return individuals, corporates


def document_rows(customers: list[dict[str, Any]], *, today: date) -> list[dict[str, Any]]:
    """Flatten each customer's documents, resolving relative dates to ISO strings.

    Expiries are relative (``expiry_days``) so a shipped fixture never ages
    into "every document expired"; submission dates are historical facts and
    stay absolute unless the fixture gives ``submitted_days_ago``.
    """
    rows: list[dict[str, Any]] = []
    for customer in customers:
        for doc_type, info in customer.get("documents", {}).items():
            expiry = info.get("expiry")
            if info.get("expiry_days") is not None:
                expiry = _iso_days_ahead(int(info["expiry_days"]), today=today)
            submitted = info.get("date")
            if info.get("submitted_days_ago") is not None:
                submitted = _iso_days_ago(int(info["submitted_days_ago"]), today=today)
            rows.append(
                {
                    "id": f"{customer['id']}-{doc_type}",
                    "customer_id": customer["id"],
                    "type": doc_type,
                    "status": info.get("status", "pending"),
                    "expiry_date": expiry,
                    "submission_date": submitted,
                }
            )
    return rows


def transaction_rows(transactions: list[dict[str, Any]], *, today: date) -> list[dict[str, Any]]:
    """Resolve ``days_ago`` offsets to ISO dates the loader stores as DATE.

    The fixture is offset-based rather than absolute so the AML time windows
    (``t.date >= date() - duration({days: 90})``) keep matching however long
    after release the example is run.
    """
    rows: list[dict[str, Any]] = []
    for txn in transactions:
        if "days_ago" in txn:
            txn_date = _iso_days_ago(int(txn["days_ago"]), today=today)
        elif "date" in txn:
            txn_date = str(txn["date"])
        else:
            raise ValueError(f"transaction {txn.get('id')} has neither days_ago nor date")
        rows.append(
            {
                "id": txn["id"],
                "customer_id": txn["customer_id"],
                "date": txn_date,
                "type": txn["type"],
                "amount": txn["amount"],
                "currency": txn.get("currency", "USD"),
                "counterparty": txn.get("counterparty"),
                "description": txn.get("description"),
            }
        )
    return rows


# ---------------------------------------------------------------------------
# Load phases
# ---------------------------------------------------------------------------


async def count_demo_nodes(session: Any) -> int:
    result = await session.run(
        "MATCH (n) WHERE any(l IN labels(n) WHERE l IN $labels) RETURN count(n) AS count",
        labels=DEMO_LABELS,
    )
    record = await result.single()
    return int(record["count"]) if record else 0


async def reset_demo_data(session: Any) -> None:
    """Delete only the demo labels, in batches, leaving everything else alone."""
    # The variable-scope form `CALL (n) { ... }` needs Neo4j 5.23+; on older
    # servers use the deprecated `CALL { WITH n ... }` spelling instead.
    await session.run(
        """
        MATCH (n) WHERE any(l IN labels(n) WHERE l IN $labels)
        CALL (n) { DETACH DELETE n } IN TRANSACTIONS OF 1000 ROWS
        """,
        labels=DEMO_LABELS,
    )


async def create_constraints(session: Any) -> None:
    """Create the uniqueness constraints the MERGEs rely on.

    No try/except: ``IF NOT EXISTS`` already makes this idempotent, so
    anything raised here is a real failure worth surfacing.
    """
    for constraint in CONSTRAINTS:
        await session.run(constraint)


async def _load_all(tx: AsyncManagedTransaction, *, today: date) -> dict[str, int]:
    """Write the whole fixture in one managed transaction.

    One transaction means a failure leaves no half-populated graph, and each
    collection is a single UNWIND round-trip rather than one per row.
    """
    customers = read_json("customers.json")
    organizations = read_json("organizations.json")
    transactions = read_json("transactions.json")
    sanctions = read_json("sanctions.json")
    pep_data = read_json("pep.json")
    alerts = read_json("alerts.json")

    individuals, corporates = customer_rows(customers)

    await tx.run(
        f"""
        UNWIND $rows AS row
        MERGE (c:Customer:IndividualCustomer:{MARKER} {{id: row.id}})
        SET c += row.props
        """,
        rows=individuals,
    )
    await tx.run(
        f"""
        UNWIND $rows AS row
        MERGE (c:Customer:CorporateCustomer:{MARKER} {{id: row.id}})
        SET c += row.props
        """,
        rows=corporates,
    )

    await tx.run(
        f"""
        UNWIND $rows AS row
        MATCH (c:Customer {{id: row.customer_id}})
        MERGE (d:Document:{MARKER} {{id: row.id}})
        SET d.type = row.type,
            d.status = row.status,
            d.expiry_date = CASE WHEN row.expiry_date IS NULL
                                 THEN null ELSE date(row.expiry_date) END,
            d.submission_date = CASE WHEN row.submission_date IS NULL
                                     THEN null ELSE date(row.submission_date) END
        MERGE (c)-[:HAS_DOCUMENT]->(d)
        """,
        rows=document_rows(customers, today=today),
    )

    await tx.run(
        f"""
        UNWIND $rows AS row
        MERGE (o:Organization:{MARKER} {{id: row.id}})
        SET o.name = row.name,
            o.type = 'ORGANIZATION',
            o.jurisdiction = row.jurisdiction,
            o.business_type = row.business_type,
            o.shell_indicators = row.shell_indicators,
            o.role = row.role
        """,
        rows=[
            {
                "id": org["id"],
                "name": org["name"],
                "jurisdiction": org.get("jurisdiction"),
                "business_type": org.get("business_type"),
                "shell_indicators": org.get("shell_indicators", []),
                "role": org.get("role"),
            }
            for org in organizations
        ],
    )

    # Connections reference either a customer id or a node name.
    await tx.run(
        """
        UNWIND $rows AS row
        MATCH (o:Organization {id: row.org_id})
        OPTIONAL MATCH (byId:Customer {id: row.target})
        OPTIONAL MATCH (customerByName:Customer {name: row.target})
        OPTIONAL MATCH (orgByName:Organization {name: row.target})
        WITH o, coalesce(byId, customerByName, orgByName) AS target
        WHERE target IS NOT NULL
        MERGE (o)-[:CONNECTED_TO]->(target)
        """,
        rows=[
            {"org_id": org["id"], "target": conn}
            for org in organizations
            for conn in org.get("connections", [])
        ],
    )
    await tx.run(
        """
        UNWIND $rows AS row
        MATCH (o:Organization {id: row.org_id})
        MATCH (c:Customer {id: row.owner_id})
        MERGE (c)-[owns:OWNS]->(o)
        SET owns.percentage = 100
        """,
        rows=[
            {"org_id": org["id"], "owner_id": owner}
            for org in organizations
            for owner in org.get("owners", [])
        ],
    )

    await tx.run(
        f"""
        UNWIND $rows AS row
        MATCH (c:Customer {{id: row.customer_id}})
        MERGE (t:Transaction:{MARKER} {{id: row.id}})
        SET t.date = date(row.date),
            t.type = row.type,
            t.amount = row.amount,
            t.currency = row.currency,
            t.counterparty = row.counterparty,
            t.description = row.description
        MERGE (c)-[:HAS_TRANSACTION]->(t)
        """,
        rows=transaction_rows(transactions, today=today),
    )

    for cypher in STORYLINE_RELATIONSHIPS:
        await tx.run(cypher)

    await tx.run(
        f"""
        UNWIND $rows AS row
        MERGE (s:SanctionedEntity:{MARKER} {{name: row.name}})
        SET s.list = row.list, s.reason = row.reason, s.added = date(row.added)
        """,
        rows=[
            {
                "name": entity["name"],
                "list": entity["list"],
                "reason": entity["reason"],
                "added": entity["added"],
            }
            for entity in sanctions
        ],
    )
    await tx.run(
        f"""
        UNWIND $rows AS row
        MATCH (s:SanctionedEntity {{name: row.entity_name}})
        MERGE (a:SanctionAlias:{MARKER} {{name: row.alias}})
        MERGE (a)-[:ALIAS_OF]->(s)
        """,
        rows=[
            {"entity_name": entity["name"], "alias": alias}
            for entity in sanctions
            for alias in entity.get("aliases", [])
        ],
    )

    await tx.run(
        f"""
        UNWIND $rows AS row
        MERGE (p:PEP:{MARKER} {{name: row.name}})
        SET p.position = row.position, p.country = row.country, p.tier = row.tier
        """,
        rows=pep_data["peps"],
    )
    await tx.run(
        f"""
        UNWIND $rows AS row
        MATCH (p:PEP {{name: row.pep}})
        MERGE (r:PEPRelative:{MARKER} {{name: row.name}})
        SET r.relation = row.relation
        MERGE (r)-[:RELATIVE_OF]->(p)
        """,
        rows=pep_data.get("pep_relatives", []),
    )

    await tx.run(
        f"""
        UNWIND $rows AS row
        MATCH (c:Customer {{id: row.customer_id}})
        MERGE (a:Alert:{MARKER} {{id: row.id}})
        SET a.type = row.type, a.severity = row.severity, a.status = row.status,
            a.title = row.title, a.description = row.description,
            a.evidence = row.evidence, a.requires_sar = row.requires_sar,
            a.auto_generated = row.auto_generated,
            a.created_at = coalesce(a.created_at, datetime())
        MERGE (c)-[:HAS_ALERT]->(a)
        """,
        rows=[
            {
                "id": alert["id"],
                "customer_id": alert["customer_id"],
                "type": alert["type"],
                "severity": alert["severity"],
                "status": alert["status"],
                "title": alert["title"],
                "description": alert["description"],
                "evidence": alert.get("evidence", []),
                "requires_sar": alert.get("requires_sar", False),
                "auto_generated": alert.get("auto_generated", True),
            }
            for alert in alerts
        ],
    )
    await tx.run(
        """
        UNWIND $rows AS row
        MATCH (a:Alert {id: row.alert_id}), (t:Transaction {id: row.transaction_id})
        MERGE (a)-[:RELATED_TO_TRANSACTION]->(t)
        """,
        rows=[
            {"alert_id": alert["id"], "transaction_id": txn_id}
            for alert in alerts
            for txn_id in alert.get("transaction_ids", [])
        ],
    )

    return {
        "customers": len(customers),
        "organizations": len(organizations),
        "transactions": len(transactions),
        "sanctioned_entities": len(sanctions),
        "peps": len(pep_data["peps"]),
        "alerts": len(alerts),
    }


async def report_counts(session: Any) -> None:
    result = await session.run(
        """
        MATCH (n:Compliance)
        UNWIND labels(n) AS label
        WITH label, count(*) AS count
        WHERE label <> 'Compliance'
        RETURN label, count ORDER BY label
        """
    )
    logger.info("Compliance graph now holds:")
    for record in await result.data():
        logger.info("  %-18s %s", record["label"], record["count"])


async def load_data(
    uri: str,
    username: str,
    password: str,
    database: str,
    *,
    reset: bool,
    assume_yes: bool,
    today: date | None = None,
) -> None:
    """Load (or re-load) the demo graph. Idempotent unless ``reset`` is set."""
    today = today or date.today()
    logger.info("Target: %s (database %s) as %s", uri, database, username)

    # The storyline MERGEs match two single nodes by id/name, which the planner
    # reports as an (harmless, 1x1) cartesian product. Keep the loader's output
    # readable by raising the notification floor rather than contorting them.
    driver = AsyncGraphDatabase.driver(
        uri, auth=(username, password), notifications_min_severity="WARNING"
    )
    try:
        async with driver.session(database=database) as session:
            if reset:
                doomed = await count_demo_nodes(session)
                logger.warning(
                    "--reset will DETACH DELETE %s node(s) carrying the demo labels "
                    "(%s) at %s. Agent memory and other data are untouched.",
                    doomed,
                    ", ".join(DEMO_LABELS),
                    uri,
                )
                if doomed and not assume_yes and sys.stdin.isatty():
                    answer = input("Type 'yes' to continue: ").strip().lower()
                    if answer != "yes":
                        raise SystemExit("Aborted; nothing was deleted.")
                await reset_demo_data(session)
                logger.info("Demo labels cleared.")

            await create_constraints(session)
            counts = await session.execute_write(_load_all, today=today)
            logger.info("Loaded fixture: %s", counts)
            await report_counts(session)
    finally:
        await driver.close()


# ---------------------------------------------------------------------------
# Optional second phase: adopt the domain graph as long-term memory
# ---------------------------------------------------------------------------


def embedding_spec() -> str:
    """Embedding provider spec for the adoption phase.

    ``MemoryClient.connect()`` sizes the memory vector indexes from the
    configured embedder and then validates existing indexes against it, so
    this must match whatever the backend you run uses — otherwise the
    backend raises ``EmbeddingDimensionMismatchError`` on startup. The
    defaults follow the two backends' own configuration; override with
    ``EMBEDDING_SPEC`` if yours differs.
    """
    explicit = os.environ.get("EMBEDDING_SPEC")
    if explicit:
        return explicit
    if os.environ.get("BEDROCK_EMBEDDING_MODEL_ID") or os.environ.get("AWS_REGION"):
        model = os.environ.get("BEDROCK_EMBEDDING_MODEL_ID", "amazon.titan-embed-text-v2:0")
        return f"bedrock/{model}"
    if os.environ.get("GOOGLE_CLOUD_PROJECT"):
        model = os.environ.get("VERTEX_EMBEDDING_MODEL", "gemini-embedding-001")
        return f"vertex_ai/{model}"
    # No cloud credentials in the environment: a local model keeps --adopt
    # runnable, but note the dimension caveat above.
    return "sentence-transformers/all-MiniLM-L6-v2"


async def adopt_existing_graph(
    uri: str,
    username: str,
    password: str,
    database: str,
    *,
    dry_run: bool = False,
) -> None:
    """Attach the library's ``:Entity`` super-label to the domain graph.

    After this runs, the compliance nodes *are* long-term memory entities:
    ``client.long_term.get_related_entities`` / ``expand_graph`` traverse the
    same customers and organizations the Cypher tools query, and entity
    extraction on a conversation turn links mentions to these nodes instead of
    MERGEing duplicates beside them.

    Bolt only — ``client.schema`` raises ``NotSupportedError`` on hosted NAMS,
    where the schema is server-managed.
    """
    from pydantic import SecretStr

    from neo4j_agent_memory import MemoryClient, MemorySettings, Neo4jConfig
    from neo4j_agent_memory.config.settings import (
        ExtractionConfig,
        ExtractorType,
        SchemaConfig,
        SchemaModel,
    )

    entity_types = list(dict.fromkeys(LABEL_TO_TYPE.values()))
    settings = MemorySettings(
        neo4j=Neo4jConfig(
            uri=uri, username=username, password=SecretStr(password), database=database
        ),
        llm=None,
        embedding=embedding_spec(),
        # The compliance types are POLE+O here, but declaring the schema
        # explicitly is what a non-POLE+O domain graph would do.
        schema_config=SchemaConfig(model=SchemaModel.CUSTOM, entity_types=entity_types),
        extraction=ExtractionConfig(
            extractor_type=ExtractorType.NONE,
            enable_spacy=False,
            enable_gliner=False,
            enable_llm_fallback=False,
        ),
    )

    logger.info(
        "Adopting the domain graph as long-term memory (%s, embedding=%s)",
        "dry run" if dry_run else "writing",
        embedding_spec(),
    )
    async with MemoryClient(settings) as client:
        report = await client.schema.adopt_existing_graph(
            label_to_type=LABEL_TO_TYPE,
            name_property_per_label=NAME_PROPERTY_PER_LABEL,
            dry_run=dry_run,
        )
        verb = "Would adopt" if dry_run else "Adopted"
        logger.info(
            "%s %s node(s): %s already adopted, %s skipped",
            verb,
            report.total_migrated,
            report.total_already_adopted,
            report.total_skipped,
        )
        for label_report in report.by_label:
            logger.info(
                "  %-18s → %-12s +%s new, =%s already, ~%s skipped",
                label_report.label,
                label_report.type,
                label_report.migrated_count,
                label_report.already_adopted_count,
                label_report.skipped_count,
            )


# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--uri", default=None, help="Neo4j URI (default: $NEO4J_URI)")
    parser.add_argument(
        "--username",
        "--user",
        dest="username",
        default=None,
        help="Neo4j user (default: $NEO4J_USERNAME, then $NEO4J_USER)",
    )
    parser.add_argument(
        "--password", default=None, help="Neo4j password (default: $NEO4J_PASSWORD, required)"
    )
    parser.add_argument(
        "--database", default=None, help="Neo4j database (default: $NEO4J_DATABASE)"
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Delete existing demo nodes first (demo labels only; agent memory is kept)",
    )
    parser.add_argument(
        "--yes", action="store_true", help="Skip the interactive confirmation for --reset"
    )
    parser.add_argument(
        "--adopt",
        action="store_true",
        help="After loading, adopt the domain graph as long-term memory entities",
    )
    parser.add_argument(
        "--adopt-only",
        action="store_true",
        help="Run only the adoption phase against an already-loaded graph",
    )
    parser.add_argument(
        "--dry-run-adopt",
        action="store_true",
        help="Report what adoption would change without mutating the graph",
    )
    return parser


async def run(args: argparse.Namespace) -> None:
    uri, username, password, database = resolve_credentials(args)
    if not args.adopt_only:
        await load_data(
            uri,
            username,
            password,
            database,
            reset=args.reset,
            assume_yes=args.yes,
        )
    if args.adopt or args.adopt_only or args.dry_run_adopt:
        await adopt_existing_graph(uri, username, password, database, dry_run=args.dry_run_adopt)
    logger.info("Done.")


def main() -> None:
    load_env_files()
    asyncio.run(run(build_parser().parse_args()))


if __name__ == "__main__":
    main()
