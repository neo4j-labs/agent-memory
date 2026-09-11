"""``create_agent`` middleware that gives a LangChain 1.x agent Neo4j memory.

LangChain 1.x replaced the pre-v1 ``BaseMemory``/``ConversationChain`` story with
agent middleware: :func:`langchain.agents.create_agent` accepts a list of
:class:`~langchain.agents.middleware.AgentMiddleware` objects whose hooks run
around every model call. :class:`Neo4jMemoryMiddleware` is that hook set for
Neo4j Agent Memory — it reads memory before the model runs and writes the turn
back after.

Requires the ``langchain`` distribution (``pip install
neo4j-agent-memory[langchain-agents]``), not just ``langchain-core``; importing
this module without it raises ``ImportError``, and
``neo4j_agent_memory.integrations.langchain`` simply omits
``Neo4jMemoryMiddleware`` from its exports in that case.

Example::

    from langchain.agents import create_agent
    from neo4j_agent_memory import MemoryClient
    from neo4j_agent_memory.integrations.langchain import Neo4jMemoryMiddleware

    async with MemoryClient(settings) as client:
        agent = create_agent(
            model,
            tools=[...],
            middleware=[Neo4jMemoryMiddleware(client, session_id="user-123")],
        )
        result = await agent.ainvoke({"messages": [("user", "Where should I eat?")]})

Only the async hooks are implemented, because Neo4j Agent Memory is async all
the way down. Invoke the agent with ``ainvoke``/``astream``; the sync hooks
raise a pointed error rather than silently skipping memory.
"""

from __future__ import annotations

import logging
from collections import OrderedDict
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from langchain.agents.middleware import AgentMiddleware, AgentState
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from neo4j_agent_memory.core.exceptions import NotSupportedError
from neo4j_agent_memory.integrations.langchain.memory import _langchain_message_to_role

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from langchain.agents.middleware import ModelRequest, ModelResponse
    from langchain_core.messages import BaseMessage
    from langgraph.runtime import Runtime

    from neo4j_agent_memory import MemoryClient

logger = logging.getLogger(__name__)

_SYNC_HINT = (
    "Neo4jMemoryMiddleware only implements the async hooks because Neo4j Agent "
    "Memory is async. Invoke the agent with `await agent.ainvoke(...)` or "
    "`agent.astream(...)`."
)

#: How many LangChain message ids to remember per middleware instance so a
#: multi-turn tool loop does not persist the same message on every model call.
_SEEN_IDS_MAXLEN = 2048


class Neo4jMemoryMiddleware(AgentMiddleware[AgentState[Any], Any, Any]):
    """Inject Neo4j Agent Memory context into an agent and persist its turns.

    Hooks:
        * ``abefore_model`` — persists any new user messages, so the turn is
          recorded even if the model call then fails.
        * ``awrap_model_call`` — appends a memory block to the request's system
          message. This is the injection point LangChain's own middleware uses
          (see ``TodoListMiddleware``); it leaves agent state untouched, so the
          memory block never accumulates in the thread's message list.
        * ``aafter_model`` — persists the assistant's reply.

    Args:
        memory_client: A connected :class:`~neo4j_agent_memory.MemoryClient`.
        session_id: Session/conversation id to read and write.
        include_short_term: Include conversation history in the injected block.
        include_long_term: Include entities/facts/preferences in the injected block.
        include_reasoning: Include similar past traces in the injected block.
        max_items: Per-layer item cap for the context lookup.
        store_messages: Persist user and assistant messages as they flow through.
        extract_entities: Run entity extraction on persisted messages.
        context_header: Heading placed above the injected memory block.
    """

    def __init__(
        self,
        memory_client: MemoryClient,
        session_id: str,
        *,
        include_short_term: bool = True,
        include_long_term: bool = True,
        include_reasoning: bool = True,
        max_items: int = 10,
        store_messages: bool = True,
        extract_entities: bool = True,
        context_header: str = "# Memory",
    ) -> None:
        """Initialize the middleware. See the class docstring for argument meanings."""
        super().__init__()
        self.memory_client = memory_client
        self.session_id = session_id
        self.include_short_term = include_short_term
        self.include_long_term = include_long_term
        self.include_reasoning = include_reasoning
        self.max_items = max_items
        self.store_messages = store_messages
        self.extract_entities = extract_entities
        self.context_header = context_header
        # No extra tools registered by this middleware.
        self.tools = []
        # Message ids already written to memory. `aafter_model` runs once per
        # model call, and a tool-calling agent calls the model repeatedly with
        # the same history, so without this the first user message would be
        # stored again on every loop iteration.
        self._persisted: OrderedDict[str, None] = OrderedDict()

    # ------------------------------------------------------------- persisting
    def _mark_persisted(self, message_id: str) -> None:
        self._persisted[message_id] = None
        while len(self._persisted) > _SEEN_IDS_MAXLEN:
            self._persisted.popitem(last=False)

    def _unpersisted(
        self, messages: Sequence[BaseMessage], kinds: tuple[type[BaseMessage], ...]
    ) -> list[BaseMessage]:
        """Messages of ``kinds`` with text content that memory has not seen yet."""
        pending = []
        for message in messages:
            if not isinstance(message, kinds) or not message.text:
                continue
            key = message.id or f"{message.type}:{message.text}"
            if key in self._persisted:
                continue
            self._mark_persisted(key)
            pending.append(message)
        return pending

    async def _store(self, messages: Sequence[BaseMessage]) -> None:
        for message in messages:
            await self.memory_client.short_term.add_message(
                self.session_id,
                _langchain_message_to_role(message),
                message.text,
                extract_entities=self.extract_entities,
            )

    # ------------------------------------------------------------- LC hooks
    async def abefore_model(
        self, state: AgentState[Any], runtime: Runtime[Any]
    ) -> dict[str, Any] | None:
        """Persist any user messages the agent has not stored yet."""
        if self.store_messages:
            await self._store(self._unpersisted(state["messages"], (HumanMessage,)))
        return None

    def before_model(self, state: AgentState[Any], runtime: Runtime[Any]) -> dict[str, Any] | None:
        """Sync hook — not supported; see :data:`_SYNC_HINT`."""
        raise NotImplementedError(_SYNC_HINT)

    async def aafter_model(
        self, state: AgentState[Any], runtime: Runtime[Any]
    ) -> dict[str, Any] | None:
        """Persist the assistant's reply."""
        if self.store_messages:
            await self._store(self._unpersisted(state["messages"], (AIMessage,)))
        return None

    def after_model(self, state: AgentState[Any], runtime: Runtime[Any]) -> dict[str, Any] | None:
        """Sync hook — not supported; see :data:`_SYNC_HINT`."""
        raise NotImplementedError(_SYNC_HINT)

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any]:
        """Append the memory block to the system message, then call the model."""
        block = await self._memory_block(request.messages)
        if not block:
            return await handler(request)

        if request.system_message is not None:
            content = f"{request.system_message.text}\n\n{block}"
        else:
            content = block
        return await handler(request.override(system_message=SystemMessage(content=content)))

    async def _memory_block(self, messages: Sequence[BaseMessage]) -> str:
        """Assemble the memory context for the most recent user message."""
        query = next(
            (m.text for m in reversed(messages) if isinstance(m, HumanMessage) and m.text),
            "",
        )
        try:
            context = await self.memory_client.get_context(
                query,
                session_id=self.session_id,
                include_short_term=self.include_short_term,
                include_long_term=self.include_long_term,
                include_reasoning=self.include_reasoning,
                max_items=self.max_items,
            )
        except NotSupportedError:
            logger.debug("get_context unsupported on this backend; skipping injection")
            return ""
        if not context:
            return ""
        return f"{self.context_header}\n{context}"
