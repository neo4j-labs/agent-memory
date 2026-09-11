"""LangChain chat-message-history adapter backed by Neo4j Agent Memory.

``Neo4jAgentMemory`` implements :class:`langchain_core.chat_history.BaseChatMessageHistory`
— a live langchain-core 1.x abstract base class — so it plugs into
:class:`~langchain_core.runnables.history.RunnableWithMessageHistory` and anything
else that accepts a chat history object.

It used to imitate the pre-v1 ``langchain_core.memory.BaseMemory`` protocol
(``memory_variables`` / ``load_memory_variables`` / ``save_context``). That module
no longer exists in langchain-core 1.x, so the class was retargeted onto
``BaseChatMessageHistory``. The old context-assembly surface is kept — it is
useful for hand-rolled prompts and for
:class:`~neo4j_agent_memory.integrations.langchain.middleware.Neo4jMemoryMiddleware`
— but its async methods are now public (``aload_memory_variables`` /
``asave_context``); the underscore-prefixed names remain as deprecated aliases
for one release.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine, Sequence
from typing import TYPE_CHECKING, Any, TypeVar

from langchain_core.chat_history import BaseChatMessageHistory
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    ChatMessage,
    HumanMessage,
    SystemMessage,
)

from neo4j_agent_memory.core.exceptions import NotSupportedError

if TYPE_CHECKING:
    from neo4j_agent_memory import MemoryClient
    from neo4j_agent_memory.memory.long_term import Preference
    from neo4j_agent_memory.memory.reasoning import ReasoningTrace
    from neo4j_agent_memory.memory.short_term import Message

logger = logging.getLogger(__name__)

_T = TypeVar("_T")

#: LangChain message ``type`` -> neo4j-agent-memory ``MessageRole`` value.
_LC_TYPE_TO_ROLE: dict[str, str] = {
    "human": "user",
    "ai": "assistant",
    "system": "system",
    "tool": "tool",
}


def _memory_message_to_langchain(message: Message) -> BaseMessage:
    """Convert a library :class:`Message` into a LangChain message."""
    role = message.role.value if hasattr(message.role, "value") else str(message.role)
    message_id = str(message.id)
    if role == "user":
        return HumanMessage(content=message.content, id=message_id)
    if role == "assistant":
        return AIMessage(content=message.content, id=message_id)
    if role == "system":
        return SystemMessage(content=message.content, id=message_id)
    # "tool" and any future role: ChatMessage keeps the role verbatim without
    # inventing a tool_call_id that memory never stored.
    return ChatMessage(content=message.content, role=role, id=message_id)


def _langchain_message_to_role(message: BaseMessage) -> str:
    """Map a LangChain message onto a library ``MessageRole`` value."""
    if isinstance(message, ChatMessage):
        return message.role
    return _LC_TYPE_TO_ROLE.get(message.type, "user")


class Neo4jAgentMemory(BaseChatMessageHistory):
    """Neo4j-backed LangChain chat message history, plus memory context assembly.

    Implements ``langchain_core.chat_history.BaseChatMessageHistory``. The async
    surface (``aget_messages`` / ``aadd_messages`` / ``aclear``) talks to Neo4j
    Agent Memory directly; the sync surface only works outside a running event
    loop (the underlying driver is async), and raises a pointed ``RuntimeError``
    otherwise rather than deadlocking on the caller's loop.

    Example — wiring it into a LangChain runnable::

        from langchain_core.runnables.history import RunnableWithMessageHistory
        from neo4j_agent_memory import MemoryClient
        from neo4j_agent_memory.integrations.langchain import Neo4jAgentMemory

        async with MemoryClient(settings) as client:
            chain_with_history = RunnableWithMessageHistory(
                chain,
                lambda session_id: Neo4jAgentMemory(
                    memory_client=client, session_id=session_id
                ),
                input_messages_key="input",
                history_messages_key="history",
            )
            await chain_with_history.ainvoke(
                {"input": "Where should I eat?"},
                config={"configurable": {"session_id": "user-123"}},
            )

    Attributes:
        memory_client: A connected :class:`~neo4j_agent_memory.MemoryClient`.
        session_id: Conversation/session identifier to read and write.
        include_short_term: Include conversation history in ``aload_memory_variables``.
        include_long_term: Include entities/preferences in ``aload_memory_variables``.
        include_reasoning: Include similar past traces in ``aload_memory_variables``.
        max_messages: Message cap for history reads.
        max_preferences: Item cap for long-term lookups.
        max_traces: Trace cap for reasoning lookups.
        extract_entities: Run entity extraction on messages written through the adapter.
        generate_embeddings: Generate embeddings for messages written through the adapter.
    """

    def __init__(
        self,
        memory_client: MemoryClient,
        session_id: str,
        *,
        include_short_term: bool = True,
        include_long_term: bool = True,
        include_reasoning: bool = True,
        max_messages: int = 10,
        max_preferences: int = 5,
        max_traces: int = 3,
        extract_entities: bool = True,
        generate_embeddings: bool = True,
    ) -> None:
        """Initialize the adapter. See the class docstring for attribute meanings."""
        self.memory_client = memory_client
        self.session_id = session_id
        self.include_short_term = include_short_term
        self.include_long_term = include_long_term
        self.include_reasoning = include_reasoning
        self.max_messages = max_messages
        self.max_preferences = max_preferences
        self.max_traces = max_traces
        self.extract_entities = extract_entities
        self.generate_embeddings = generate_embeddings

    # ------------------------------------------------------------------ sync
    @staticmethod
    def _run_sync(coro: Coroutine[Any, Any, _T], *, async_alternative: str) -> _T:
        """Run ``coro`` to completion, refusing to block a running event loop.

        Neo4j Agent Memory is async all the way down and its driver is bound to
        the loop that created it. Scheduling onto the caller's own running loop
        and then blocking on the result (the adapter's pre-1.x behaviour)
        deadlocks, so from async code we raise and name the coroutine to await.
        """
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)

        coro.close()
        msg = (
            "Neo4jAgentMemory's synchronous API cannot be used from inside a "
            f"running event loop. Await {async_alternative} instead."
        )
        raise RuntimeError(msg)

    # -------------------------------------------- BaseChatMessageHistory API
    @property
    def messages(self) -> list[BaseMessage]:  # type: ignore[override]  # ty: ignore[unused-ignore-comment]  # BaseChatMessageHistory declares `messages` as a plain attribute; a read-only property is the documented way to implement it
        """Conversation history as LangChain messages (sync; see :meth:`aget_messages`)."""
        return self._run_sync(self.aget_messages(), async_alternative="aget_messages()")

    async def aget_messages(self) -> list[BaseMessage]:
        """Return this session's messages as LangChain messages, oldest first."""
        conversation = await self.memory_client.short_term.get_conversation(
            self.session_id, limit=self.max_messages
        )
        return [_memory_message_to_langchain(m) for m in conversation.messages]

    def add_messages(self, messages: Sequence[BaseMessage]) -> None:
        """Persist ``messages`` (sync; see :meth:`aadd_messages`)."""
        self._run_sync(self.aadd_messages(messages), async_alternative="aadd_messages(messages)")

    async def aadd_messages(self, messages: Sequence[BaseMessage]) -> None:
        """Persist ``messages`` to short-term memory, in order.

        Messages with no text content (for example an ``AIMessage`` that only
        carries tool calls) are skipped — memory stores text.
        """
        for message in messages:
            content = message.text
            if not content:
                continue
            await self.memory_client.short_term.add_message(
                self.session_id,
                _langchain_message_to_role(message),
                content,
                extract_entities=self.extract_entities,
                generate_embedding=self.generate_embeddings,
            )

    def clear(self) -> None:
        """Clear this session's history (sync; see :meth:`aclear`)."""
        self._run_sync(self.aclear(), async_alternative="aclear()")

    async def aclear(self) -> None:
        """Clear all short-term memory for this session."""
        await self.memory_client.short_term.clear_session(self.session_id)

    # ------------------------------------------------- context assembly API
    @property
    def memory_variables(self) -> list[str]:
        """Names of the keys :meth:`aload_memory_variables` returns."""
        variables = []
        if self.include_short_term:
            variables.append("history")
        if self.include_long_term:
            variables.extend(["context", "preferences"])
        if self.include_reasoning:
            variables.append("similar_tasks")
        return variables

    def load_memory_variables(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """Assemble memory context (sync; see :meth:`aload_memory_variables`)."""
        return self._run_sync(
            self.aload_memory_variables(inputs),
            async_alternative="aload_memory_variables(inputs)",
        )

    async def aload_memory_variables(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """Assemble memory context for the current input.

        Backend tolerance: preference search and similar-trace search are
        bolt-only (NAMS raises :class:`NotSupportedError`). Those keys come back
        empty on the hosted backend instead of raising, and ``context`` falls
        back to entity search when ``long_term.get_context`` yields nothing.

        Args:
            inputs: Chain inputs; ``inputs["input"]`` is used as the search query.

        Returns:
            A dict whose keys are :attr:`memory_variables`.
        """
        query = inputs.get("input", "")
        result: dict[str, Any] = {}

        if self.include_short_term:
            conv = await self.memory_client.short_term.get_conversation(
                self.session_id, limit=self.max_messages
            )
            result["history"] = self._format_messages(conv.messages)

        if self.include_long_term:
            result["context"] = await self._load_long_term_context(query)
            result["preferences"] = await self._load_preferences(query)

        if self.include_reasoning:
            result["similar_tasks"] = self._format_traces(await self._load_traces(query))

        return result

    def save_context(self, inputs: dict[str, Any], outputs: dict[str, str]) -> None:
        """Persist one exchange (sync; see :meth:`asave_context`)."""
        self._run_sync(
            self.asave_context(inputs, outputs),
            async_alternative="asave_context(inputs, outputs)",
        )

    async def asave_context(self, inputs: dict[str, Any], outputs: dict[str, str]) -> None:
        """Persist one user/assistant exchange to short-term memory."""
        user_input = inputs.get("input", "")
        assistant_output = outputs.get("output", "")

        if user_input:
            await self.memory_client.short_term.add_message(
                self.session_id,
                "user",
                user_input,
                extract_entities=self.extract_entities,
                generate_embedding=self.generate_embeddings,
            )

        if assistant_output:
            await self.memory_client.short_term.add_message(
                self.session_id,
                "assistant",
                assistant_output,
                extract_entities=self.extract_entities,
                generate_embedding=self.generate_embeddings,
            )

    # Deprecated one-release aliases for the old private coroutines. Callers
    # that reached into the underscore names (there was no public async surface
    # before 0.6) keep working; prefer the public names above.
    _load_memory_variables_async = aload_memory_variables
    _save_context_async = asave_context

    # ----------------------------------------------------------- internals
    async def _load_long_term_context(self, query: str) -> str:
        """Long-term context, falling back to entity search on NAMS."""
        try:
            context: str = await self.memory_client.long_term.get_context(
                query, max_items=self.max_preferences
            )
        except NotSupportedError:
            context = ""
        if context:
            return context

        # NAMS has no long-term context endpoint (it returns ""), but entity
        # search is supported — assemble an equivalent block from it.
        try:
            entities = await self.memory_client.long_term.search_entities(
                query, limit=self.max_preferences
            )
        except NotSupportedError:
            return ""
        lines = []
        for entity in entities:
            line = f"- {entity.display_name} ({entity.full_type})"
            if entity.description:
                line += f": {entity.description}"
            lines.append(line)
        return "\n".join(lines)

    async def _load_preferences(self, query: str) -> list[dict[str, str]]:
        """Preference lookups are bolt-only; return [] where unsupported."""
        try:
            prefs: list[Preference] = await self.memory_client.long_term.search_preferences(
                query, limit=self.max_preferences
            )
        except NotSupportedError:
            logger.debug("search_preferences unsupported on this backend; returning []")
            return []
        return [{"category": p.category, "preference": p.preference} for p in prefs]

    async def _load_traces(self, query: str) -> list[ReasoningTrace]:
        """Similar-trace search is bolt-only; return [] where unsupported."""
        try:
            traces: list[ReasoningTrace] = await self.memory_client.reasoning.get_similar_traces(
                query, limit=self.max_traces
            )
        except NotSupportedError:
            logger.debug("get_similar_traces unsupported on this backend; returning []")
            return []
        return traces

    def _format_messages(self, messages: list[Message]) -> str:
        """Format messages for context."""
        return "\n".join(f"{msg.role.value}: {msg.content}" for msg in messages)

    def _format_traces(self, traces: list[ReasoningTrace]) -> str:
        """Format reasoning traces for context."""
        lines = []
        for trace in traces:
            lines.append(f"Task: {trace.task}")
            if trace.outcome:
                lines.append(f"  Outcome: {trace.outcome}")
        return "\n".join(lines)
