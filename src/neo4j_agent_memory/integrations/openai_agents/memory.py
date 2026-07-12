"""OpenAI Agents SDK memory integration.

Provides Neo4j-backed memory for OpenAI agents with:
- Conversation history persistence
- Entity and preference retrieval
- Function tools for memory operations
"""

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from neo4j_agent_memory import MemoryClient
    from neo4j_agent_memory.memory.short_term import Message

try:
    import openai  # noqa: F401 - verify openai is installed

    class Neo4jOpenAIMemory:
        """
        OpenAI Agents SDK memory backed by Neo4j Agent Memory.

        Provides persistent memory storage and retrieval for OpenAI agents,
        including conversation history, entity knowledge, and preferences.

        Example:
            from neo4j_agent_memory import MemoryClient, MemorySettings
            from neo4j_agent_memory.integrations.openai_agents import Neo4jOpenAIMemory

            async with MemoryClient(settings) as client:
                memory = Neo4jOpenAIMemory(
                    memory_client=client,
                    session_id="user-123",
                )

                # Get context for system prompt
                context = await memory.get_context("What should I recommend?")

                # Save a message
                await memory.save_message("user", "Hello!")

                # Get conversation in OpenAI format
                messages = await memory.get_conversation(limit=10)
        """

        def __init__(
            self,
            memory_client: "MemoryClient",
            session_id: str,
            user_id: str | None = None,
        ):
            """
            Initialize OpenAI memory integration.

            Args:
                memory_client: Neo4j Agent Memory client
                session_id: Session identifier for conversation tracking
                user_id: Optional user identifier for personalization
            """
            self._client = memory_client
            self._session_id = session_id
            self._user_id = user_id

        @property
        def session_id(self) -> str:
            """Get the session ID."""
            return self._session_id

        @property
        def user_id(self) -> str | None:
            """Get the user ID."""
            return self._user_id

        @property
        def memory_client(self) -> "MemoryClient":
            """Get the underlying memory client."""
            return self._client

        async def get_context(
            self,
            query: str,
            include_short_term: bool = True,
            include_long_term: bool = True,
            include_reasoning: bool = True,
            max_items: int = 10,
        ) -> str:
            """
            Get combined context from all memory types for a query.

            This method retrieves relevant information from conversation history,
            entity knowledge, preferences, and similar past reasoning traces.

            Args:
                query: The query to find relevant context for
                include_short_term: Include recent conversation history
                include_long_term: Include entities and preferences
                include_reasoning: Include similar past reasoning traces
                max_items: Maximum items per memory type

            Returns:
                Formatted context string suitable for system prompts
            """
            return await self._client.get_context(
                query=query,
                session_id=self._session_id,
                include_short_term=include_short_term,
                include_long_term=include_long_term,
                include_reasoning=include_reasoning,
                max_items=max_items,
            )

        async def save_message(
            self,
            role: str,
            content: str,
            tool_calls: list[dict[str, Any]] | None = None,
            tool_call_id: str | None = None,
            extract_entities: bool = True,
            generate_embedding: bool = True,
        ) -> "Message":
            """
            Save a conversation message to memory.

            Args:
                role: Message role (user, assistant, system, tool)
                content: Message content
                tool_calls: Optional tool calls for assistant messages
                tool_call_id: Optional tool call ID for tool response messages
                extract_entities: Whether to extract entities from message
                generate_embedding: Whether to generate embedding for search

            Returns:
                The saved Message object
            """
            metadata: dict[str, Any] = {}
            if tool_calls:
                metadata["tool_calls"] = tool_calls
            if tool_call_id:
                metadata["tool_call_id"] = tool_call_id

            return await self._client.short_term.add_message(
                session_id=self._session_id,
                role=role,
                content=content,
                extract_entities=extract_entities,
                generate_embedding=generate_embedding,
                metadata=metadata if metadata else None,
            )

        async def get_conversation(
            self,
            limit: int = 50,
            include_system: bool = True,
        ) -> list[dict[str, Any]]:
            """
            Get conversation history in OpenAI message format.

            Args:
                limit: Maximum number of messages to retrieve
                include_system: Whether to include system messages

            Returns:
                List of message dicts in OpenAI format:
                [{"role": "user", "content": "..."}, ...]
            """
            conv = await self._client.short_term.get_conversation(
                session_id=self._session_id,
                limit=limit,
            )

            messages = []
            for msg in conv.messages:
                if not include_system and msg.role.value == "system":
                    continue

                message_dict: dict[str, Any] = {
                    "role": msg.role.value,
                    "content": msg.content,
                }

                # Add tool_calls if present (for assistant messages)
                if msg.metadata and msg.metadata.get("tool_calls"):
                    message_dict["tool_calls"] = msg.metadata["tool_calls"]

                # Add tool_call_id if present (for tool response messages)
                if msg.metadata and msg.metadata.get("tool_call_id"):
                    message_dict["tool_call_id"] = msg.metadata["tool_call_id"]

                messages.append(message_dict)

            return messages

        async def search(
            self,
            query: str,
            limit: int = 10,
            include_messages: bool = True,
            include_entities: bool = True,
            include_preferences: bool = True,
        ) -> list[dict[str, Any]]:
            """
            Search across all memory types.

            Args:
                query: Search query
                limit: Maximum results per type
                include_messages: Search conversation history
                include_entities: Search entity knowledge
                include_preferences: Search user preferences

            Returns:
                List of search results with type and content
            """
            results = []

            if include_messages:
                messages = await self._client.short_term.search_messages(
                    query=query,
                    session_id=self._session_id,
                    limit=limit,
                )
                for msg in messages:
                    results.append(
                        {
                            "type": "message",
                            "role": msg.role.value,
                            "content": msg.content,
                            "id": str(msg.id),
                        }
                    )

            if include_entities:
                entities = await self._client.long_term.search_entities(
                    query=query,
                    limit=limit,
                )
                for entity in entities:
                    content = entity.display_name
                    if entity.description:
                        content += f": {entity.description}"
                    # entity.type may be a string or enum
                    entity_type = (
                        entity.type.value if hasattr(entity.type, "value") else str(entity.type)
                    )
                    results.append(
                        {
                            "type": "entity",
                            "entity_type": entity_type,
                            "name": entity.display_name,
                            "content": content,
                            "id": str(entity.id),
                        }
                    )

            if include_preferences:
                prefs = await self._client.long_term.search_preferences(
                    query=query,
                    limit=limit,
                )
                for pref in prefs:
                    results.append(
                        {
                            "type": "preference",
                            "category": pref.category,
                            "content": pref.preference,
                            "id": str(pref.id),
                        }
                    )

            return results

        async def add_preference(
            self,
            category: str,
            preference: str,
            generate_embedding: bool = True,
        ) -> Any:
            """
            Add a user preference to long-term memory.

            Args:
                category: Preference category (e.g., "communication", "food")
                preference: The preference text
                generate_embedding: Whether to generate embedding for search

            Returns:
                The saved Preference object
            """
            return await self._client.long_term.add_preference(
                category=category,
                preference=preference,
                generate_embedding=generate_embedding,
            )

        async def search_preferences(
            self,
            query: str,
            category: str | None = None,
            limit: int = 10,
        ) -> list[dict[str, Any]]:
            """
            Search user preferences.

            Args:
                query: Search query
                category: Optional category filter
                limit: Maximum results

            Returns:
                List of matching preferences
            """
            prefs = await self._client.long_term.search_preferences(
                query=query,
                category=category,
                limit=limit,
            )
            return [
                {
                    "category": p.category,
                    "preference": p.preference,
                    "id": str(p.id),
                }
                for p in prefs
            ]

        async def clear_session(self) -> None:
            """Clear all messages in the current session."""
            await self._client.short_term.clear_session(self._session_id)

    def create_memory_tools(memory: Neo4jOpenAIMemory) -> list[dict[str, Any]]:
        """
        Create OpenAI function tools for memory operations.

        These tools allow the agent to interact with the memory system
        using OpenAI's function calling format.

        Args:
            memory: The Neo4jOpenAIMemory instance

        Returns:
            List of tool definitions in OpenAI format

        Example:
            tools = create_memory_tools(memory)
            # Use with OpenAI client
            response = client.chat.completions.create(
                model="gpt-4",
                messages=messages,
                tools=tools,
            )
        """
        return [
            {
                "type": "function",
                "function": {
                    "name": "search_memory",
                    "description": "Search conversation history, entity knowledge, and preferences for relevant information.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "The search query to find relevant memories",
                            },
                            "limit": {
                                "type": "integer",
                                "description": "Maximum number of results to return",
                                "default": 5,
                            },
                        },
                        "required": ["query"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "save_preference",
                    "description": "Save a user preference to long-term memory for future reference.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "category": {
                                "type": "string",
                                "description": "Category of the preference (e.g., 'communication', 'food', 'style')",
                            },
                            "preference": {
                                "type": "string",
                                "description": "The preference to save",
                            },
                        },
                        "required": ["category", "preference"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "recall_preferences",
                    "description": "Recall user preferences from memory by category or search query.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "Search query to find relevant preferences",
                            },
                            "category": {
                                "type": "string",
                                "description": "Optional category to filter preferences",
                            },
                        },
                        "required": ["query"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "search_entities",
                    "description": "Search for entities (people, places, organizations, etc.) in the knowledge graph.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "Search query to find relevant entities",
                            },
                            "entity_type": {
                                "type": "string",
                                "description": "Optional entity type filter (PERSON, LOCATION, ORGANIZATION, EVENT, OBJECT)",
                                "enum": ["PERSON", "LOCATION", "ORGANIZATION", "EVENT", "OBJECT"],
                            },
                            "limit": {
                                "type": "integer",
                                "description": "Maximum number of results",
                                "default": 5,
                            },
                        },
                        "required": ["query"],
                    },
                },
            },
        ]

    async def execute_memory_tool(
        memory: Neo4jOpenAIMemory,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> str:
        """
        Execute a memory tool and return the result.

        This helper function executes the memory tools created by
        create_memory_tools() and returns JSON-formatted results.

        Args:
            memory: The Neo4jOpenAIMemory instance
            tool_name: Name of the tool to execute
            arguments: Tool arguments

        Returns:
            JSON string with tool results
        """
        if tool_name == "search_memory":
            results = await memory.search(
                query=arguments["query"],
                limit=arguments.get("limit", 5),
            )
            return json.dumps({"results": results})

        elif tool_name == "save_preference":
            await memory.add_preference(
                category=arguments["category"],
                preference=arguments["preference"],
            )
            return json.dumps(
                {
                    "status": "saved",
                    "category": arguments["category"],
                    "preference": arguments["preference"],
                }
            )

        elif tool_name == "recall_preferences":
            prefs = await memory.search_preferences(
                query=arguments["query"],
                category=arguments.get("category"),
            )
            return json.dumps({"preferences": prefs})

        elif tool_name == "search_entities":
            # Convert singular entity_type to entity_types list if provided
            entity_types = None
            if arguments.get("entity_type"):
                entity_types = [arguments["entity_type"]]

            entities = await memory._client.long_term.search_entities(
                query=arguments["query"],
                entity_types=entity_types,
                limit=arguments.get("limit", 5),
            )
            results = []
            for e in entities:
                # e.type may be a string or enum
                entity_type_value = e.type.value if hasattr(e.type, "value") else str(e.type)
                results.append(
                    {
                        "name": e.display_name,
                        "type": entity_type_value,
                        "description": e.description,
                    }
                )
            return json.dumps({"entities": results})

        else:
            return json.dumps({"error": f"Unknown tool: {tool_name}"})


except ImportError:
    # OpenAI not installed
    pass
