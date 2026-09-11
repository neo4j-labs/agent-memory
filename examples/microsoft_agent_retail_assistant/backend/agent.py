"""Microsoft Agent Framework agent for the retail assistant.

Targets the Agent Framework 1.x GA line (``agent-framework-core>=1.17``
plus ``agent-framework-openai`` for the chat client).

What this module wires up:

* a chat client (OpenAI, or Azure OpenAI through the same class),
* the memory tools shipped by ``neo4j_agent_memory`` —
  ``create_memory_tools(memory, include_gds_tools=...)``,
* the catalog tools, which are **thin adapters** over ``backend/tools/*.py``
  (one implementation per operation; the REST API in ``main.py`` calls the
  same functions),
* ``memory.context_provider``, which owns conversation persistence. Nothing
  here calls ``memory.save_message()``: ``Neo4jContextProvider.after_run()``
  writes the user turn and the assistant turn, so doing both would store
  every message twice.
* a reasoning trace per turn, recorded through ``client.reasoning`` so it can
  carry ``triggered_by_message_id``, ``touched_entities`` audit edges and a
  structured ``TraceOutcome`` — including on failure.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncGenerator
from typing import Annotated, Any
from uuid import UUID

from agent_framework import Agent, FunctionTool, Message, tool
from agent_framework.openai import OpenAIChatClient
from memory_config import Settings
from tools import (
    add_to_cart,
    check_inventory,
    explain_product_connection,
    find_alternatives,
    get_bought_together,
    get_cart,
    get_product_details,
    get_recommendations,
    get_related_products,
    remove_from_cart,
    search_products,
)

from neo4j_agent_memory.embeddings.base import Embedder
from neo4j_agent_memory.integrations.microsoft_agent import (
    Neo4jMicrosoftMemory,
    create_memory_tools,
)
from neo4j_agent_memory.memory.reasoning import ToolCallStatus
from neo4j_agent_memory.schema import EntityRef, TraceOutcome

logger = logging.getLogger(__name__)

settings = Settings()

# System prompt for the retail assistant
SYSTEM_PROMPT = """You are a helpful shopping assistant for an online retail store. Your role is to:

1. Help customers find products that match their needs
2. Learn and remember their preferences (brands, styles, budget, sizes)
3. Provide personalized recommendations based on their history
4. Answer questions about products, availability, and shipping
5. Assist with comparing products and making decisions

Key behaviors:
- Always be helpful, friendly, and professional
- When customers express preferences, acknowledge them AND call remember_preference
  so they survive to the next conversation
- When recommending products, explain why they match the customer's needs
- If a product is out of stock, use find_alternatives to suggest substitutes
- Ask clarifying questions when needs are unclear

You have access to memory tools to:
- Search your memory for relevant past conversations and preferences
- Save new preferences the customer expresses
- Find products similar to ones discussed before
- Track the customer's shopping journey

Always use the appropriate tools to provide personalized assistance."""


def get_chat_client() -> OpenAIChatClient:
    """Create the chat client based on settings.

    At Agent Framework 1.x GA a single ``OpenAIChatClient`` serves both OpenAI
    and Azure OpenAI: passing ``azure_endpoint`` switches it to Azure routing.
    The old ``agent_framework.azure.AzureOpenAIResponsesClient`` is deprecated
    and lives in a separate pre-release distribution, so it is not used here.
    """
    if settings.azure_openai_endpoint:
        return OpenAIChatClient(
            model=settings.azure_openai_deployment or settings.openai_model,
            azure_endpoint=settings.azure_openai_endpoint,
            api_key=(
                settings.azure_openai_api_key.get_secret_value()
                if settings.azure_openai_api_key
                else None
            ),
            api_version=settings.azure_openai_api_version,
        )
    if settings.openai_api_key:
        return OpenAIChatClient(
            model=settings.openai_model,
            api_key=settings.openai_api_key.get_secret_value(),
        )
    raise ValueError(
        "No OpenAI configuration found. Set OPENAI_API_KEY, or AZURE_OPENAI_ENDPOINT "
        "plus AZURE_OPENAI_API_KEY. See backend/.env.example."
    )


def get_product_tools(
    memory: Neo4jMicrosoftMemory,
    embedder: Embedder | None = None,
) -> list[FunctionTool]:
    """Adapt the catalog functions in ``backend/tools/`` into agent tools.

    Each wrapper binds the client (and, for search, the embedder) and leaves
    the model-visible signature to the annotated parameters — the same
    ``_bind_tool``-style pattern the Google ADK example uses. The Cypher lives
    in ``tools/*.py`` only, so the REST API and the agent cannot drift.
    """
    client = memory.memory_client
    session_id = memory.session_id

    @tool(
        name="search_products",
        description=(
            "Search the product catalog for items matching a query. "
            "Use when customers ask about products."
        ),
    )
    async def search_products_tool(
        query: Annotated[str, "Search query describing the product"],
        category: Annotated[
            str | None, "Optional category filter (e.g., 'Running Shoes', 'Apparel')"
        ] = None,
        brand: Annotated[str | None, "Optional brand filter"] = None,
        max_price: Annotated[float | None, "Optional maximum price filter"] = None,
    ) -> str:
        result = await search_products(
            client,
            query,
            category=category,
            brand=brand,
            max_price=max_price,
            embedder=embedder,
        )
        return json.dumps(result, default=str)

    @tool(
        name="get_product_details",
        description="Get detailed information about a specific product by ID.",
    )
    async def get_product_details_tool(
        product_id: Annotated[str, "The product ID"],
    ) -> str:
        product = await get_product_details(client, product_id)
        return json.dumps(product or {"error": "Product not found"}, default=str)

    @tool(
        name="get_related_products",
        description=(
            "Find products related to a given product: same category, same brand, "
            "explicitly similar, frequently bought together, or sharing an attribute."
        ),
    )
    async def get_related_products_tool(
        product_id: Annotated[str, "The product ID to find related items for"],
        limit: Annotated[int, "Maximum number of related products"] = 5,
    ) -> str:
        result = await get_related_products(client, product_id, limit=limit)
        return json.dumps(result, default=str)

    @tool(
        name="get_bought_together",
        description="Get products frequently bought together with a given product.",
    )
    async def get_bought_together_tool(
        product_id: Annotated[str, "The product ID"],
        limit: Annotated[int, "Maximum number of results"] = 3,
    ) -> str:
        result = await get_bought_together(client, product_id, limit=limit)
        return json.dumps(result, default=str)

    @tool(
        name="explain_product_connection",
        description=(
            "Explain how two products are connected in the graph "
            "(shared category, brand, attribute, or a longer path)."
        ),
    )
    async def explain_product_connection_tool(
        product_id_1: Annotated[str, "First product ID"],
        product_id_2: Annotated[str, "Second product ID"],
    ) -> str:
        result = await explain_product_connection(client, product_id_1, product_id_2)
        return json.dumps(result, default=str)

    @tool(
        name="check_inventory",
        description="Check if a product is in stock and get availability info.",
    )
    async def check_inventory_tool(
        product_id: Annotated[str, "The product ID to check"],
    ) -> str:
        result = await check_inventory(client, product_id)
        return json.dumps(result, default=str)

    @tool(
        name="find_alternatives",
        description="Find in-stock alternatives for a product that is unavailable.",
    )
    async def find_alternatives_tool(
        product_id: Annotated[str, "The out-of-stock product ID"],
        limit: Annotated[int, "Maximum number of alternatives"] = 3,
    ) -> str:
        result = await find_alternatives(client, product_id, limit=limit)
        return json.dumps(result, default=str)

    @tool(
        name="get_recommendations",
        description=(
            "Get personalized product recommendations based on stored preferences "
            "and the products discussed in this session."
        ),
    )
    async def get_recommendations_tool(
        category: Annotated[str | None, "Optional category to recommend within"] = None,
        limit: Annotated[int, "Maximum number of recommendations"] = 5,
    ) -> str:
        result = await get_recommendations(
            client,
            user_id=memory.user_id,
            session_id=session_id,
            category=category,
            limit=limit,
        )
        return json.dumps(result, default=str)

    @tool(name="get_cart", description="Show the customer's current shopping cart.")
    async def get_cart_tool() -> str:
        result = await get_cart(client, session_id)
        return json.dumps(result, default=str)

    @tool(name="add_to_cart", description="Add a product to the customer's cart.")
    async def add_to_cart_tool(
        product_id: Annotated[str, "The product ID to add"],
        quantity: Annotated[int, "How many to add"] = 1,
    ) -> str:
        result = await add_to_cart(client, session_id, product_id, quantity=quantity)
        return json.dumps(result, default=str)

    @tool(name="remove_from_cart", description="Remove a product from the cart.")
    async def remove_from_cart_tool(
        product_id: Annotated[str, "The product ID to remove"],
    ) -> str:
        result = await remove_from_cart(client, session_id, product_id)
        return json.dumps(result, default=str)

    return [
        search_products_tool,
        get_product_details_tool,
        get_related_products_tool,
        get_bought_together_tool,
        explain_product_connection_tool,
        check_inventory_tool,
        find_alternatives_tool,
        get_recommendations_tool,
        get_cart_tool,
        add_to_cart_tool,
        remove_from_cart_tool,
    ]


async def create_agent(
    memory: Neo4jMicrosoftMemory,
    embedder: Embedder | None = None,
) -> Agent:
    """Create a shopping assistant agent with Neo4j memory."""
    chat_client = get_chat_client()

    # Memory tools shipped by the library (search_memory, remember_preference,
    # recall_preferences, search_knowledge, remember_fact, find_similar_tasks,
    # plus GDS-backed tools when a GDSConfig is enabled).
    memory_tools = create_memory_tools(memory, include_gds_tools=bool(memory.gds))

    product_tools = get_product_tools(memory, embedder)

    return chat_client.as_agent(
        name="ShoppingAssistant",
        instructions=SYSTEM_PROMPT,
        tools=[*memory_tools, *product_tools],
        # The provider injects memory before the model call and persists the
        # turn afterwards. Do not also write messages by hand.
        context_providers=[memory.context_provider],
    )


def _product_names(result_text: str, limit: int = 5) -> list[str]:
    """Best-effort product names out of a tool result payload.

    Used to build ``(:ReasoningStep)-[:TOUCHED]->(:Entity)`` audit edges, so a
    later question like "which tool call touched the Nike Pegasus 40?" is a
    one-hop query.
    """
    try:
        payload = json.loads(result_text)
    except (TypeError, ValueError):
        return []

    names: list[str] = []

    def walk(node: Any) -> None:
        if len(names) >= limit:
            return
        if isinstance(node, dict):
            name = node.get("name")
            if isinstance(name, str) and name and name not in names:
                names.append(name)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)
    return names


async def _latest_user_message_id(
    memory: Neo4jMicrosoftMemory,
    content: str,
) -> UUID | None:
    """Find the id of the just-persisted user message, for INITIATED_BY.

    The context provider writes the turn during ``agent.run()``, so by the
    time the stream is finished the message exists and can be linked.
    """
    try:
        conversation = await memory.memory_client.short_term.get_conversation(
            memory.session_id, limit=10
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Could not read back the conversation for trace linking: %s", exc)
        return None

    for message in reversed(conversation.messages):
        role = getattr(message.role, "value", message.role)
        if role == "user" and message.content == content:
            return message.id
    return None


async def _record_trace(
    memory: Neo4jMicrosoftMemory,
    task: str,
    response: str,
    tool_calls: list[dict[str, Any]],
    error: Exception | None,
    started: float,
) -> None:
    """Record one turn as a reasoning trace with audit edges.

    ``record_agent_trace()`` from the integration is the one-call version, but
    it exposes neither ``touched_entities`` nor ``TraceOutcome``, so this drops
    to ``client.reasoning`` directly. Failures are recorded too — a trace store
    that only ever contains successes teaches the agent nothing.
    """
    client = memory.memory_client
    message_id = await _latest_user_message_id(memory, task)

    trace = await client.reasoning.start_trace(
        memory.session_id,
        task=task,
        triggered_by_message_id=message_id,
        metadata={"user_id": memory.user_id} if memory.user_id else None,
    )

    touched: list[EntityRef] = []
    step = await client.reasoning.add_step(
        trace.id,
        thought="Answer the shopper's question using the catalog and stored memory.",
        action=f"{len(tool_calls)} tool call(s)" if tool_calls else "direct answer",
        observation=response[:500] if response else None,
    )

    for call in tool_calls:
        entity_refs = [
            EntityRef(name=name, type="OBJECT") for name in _product_names(call.get("result", ""))
        ]
        touched.extend(entity_refs)
        await client.reasoning.record_tool_call(
            step.id,
            tool_name=call["name"],
            arguments=call.get("arguments") or {},
            result=call.get("result"),
            status=ToolCallStatus.SUCCESS,
            message_id=message_id,
            touched_entities=entity_refs or None,
        )

    outcome = TraceOutcome(
        success=error is None,
        summary=(response[:500] if error is None else str(error)[:500]) or "no response",
        error_kind=type(error).__name__ if error else None,
        related_entities=touched[:10],
        metrics={
            "latency_ms": round((time.monotonic() - started) * 1000, 1),
            "tools_called": float(len(tool_calls)),
        },
    )
    await client.reasoning.complete_trace(trace.id, outcome=outcome)


async def run_agent_stream(
    agent: Agent,
    message: str,
    memory: Neo4jMicrosoftMemory,
) -> AsyncGenerator[dict, None]:
    """
    Run the agent and stream responses.

    With callable tools, the agent framework auto-invokes tools during
    streaming. This function only observes text and tool events — no
    manual tool execution needed.

    Yields:
        Events with format: {"event": str, "data": str (JSON)}
        - token: {"content": str} - Response token
        - tool_call: {"name": str, "arguments": str} - Tool invocation
        - tool_result: {"name": str, "result": str} - Tool result
        - error: {"error": str} - Error

    ``main.py`` appends the terminating ``done`` event.
    """
    started = time.monotonic()
    tool_calls_for_trace: list[dict[str, Any]] = []
    # function_result content carries only the call_id, so remember which name
    # each id belongs to as the calls stream in.
    call_names: dict[str, str] = {}
    call_arguments: dict[str, Any] = {}
    full_response = ""
    failure: Exception | None = None

    try:
        # Messages are persisted by Neo4jContextProvider.after_run(); do not
        # double-write them here.
        user_msg = Message("user", [message])

        async for update in agent.run(user_msg, stream=True):
            if update.text:
                full_response += update.text
                yield {"event": "token", "data": json.dumps({"content": update.text})}

            for content in update.contents:
                if content.type == "function_call":
                    call_names[str(content.call_id)] = content.name or "unknown_tool"
                    call_arguments[str(content.call_id)] = content.arguments
                    yield {
                        "event": "tool_call",
                        "data": json.dumps(
                            {
                                "call_id": str(content.call_id),
                                "name": content.name,
                                "arguments": content.arguments,
                            },
                            default=str,
                        ),
                    }

                elif content.type == "function_result":
                    call_id = str(content.call_id)
                    name = call_names.get(call_id, "unknown_tool")
                    result = "" if content.result is None else str(content.result)
                    arguments = call_arguments.get(call_id)
                    if isinstance(arguments, str):
                        try:
                            arguments = json.loads(arguments)
                        except ValueError:
                            arguments = {"raw": arguments}
                    tool_calls_for_trace.append(
                        {
                            "name": name,
                            "arguments": arguments if isinstance(arguments, dict) else {},
                            "result": result,
                        }
                    )
                    yield {
                        "event": "tool_result",
                        # call_id lets the UI pair a result with its call:
                        # function_result content carries no tool name of its own.
                        "data": json.dumps(
                            {"call_id": call_id, "name": name, "result": result}, default=str
                        ),
                    }

    except Exception as e:
        failure = e
        logger.exception("Error in agent stream")
        yield {"event": "error", "data": json.dumps({"error": str(e)})}

    # Record the turn either way: a failed turn is the interesting one.
    try:
        await _record_trace(
            memory,
            task=message,
            response=full_response,
            tool_calls=tool_calls_for_trace,
            error=failure,
            started=started,
        )
    except Exception as trace_error:  # pragma: no cover - never fail a response on this
        logger.warning("Could not record reasoning trace: %s", trace_error)
