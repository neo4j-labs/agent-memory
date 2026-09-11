"""Microsoft Agent Framework memory tools.

Provides callable FunctionTool definitions for memory operations that can be
used with Microsoft Agent Framework agents. Tools are auto-invoked by the
framework during streaming — no manual dispatch needed.
"""

from __future__ import annotations

import json
import logging
import warnings
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .memory import Neo4jMicrosoftMemory

logger = logging.getLogger(__name__)

try:
    from typing import Annotated

    from agent_framework import FunctionTool, tool

    from .gds import GDSAlgorithm

    def create_memory_tools(
        memory: Neo4jMicrosoftMemory,
        include_gds_tools: bool = True,
    ) -> list[FunctionTool]:
        """
        Create callable memory tools bound to a memory instance.

        Returns FunctionTool instances that the agent framework auto-invokes
        during streaming. No manual ``execute_memory_tool()`` dispatch needed.

        .. note::
            Tool format follows Microsoft Agent Framework conventions
            (``@tool``-decorated callables returning ``FunctionTool``), as
            shipped on the 1.x GA line.

        Args:
            memory: The Neo4jMicrosoftMemory instance.
            include_gds_tools: Whether to include GDS algorithm tools.

        Returns:
            List of FunctionTool instances.

        Example:
            from neo4j_agent_memory.integrations.microsoft_agent import (
                Neo4jMicrosoftMemory,
                create_memory_tools,
            )

            memory = Neo4jMicrosoftMemory.from_memory_client(client, "session-123")
            tools = create_memory_tools(memory)

            # Use with Agent — framework auto-invokes tools
            agent = chat_client.as_agent(
                tools=tools,
            )
        """

        @tool(
            name="search_memory",
            description=(
                "Search the user's memory for relevant information including "
                "past conversations, known facts, preferences, and entities. "
                "Use this to recall information about the user or past interactions."
            ),
        )
        async def search_memory(
            query: Annotated[str, "Search query describing what information to find"],
            memory_types: Annotated[
                list[str] | None,
                "Which memory types to search: messages, entities, preferences (default: all)",
            ] = None,
            limit: Annotated[int, "Maximum results per memory type"] = 5,
        ) -> str:
            """Search across all memory types."""
            include_msgs = not memory_types or "messages" in memory_types
            include_ents = not memory_types or "entities" in memory_types
            include_prefs = not memory_types or "preferences" in memory_types

            results = await memory.search_memory(
                query=query,
                include_messages=include_msgs,
                include_entities=include_ents,
                include_preferences=include_prefs,
                limit=limit,
            )
            return json.dumps({"results": results})

        @tool(
            name="remember_preference",
            description=(
                "Save a user preference for future reference. "
                "Use this when the user explicitly states a preference or "
                "when you infer a strong preference from the conversation."
            ),
        )
        async def remember_preference(
            category: Annotated[
                str,
                "Category of preference (e.g., 'shopping', 'style', 'brand', 'budget', 'size', 'color')",
            ],
            preference: Annotated[str, "The preference statement to remember"],
            context: Annotated[
                str | None, "Optional context for when this preference applies"
            ] = None,
        ) -> str:
            """Save a user preference to memory."""
            await memory.add_preference(
                category=category,
                preference=preference,
                context=context,
            )
            return json.dumps(
                {
                    "status": "saved",
                    "category": category,
                    "preference": preference,
                }
            )

        @tool(
            name="recall_preferences",
            description=(
                "Recall user preferences related to a topic or category. "
                "Use this before making recommendations or suggestions."
            ),
        )
        async def recall_preferences(
            topic: Annotated[str, "Topic to find preferences for"],
            category: Annotated[str | None, "Optional category filter"] = None,
        ) -> str:
            """Recall saved user preferences."""
            prefs = await memory._client.long_term.search_preferences(
                query=topic,
                category=category,
                limit=10,
            )
            return json.dumps(
                {
                    "preferences": [
                        {
                            "category": p.category,
                            "preference": p.preference,
                            "context": p.context,
                        }
                        for p in prefs
                    ]
                }
            )

        @tool(
            name="search_knowledge",
            description=(
                "Search the knowledge graph for entities (products, brands, "
                "categories, people, places) and their relationships. "
                "Use this to find factual information."
            ),
        )
        async def search_knowledge(
            query: Annotated[str, "Search query for entities"],
            entity_type: Annotated[
                str | None,
                "Optional filter by entity type: PERSON, LOCATION, ORGANIZATION, EVENT, OBJECT",
            ] = None,
            limit: Annotated[int, "Maximum entities to return"] = 5,
        ) -> str:
            """Search the knowledge graph for entities."""
            entity_types = [entity_type] if entity_type else None
            entities = await memory._client.long_term.search_entities(
                query=query,
                entity_types=entity_types,
                limit=limit,
            )
            return json.dumps(
                {
                    "entities": [
                        {
                            "name": e.display_name,
                            "type": e.type.value if hasattr(e.type, "value") else str(e.type),
                            "description": e.description,
                        }
                        for e in entities
                    ]
                }
            )

        @tool(
            name="remember_fact",
            description=(
                "Save a factual statement for future reference. "
                "Use this for important facts that should be remembered long-term."
            ),
        )
        async def remember_fact(
            subject: Annotated[str, "Subject of the fact (e.g., 'user', 'John')"],
            predicate: Annotated[str, "Relationship (e.g., 'prefers', 'bought', 'lives in')"],
            object: Annotated[str, "Object of the fact"],
        ) -> str:
            """Save a factual statement to memory."""
            await memory.add_fact(
                subject=subject,
                predicate=predicate,
                obj=object,
            )
            return json.dumps(
                {
                    "status": "saved",
                    "fact": f"{subject} {predicate} {object}",
                }
            )

        @tool(
            name="find_similar_tasks",
            description=(
                "Find similar tasks from past interactions to learn from "
                "previous successes or failures. Useful for complex multi-step tasks."
            ),
        )
        async def find_similar_tasks(
            task_description: Annotated[str, "Description of the current task"],
            limit: Annotated[int, "Maximum traces to return"] = 3,
        ) -> str:
            """Find similar past reasoning traces."""
            traces = await memory.get_similar_traces(
                task=task_description,
                limit=limit,
            )
            return json.dumps(
                {
                    "similar_tasks": [
                        {
                            "task": t.task,
                            "outcome": t.outcome,
                            "success": t.success,
                        }
                        for t in traces
                    ]
                }
            )

        tools: list[FunctionTool] = [
            search_memory,
            remember_preference,
            recall_preferences,
            search_knowledge,
            remember_fact,
            find_similar_tasks,
        ]

        # Add GDS tools if enabled
        if include_gds_tools and memory.gds:
            gds_config = memory.gds.config
            tools_to_expose = gds_config.expose_as_tools if gds_config else []

            if GDSAlgorithm.SHORTEST_PATH in tools_to_expose or not tools_to_expose:

                @tool(
                    name="find_connection_path",
                    description=(
                        "Find how two entities are connected in the knowledge graph. "
                        "Useful for understanding relationships between products, "
                        "brands, or concepts."
                    ),
                )
                async def find_connection_path(
                    source: Annotated[str, "Name of the starting entity"],
                    target: Annotated[str, "Name of the destination entity"],
                ) -> str:
                    """Find the path between two entities."""
                    path = await memory.find_entity_path(
                        source=source,
                        target=target,
                    )
                    if path:
                        return json.dumps({"path": path})
                    else:
                        return json.dumps({"path": None, "message": "No connection found"})

                tools.append(find_connection_path)

            if GDSAlgorithm.NODE_SIMILARITY in tools_to_expose or not tools_to_expose:

                @tool(
                    name="find_similar_items",
                    description=(
                        "Find items similar to a given entity based on their "
                        "relationships. Useful for product recommendations."
                    ),
                )
                async def find_similar_items(
                    entity_name: Annotated[str, "Name of the entity to find similar items for"],
                    limit: Annotated[int, "Maximum similar items to return"] = 5,
                ) -> str:
                    """Find similar entities."""
                    similar = await memory.find_similar_entities(
                        entity=entity_name,
                        limit=limit,
                    )
                    return json.dumps({"similar_items": similar})

                tools.append(find_similar_items)

            if GDSAlgorithm.PAGERANK in tools_to_expose:

                @tool(
                    name="find_important_entities",
                    description=(
                        "Find the most important/popular entities in a topic area. "
                        "Uses graph algorithms to identify key items."
                    ),
                )
                async def find_important_entities(
                    topic: Annotated[str, "Topic to find important entities for"],
                    limit: Annotated[int, "Maximum entities to return"] = 10,
                ) -> str:
                    """Find important entities using graph algorithms."""
                    entities = await memory._client.long_term.search_entities(
                        query=topic,
                        limit=50,
                    )
                    if entities and memory.gds:
                        entity_ids = [str(e.id) for e in entities]
                        important = await memory.gds.get_central_entities(
                            entity_ids=entity_ids,
                            limit=limit,
                        )
                        return json.dumps({"important_entities": important})
                    else:
                        return json.dumps(
                            {
                                "important_entities": [
                                    {
                                        "name": e.display_name,
                                        "type": e.type.value
                                        if hasattr(e.type, "value")
                                        else str(e.type),
                                        "description": e.description,
                                    }
                                    for e in entities[:limit]
                                ]
                            }
                        )

                tools.append(find_important_entities)

        return tools

    async def execute_memory_tool(
        memory: Neo4jMicrosoftMemory,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> str:
        """
        Execute a memory tool and return JSON result.

        .. deprecated::
            Use ``create_memory_tools()`` which returns callable ``FunctionTool``
            instances. The agent framework auto-invokes these during streaming,
            making manual dispatch unnecessary.

        Args:
            memory: The Neo4jMicrosoftMemory instance.
            tool_name: Name of the tool to execute.
            arguments: Tool arguments.

        Returns:
            JSON string with tool results.
        """
        warnings.warn(
            "execute_memory_tool() is deprecated. Use create_memory_tools() which "
            "returns callable FunctionTool instances that the agent framework "
            "auto-invokes during streaming.",
            DeprecationWarning,
            stacklevel=2,
        )
        try:
            if tool_name == "search_memory":
                query = arguments["query"]
                memory_types = arguments.get(
                    "memory_types", ["messages", "entities", "preferences"]
                )
                limit = arguments.get("limit", 5)

                results = await memory.search_memory(
                    query=query,
                    include_messages="messages" in memory_types,
                    include_entities="entities" in memory_types,
                    include_preferences="preferences" in memory_types,
                    limit=limit,
                )
                return json.dumps({"results": results})

            elif tool_name == "remember_preference":
                await memory.add_preference(
                    category=arguments["category"],
                    preference=arguments["preference"],
                    context=arguments.get("context"),
                )
                return json.dumps(
                    {
                        "status": "saved",
                        "category": arguments["category"],
                        "preference": arguments["preference"],
                    }
                )

            elif tool_name == "recall_preferences":
                prefs = await memory._client.long_term.search_preferences(
                    query=arguments["topic"],
                    category=arguments.get("category"),
                    limit=10,
                )
                return json.dumps(
                    {
                        "preferences": [
                            {
                                "category": p.category,
                                "preference": p.preference,
                                "context": p.context,
                            }
                            for p in prefs
                        ]
                    }
                )

            elif tool_name == "search_knowledge":
                query = arguments["query"]
                entity_type = arguments.get("entity_type")
                limit = arguments.get("limit", 5)

                entity_types = [entity_type] if entity_type else None
                entities = await memory._client.long_term.search_entities(
                    query=query,
                    entity_types=entity_types,
                    limit=limit,
                )
                return json.dumps(
                    {
                        "entities": [
                            {
                                "name": e.display_name,
                                "type": e.type.value if hasattr(e.type, "value") else str(e.type),
                                "description": e.description,
                            }
                            for e in entities
                        ]
                    }
                )

            elif tool_name == "remember_fact":
                await memory.add_fact(
                    subject=arguments["subject"],
                    predicate=arguments["predicate"],
                    obj=arguments["object"],
                )
                return json.dumps(
                    {
                        "status": "saved",
                        "fact": f"{arguments['subject']} {arguments['predicate']} {arguments['object']}",
                    }
                )

            elif tool_name == "find_similar_tasks":
                traces = await memory.get_similar_traces(
                    task=arguments["task_description"],
                    limit=arguments.get("limit", 3),
                )
                return json.dumps(
                    {
                        "similar_tasks": [
                            {
                                "task": t.task,
                                "outcome": t.outcome,
                                "success": t.success,
                            }
                            for t in traces
                        ]
                    }
                )

            elif tool_name == "find_connection_path":
                path = await memory.find_entity_path(
                    source=arguments["source"],
                    target=arguments["target"],
                )
                if path:
                    return json.dumps({"path": path})
                else:
                    return json.dumps({"path": None, "message": "No connection found"})

            elif tool_name == "find_similar_items":
                similar = await memory.find_similar_entities(
                    entity=arguments["entity_name"],
                    limit=arguments.get("limit", 5),
                )
                return json.dumps({"similar_items": similar})

            elif tool_name == "find_important_entities":
                entities = await memory._client.long_term.search_entities(
                    query=arguments["topic"],
                    limit=50,
                )
                if entities and memory.gds:
                    entity_ids = [str(e.id) for e in entities]
                    important = await memory.gds.get_central_entities(
                        entity_ids=entity_ids,
                        limit=arguments.get("limit", 10),
                    )
                    return json.dumps({"important_entities": important})
                else:
                    return json.dumps(
                        {
                            "important_entities": [
                                {
                                    "name": e.display_name,
                                    "type": e.type.value
                                    if hasattr(e.type, "value")
                                    else str(e.type),
                                    "description": e.description,
                                }
                                for e in entities[: arguments.get("limit", 10)]
                            ]
                        }
                    )

            else:
                return json.dumps({"error": f"Unknown tool: {tool_name}"})

        except Exception as e:
            logger.error(f"Error executing tool {tool_name}: {e}")
            return json.dumps({"error": str(e)})


except ImportError:
    # Microsoft Agent Framework not installed
    pass
