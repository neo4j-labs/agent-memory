"""LangChain memory integration."""

from collections.abc import Coroutine
from typing import TYPE_CHECKING, Any, TypeVar

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from neo4j_agent_memory.memory.reasoning import ReasoningTrace
    from neo4j_agent_memory.memory.short_term import Message

_T = TypeVar("_T")


class Neo4jAgentMemory(BaseModel):
    """
    LangChain memory that uses Neo4j Agent Memory.

    Provides:
    - Conversation history (short-term)
    - User facts and preferences (long-term)
    - Similar past task traces (reasoning)

    Example:
        from neo4j_agent_memory import MemoryClient, MemorySettings
        from neo4j_agent_memory.integrations.langchain import Neo4jAgentMemory

        async with MemoryClient(settings) as client:
            memory = Neo4jAgentMemory(
                memory_client=client,
                session_id="user-123"
            )
            # Use with LangChain agent
    """

    memory_client: Any  # MemoryClient - using Any to avoid pydantic issues
    session_id: str
    include_short_term: bool = True
    include_long_term: bool = True
    include_reasoning: bool = True
    max_messages: int = 10
    max_preferences: int = 5
    max_traces: int = 3

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def _run_async(self, coro: Coroutine[Any, Any, _T]) -> _T:
        """
        Run an async coroutine from sync context.

        Handles the case where we're already in an async context by
        scheduling the coroutine on the existing event loop.
        """
        import asyncio

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is not None and loop.is_running():
            # We're in an async context - schedule on the existing loop
            future = asyncio.run_coroutine_threadsafe(coro, loop)
            return future.result(timeout=30)
        else:
            # Not in async context - create a new loop
            return asyncio.run(coro)

    @property
    def memory_variables(self) -> list[str]:
        """Return memory variables."""
        variables = []
        if self.include_short_term:
            variables.append("history")
        if self.include_long_term:
            variables.extend(["context", "preferences"])
        if self.include_reasoning:
            variables.append("similar_tasks")
        return variables

    def load_memory_variables(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """
        Load memory context for the current input.

        This is a sync wrapper that handles async execution properly.
        """
        return self._run_async(self._load_memory_variables_async(inputs))

    async def _load_memory_variables_async(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """Async implementation of load_memory_variables."""
        query = inputs.get("input", "")
        result: dict[str, Any] = {}

        if self.include_short_term:
            conv = await self.memory_client.short_term.get_conversation(
                self.session_id, limit=self.max_messages
            )
            result["history"] = self._format_messages(conv.messages)

        if self.include_long_term:
            context = await self.memory_client.long_term.get_context(
                query, max_items=self.max_preferences
            )
            result["context"] = context

            prefs = await self.memory_client.long_term.search_preferences(
                query, limit=self.max_preferences
            )
            result["preferences"] = [
                {"category": p.category, "preference": p.preference} for p in prefs
            ]

        if self.include_reasoning:
            traces = await self.memory_client.reasoning.get_similar_traces(
                query, limit=self.max_traces
            )
            result["similar_tasks"] = self._format_traces(traces)

        return result

    def save_context(self, inputs: dict[str, Any], outputs: dict[str, str]) -> None:
        """
        Save the current interaction to memory.

        This is a sync wrapper that handles async execution properly.
        """
        self._run_async(self._save_context_async(inputs, outputs))

    async def _save_context_async(self, inputs: dict[str, Any], outputs: dict[str, str]) -> None:
        """Async implementation of save_context."""
        user_input = inputs.get("input", "")
        assistant_output = outputs.get("output", "")

        if user_input:
            await self.memory_client.short_term.add_message(self.session_id, "user", user_input)

        if assistant_output:
            await self.memory_client.short_term.add_message(
                self.session_id, "assistant", assistant_output
            )

    def clear(self) -> None:
        """Clear conversation history for this session."""
        self._run_async(self.memory_client.short_term.clear_session(self.session_id))

    def _format_messages(self, messages: list["Message"]) -> str:
        """Format messages for context."""
        lines = []
        for msg in messages:
            lines.append(f"{msg.role.value}: {msg.content}")
        return "\n".join(lines)

    def _format_traces(self, traces: list["ReasoningTrace"]) -> str:
        """Format reasoning traces for context."""
        lines = []
        for trace in traces:
            lines.append(f"Task: {trace.task}")
            if trace.outcome:
                lines.append(f"  Outcome: {trace.outcome}")
        return "\n".join(lines)
