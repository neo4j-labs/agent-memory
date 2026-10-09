"""The STATE-Bench records as entities in the memory graph, and the tools run against them.

Every customer, order, order line, product and warranty the environment knows is
an ontology-typed entity (``:Entity:Person:Customer``, ``:Entity:Event:Order``,
``:Entity:Object:OrderLine`` ...) carrying the environment's record as a JSON
``record`` property, linked by ``PLACED``, ``CONTAINS``, ``OF_PRODUCT``,
``COVERS`` and ``REPLACED_BY``. Entity names are what people write: the
customer's name (``cust_005`` and the email are aliases), the product's name
(every product id is an alias), and the order, item and warranty ids. So a
mention GLiNER2.5 extracts from a message resolves exactly onto its record.

:func:`run_tool` loads the records, builds the vendored
``CustomerSupportEnvironment`` from them, calls one tool and writes whatever
the tool changed back to the graph: a processed return updates the order line,
an exchange adds the replacement line with its ``REPLACED_BY`` edge. The
environment's rules are stateful within a conversation (a write tool needs
``get_policies`` first, and a preview before it confirms), so that state is
kept per chat session and restored into each fresh environment.
"""

from __future__ import annotations

import asyncio
import json
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from neo4j_agent_memory import BoltMemoryClient
from src.ontology import CUSTOMER, ORDER, ORDER_LINE, POLICY, PRODUCT, WARRANTY
from src.statebench.dataset import ID_FIELDS, RECORD_KINDS
from src.statebench.vendor import tools as tool_schemas
from src.statebench.vendor.environment import CustomerSupportEnvironment
from src.statebench.vendor.schemas import CSEnvironmentData

#: The ontology role of each record kind.
RECORD_ROLES: dict[str, tuple[str, str]] = {
    "customers": CUSTOMER,
    "products": PRODUCT,
    "orders": ORDER,
    "order_items": ORDER_LINE,
    "warranties": WARRANTY,
}

#: The six topics ``get_policies`` answers, one Policy entity each.
POLICY_TOPICS = ("return", "refund", "exchange", "cancellation", "shipping", "warranty")

#: The environment's tools, by name, and the ones that change records.
TOOL_NAMES: tuple[str, ...] = tuple(schema["name"] for schema in tool_schemas.TOOL_SCHEMAS)
WRITE_TOOL_NAMES = frozenset(tool_schemas.WRITE_TOOL_NAMES)

#: Cap on audit edges per tool call, so one broad result cannot flood the graph.
MAX_TOUCHED = 12

#: Chat sessions whose policy and preview state is kept (least recently used dropped).
MAX_SESSIONS = 256

# ``properties(e).x`` and ``'x' IN keys(e)`` rather than ``e.x``: on a database
# the seed has not run against, the property does not exist yet and Neo4j would
# warn about a static reference to it.
LOAD_RECORDS = """
MATCH (e:Entity)
WHERE 'record_kind' IN keys(e) AND NOT 'merged_into' IN keys(e)
RETURN e.id AS id, e.name AS name, e.type AS type, properties(e).record_kind AS kind,
       properties(e).record AS record, properties(e).as_of AS as_of
"""

#: A mention extracted under the wrong type ("ORD-7213" typed Product) cannot
#: resolve onto its record, because resolution never crosses types. When a
#: mentioned entity's whole name is a record's id, the MENTIONS edge is moved to
#: the record, and the stray entity is removed once nothing mentions it.
REPAIR_ID_MENTIONS = """
MATCH (m:Message)-[r:MENTIONS]->(stray:Entity)
WHERE m.id IN $message_ids
  AND NOT 'record_kind' IN keys(stray) AND NOT 'merged_into' IN keys(stray)
MATCH (record:Entity)
WHERE 'record_ids' IN keys(record) AND stray.name IN record.record_ids
MERGE (m)-[moved:MENTIONS]->(record)
ON CREATE SET moved = properties(r)
DELETE r
WITH DISTINCT stray
WHERE NOT EXISTS { MATCH (stray)<-[:MENTIONS]-(:Message) }
DETACH DELETE stray
RETURN count(stray) AS removed
"""

SET_POLICY_LABEL = """
MATCH (e:Entity {id: $id}) SET e:Policy
"""

SET_RECORD = """
MATCH (e:Entity {id: $id})
SET e.record_kind = $kind, e.record = $record, e.record_ids = $record_ids,
    e.description = $description,
    e.as_of = coalesce($as_of, e.as_of),
    e.source_task = coalesce($source_task, e.source_task)
"""


@dataclass
class GraphWorld:
    """The records as the graph holds them, plus where each one lives."""

    records: dict[str, dict[str, dict[str, Any]]] = field(
        default_factory=lambda: {kind: {} for kind in RECORD_KINDS}
    )
    #: record id (``ORD-6014``, ``PROD-2092``, ``cust_005``, ``policy:return``) -> entity id.
    entity_of: dict[str, str] = field(default_factory=dict)
    #: entity id -> (name, POLE+O type), for TOUCHED edges.
    entities: dict[str, tuple[str, str]] = field(default_factory=dict)
    #: order_id -> the date the order's task is evaluated at.
    as_of: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# How each record is named and described
# ---------------------------------------------------------------------------


def record_kind(kind: str) -> str:
    """The ``record_kind`` property value: ``order_items`` -> ``item``."""
    return {"order_items": "item", "warranties": "warranty", "policies": "policy"}.get(
        kind, kind.rstrip("s")
    )


def _money(value: Any) -> str:
    return f"${value}" if value is not None else "n/a"


def describe(kind: str, record: dict[str, Any], products: dict[str, dict[str, Any]]) -> str:
    """A one-line description of a record, for the entity's ``description``."""
    if kind == "customers":
        return (
            f"{record.get('name')} ({record.get('customer_id')}), "
            f"{record.get('membership_tier', 'standard')} member, store credit "
            f"{_money(record.get('store_credit_balance'))}"
            + (", Prime shipping" if record.get("has_prime_shipping") else "")
        )
    if kind == "orders":
        return (
            f"Order {record.get('order_id')}: {record.get('status')}, shipping "
            f"{record.get('shipping_status')}, total paid {_money(record.get('total_paid'))}"
        )
    if kind == "order_items":
        product = products.get(str(record.get("product_id")), {})
        text = (
            f"{record.get('quantity', 1)} x {product.get('name', record.get('product_id'))} "
            f"on {record.get('order_id')}: {record.get('item_status')}"
        )
        if record.get("refund_amount") is not None:
            text += f", refunded {_money(record.get('refund_amount'))}"
        return text
    if kind == "warranties":
        return (
            f"{record.get('warranty_type', 'manufacturer')} warranty {record.get('warranty_id')} "
            f"for {record.get('item_id')}: {record.get('status')} until {record.get('end_date')}"
        )
    raise ValueError(f"Unknown record kind {kind!r}")


def describe_product(name: str, records: dict[str, dict[str, Any]]) -> str:
    first = next(iter(records.values()))
    return (
        f"{name}: {first.get('category')} ({first.get('subcategory')}), "
        f"{_money(first.get('price'))}, {first.get('return_window_days')}-day returns, "
        f"{first.get('warranty_months')}-month warranty"
    )


# ---------------------------------------------------------------------------
# Writing records
# ---------------------------------------------------------------------------


async def _create_entity(
    client: BoltMemoryClient,
    role: tuple[str, str],
    name: str,
    *,
    description: str,
    aliases: list[str],
) -> str:
    """One entity under ``role``. Resolution is off: records are the ground truth."""
    entity, _ = await client.long_term.add_entity(
        name,
        role[0],
        subtype=role[1],
        description=description,
        aliases=aliases,
        resolve=False,
        deduplicate=False,
        enrich=False,
        geocode=False,
    )
    return str(entity.id)


async def _set_record(
    client: BoltMemoryClient,
    entity_id: str,
    kind: str,
    record: Any,
    *,
    record_ids: list[str],
    description: str,
    as_of: str | None = None,
    source_task: str | None = None,
) -> None:
    await client.graph.execute_write(
        SET_RECORD,
        {
            "id": entity_id,
            "kind": record_kind(kind),
            "record": json.dumps(record, sort_keys=True),
            "record_ids": record_ids,
            "description": description,
            "as_of": as_of,
            "source_task": source_task,
        },
    )


async def create_record(
    client: BoltMemoryClient,
    world: GraphWorld,
    kind: str,
    record: dict[str, Any],
    *,
    as_of: str | None = None,
    source_task: str | None = None,
) -> str:
    """Store a customer, order, item or warranty record as an entity."""
    record_id = str(record[ID_FIELDS[kind]])
    if kind == "customers":
        name = str(record["name"])
        aliases = [record_id] + ([str(record["email"])] if record.get("email") else [])
    else:
        name, aliases = record_id, []
    description = describe(kind, record, world.records["products"])
    entity_id = await _create_entity(
        client, RECORD_ROLES[kind], name, description=description, aliases=aliases
    )
    await _set_record(
        client,
        entity_id,
        kind,
        record,
        record_ids=[record_id],
        description=description,
        as_of=as_of,
        source_task=source_task,
    )
    world.records[kind][record_id] = record
    world.entity_of[record_id] = entity_id
    world.entities[entity_id] = (name, RECORD_ROLES[kind][0])
    if kind == "orders" and as_of:
        world.as_of[record_id] = as_of
    return entity_id


async def create_product(
    client: BoltMemoryClient, world: GraphWorld, name: str, records: dict[str, dict[str, Any]]
) -> str:
    """One Product entity per catalogue name; every product id is an alias."""
    ids = sorted(records)
    description = describe_product(name, records)
    entity_id = await _create_entity(client, PRODUCT, name, description=description, aliases=ids)
    await _set_record(
        client, entity_id, "products", records, record_ids=ids, description=description
    )
    for product_id, record in records.items():
        world.records["products"][product_id] = record
        world.entity_of[product_id] = entity_id
    world.entities[entity_id] = (name, PRODUCT[0])
    return entity_id


async def create_policy(client: BoltMemoryClient, world: GraphWorld, topic: str) -> str:
    """A Policy entity for one ``get_policies`` topic, holding the rules it returns."""
    rules = CustomerSupportEnvironment(
        CSEnvironmentData([], [], [], [], []), "2026-01-01T00:00:00"
    ).get_policies({"topic": topic})
    name = f"{topic.capitalize()} policy"
    description = f"The store's {topic} policy, as get_policies(topic={topic!r}) returns it."
    entity_id = await _create_entity(client, POLICY, name, description=description, aliases=[])
    await _set_record(
        client,
        entity_id,
        "policies",
        rules,
        record_ids=[f"policy:{topic}"],
        description=description,
    )
    # The ontology does not declare policies, so OBJECT:POLICY gets no subtype
    # label of its own; give the node one.
    await client.graph.execute_write(SET_POLICY_LABEL, {"id": entity_id})
    world.entity_of[f"policy:{topic}"] = entity_id
    world.entities[entity_id] = (name, POLICY[0])
    return entity_id


async def _relate(
    client: BoltMemoryClient, source: str | None, target: str | None, rel: str
) -> None:
    if source and target and source != target:
        await client.long_term.add_relationship(
            UUID(source), UUID(target), rel, extractor="state-bench"
        )


async def link_record(
    client: BoltMemoryClient, world: GraphWorld, kind: str, record: dict[str, Any]
) -> None:
    """The typed edges a record implies (owner -> owned)."""
    entity = world.entity_of.get(str(record[ID_FIELDS[kind]]))
    if kind == "orders":
        await _relate(client, world.entity_of.get(str(record.get("customer_id"))), entity, "PLACED")
    elif kind == "order_items":
        await _relate(client, world.entity_of.get(str(record.get("order_id"))), entity, "CONTAINS")
        await _relate(
            client, entity, world.entity_of.get(str(record.get("product_id"))), "OF_PRODUCT"
        )
        replacement = record.get("replacement_item_id")
        if replacement:
            await _relate(client, entity, world.entity_of.get(str(replacement)), "REPLACED_BY")
    elif kind == "warranties":
        await _relate(client, entity, world.entity_of.get(str(record.get("item_id"))), "COVERS")


# ---------------------------------------------------------------------------
# Reading records
# ---------------------------------------------------------------------------


async def load_world(client: BoltMemoryClient) -> GraphWorld:
    """Every record entity in the graph."""
    world = GraphWorld()
    by_kind = {record_kind(kind): kind for kind in (*RECORD_KINDS, "policies")}
    for row in await client.query.cypher(LOAD_RECORDS):
        kind = by_kind.get(str(row["kind"]))
        if kind is None:
            continue
        entity_id = str(row["id"])
        world.entities[entity_id] = (str(row["name"]), str(row["type"]))
        record = json.loads(row["record"]) if row.get("record") else {}
        if kind == "policies":
            world.entity_of[f"policy:{record.get('topic', row['name'])}"] = entity_id
        elif kind == "products":
            for product_id, product in record.items():
                world.records["products"][product_id] = product
                world.entity_of[product_id] = entity_id
        else:
            record_id = str(record[ID_FIELDS[kind]])
            world.records[kind][record_id] = record
            world.entity_of[record_id] = entity_id
            if kind == "orders" and row.get("as_of"):
                world.as_of[record_id] = str(row["as_of"])
    return world


def environment_data(world: GraphWorld) -> CSEnvironmentData:
    return CSEnvironmentData.from_dict(
        {kind: list(world.records[kind].values()) for kind in RECORD_KINDS}
    )


def now_for(world: GraphWorld, arguments: dict[str, Any]) -> str:
    """The date a call is evaluated at: its order's task date, else the latest one."""
    order_id = arguments.get("order_id")
    item_id = arguments.get("item_id")
    if not order_id and item_id:
        order_id = world.records["order_items"].get(str(item_id), {}).get("order_id")
    if not order_id and arguments.get("warranty_id"):
        order_id = (
            world.records["warranties"].get(str(arguments["warranty_id"]), {}).get("order_id")
        )
    if order_id and str(order_id) in world.as_of:
        return world.as_of[str(order_id)]
    return max(world.as_of.values(), default="2026-07-20T10:00:00")


def touched(world: GraphWorld, tool_name: str, *payloads: Any) -> list[dict[str, Any]]:
    """The entities a call's arguments and result name, as ``{"id", "name", "type"}``."""
    found: list[str] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for item in value.values():
                visit(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                visit(item)
        elif isinstance(value, str):
            entity = world.entity_of.get(value)
            if entity and entity not in found:
                found.append(entity)

    if tool_name == "get_policies":
        for payload in payloads[:1]:
            topic = payload.get("topic") if isinstance(payload, dict) else None
            entity = world.entity_of.get(f"policy:{topic}")
            if entity:
                found.append(entity)
    for payload in payloads:
        visit(payload)
    return [
        {"id": entity, "name": world.entities[entity][0], "type": world.entities[entity][1]}
        for entity in found[:MAX_TOUCHED]
        if entity in world.entities
    ]


# ---------------------------------------------------------------------------
# Running a tool
# ---------------------------------------------------------------------------


@dataclass
class _Gates:
    """The environment's per-conversation state: policies looked up, previews made."""

    policies_checked: set[str] = field(default_factory=set)
    previewed: dict[str, set[str]] = field(default_factory=dict)
    previewed_refunds: dict[str, set[tuple[str, Any]]] = field(default_factory=dict)


_gates: OrderedDict[str, _Gates] = OrderedDict()
_locks: dict[asyncio.AbstractEventLoop, asyncio.Lock] = {}


def _lock() -> asyncio.Lock:
    """One lock per event loop: calls are serialised, so no write is lost."""
    loop = asyncio.get_running_loop()
    if loop not in _locks:
        _locks[loop] = asyncio.Lock()
    return _locks[loop]


def _restore(env: CustomerSupportEnvironment, session_id: str) -> None:
    gates = _gates.get(session_id)
    if gates is None:
        return
    _gates.move_to_end(session_id)
    # The environment keeps this state on private attributes; it is restored
    # as the environment itself left it after the previous call.
    env._policies_checked = set(gates.policies_checked)
    for name, done in gates.previewed.items():
        env._previewed.setdefault(name, set()).update(done)
    env._previewed_refunds = {key: set(value) for key, value in gates.previewed_refunds.items()}


def _save(env: CustomerSupportEnvironment, session_id: str) -> None:
    _gates[session_id] = _Gates(
        policies_checked=set(env._policies_checked),
        previewed={name: set(done) for name, done in env._previewed.items()},
        previewed_refunds={key: set(value) for key, value in env._previewed_refunds.items()},
    )
    _gates.move_to_end(session_id)
    while len(_gates) > MAX_SESSIONS:
        _gates.popitem(last=False)


async def repair_id_mentions(client: BoltMemoryClient, message_ids: list[str]) -> int:
    """Point id-shaped mentions extracted under the wrong type at their records.

    GLiNER2.5 types about one order-id mention in six as a product or an order
    line; the id itself is unambiguous. Returns how many stray entities were
    removed.
    """
    if not message_ids:
        return 0
    rows = await client.graph.execute_write(REPAIR_ID_MENTIONS, {"message_ids": message_ids})
    return int(rows[0]["removed"]) if rows else 0


def forget_session(session_id: str) -> None:
    _gates.pop(session_id, None)


async def _persist(
    client: BoltMemoryClient,
    world: GraphWorld,
    before: dict[str, dict[str, dict[str, Any]]],
    after: dict[str, dict[str, dict[str, Any]]],
) -> list[str]:
    """Write every record the call changed or created; new records are linked."""
    changed: list[str] = []
    created: list[tuple[str, dict[str, Any]]] = []
    for kind in ("customers", "orders", "order_items", "warranties"):
        for record_id, record in after.get(kind, {}).items():
            if before.get(kind, {}).get(record_id) == record:
                continue
            entity_id = world.entity_of.get(record_id)
            if entity_id is None:
                order_id = record.get("order_id")
                await create_record(
                    client,
                    world,
                    kind,
                    record,
                    as_of=world.as_of.get(str(order_id)) if order_id else None,
                    source_task="live",
                )
                created.append((kind, record))
            else:
                description = describe(kind, record, world.records["products"])
                await _set_record(
                    client,
                    entity_id,
                    kind,
                    record,
                    record_ids=[record_id],
                    description=description,
                )
                world.records[kind][record_id] = record
                previous = before.get(kind, {}).get(record_id, {})
                if kind == "order_items" and record.get("replacement_item_id") != previous.get(
                    "replacement_item_id"
                ):
                    created.append((kind, record))
            changed.append(record_id)
    for kind, record in created:
        await link_record(client, world, kind, record)
    return changed


async def run_tool(
    client: BoltMemoryClient, session_id: str, tool_name: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Run one STATE-Bench tool against the graph's records and persist its changes."""
    if tool_name not in TOOL_NAMES:
        return {"error": f"Unknown tool {tool_name!r}."}
    async with _lock():
        world = await load_world(client)
        now = now_for(world, arguments)
        env = CustomerSupportEnvironment(environment_data(world), now)
        _restore(env, session_id)
        before = env.get_full_snapshot()
        try:
            result = env.tool_handlers[tool_name](dict(arguments))
        except Exception as exc:  # a malformed argument, not a failed turn
            result = {"error": f"{type(exc).__name__}: {exc}"}
        _save(env, session_id)
        if tool_name in WRITE_TOOL_NAMES:
            await _persist(client, world, before, env.get_full_snapshot())
    result = result if isinstance(result, dict) else {"result": result}
    # The benchmark gives its agent today's date in the system prompt; here every
    # order has its own, so each result says which date it was evaluated at.
    return {**result, "today": now[:10]}
