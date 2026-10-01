"""The support-desk agent (PydanticAI 2.x).

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

from pydantic_ai import Agent, RunContext
from pydantic_ai.models import Model
from pydantic_ai.models.test import TestModel

from src.agent import tools
from src.agent.deps import SupportDeskDeps
from src.config import TEST_MODEL, get_settings
from src.ontology import TICKET, role_label

#: The tools, in the order the instructions introduce them.
TOOL_NAMES = (
    "recall_similar_tasks",
    "find_customer",
    "get_ticket",
    "list_tickets",
    "get_order",
    "search_support_history",
    "get_ontology",
)

INSTRUCTIONS = """\
You are a support-desk assistant for an online furniture and desk-accessories store.

Your memory is a Neo4j knowledge graph built from earlier support conversations.
It is typed by the `support-desk` ontology: customers, orders (numbers start
with SO-), products, and support tickets (references start with TK-). Which label
tickets carry depends on the active ontology revision: `Ticket` in revision 1,
`SupportCase` after the rename. The tools always use the active one.

How to work:
1. For any request that needs more than a greeting, call `recall_similar_tasks`
   first with a one-line description of the task. Earlier successful work shows
   which tools answered a similar question; follow it when it fits.
2. Look things up rather than guessing: `find_customer`, `get_ticket`,
   `list_tickets`, `get_order`, `search_support_history`, `get_ontology`.
3. Related items come from typed relationships when extraction found them, and
   from being mentioned together otherwise; each item says which (`via`).
   Treat "same conversation" links as likely, not certain.
4. A customer may appear twice, once by first name only, with a pending review
   pair (`possible_duplicates`). Say so instead of silently merging them.
5. Cite ticket and order ids (TK-…, SO-…) in your answer, keep it short, and
   say plainly when the graph has nothing on a question.
"""


def _resolve_model(model: str | Model) -> str | Model:
    if isinstance(model, str) and model.strip().lower() == TEST_MODEL:
        return TestModel()
    return model


def build_agent(model: str | Model) -> Agent[SupportDeskDeps, str]:
    """Build the agent for ``model`` (a PydanticAI model string, ``test``, or a Model)."""
    agent: Agent[SupportDeskDeps, str] = Agent(
        _resolve_model(model),
        deps_type=SupportDeskDeps,
        output_type=str,
        instructions=INSTRUCTIONS,
    )

    @agent.instructions
    def active_labels(ctx: RunContext[SupportDeskDeps]) -> str:
        """Name the ticket label this client extracts and queries with."""
        client = ctx.deps.client
        document = client.ontology_document
        domain = document.domain.id if document is not None else "none"
        return (
            f"This session's ontology: {domain} ({client.validation_mode}); "
            f"tickets carry the `{role_label(document, TICKET)}` label."
        )

    @agent.tool
    async def recall_similar_tasks(ctx: RunContext[SupportDeskDeps], task: str) -> dict[str, Any]:
        """Find earlier successful agent work on a similar task (reasoning memory).

        Call this first for any non-trivial request.

        Args:
            task: A one-line description of what the user wants done.
        """
        return await tools.recall_similar_tasks(ctx.deps.client, task)

    @agent.tool
    async def find_customer(ctx: RunContext[SupportDeskDeps], name: str) -> dict[str, Any]:
        """Look up a customer by full name, first name or alias.

        Returns the customer, their orders and tickets, possible duplicate
        records, and the conversations that mention them.

        Args:
            name: The customer's name as the user wrote it.
        """
        return await tools.find_customer(ctx.deps.client, name)

    @agent.tool
    async def get_ticket(ctx: RunContext[SupportDeskDeps], reference: str) -> dict[str, Any]:
        """Get one support ticket with its order, product, customer and message history.

        Args:
            reference: The ticket reference, e.g. TK-2210.
        """
        return await tools.get_ticket(ctx.deps.client, reference)

    @agent.tool
    async def list_tickets(
        ctx: RunContext[SupportDeskDeps], customer: str | None = None
    ) -> dict[str, Any]:
        """List support tickets with their orders and products.

        Args:
            customer: Only this customer's tickets (a name). Omit to list all tickets.
        """
        return await tools.list_tickets(ctx.deps.client, customer)

    @agent.tool
    async def get_order(ctx: RunContext[SupportDeskDeps], order_number: str) -> dict[str, Any]:
        """Get one order with its products, tickets and customer.

        Args:
            order_number: The order number, e.g. SO-4417.
        """
        return await tools.get_order(ctx.deps.client, order_number)

    @agent.tool
    async def search_support_history(
        ctx: RunContext[SupportDeskDeps], query: str
    ) -> dict[str, Any]:
        """Search every past support conversation by meaning, plus matching entities.

        Args:
            query: What to look for, in plain words.
        """
        return await tools.search_support_history(ctx.deps.client, query)

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
