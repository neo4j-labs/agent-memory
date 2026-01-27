"""Microsoft Agent Framework agent for the retail assistant.

This module creates and configures the shopping assistant agent with:
- Neo4j memory integration (context provider, message store)
- Product search and recommendation tools
- Preference learning capabilities
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, AsyncGenerator

from agent_framework import ChatAgent, ChatMessage
from agent_framework.azure import AzureOpenAIChatClient
from agent_framework.openai import OpenAIChatClient
from memory_config import Settings

from neo4j_agent_memory.integrations.microsoft_agent import (
    Neo4jMicrosoftMemory,
    create_memory_tools,
    execute_memory_tool,
    record_agent_trace,
)

if TYPE_CHECKING:
    from agent_framework import ChatClientProtocol

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
- When customers express preferences, acknowledge and remember them
- Use the memory tools to save important preferences and recall relevant information
- When recommending products, explain why they match the customer's needs
- If a product is out of stock, suggest alternatives
- Ask clarifying questions when needs are unclear

You have access to memory tools to:
- Search your memory for relevant past conversations and preferences
- Save new preferences the customer expresses
- Find products similar to ones discussed before
- Track the customer's shopping journey

Always use the appropriate tools to provide personalized assistance."""


def get_chat_client() -> "ChatClientProtocol":
    """Create the chat completion client based on settings."""
    if settings.azure_openai_api_key and settings.azure_openai_endpoint:
        # Use Azure OpenAI
        return AzureOpenAIChatClient(
            api_key=settings.azure_openai_api_key,
            endpoint=settings.azure_openai_endpoint,
            deployment_name=settings.azure_openai_deployment or "gpt-4",
            api_version="2024-02-01",
        )
    elif settings.openai_api_key:
        # Use OpenAI directly
        return OpenAIChatClient(
            api_key=settings.openai_api_key,
            model_id="gpt-4-turbo-preview",
        )
    else:
        raise ValueError(
            "No OpenAI configuration found. Set OPENAI_API_KEY or Azure OpenAI settings."
        )


def get_product_tools() -> list[dict]:
    """Get product-related tool definitions."""
    return [
        {
            "type": "function",
            "function": {
                "name": "search_products",
                "description": "Search the product catalog for items matching a query. Use when customers ask about products.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search query describing the product",
                        },
                        "category": {
                            "type": "string",
                            "description": "Optional category filter (e.g., 'shoes', 'electronics')",
                        },
                        "brand": {
                            "type": "string",
                            "description": "Optional brand filter",
                        },
                        "max_price": {
                            "type": "number",
                            "description": "Optional maximum price filter",
                        },
                    },
                    "required": ["query"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_product_details",
                "description": "Get detailed information about a specific product by ID.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "product_id": {
                            "type": "string",
                            "description": "The product ID",
                        },
                    },
                    "required": ["product_id"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_related_products",
                "description": "Find products related to a given product (similar items, accessories, etc).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "product_id": {
                            "type": "string",
                            "description": "The product ID to find related items for",
                        },
                        "relationship_type": {
                            "type": "string",
                            "enum": ["similar", "accessory", "bundle", "any"],
                            "description": "Type of relationship to look for",
                        },
                    },
                    "required": ["product_id"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "check_inventory",
                "description": "Check if a product is in stock and get availability info.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "product_id": {
                            "type": "string",
                            "description": "The product ID to check",
                        },
                    },
                    "required": ["product_id"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_recommendations",
                "description": "Get personalized product recommendations for the customer based on their preferences and history.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "category": {
                            "type": "string",
                            "description": "Optional category to get recommendations for",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum number of recommendations",
                            "default": 5,
                        },
                    },
                },
            },
        },
    ]


async def execute_product_tool(
    tool_name: str,
    arguments: dict,
    memory: Neo4jMicrosoftMemory,
) -> str:
    """Execute a product-related tool."""
    client = memory.memory_client

    try:
        if tool_name == "search_products":
            # Vector search with optional filters
            query = arguments["query"]
            category = arguments.get("category")
            brand = arguments.get("brand")
            max_price = arguments.get("max_price")

            # Build filter conditions
            conditions = []
            params = {"query": query, "limit": 10}

            if category:
                conditions.append("p.category = $category")
                params["category"] = category
            if brand:
                conditions.append("p.brand = $brand")
                params["brand"] = brand
            if max_price:
                conditions.append("p.price <= $max_price")
                params["max_price"] = max_price

            where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

            # Try vector search first
            try:
                embedding = await client.embeddings.embed(query)
                cypher = f"""
                CALL db.index.vector.queryNodes('product_embedding', 10, $embedding)
                YIELD node as p, score
                {where_clause}
                RETURN p.name as name, p.description as description, p.price as price,
                       p.category as category, p.brand as brand, p.in_stock as in_stock,
                       elementId(p) as id, score
                ORDER BY score DESC
                LIMIT 10
                """
                params["embedding"] = embedding
                result = await client.graph.execute_query(cypher, params)
            except Exception:
                # Fallback to text search
                cypher = f"""
                MATCH (p:Product)
                WHERE (p.name CONTAINS $query OR p.description CONTAINS $query)
                {" AND " + " AND ".join(conditions) if conditions else ""}
                RETURN p.name as name, p.description as description, p.price as price,
                       p.category as category, p.brand as brand, p.in_stock as in_stock,
                       elementId(p) as id, 1.0 as score
                LIMIT 10
                """
                result = await client.graph.execute_query(cypher, params)

            products = [dict(r) for r in result]
            return json.dumps({"products": products, "count": len(products)})

        elif tool_name == "get_product_details":
            product_id = arguments["product_id"]
            cypher = """
            MATCH (p:Product)
            WHERE elementId(p) = $product_id OR p.id = $product_id
            RETURN p.name as name, p.description as description, p.price as price,
                   p.category as category, p.brand as brand, p.in_stock as in_stock,
                   p.inventory as inventory, p.attributes as attributes,
                   elementId(p) as id
            """
            result = await client.graph.execute_query(cypher, {"product_id": product_id})
            if result:
                return json.dumps(dict(result[0]))
            return json.dumps({"error": "Product not found"})

        elif tool_name == "get_related_products":
            product_id = arguments["product_id"]
            rel_type = arguments.get("relationship_type", "any")

            if rel_type == "any":
                cypher = """
                MATCH (p:Product)
                WHERE elementId(p) = $product_id OR p.id = $product_id
                CALL {
                    WITH p
                    MATCH (p)-[:IN_CATEGORY]->(c)<-[:IN_CATEGORY]-(related:Product)
                    WHERE related <> p
                    RETURN related, 'same category' as reason
                    LIMIT 3
                    UNION
                    WITH p
                    MATCH (p)-[:MADE_BY]->(b)<-[:MADE_BY]-(related:Product)
                    WHERE related <> p
                    RETURN related, 'same brand' as reason
                    LIMIT 3
                }
                RETURN related.name as name, related.price as price,
                       elementId(related) as id, reason
                LIMIT 5
                """
            else:
                rel_map = {
                    "similar": "SIMILAR_TO",
                    "accessory": "ACCESSORY_FOR",
                    "bundle": "BUNDLED_WITH",
                }
                rel = rel_map.get(rel_type, "SIMILAR_TO")
                cypher = f"""
                MATCH (p:Product)-[:{rel}]-(related:Product)
                WHERE elementId(p) = $product_id OR p.id = $product_id
                RETURN related.name as name, related.price as price,
                       elementId(related) as id, '{rel_type}' as reason
                LIMIT 5
                """

            result = await client.graph.execute_query(cypher, {"product_id": product_id})
            return json.dumps({"related": [dict(r) for r in result]})

        elif tool_name == "check_inventory":
            product_id = arguments["product_id"]
            cypher = """
            MATCH (p:Product)
            WHERE elementId(p) = $product_id OR p.id = $product_id
            RETURN p.name as name, p.in_stock as in_stock, p.inventory as quantity
            """
            result = await client.graph.execute_query(cypher, {"product_id": product_id})
            if result:
                r = result[0]
                return json.dumps(
                    {
                        "name": r["name"],
                        "in_stock": r["in_stock"],
                        "quantity": r["quantity"] or 0,
                        "status": "Available" if r["in_stock"] else "Out of Stock",
                    }
                )
            return json.dumps({"error": "Product not found"})

        elif tool_name == "get_recommendations":
            category = arguments.get("category")
            limit = arguments.get("limit", 5)

            # Get user preferences from memory
            preferences = await client.long_term.get_preferences(limit=10)
            pref_categories = [p.category for p in preferences]
            pref_values = [p.preference for p in preferences]

            # Build recommendation query based on preferences
            if preferences:
                cypher = """
                MATCH (p:Product)
                WHERE p.in_stock = true
                AND (p.brand IN $prefs OR p.category IN $categories)
                RETURN p.name as name, p.price as price, p.category as category,
                       p.brand as brand, elementId(p) as id,
                       'Based on your preferences' as reason
                LIMIT $limit
                """
                params = {
                    "prefs": pref_values,
                    "categories": pref_categories + ([category] if category else []),
                    "limit": limit,
                }
            else:
                # No preferences yet, return popular items
                cypher = """
                MATCH (p:Product)
                WHERE p.in_stock = true
                RETURN p.name as name, p.price as price, p.category as category,
                       p.brand as brand, elementId(p) as id,
                       'Popular item' as reason
                ORDER BY p.popularity DESC
                LIMIT $limit
                """
                params = {"limit": limit}

            if category:
                cypher = cypher.replace(
                    "WHERE p.in_stock = true",
                    "WHERE p.in_stock = true AND p.category = $category",
                )
                params["category"] = category

            result = await client.graph.execute_query(cypher, params)
            return json.dumps({"recommendations": [dict(r) for r in result]})

        else:
            return json.dumps({"error": f"Unknown tool: {tool_name}"})

    except Exception as e:
        logger.exception(f"Error executing tool {tool_name}")
        return json.dumps({"error": str(e)})


async def create_agent(memory: Neo4jMicrosoftMemory) -> ChatAgent:
    """Create a shopping assistant agent with Neo4j memory."""
    chat_client = get_chat_client()

    # Get memory tools
    memory_tools = create_memory_tools(
        memory=memory,
        include_gds_tools=True,  # Include graph algorithm tools
    )

    # Get product tools
    product_tools = get_product_tools()

    # Combine all tools
    all_tools = memory_tools + product_tools

    # Create agent with context provider
    agent = ChatAgent(
        chat_client=chat_client,
        name="ShoppingAssistant",
        instructions=SYSTEM_PROMPT,
        tools=all_tools,
        context_providers=[memory.context_provider],
    )

    return agent


async def run_agent_stream(
    agent: ChatAgent,
    message: str,
    memory: Neo4jMicrosoftMemory,
) -> AsyncGenerator[dict, None]:
    """
    Run the agent and stream responses.

    Yields:
        Events with format: {"event": str, "data": str (JSON)}
        - token: {"content": str} - Response token
        - tool_call: {"name": str, "arguments": dict} - Tool invocation
        - tool_result: {"name": str, "result": str} - Tool result
        - done: {"session_id": str} - Completion
        - error: {"error": str} - Error
    """
    tool_calls_for_trace = []

    try:
        # Save user message first
        await memory.save_message("user", message)

        # Create user message (Microsoft Agent Framework uses 'text' not 'content')
        user_msg = ChatMessage(role="user", text=message)

        # Stream agent response
        full_response = ""
        async for chunk in agent.stream(user_msg):
            # Handle content - check both .text (Microsoft Agent) and .content (generic)
            chunk_content = getattr(chunk, "text", None) or getattr(chunk, "content", None)
            if chunk_content:
                full_response += chunk_content
                yield {
                    "event": "token",
                    "data": json.dumps({"content": chunk_content}),
                }

            elif hasattr(chunk, "tool_calls") and chunk.tool_calls:
                for tool_call in chunk.tool_calls:
                    tool_name = tool_call.function.name
                    arguments = json.loads(tool_call.function.arguments)

                    yield {
                        "event": "tool_call",
                        "data": json.dumps(
                            {
                                "name": tool_name,
                                "arguments": arguments,
                            }
                        ),
                    }

                    # Execute the tool
                    if tool_name.startswith(("search_memory", "remember_", "recall_", "find_")):
                        # Memory tool
                        result = await execute_memory_tool(
                            memory,
                            tool_name,
                            arguments,
                        )
                    else:
                        # Product tool
                        result = await execute_product_tool(tool_name, arguments, memory)

                    # Track for trace
                    tool_calls_for_trace.append(
                        {
                            "tool": tool_name,
                            "input": arguments,
                            "output": result,
                        }
                    )

                    yield {
                        "event": "tool_result",
                        "data": json.dumps(
                            {
                                "name": tool_name,
                                "result": result,
                            }
                        ),
                    }

        # Save assistant response
        if full_response:
            await memory.save_message("assistant", full_response)

            # Record trace for learning
            messages_for_trace = [
                {"role": "user", "content": message},
                {"role": "assistant", "content": full_response[:500]},  # Truncate long responses
            ]

            await record_agent_trace(
                memory=memory,
                messages=messages_for_trace,
                task=message,
                tool_calls=tool_calls_for_trace,
                outcome="success",
                success=True,
                generate_embedding=True,
            )

    except Exception as e:
        logger.exception("Error in agent stream")
        yield {
            "event": "error",
            "data": json.dumps({"error": str(e)}),
        }
