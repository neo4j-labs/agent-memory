"""Microsoft Agent Framework HistoryProvider implementation.

Provides Neo4j-backed persistent chat history storage for
Microsoft Agent Framework agents.
"""

from __future__ import annotations

import logging
from collections.abc import MutableMapping, Sequence
from typing import TYPE_CHECKING, Any

from ..base import validate_session_id

if TYPE_CHECKING:
    from neo4j_agent_memory import MemoryClient

logger = logging.getLogger(__name__)

try:
    from agent_framework import HistoryProvider, Message

    class Neo4jChatMessageStore(HistoryProvider):
        """
        Neo4j-backed implementation of chat history storage.

        Extends HistoryProvider to integrate directly into the agent's
        context provider pipeline. The agent automatically loads history
        before invocation and saves after via before_run/after_run.

        Messages are stored with NEXT_MESSAGE relationships forming a
        conversation chain in Neo4j.

        Example:
            from neo4j_agent_memory import MemoryClient, MemorySettings
            from neo4j_agent_memory.integrations.microsoft_agent import Neo4jChatMessageStore
            from agent_framework.azure import AzureOpenAIResponsesClient

            async with MemoryClient(settings) as client:
                history = Neo4jChatMessageStore(
                    memory_client=client,
                    session_id="user-123",
                )

                agent = chat_client.as_agent(
                    instructions="You are a helpful assistant.",
                    context_providers=[history],
                )

                # History is loaded/saved automatically by the framework

        Attributes:
            session_id: The session identifier for message storage.
            max_messages: Optional limit on messages to retrieve.
        """

        def __init__(
            self,
            memory_client: MemoryClient,
            session_id: str,
            *,
            source_id: str = "neo4j-history",
            max_messages: int | None = None,
            extract_entities: bool = False,
            generate_embeddings: bool = True,
            load_messages: bool = True,
            store_inputs: bool = True,
            store_outputs: bool = True,
        ):
            """
            Initialize the Neo4j chat history provider.

            Args:
                memory_client: Connected MemoryClient instance.
                session_id: Session identifier for message storage.
                source_id: Unique identifier for this provider in the pipeline.
                max_messages: Optional limit on messages to store/retrieve.
                extract_entities: Whether to extract entities on message add.
                generate_embeddings: Whether to generate embeddings for search.
                load_messages: Whether to load messages before invocation.
                store_inputs: Whether to store input messages.
                store_outputs: Whether to store response messages.
            """
            super().__init__(
                source_id=source_id,
                load_messages=load_messages,
                store_inputs=store_inputs,
                store_outputs=store_outputs,
            )
            self._client = memory_client
            self._session_id = validate_session_id(session_id)
            self._max_messages = max_messages
            self._extract_entities = extract_entities
            self._generate_embeddings = generate_embeddings

        @property
        def session_id(self) -> str:
            """Get the session ID."""
            return self._session_id

        @property
        def memory_client(self) -> MemoryClient:
            """Get the underlying memory client."""
            return self._client

        async def get_messages(
            self,
            session_id: str | None = None,
            *,
            state: dict[str, Any] | None = None,
            **kwargs: Any,
        ) -> list[Message]:
            """
            Retrieve messages from Neo4j in chronological order.

            Called by HistoryProvider.before_run() to load conversation
            history before agent invocation.

            Args:
                session_id: Session ID (uses self._session_id if None).
                state: Provider-scoped session state supplied by the framework.
                    Unused — history lives in Neo4j, not in session state.
                **kwargs: Additional arguments.

            Returns:
                List of Message objects in conversation order.
            """
            limit = self._max_messages or 1000

            conv = await self._client.short_term.get_conversation(
                session_id=self._session_id,
                limit=limit,
            )

            messages: list[Message] = []
            for msg in conv.messages:
                role = msg.role.value if hasattr(msg.role, "value") else str(msg.role)
                content = msg.content
                messages.append(Message(role, [content]))

            return messages

        async def save_messages(
            self,
            session_id: str | None,
            messages: Sequence[Message],
            *,
            state: dict[str, Any] | None = None,
            **kwargs: Any,
        ) -> None:
            """
            Save messages to Neo4j storage.

            Called by HistoryProvider.after_run() to persist messages
            after agent invocation.

            Args:
                session_id: Session ID (uses self._session_id if None).
                messages: Sequence of Message objects to store.
                state: Provider-scoped session state supplied by the framework.
                    Unused — history lives in Neo4j, not in session state.
                **kwargs: Additional arguments.
            """
            if not messages:
                return

            for msg in messages:
                role = msg.role
                content = msg.text

                if not content:
                    continue

                # Build metadata from message attributes
                metadata: dict[str, Any] = {}

                if msg.author_name:
                    metadata["name"] = msg.author_name

                # Check for function call/result content
                for c in msg.contents:
                    if c.type == "function_call" and c.name:
                        metadata.setdefault("tool_calls", []).append(
                            {
                                "id": c.call_id,
                                "name": c.name,
                                "arguments": c.arguments,
                            }
                        )
                    elif c.type == "function_result" and c.call_id:
                        metadata["tool_call_id"] = c.call_id

                await self._client.short_term.add_message(
                    session_id=self._session_id,
                    role=role,
                    content=content,
                    extract_entities=self._extract_entities,
                    generate_embedding=self._generate_embeddings,
                    metadata=metadata if metadata else None,
                )

        async def add_messages(self, messages: Sequence[Message]) -> None:
            """
            Add messages to Neo4j storage.

            Convenience method that delegates to save_messages.

            Args:
                messages: Sequence of Message objects to store.
            """
            await self.save_messages(self._session_id, messages)

        async def list_messages(self) -> list[Message]:
            """
            Retrieve messages from Neo4j in chronological order.

            Convenience method that delegates to get_messages.

            Returns:
                List of Message objects in conversation order.
            """
            return await self.get_messages()

        async def clear(self) -> None:
            """
            Clear all messages for this session.

            This removes all messages from the conversation but preserves
            any entities or preferences that were extracted.
            """
            await self._client.short_term.clear_session(self._session_id)

        def serialize(self) -> MutableMapping[str, Any]:
            """
            Serialize store state for persistence.

            Returns:
                Dictionary containing serializable state.
            """
            return {
                "session_id": self._session_id,
                "source_id": self.source_id,
                "max_messages": self._max_messages,
                "extract_entities": self._extract_entities,
                "generate_embeddings": self._generate_embeddings,
            }

        @classmethod
        def deserialize(
            cls,
            serialized_state: MutableMapping[str, Any],
            memory_client: MemoryClient,
        ) -> Neo4jChatMessageStore:
            """
            Restore store from serialized state.

            .. warning::
                Requires a fresh MemoryClient to be provided. Database
                connections cannot be serialized.

            Args:
                serialized_state: Previously serialized state.
                memory_client: A connected MemoryClient instance.

            Returns:
                Restored Neo4jChatMessageStore instance.
            """
            return cls(
                memory_client=memory_client,
                session_id=serialized_state["session_id"],
                source_id=serialized_state.get("source_id", "neo4j-history"),
                max_messages=serialized_state.get("max_messages"),
                extract_entities=serialized_state.get("extract_entities", False),
                generate_embeddings=serialized_state.get("generate_embeddings", True),
            )


except ImportError:
    # Microsoft Agent Framework not installed
    pass
