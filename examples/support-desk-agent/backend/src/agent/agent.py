"""The support-desk agent (PydanticAI 2.x).

The agent has the eleven tools of the STATE-Bench customer-support environment,
under their own names and JSON schemas (``get_order``, ``get_policies``,
``process_return`` ...). They run the vendored environment against the records
in the memory graph, so a confirmed return or exchange changes the graph. Four
more tools read memory itself: earlier traces, customers, earlier conversations
and the ontology.

``AGENT_MODEL`` is any PydanticAI model string (``openai:gpt-5-mini`` by
default). ``AGENT_MODEL=test`` selects PydanticAI's ``TestModel``: it calls
every tool once with placeholder arguments and answers with a JSON dump of the
results. It needs no key and exercises the whole streaming and reasoning-trace
path, which is what the smoke tests and CI use it for. It is not a real agent.
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

from pydantic_ai import Agent, RunContext, Tool
from pydantic_ai.models import Model
from pydantic_ai.models.test import TestModel

from src.agent import tools
from src.agent.deps import SupportDeskDeps
from src.config import TEST_MODEL, get_settings
from src.ontology import ORDER_LINE, role_label
from src.statebench import world
from src.statebench.vendor import tools as statebench_tools

#: The memory tools, then the STATE-Bench tools.
MEMORY_TOOL_NAMES = (
    "recall_similar_tasks",
    "find_customer",
    "search_support_history",
    "get_ontology",
)
TOOL_NAMES: tuple[str, ...] = MEMORY_TOOL_NAMES + world.TOOL_NAMES

INSTRUCTIONS = """\
You are a customer service agent for an e-commerce company. You help customers
with orders, returns, refunds, exchanges, cancellations, shipping problems and
warranty claims.

Your memory is a Neo4j knowledge graph. It holds the store's records
(customers, orders, order lines, products, warranties and policies), earlier
support conversations, and reasoning traces: how earlier requests were handled,
tool call by tool call.

How to work:
1. When a new request starts, call `recall_similar_tasks` with a one-line
   description of it. Earlier work shows which tools and policy checks resolved
   a similar case; follow it when it fits. A follow-up in the same conversation
   ("yes, go ahead") continues the request: do not recall or look up again.
2. Identify the customer. `find_customer` turns a name, email or customer id
   into the customer and their orders; `get_customer` then gives the profile
   (membership tier, store credit, Prime shipping).
3. Look things up rather than guessing: `get_order`, `get_product_details`,
   `search_products`, `get_warranty_status`.
4. Before any change, call `get_policies` for the topic (return, refund,
   exchange, cancellation, shipping or warranty). Every change is two steps:
   call the tool with confirm=false to preview it, tell the customer what will
   happen, and only call it again with confirm=true once they agree. For
   `process_return`, submit as `amount` the net refund computed from the
   preview's breakdown.
5. Do not decide eligibility, windows, fees or amounts yourself. Each order is
   evaluated at its own date (`today` in the tool results), not at the
   calendar date; the preview says what is eligible and what it costs.
6. Never make a change the customer has not asked for. If a request is
   ambiguous (which item? which order?), ask instead of guessing.
7. Keep customer-facing replies natural and direct, usually 1-4 sentences. Do
   not reveal tool names or policy categories; you may say you checked the
   policy. Cite order and item ids (ORD-…, ITEM-…) when they help.
"""


def _resolve_model(model: str | Model) -> str | Model:
    if isinstance(model, str) and model.strip().lower() == TEST_MODEL:
        return TestModel()
    return model


def _statebench_tool(schema: dict[str, Any]) -> Tool[SupportDeskDeps]:
    """One STATE-Bench tool, with the benchmark's own name, description and schema."""
    name = str(schema["name"])

    async def call(ctx: RunContext[SupportDeskDeps], **arguments: Any) -> dict[str, Any]:
        return await world.run_tool(ctx.deps.client, ctx.deps.session_id, name, arguments)

    return Tool.from_schema(
        call,
        name=name,
        description=str(schema.get("description") or ""),
        json_schema=schema["parameters"],
        takes_ctx=True,
        # A write reads, changes and stores records: one at a time.
        sequential=name in world.WRITE_TOOL_NAMES,
    )


def build_agent(model: str | Model) -> Agent[SupportDeskDeps, str]:
    """Build the agent for ``model`` (a PydanticAI model string, ``test``, or a Model)."""
    agent: Agent[SupportDeskDeps, str] = Agent(
        _resolve_model(model),
        deps_type=SupportDeskDeps,
        output_type=str,
        instructions=INSTRUCTIONS,
        tools=[_statebench_tool(schema) for schema in statebench_tools.TOOL_SCHEMAS],
    )

    @agent.instructions
    def active_labels(ctx: RunContext[SupportDeskDeps]) -> str:
        """Name the ontology and the order-line label this client uses."""
        client = ctx.deps.client
        document = client.ontology_document
        domain = document.domain.id if document is not None else "none"
        return (
            f"This session's ontology: {domain} ({client.validation_mode}); "
            f"order lines carry the `{role_label(document, ORDER_LINE)}` label."
        )

    @agent.tool
    async def recall_similar_tasks(ctx: RunContext[SupportDeskDeps], task: str) -> dict[str, Any]:
        """Find earlier successful agent work on a similar task (reasoning memory).

        Call this first for any non-trivial request.

        Args:
            task: A one-line description of what the customer wants done.
        """
        return await tools.recall_similar_tasks(ctx.deps.client, task)

    @agent.tool
    async def find_customer(ctx: RunContext[SupportDeskDeps], name: str) -> dict[str, Any]:
        """Find a customer by full name, first name, email or customer id.

        Returns the customer (with the `customer_id` that `get_customer` takes),
        their orders and the conversations that mention them.

        Args:
            name: The customer's name, email or id, as the customer gave it.
        """
        return await tools.find_customer(ctx.deps.client, name)

    @agent.tool
    async def search_support_history(
        ctx: RunContext[SupportDeskDeps], query: str
    ) -> dict[str, Any]:
        """Search earlier support conversations by meaning, plus matching entities.

        Leaves out this conversation, which you already have. Each hit lists
        the orders and products its conversation mentions.

        Args:
            query: What to look for, in plain words.
        """
        return await tools.search_support_history(
            ctx.deps.client, query, exclude_session_id=ctx.deps.session_id
        )

    @agent.tool
    async def get_ontology(ctx: RunContext[SupportDeskDeps]) -> dict[str, Any]:
        """Show the active ontology revision: labels, relationships and validation mode."""
        return await tools.get_ontology(ctx.deps.client)

    return agent


@lru_cache
def get_agent() -> Agent[SupportDeskDeps, str]:
    """The agent for ``AGENT_MODEL`` (cached; tests call ``cache_clear()``)."""
    settings = get_settings()
    key = settings.openai_api_key.get_secret_value()
    if key:
        # PydanticAI's OpenAI provider reads the key from the environment.
        os.environ.setdefault("OPENAI_API_KEY", key)
    return build_agent(settings.agent_model)
