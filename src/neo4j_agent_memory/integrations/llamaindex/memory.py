"""LlamaIndex memory integration."""

import asyncio
import json
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from neo4j_agent_memory import MemoryClient

try:
    from llama_index.core.base.llms.types import ChatMessage, MessageRole
    from llama_index.core.memory import BaseMemory

    # Check if MessageRole has TOOL; if not, we need to handle it specially.
    _HAS_TOOL_ROLE = hasattr(MessageRole, "TOOL")

    # Pattern to extract actual text from MCP CallToolResult repr strings.
    # Matches: meta=None content=[TextContent(type='text', text='...'...)] ...
    _MCP_RESULT_RE = re.compile(
        r"^meta=None\s+content=\[TextContent\(type='text',\s*text='(.*)',\s*annotations=.*?\)\]"
        r"\s+structuredContent=\S+\s+isError=\S+$",
        re.DOTALL,
    )

    class Neo4jLlamaIndexMemory(BaseMemory):
        """
        LlamaIndex memory backed by Neo4j Agent Memory.

        Uses the current BaseMemory interface (ChatMessage-based).

        Example:
            from neo4j_agent_memory import MemoryClient, MemorySettings
            from neo4j_agent_memory.integrations.llamaindex import Neo4jLlamaIndexMemory

            async with MemoryClient(settings) as client:
                memory = Neo4jLlamaIndexMemory(
                    memory_client=client,
                    session_id="user-123"
                )
                # Use with LlamaIndex agent
                response = await agent.run("Hello!", memory=memory)
        """

        def __init__(
            self,
            memory_client: "MemoryClient",
            session_id: str,
        ):
            self._client = memory_client
            self._session_id = session_id
            try:
                self._loop = asyncio.get_running_loop()
            except RuntimeError:
                self._loop = None

        # ------------------------------------------------------------------
        # Internal helpers
        # ------------------------------------------------------------------

        def _run_async(self, coro: Any) -> Any:
            """Run an async coroutine from sync context."""
            if self._loop is None or self._loop.is_closed():
                return asyncio.run(coro)
            try:
                running = asyncio.get_running_loop()
            except RuntimeError:
                running = None
            if running is self._loop:
                import nest_asyncio

                nest_asyncio.apply()
                return self._loop.run_until_complete(coro)
            future = asyncio.run_coroutine_threadsafe(coro, self._loop)
            return future.result()

        @staticmethod
        def _parse_role(role_value: str) -> MessageRole:
            """Convert a role string to MessageRole enum."""
            if role_value == "tool":
                if _HAS_TOOL_ROLE:
                    return MessageRole.TOOL
                return (
                    MessageRole.CHATBOT
                    if hasattr(MessageRole, "CHATBOT")
                    else MessageRole.ASSISTANT
                )
            try:
                return MessageRole(role_value)
            except ValueError:
                return MessageRole.USER

        @staticmethod
        def _make_serializable(obj: Any) -> Any:
            """Recursively convert Pydantic models / other objects to plain dicts."""
            if obj is None or isinstance(obj, (str, int, float, bool)):
                return obj
            if hasattr(obj, "model_dump"):
                return obj.model_dump()
            if hasattr(obj, "dict"):
                return obj.dict()
            if isinstance(obj, dict):
                return {k: Neo4jLlamaIndexMemory._make_serializable(v) for k, v in obj.items()}
            if isinstance(obj, (list, tuple)):
                return [Neo4jLlamaIndexMemory._make_serializable(i) for i in obj]
            return str(obj)

        @staticmethod
        def _get_metadata_dict(msg: Any) -> dict:
            """Safely extract the metadata dict from a stored message.

            Handles both dict and JSON-string forms of the metadata field.
            """
            raw_meta = getattr(msg, "metadata", None)
            if raw_meta is None:
                return {}
            if isinstance(raw_meta, dict):
                return dict(raw_meta)
            if isinstance(raw_meta, str):
                try:
                    parsed = json.loads(raw_meta)
                    return parsed if isinstance(parsed, dict) else {}
                except (json.JSONDecodeError, TypeError):
                    return {}
            return {}

        @staticmethod
        def _extract_mcp_text(content: str) -> str:
            """Extract the actual text payload from an MCP CallToolResult repr.

            Tool responses stored via `str(tool_result)` look like:

                meta=None content=[TextContent(type='text', text='...'...)] ...

            This method extracts the inner text so the LLM receives clean
            tool output rather than a Python repr string.
            """
            if not content or not content.startswith("meta="):
                return content
            m = _MCP_RESULT_RE.match(content)
            if m:
                # The inner text has escaped quotes/newlines; unescape them.
                inner = m.group(1)
                try:
                    # Try JSON-decoding in case it was a JSON-escaped string
                    return json.loads(f'"{inner}"')
                except (json.JSONDecodeError, ValueError):
                    return inner.replace("\\'", "'").replace("\\n", "\n")
            return content

        @staticmethod
        def _has_tool_calls(msg: Any) -> bool:
            meta = Neo4jLlamaIndexMemory._get_metadata_dict(msg)
            return bool(meta.get("tool_calls"))

        @staticmethod
        def _get_tool_call_ids(msg: Any) -> set[str]:
            meta = Neo4jLlamaIndexMemory._get_metadata_dict(msg)
            return {
                tc["id"] for tc in meta.get("tool_calls", []) if isinstance(tc, dict) and "id" in tc
            }

        @staticmethod
        def _get_tool_call_id(msg: Any) -> str | None:
            meta = Neo4jLlamaIndexMemory._get_metadata_dict(msg)
            return meta.get("tool_call_id")

        @staticmethod
        def _is_tool_call_msg(msg: Any) -> bool:
            role_str = msg.role.value if hasattr(msg.role, "value") else str(msg.role)
            return role_str == "assistant" and Neo4jLlamaIndexMemory._has_tool_calls(msg)

        @staticmethod
        def _is_tool_response_msg(msg: Any) -> bool:
            role_str = msg.role.value if hasattr(msg.role, "value") else str(msg.role)
            return role_str == "tool"

        def _msg_to_chat_message(self, msg: Any, source: str = "short_term") -> ChatMessage:
            """Convert a stored message object to a ChatMessage.

            Reconstructs additional_kwargs from the metadata field so that
            tool_calls, tool_call_id, and name survive the round-trip.
            Also cleans MCP repr content for tool response messages.
            """
            role_str = msg.role.value if hasattr(msg.role, "value") else str(msg.role)
            stored_kwargs = self._get_metadata_dict(msg)
            stored_kwargs.setdefault("source", source)
            stored_kwargs.setdefault("id", str(msg.id))

            content = msg.content
            # Clean up MCP result repr in tool responses
            if role_str == "tool" and content:
                content = self._extract_mcp_text(content)

            return ChatMessage(
                role=self._parse_role(role_str),
                content=content,
                additional_kwargs=stored_kwargs,
            )

        def _ensure_tool_call_integrity(self, messages: list) -> list:
            """Filter out incomplete tool-call pairs to prevent API errors.

            OpenAI requires that every assistant message with tool_calls is
            immediately followed by tool messages for each tool_call_id.
            If we can't guarantee the pair is complete, we drop both the
            assistant tool_call message and any orphaned tool responses.
            """
            # Index all available tool responses by their tool_call_id
            available_responses: dict[str, Any] = {}
            for msg in messages:
                if self._is_tool_response_msg(msg):
                    tcid = self._get_tool_call_id(msg)
                    if tcid:
                        available_responses[tcid] = msg

            # Index all tool_call_ids from assistant messages
            available_call_ids: set[str] = set()
            for msg in messages:
                if self._is_tool_call_msg(msg):
                    available_call_ids.update(self._get_tool_call_ids(msg))

            # First pass: keep assistant tool_call msgs only if ALL their
            # responses exist; keep tool responses only if their calling
            # assistant msg exists.
            kept_assistant_call_ids: set[str] = set()
            filtered: list = []
            for msg in messages:
                if self._is_tool_call_msg(msg):
                    needed = self._get_tool_call_ids(msg)
                    if needed and needed.issubset(available_responses):
                        filtered.append(msg)
                        kept_assistant_call_ids.update(needed)
                elif self._is_tool_response_msg(msg):
                    tcid = self._get_tool_call_id(msg)
                    if tcid and tcid in available_call_ids:
                        filtered.append(msg)
                else:
                    filtered.append(msg)

            # Second pass: drop tool responses whose assistant was dropped
            final: list = []
            for msg in filtered:
                if self._is_tool_response_msg(msg):
                    tcid = self._get_tool_call_id(msg)
                    if tcid and tcid in kept_assistant_call_ids:
                        final.append(msg)
                else:
                    final.append(msg)

            return final

        # ------------------------------------------------------------------
        # Sync interface
        # ------------------------------------------------------------------

        def get(self, input: str | None = None, **kwargs: Any) -> list[ChatMessage]:
            return self._run_async(self.aget(input, **kwargs))

        def put(self, message: ChatMessage) -> None:
            self._run_async(self.aput(message))

        def get_all(self) -> list[ChatMessage]:
            return self._run_async(self.aget_all())

        def set(self, messages: list[ChatMessage]) -> None:
            self._run_async(self.aset(messages))

        def reset(self) -> None:
            self._run_async(self.areset())

        def put_messages(self, messages: list[ChatMessage]) -> None:
            for msg in messages:
                self.put(msg)

        # ------------------------------------------------------------------
        # Async interface
        # ------------------------------------------------------------------

        async def aget(self, input: str | None = None, **kwargs: Any) -> list[ChatMessage]:
            raw_messages: list = []
            seen_ids: set[str] = set()

            # ---- Always load session conversation history first ----
            conv = await self._client.short_term.get_conversation(self._session_id, limit=10)
            for msg in conv.messages:
                msg_id = str(msg.id)
                if msg_id not in seen_ids:
                    raw_messages.append(msg)
                    seen_ids.add(msg_id)

            # ---- Optionally augment with semantic search results ----
            if input:
                short_term_results = await self._client.short_term.search_messages(input, limit=5)
                for msg in short_term_results:
                    msg_id = str(msg.id)
                    if msg_id not in seen_ids:
                        raw_messages.append(msg)
                        seen_ids.add(msg_id)

            # Ensure tool-call pairs are complete before converting
            raw_messages = self._ensure_tool_call_integrity(raw_messages)

            messages: list[ChatMessage] = []
            for msg in raw_messages:
                messages.append(self._msg_to_chat_message(msg, source="short_term"))

            # ---- Augment with long-term entity knowledge ----
            if input:
                entities = await self._client.long_term.search_entities(input, limit=5)
                for entity in entities:
                    text = entity.display_name
                    if entity.description:
                        text += f": {entity.description}"
                    entity_type = (
                        entity.type.value if hasattr(entity.type, "value") else str(entity.type)
                    )
                    messages.append(
                        ChatMessage(
                            role=MessageRole.SYSTEM,
                            content=text,
                            additional_kwargs={
                                "source": "long_term",
                                "entity_type": entity_type,
                                "id": str(entity.id),
                            },
                        )
                    )

            return messages

        async def aput(self, message: ChatMessage) -> None:
            """Store a ChatMessage in memory.

            Serializes additional_kwargs into metadata, and cleans MCP result
            repr strings in tool responses so stored content is the actual
            tool output text.
            """
            role_str = message.role.value if hasattr(message.role, "value") else str(message.role)
            metadata = (
                self._make_serializable(message.additional_kwargs)
                if message.additional_kwargs
                else None
            )

            content = message.content
            # Clean MCP result repr on write so data is stored cleanly
            if role_str == "tool" and content:
                content = self._extract_mcp_text(content)

            await self._client.short_term.add_message(
                self._session_id, role_str, content, metadata=metadata
            )

        async def aget_all(self) -> list[ChatMessage]:
            return await self.aget(input=None)

        async def aset(self, messages: list[ChatMessage]) -> None:
            await self.areset()
            for msg in messages:
                await self.aput(msg)

        async def areset(self) -> None:
            await self._client.short_term.clear_session(self._session_id)

        @classmethod
        def from_defaults(
            cls,
            memory_client: "MemoryClient",
            session_id: str,
        ) -> "Neo4jLlamaIndexMemory":
            return cls(memory_client=memory_client, session_id=session_id)

except ImportError:
    pass
