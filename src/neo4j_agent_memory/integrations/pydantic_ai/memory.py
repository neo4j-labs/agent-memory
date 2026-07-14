"""Pydantic AI integration for neo4j-agent-memory."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypeVar

if TYPE_CHECKING:
    from pydantic_ai.agent import AgentRunResult

    from neo4j_agent_memory import MemoryClient
    from neo4j_agent_memory.memory.reasoning import ReasoningMemory, ReasoningTrace

T = TypeVar("T")


@dataclass
class MemoryDependency:
    """
    Pydantic AI dependency for memory access.

    This can be used as the deps_type for a Pydantic AI agent to provide
    memory capabilities.

    Example:
        from pydantic_ai import Agent
        from neo4j_agent_memory import MemoryClient, MemorySettings
        from neo4j_agent_memory.integrations.pydantic_ai import MemoryDependency

        agent = Agent(
            'openai:gpt-4o',
            deps_type=MemoryDependency,
            system_prompt=dynamic_system_prompt,
        )

        async def dynamic_system_prompt(ctx: RunContext[MemoryDependency]) -> str:
            memory = ctx.deps
            context = await memory.get_context(ctx.messages[-1].content)
            return f"You are a helpful assistant.\\n\\nContext:\\n{context}"

        async with MemoryClient(settings) as client:
            deps = MemoryDependency(client=client, session_id="user-123")
            result = await agent.run("Find me a restaurant", deps=deps)
    """

    client: "MemoryClient"
    session_id: str

    async def get_context(self, query: str) -> str:
        """
        Get combined context from all memory types.

        Args:
            query: Query to find relevant context

        Returns:
            Formatted context string for LLM prompts
        """
        return await self.client.get_context(query, session_id=self.session_id)

    async def save_interaction(
        self,
        user_message: str,
        assistant_message: str,
        *,
        extract_entities: bool = True,
    ) -> None:
        """
        Save an interaction to memory.

        Args:
            user_message: The user's message
            assistant_message: The assistant's response
            extract_entities: Whether to extract entities from messages
        """
        await self.client.short_term.add_message(
            self.session_id,
            "user",
            user_message,
            extract_entities=extract_entities,
        )
        await self.client.short_term.add_message(
            self.session_id,
            "assistant",
            assistant_message,
            extract_entities=extract_entities,
        )

    async def add_preference(
        self,
        category: str,
        preference: str,
        context: str | None = None,
    ) -> None:
        """
        Add a user preference.

        Args:
            category: Preference category
            preference: The preference statement
            context: When/where preference applies
        """
        await self.client.long_term.add_preference(category, preference, context=context)

    async def search_preferences(
        self,
        query: str,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """
        Search for relevant preferences.

        Args:
            query: Search query
            limit: Maximum results

        Returns:
            List of preference dictionaries
        """
        prefs = await self.client.long_term.search_preferences(query, limit=limit)
        return [
            {
                "category": p.category,
                "preference": p.preference,
                "context": p.context,
                "confidence": p.confidence,
            }
            for p in prefs
        ]


def create_memory_tools(memory: "MemoryClient") -> list[Callable[..., Any]]:
    """
    Create Pydantic AI tools for memory operations.

    Returns tools that can be registered with a Pydantic AI agent for:
    - search_memory: Search across all memory types
    - save_preference: Save a user preference
    - recall_preferences: Get user preferences for a topic

    Example:
        from pydantic_ai import Agent
        from neo4j_agent_memory.integrations.pydantic_ai import create_memory_tools

        async with MemoryClient(settings) as client:
            tools = create_memory_tools(client)
            agent = Agent('openai:gpt-4o', tools=tools)
    """

    async def search_memory(
        query: str,
        memory_types: list[str] | None = None,
    ) -> str:
        """
        Search the agent's memory for relevant information.

        Args:
            query: Search query
            memory_types: Types to search (episodic, semantic, reasoning)

        Returns:
            Relevant memories as formatted text
        """
        results = []
        types = memory_types or ["short_term", "long_term", "reasoning"]

        if "short_term" in types:
            messages = await memory.short_term.search_messages(query, limit=5)
            for msg in messages:
                results.append(f"[{msg.role.value}] {msg.content}")

        if "long_term" in types:
            entities = await memory.long_term.search_entities(query, limit=5)
            for entity in entities:
                desc = f": {entity.description}" if entity.description else ""
                # entity.type may be a string or enum
                entity_type = (
                    entity.type.value if hasattr(entity.type, "value") else str(entity.type)
                )
                results.append(f"[{entity_type}] {entity.display_name}{desc}")

            prefs = await memory.long_term.search_preferences(query, limit=5)
            for pref in prefs:
                results.append(f"[PREFERENCE:{pref.category}] {pref.preference}")

        if "reasoning" in types:
            traces = await memory.reasoning.get_similar_traces(query, limit=3)
            for trace in traces:
                status = "succeeded" if trace.success else "failed"
                results.append(f"[TASK] {trace.task} - {status}")

        return "\n".join(results) if results else "No relevant memories found."

    async def save_preference(
        category: str,
        preference: str,
        context: str | None = None,
    ) -> str:
        """
        Save a user preference to memory.

        Args:
            category: Preference category (food, music, etc.)
            preference: The preference statement
            context: When/where it applies

        Returns:
            Confirmation message
        """
        await memory.long_term.add_preference(category, preference, context=context)
        return f"Saved preference: {preference} (category: {category})"

    async def recall_preferences(topic: str) -> str:
        """
        Recall user preferences related to a topic.

        Args:
            topic: Topic to search for

        Returns:
            Relevant preferences as formatted text
        """
        prefs = await memory.long_term.search_preferences(topic, limit=10)
        if not prefs:
            return "No preferences found for this topic."
        return "\n".join([f"- [{p.category}] {p.preference}" for p in prefs])

    return [search_memory, save_preference, recall_preferences]


async def record_agent_trace(
    reasoning_memory: "ReasoningMemory",
    session_id: str,
    result: "AgentRunResult[T]",
    *,
    task: str | None = None,
    include_tool_calls: bool = True,
    generate_embeddings: bool = False,
) -> "ReasoningTrace":
    """
    Record a reasoning trace from a PydanticAI RunResult.

    This function extracts tool calls and their results from a completed
    PydanticAI agent run and records them as a reasoning trace in reasoning
    memory.

    Args:
        reasoning_memory: The reasoning memory instance to record to
        session_id: Session ID for the trace
        result: The RunResult from a PydanticAI agent run
        task: Optional task description (defaults to extracting from messages)
        include_tool_calls: Whether to record individual tool calls
        generate_embeddings: Whether to generate embeddings for steps

    Returns:
        The created ReasoningTrace

    Example:
        from pydantic_ai import Agent
        from neo4j_agent_memory.integrations.pydantic_ai import record_agent_trace

        agent = Agent('openai:gpt-4o')
        result = await agent.run("Find restaurants near me")

        # Record the trace
        trace = await record_agent_trace(
            client.reasoning,
            session_id="user-123",
            result=result,
            task="Find restaurants",
        )
    """
    # Import here to avoid circular imports and allow optional pydantic-ai dependency
    try:
        from pydantic_ai.messages import (
            ModelRequest,
            ModelResponse,
            ToolCallPart,
            ToolReturnPart,
            UserPromptPart,
        )
    except ImportError as e:
        raise ImportError(
            "pydantic-ai is required for record_agent_trace. Install with: pip install pydantic-ai"
        ) from e

    from neo4j_agent_memory.memory.reasoning import ToolCallStatus

    # Extract task from first user message if not provided
    if task is None:
        task = "PydanticAI agent run"
        for msg in result.all_messages():
            if isinstance(msg, ModelRequest):
                user_part = next((p for p in msg.parts if isinstance(p, UserPromptPart)), None)
                if user_part is not None:
                    task = str(user_part.content)[:500]  # Truncate long tasks
                    break

    # Start the trace
    trace = await reasoning_memory.start_trace(
        session_id=session_id,
        task=task,
        metadata={
            "source": "pydantic_ai",
            "model": getattr(result, "model", None),
        },
    )

    if include_tool_calls:
        # Collect tool calls and their results
        # Tool calls come from ModelResponse, results come from ModelRequest
        tool_calls: dict[str, dict[str, Any]] = {}  # tool_call_id -> {name, args}
        tool_results: dict[str, Any] = {}  # tool_call_id -> result

        for msg in result.all_messages():
            if isinstance(msg, ModelResponse):
                for part in msg.parts:
                    if isinstance(part, ToolCallPart):
                        tool_call_id = part.tool_call_id or f"call_{len(tool_calls)}"
                        # args_as_dict() normalizes str/dict args to a dict.
                        args: dict[str, Any] = part.args_as_dict() if part.args is not None else {}

                        tool_calls[tool_call_id] = {
                            "name": part.tool_name,
                            "args": args,
                        }

            elif isinstance(msg, ModelRequest):
                for req_part in msg.parts:
                    if isinstance(req_part, ToolReturnPart):
                        tool_call_id = req_part.tool_call_id or ""
                        tool_results[tool_call_id] = req_part.content

        # Create steps for each tool call
        step_number = 0
        for tool_call_id, call_info in tool_calls.items():
            step_number += 1
            tool_name = call_info["name"]
            tool_args = call_info["args"]
            tool_result = tool_results.get(tool_call_id)

            # Create a reasoning step
            step = await reasoning_memory.add_step(
                trace.id,
                thought=f"Using {tool_name} tool",
                action=f"Call {tool_name}",
                observation=_format_tool_result(tool_result) if tool_result else None,
                generate_embedding=generate_embeddings,
                metadata={
                    "tool_call_id": tool_call_id,
                    "step_number": step_number,
                },
            )

            # Record the tool call
            is_error = _is_error_result(tool_result)
            await reasoning_memory.record_tool_call(
                step_id=step.id,
                tool_name=tool_name,
                arguments=tool_args,
                result=tool_result,
                status=ToolCallStatus.ERROR if is_error else ToolCallStatus.SUCCESS,
                error=str(tool_result) if is_error else None,
            )

    # Complete the trace with the final output
    final_output = str(result.data) if hasattr(result, "data") else None
    completed_trace = await reasoning_memory.complete_trace(
        trace.id,
        outcome=final_output[:1000] if final_output else "Completed",
        success=True,
        generate_step_embeddings=generate_embeddings,
    )

    return completed_trace


def _format_tool_result(result: Any) -> str:
    """Format a tool result for observation field."""
    if result is None:
        return "No result"
    if isinstance(result, str):
        return result[:500] if len(result) > 500 else result
    if isinstance(result, (list, dict)):
        import json

        try:
            formatted = json.dumps(result, default=str)
            return formatted[:500] if len(formatted) > 500 else formatted
        except Exception:
            return str(result)[:500]
    return str(result)[:500]


def _is_error_result(result: Any) -> bool:
    """Check if a tool result indicates an error."""
    if result is None:
        return False
    if isinstance(result, dict):
        return "error" in result or "Error" in result
    if isinstance(result, list) and result:
        first = result[0]
        if isinstance(first, dict):
            return "error" in first or "Error" in first
    if isinstance(result, str):
        return result.lower().startswith("error:")
    return False


def nams_memory_tools(memory: "MemoryClient") -> list[Callable[..., Any]]:
    """Pydantic AI tools exposing NAMS Platinum operations.

    Returns the standard :func:`create_memory_tools` set plus four
    Platinum-tier tools that NAMS exposes via REST:

    * ``set_entity_feedback`` — record positive/negative feedback on
      an entity (NAMS persists this and uses it to bias future
      retrieval).
    * ``get_entity_history`` — the conversation-and-mention history
      for an entity, useful for the agent to reason about whether an
      entity has shown up before.
    * ``get_entity_provenance`` — the source messages and extractors
      that produced an entity (great for citation-style outputs).
    * ``cypher_query`` — read-only Cypher escape hatch via NAMS
      ``POST /v1/query``.

    All four raise :class:`NotSupportedError` at tool-call time if the
    underlying ``MemoryClient`` is on the bolt backend without the
    corresponding Platinum-tier method. Portable code can catch the
    error and fall back; agents typically don't need to.

    Example::

        from pydantic_ai import Agent
        from neo4j_agent_memory.integrations.pydantic_ai import (
            nams_memory_tools,
        )

        async with MemoryClient(MemorySettings(backend="nams", ...)) as c:
            agent = Agent("openai:gpt-4o", tools=nams_memory_tools(c))

    Returns the base tools followed by the four Platinum tools.
    """

    async def set_entity_feedback(
        entity_id: str,
        feedback: str,
        user_identifier: str | None = None,
    ) -> str:
        """
        Record feedback (e.g. "positive"/"negative") on an entity.

        Args:
            entity_id: Entity UUID.
            feedback: Feedback string — convention is "positive" or
                "negative", but any string the server accepts works.
            user_identifier: Optional per-user scoping.

        Returns:
            Confirmation message.
        """
        # Platinum-tier method — present on NamsLongTermMemory at runtime; the
        # bolt LongTermMemory class doesn't declare it (Protocol-vs-class lie).
        await memory.long_term.set_entity_feedback(  # type: ignore[attr-defined]
            entity_id,
            feedback,
            user_identifier=user_identifier,
        )
        return f"Recorded {feedback!r} feedback on entity {entity_id}."

    async def get_entity_history(entity_id: str, limit: int = 20) -> str:
        """
        Get the recent edit/mention history for an entity.

        Args:
            entity_id: Entity UUID.
            limit: Maximum history entries to return.

        Returns:
            Formatted history as plain text.
        """
        entries = await memory.long_term.get_entity_history(  # type: ignore[attr-defined]
            entity_id, limit=limit
        )
        if not entries:
            return "No history for this entity."
        lines = []
        for entry in entries:
            cid = entry.get("conversation_id", "?")
            mentions = entry.get("mention_count", 0)
            lines.append(f"- conversation={cid} mentions={mentions}")
        return "\n".join(lines)

    async def get_entity_provenance(entity_id: str) -> str:
        """
        Get the provenance (source messages + extractors) for an entity.

        Args:
            entity_id: Entity UUID.

        Returns:
            Formatted provenance summary.
        """
        prov = await memory.long_term.get_entity_provenance(entity_id)  # type: ignore[arg-type]
        sources = prov.get("sources") or []
        extractors = prov.get("extractors") or []
        lines = [f"Sources ({len(sources)}):"]
        for s in sources[:10]:
            lines.append(f"- message {s.get('message_id', '?')}")
        lines.append(f"Extractors ({len(extractors)}):")
        for e in extractors[:10]:
            lines.append(f"- {e.get('name', '?')} v{e.get('version', '?')}")
        return "\n".join(lines)

    async def cypher_query(query: str, params: dict[str, Any] | None = None) -> str:
        """
        Execute a read-only Cypher query via NAMS (Platinum ``POST /v1/query``).

        Args:
            query: Read-only Cypher (writes are rejected client-side).
            params: Optional query parameters.

        Returns:
            JSON-encoded result rows.
        """
        import json as _json

        rows = await memory.query.cypher(query, params)
        return _json.dumps(rows, default=str)

    return [
        *create_memory_tools(memory),
        set_entity_feedback,
        get_entity_history,
        get_entity_provenance,
        cypher_query,
    ]
