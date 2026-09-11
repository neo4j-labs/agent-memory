"""Google ADK ``BaseMemoryService`` implementation backed by Neo4j.

Targets the **google-adk 2.x** memory contract
(``google.adk.memory.base_memory_service``):

============================================  =========================================
ADK 2.x method                                 Implemented here
============================================  =========================================
``add_session_to_memory(session)``             ingests ``session.events`` (or a dict /
                                               list of messages) into short-term memory
``add_events_to_memory(*, app_name, user_id,   ingests an incremental slice of events —
events, session_id, custom_metadata)``         used by ``Context.add_events_to_memory()``
``add_memory(*, app_name, user_id, memories,   writes explicit ADK ``MemoryEntry`` items;
custom_metadata)``                             also keeps the 0.5.0 convenience form
                                               ``add_memory(content=..., memory_type=...)``
``search_memory(*, app_name, user_id, query)``  hybrid message + entity + preference
                                               recall, returned as a
                                               ``SearchMemoryResponse``
============================================  =========================================

``SearchMemoryResponse.memories`` holds ADK ``MemoryEntry`` objects whose
``content`` is a ``google.genai.types.Content`` — so ``load_memory`` and
``preload_memory`` render them directly. ``author`` carries the source
(message role, or ``"entity"`` / ``"preference"`` for long-term recall),
``timestamp`` the ISO-8601 creation time, and ``custom_metadata`` the memory
type and similarity score that the library's own ``MemoryEntry`` dataclass
exposes.

The ADK base class is imported lazily: ``google-adk`` is an optional extra
(``pip install "neo4j-agent-memory[google-adk]"``), so when it is absent the
module falls back to local stand-ins with the same attribute shape and the
service still works against plain dict sessions.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    # ``_ADKBase`` is aliased to ``object`` for static analysis: the real base
    # is an ABC that only exists when the optional extra is installed, and this
    # class deliberately widens two of its signatures (``query`` stays
    # positional-or-keyword, and ``add_memory`` keeps the 0.5.0 convenience
    # form which returns an entry). Runtime conformance is asserted in
    # ``tests/unit/integrations/test_google_adk.py``.
    from google.adk.memory.base_memory_service import SearchMemoryResponse
    from google.adk.memory.memory_entry import MemoryEntry as ADKMemoryEntry
    from google.genai.types import Content as ADKContent
    from google.genai.types import Part as ADKPart

    _ADKBase = object
else:
    try:
        from google.adk.memory.base_memory_service import (
            BaseMemoryService as _ADKBase,
        )
        from google.adk.memory.base_memory_service import SearchMemoryResponse
        from google.adk.memory.memory_entry import MemoryEntry as ADKMemoryEntry
        from google.genai.types import Content as ADKContent
        from google.genai.types import Part as ADKPart
    except ImportError:  # pragma: no cover - exercised in no-ADK environments
        _ADKBase = object

        class ADKPart:
            """Stand-in for ``google.genai.types.Part`` (text parts only)."""

            def __init__(self, text: str | None = None, **_: Any) -> None:
                self.text = text

        class ADKContent:
            """Stand-in for ``google.genai.types.Content``."""

            def __init__(
                self,
                role: str | None = None,
                parts: list[Any] | None = None,
                **_: Any,
            ) -> None:
                self.role = role
                self.parts = list(parts or [])

        class ADKMemoryEntry:
            """Stand-in for ``google.adk.memory.MemoryEntry`` (ADK 2.x shape)."""

            def __init__(
                self,
                *,
                content: Any,
                custom_metadata: dict[str, Any] | None = None,
                id: str | None = None,  # mirrors the ADK field name
                author: str | None = None,
                timestamp: str | None = None,
                **_: Any,
            ) -> None:
                self.content = content
                self.custom_metadata = custom_metadata or {}
                self.id = id
                self.author = author
                self.timestamp = timestamp

        class SearchMemoryResponse:
            """Stand-in for ``google.adk.memory.SearchMemoryResponse``."""

            def __init__(self, memories: Sequence[Any] | None = None, **_: Any) -> None:
                self.memories: list[Any] = list(memories or [])


from neo4j_agent_memory.integrations.google_adk.types import (
    MemoryEntry,
    SessionMessage,
    entity_to_memory_entry,
    message_to_memory_entry,
    preference_to_memory_entry,
    session_message_from_dict,
)

if TYPE_CHECKING:
    from neo4j_agent_memory import MemoryClient

logger = logging.getLogger(__name__)

#: Roles accepted by ``google.genai.types.Content``. Everything the library
#: stores that is not a user turn is surfaced to the model as ``"model"``.
_USER_ROLE = "user"
_MODEL_ROLE = "model"


def _content_role(author: str | None) -> str:
    """Map a library role/author onto a google-genai ``Content.role``."""
    return _USER_ROLE if (author or "").lower() == _USER_ROLE else _MODEL_ROLE


def _message_role(author: str | None) -> str:
    """Map an ADK event ``author`` onto a library ``MessageRole`` value.

    ADK authors are ``"user"`` for user turns and the **agent name** for model
    turns (``"memory_demo"``, ``"supervisor"``, …), while the library validates
    roles against ``MessageRole`` (user / assistant / system / tool). Anything
    that is not a known role is an agent, so it is stored as ``"assistant"``
    and the original author is preserved in the message metadata — without
    this, every agent-authored event is rejected on write.
    """
    role = (author or _USER_ROLE).lower()
    if role in {"user", "assistant", "system", "tool"}:
        return role
    # ``"model"`` and agent names alike are assistant turns.
    return "assistant"


def _iso_timestamp(value: Any) -> str | None:
    """Render a timestamp as the ISO-8601 string ADK's ``MemoryEntry`` wants.

    Accepts ``datetime`` as well as ``neo4j.time.DateTime`` (both expose
    ``isoformat()``) and yields ``None`` for anything else, since ADK validates
    the field as ``Optional[str]``.
    """
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        rendered = isoformat()
        if isinstance(rendered, str):
            return rendered
    return None


def _adk_author(entry: MemoryEntry) -> str:
    """Derive the ADK ``author`` for a library memory entry.

    Messages keep their role (``user`` / ``assistant`` / an agent name);
    long-term recall is attributed to its memory type so ``preload_memory``
    renders ``entity: Acme Corp: ...`` rather than an anonymous line.
    """
    if entry.memory_type == "message":
        metadata = entry.metadata or {}
        # ``adk_author`` preserves the ADK agent name for model turns.
        author = metadata.get("adk_author") or metadata.get("role")
        return str(author) if author else _USER_ROLE
    return entry.memory_type


def to_adk_memory_entry(entry: MemoryEntry) -> ADKMemoryEntry:
    """Convert a library :class:`MemoryEntry` into an ADK 2.x ``MemoryEntry``.

    Args:
        entry: The library-side entry produced by the ``*_to_memory_entry``
            converters in :mod:`neo4j_agent_memory.integrations.google_adk.types`.

    Returns:
        An ADK ``MemoryEntry`` with ``content`` as a ``types.Content``,
        ``author``, ISO-8601 ``timestamp``, ``id`` and ``custom_metadata``.
    """
    author = _adk_author(entry)
    custom_metadata: dict[str, Any] = {"memory_type": entry.memory_type}
    if entry.score is not None:
        custom_metadata["score"] = entry.score
    for key in ("session_id", "category", "type"):
        value = (entry.metadata or {}).get(key)
        if value is not None:
            custom_metadata[key] = value

    return ADKMemoryEntry(
        content=ADKContent(
            role=_content_role(author),
            parts=[ADKPart(text=str(entry.content))],
        ),
        author=author,
        timestamp=_iso_timestamp(entry.timestamp),
        id=str(entry.id) if entry.id else None,
        custom_metadata=custom_metadata,
    )


class Neo4jMemoryService(_ADKBase):
    """Neo4j-backed memory service for Google ADK agents.

    Implements the google-adk 2.x ``BaseMemoryService`` interface to provide:

    - session / event ingestion into short-term memory (with entity extraction)
    - hybrid semantic search across messages, entities and preferences
    - direct memory writes (ADK ``MemoryEntry`` items or the convenience form)

    Example:
        from google.adk.agents import LlmAgent
        from google.adk.runners import Runner
        from google.adk.sessions import InMemorySessionService
        from google.adk.tools import load_memory

        from neo4j_agent_memory import MemoryClient, MemorySettings
        from neo4j_agent_memory.integrations.google_adk import Neo4jMemoryService

        async with MemoryClient(MemorySettings()) as client:
            memory_service = Neo4jMemoryService(memory_client=client, user_id="user-123")
            runner = Runner(
                app_name="memory-demo",
                agent=LlmAgent(name="demo", model="gemini-2.5-flash", tools=[load_memory]),
                session_service=InMemorySessionService(),
                memory_service=memory_service,
            )
            # ... run a turn, then persist it:
            await memory_service.add_session_to_memory(session)

            response = await memory_service.search_memory(
                app_name="memory-demo", user_id="user-123", query="project deadline"
            )
            for entry in response.memories:
                print(entry.author, "".join(p.text or "" for p in entry.content.parts))

    Attributes:
        user_id: Optional user identifier for personalization.
        include_entities: Whether to search entities.
        include_preferences: Whether to search preferences.
    """

    def __init__(
        self,
        memory_client: MemoryClient,
        *,
        user_id: str | None = None,
        include_entities: bool = True,
        include_preferences: bool = True,
        extract_on_store: bool = True,
    ):
        """Initialize the Neo4j memory service.

        Args:
            memory_client: Connected MemoryClient instance.
            user_id: Optional user identifier for personalization.
            include_entities: Whether to include entities in search.
            include_preferences: Whether to include preferences in search.
            extract_on_store: Whether to extract entities when storing sessions.
        """
        super().__init__()
        self._client = memory_client
        self._user_id = user_id
        self._include_entities = include_entities
        self._include_preferences = include_preferences
        self._extract_on_store = extract_on_store
        # Last session written/added to. ADK's search_memory() carries no
        # session id, but NAMS message search is conversation-scoped — we
        # thread this through so searches stay scoped on NAMS (issue #130).
        self._current_session_id: str | None = None

    @property
    def user_id(self) -> str | None:
        """Get the user ID."""
        return self._user_id

    @property
    def memory_client(self) -> MemoryClient:
        """Get the underlying memory client."""
        return self._client

    async def add_session_to_memory(
        self,
        session: Any,
        *,
        session_id: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Store a session's messages and extract entities.

        ADK 2.x calls this with a single positional ``Session`` whose
        ``events`` carry the turn history. Dicts and plain message lists are
        also accepted so the adapter is usable without the ADK installed.

        Args:
            session: The ADK Session object or dict with messages.
            session_id: Override session ID (uses session.id if not provided).
            **kwargs: Additional arguments (for API compatibility).
        """
        # Extract session ID
        if session_id is None:
            if hasattr(session, "id"):
                session_id = str(session.id)
            elif isinstance(session, dict):
                session_id = session.get("id", "default")
            else:
                session_id = "default"

        messages = self._extract_messages(session)
        await self._store_messages(session_id, messages)

    async def add_events_to_memory(
        self,
        *,
        app_name: str | None = None,
        user_id: str | None = None,
        events: Sequence[Any] = (),
        session_id: str | None = None,
        custom_metadata: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        """Store an incremental slice of ADK events (ADK 2.x).

        ``Context.add_events_to_memory(events=...)`` routes here when an agent
        wants to persist only the latest turn instead of re-ingesting the whole
        session. Events are treated as a delta: each text-bearing event becomes
        one message on the resolved session.

        Args:
            app_name: ADK application name (accepted for contract parity).
            user_id: ADK user id (accepted for contract parity).
            events: The events to ingest.
            session_id: Session scope; falls back to the last session written.
            custom_metadata: Portable metadata merged into each message's
                ``metadata`` map.
            **kwargs: Additional arguments (for API compatibility).
        """
        scope = session_id or self._current_session_id or "default"
        messages = self._extract_messages_from_events(events)
        if custom_metadata:
            extra = dict(custom_metadata)
            for msg in messages:
                msg.metadata = {**(msg.metadata or {}), **extra}
        await self._store_messages(scope, messages)

    async def search_memory(
        self,
        query: str,
        *,
        app_name: str | None = None,
        user_id: str | None = None,
        limit: int = 10,
        threshold: float = 0.7,
        **kwargs: Any,
    ) -> SearchMemoryResponse:
        """Search across all memory types.

        Performs hybrid vector + graph search across messages, entities,
        and preferences to find relevant memories.

        ADK 2.x always calls this with keyword arguments
        (``app_name=``, ``user_id=``, ``query=``); ``query`` stays
        positional-or-keyword so 0.5.0 callers keep working.

        Args:
            query: The search query.
            app_name: ADK application name (accepted for contract parity).
            user_id: ADK user id (accepted for contract parity; tenant scoping
                is not wired yet — see the multi-tenancy how-to).
            limit: Maximum number of results.
            threshold: Minimum similarity threshold.
            **kwargs: Additional arguments — ``session_id`` scopes the message
                search explicitly.

        Returns:
            A ``SearchMemoryResponse`` whose ``memories`` are ADK
            ``MemoryEntry`` objects (``content`` is a ``types.Content``).
        """
        results: list[MemoryEntry] = []

        # Resolve a session to scope message search. NAMS requires it; bolt
        # treats it as an optional filter. Caller-supplied kwarg wins over the
        # tracked session (issue #130, defect 1).
        session_id = kwargs.get("session_id") or self._current_session_id

        try:
            # Search messages — conversation-scoped on NAMS.
            if session_id is not None:
                messages = await self._client.short_term.search_messages(
                    query=query,
                    session_id=session_id,
                    limit=limit,
                    threshold=threshold,
                )
                for msg in messages:
                    results.append(message_to_memory_entry(msg))
            elif self._client.backend == "nams":
                # No session context and NAMS can't do unscoped message search.
                # Skip messages and rely on entity/preference recall (which are
                # workspace-scoped and genuinely cross-session).
                logger.debug(
                    "search_memory: no session id on NAMS — skipping message "
                    "search; entities/preferences still searched."
                )
            else:
                # Bolt: unscoped message search is supported.
                messages = await self._client.short_term.search_messages(
                    query=query,
                    limit=limit,
                    threshold=threshold,
                )
                for msg in messages:
                    results.append(message_to_memory_entry(msg))

            # Search entities if enabled
            if self._include_entities:
                entities = await self._client.long_term.search_entities(
                    query=query,
                    limit=limit,
                )
                for entity in entities:
                    results.append(entity_to_memory_entry(entity))

            # Search preferences if enabled
            if self._include_preferences:
                prefs = await self._client.long_term.search_preferences(
                    query=query,
                    limit=limit,
                )
                for pref in prefs:
                    results.append(preference_to_memory_entry(pref))

        except Exception as e:
            logger.error(f"Error searching memories: {e}")

        # Sort by score (descending) and limit
        results.sort(key=lambda x: x.score or 0, reverse=True)

        return SearchMemoryResponse(
            memories=[to_adk_memory_entry(entry) for entry in results[:limit]]
        )

    async def get_memories_for_session(
        self,
        session_id: str,
        *,
        limit: int = 50,
        **kwargs: Any,
    ) -> list[MemoryEntry]:
        """Get memories relevant to a session.

        Retrieves conversation history for a session. This is a library
        convenience (not part of the ADK contract), so it returns the library's
        own :class:`MemoryEntry` dataclass rather than ADK entries.

        Args:
            session_id: The session ID to get memories for.
            limit: Maximum number of messages.
            **kwargs: Additional arguments (for API compatibility).

        Returns:
            List of MemoryEntry objects for the session.
        """
        results: list[MemoryEntry] = []

        try:
            # Get conversation history
            conversation = await self._client.short_term.get_conversation(
                session_id=session_id,
                limit=limit,
            )

            for msg in conversation.messages:
                results.append(message_to_memory_entry(msg))

        except Exception as e:
            logger.error(f"Error getting session memories: {e}")

        return results

    async def add_memory(
        self,
        content: str | None = None,
        *,
        memories: Sequence[Any] | None = None,
        app_name: str | None = None,
        user_id: str | None = None,
        custom_metadata: Mapping[str, Any] | None = None,
        memory_type: str = "message",
        session_id: str = "default",
        role: str = "user",
        category: str | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> MemoryEntry | None:
        """Write memory directly, in either the ADK 2.x or the convenience form.

        ADK 2.x form — ``add_memory(app_name=..., user_id=..., memories=[...])``
        with ADK ``MemoryEntry`` items (what ``Context.add_memory()`` sends).
        Each entry's text parts are stored as one message, authored by
        ``entry.author``, and ``None`` is returned.

        Convenience form (kept from 0.5.0) — ``add_memory(content="...",
        memory_type="message" | "preference", ...)``, which returns the created
        library :class:`MemoryEntry`.

        Args:
            content: The memory content (convenience form).
            memories: ADK ``MemoryEntry`` items (ADK 2.x form).
            app_name: ADK application name (accepted for contract parity).
            user_id: ADK user id (accepted for contract parity).
            custom_metadata: Portable metadata merged into each message's
                ``metadata`` map (ADK 2.x form).
            memory_type: Type of memory ("message", "preference").
            session_id: Session ID for messages.
            role: Message role (for message type).
            category: Preference category (for preference type).
            metadata: Optional metadata.
            **kwargs: Additional arguments.

        Returns:
            The created MemoryEntry for the convenience form, otherwise None.
        """
        if memories is not None:
            # The ADK form carries no session id, so fall back to the session
            # last written to; an explicit session_id= still wins.
            scope = (
                session_id if session_id != "default" else (self._current_session_id or session_id)
            )
            extra = dict(custom_metadata) if custom_metadata else {}
            session_messages: list[SessionMessage] = []
            for entry in memories:
                text = self._extract_text_from_content(getattr(entry, "content", None))
                if not text:
                    continue
                author = str(getattr(entry, "author", None) or role)
                entry_metadata = dict(extra)
                if author != _message_role(author):
                    entry_metadata["adk_author"] = author
                session_messages.append(
                    SessionMessage(
                        role=_message_role(author),
                        content=text,
                        metadata=entry_metadata or None,
                    )
                )
            await self._store_messages(scope, session_messages)
            return None

        if content is None:
            logger.warning("add_memory: no content and no memories provided")
            return None

        try:
            if memory_type == "message":
                self._current_session_id = session_id
                msg = await self._client.short_term.add_message(
                    session_id=session_id,
                    role=role,
                    content=content,
                    metadata=metadata,
                    extract_entities=self._extract_on_store,
                    generate_embedding=True,
                )
                return message_to_memory_entry(msg)

            elif memory_type == "preference":
                if not category:
                    category = "general"
                pref = await self._client.long_term.add_preference(
                    category=category,
                    preference=content,
                    generate_embedding=True,
                )
                return preference_to_memory_entry(pref)

            else:
                logger.warning(f"Unknown memory type: {memory_type}")
                return None

        except Exception as e:
            logger.error(f"Error adding memory: {e}")
            return None

    async def clear_session(self, session_id: str) -> None:
        """Clear all memories for a session.

        Args:
            session_id: The session ID to clear.
        """
        try:
            await self._client.short_term.clear_session(session_id)
            logger.debug(f"Cleared session {session_id}")
        except Exception as e:
            logger.error(f"Error clearing session: {e}")

    async def _store_messages(self, session_id: str, messages: list[SessionMessage]) -> None:
        """Write extracted session messages to short-term memory.

        Remembers the session so ``search_memory()`` can scope to it (NAMS
        message search is conversation-scoped — issue #130).
        """
        self._current_session_id = session_id

        if not messages:
            logger.debug(f"No messages to store for session {session_id}")
            return

        failures = 0
        for msg in messages:
            try:
                await self._client.short_term.add_message(
                    session_id=session_id,
                    role=msg.role,
                    content=msg.content,
                    metadata=msg.metadata,
                    extract_entities=self._extract_on_store,
                    generate_embedding=True,
                )
            except Exception as e:
                failures += 1
                logger.error(f"Error storing message for session {session_id}: {e}")

        stored = len(messages) - failures
        if failures:
            logger.error(
                f"Stored {stored}/{len(messages)} messages for session "
                f"{session_id} ({failures} failed)"
            )
        else:
            logger.debug(f"Stored {stored} messages for session {session_id}")

    @staticmethod
    def _extract_text_from_content(content: Any) -> str:
        """Extract plain text from ADK Content/Parts objects or strings.

        ADK uses ``google.genai.types.Content`` objects with a ``.parts`` list,
        where each Part may carry ``.text``, ``.function_call``,
        ``.function_response``, etc. We ingest **only** the text parts. Tool
        events (function calls / responses) carry no ``.text``, so they yield an
        empty string and the caller skips them — they must never be
        ``str()``-ified into the entity pipeline, where the JSON noise breaks
        server-side extraction and yields empty knowledge graphs (issue #130,
        defect 2).

        Args:
            content: A string, Content object, or other content type.

        Returns:
            The joined text parts, or ``""`` when there is no text to ingest.
        """
        if isinstance(content, str):
            return content
        parts = getattr(content, "parts", None) or []
        texts = [part.text for part in parts if getattr(part, "text", None)]
        return "\n".join(texts)

    def _extract_messages_from_events(self, events: Sequence[Any]) -> list[SessionMessage]:
        """Convert ADK ``Event`` objects into :class:`SessionMessage` values.

        ADK events carry ``.content`` (a ``types.Content``) and ``.author``
        instead of a role. Events with no text part (tool calls / responses)
        are skipped — see :meth:`_extract_text_from_content`.
        """
        messages: list[SessionMessage] = []
        for event in events:
            content = getattr(event, "content", None)
            if content is None:
                continue
            text = self._extract_text_from_content(content)
            if not text:
                continue
            author = getattr(event, "author", None)
            metadata = dict(getattr(event, "metadata", None) or {})
            if author and author != _message_role(author):
                # Keep the real ADK author (usually the agent name) alongside
                # the coarse MessageRole the library stores.
                metadata["adk_author"] = str(author)
            messages.append(
                SessionMessage(
                    role=_message_role(author),
                    content=text,
                    timestamp=getattr(event, "timestamp", None),
                    metadata=metadata or None,
                )
            )
        return messages

    def _extract_messages(self, session: Any) -> list[SessionMessage]:
        """Extract messages from various session formats.

        Supports:
        - ADK Session objects (``session.events`` with ``Event.content``/``.author``)
        - ADK Session objects with ``session.messages``
        - Plain dicts with a "messages" key
        - Lists of message dicts

        Args:
            session: Session object or dict.

        Returns:
            List of SessionMessage objects.
        """
        messages: list[SessionMessage] = []

        # Handle ADK Session with events (Event has .content and .author)
        if hasattr(session, "events"):
            messages.extend(self._extract_messages_from_events(session.events))

        # Handle ADK Session objects with messages attribute
        elif hasattr(session, "messages"):
            for msg in session.messages:
                if hasattr(msg, "role") and hasattr(msg, "content"):
                    text = self._extract_text_from_content(msg.content)
                    if not text:
                        # Tool-only event (function call/response) — skip so its
                        # JSON noise never reaches the entity pipeline (#130).
                        continue
                    role_val = msg.role
                    role = str(role_val.value) if hasattr(role_val, "value") else str(role_val)
                    messages.append(
                        SessionMessage(
                            role=role,
                            content=text,
                            timestamp=getattr(msg, "timestamp", None),
                            metadata=getattr(msg, "metadata", None),
                        )
                    )
                elif isinstance(msg, dict):
                    messages.append(session_message_from_dict(msg))

        # Handle dict with messages
        elif isinstance(session, dict) and "messages" in session:
            for msg in session["messages"]:
                if isinstance(msg, dict):
                    messages.append(session_message_from_dict(msg))

        # Handle list of messages directly
        elif isinstance(session, list):
            for msg in session:
                if isinstance(msg, dict):
                    messages.append(session_message_from_dict(msg))
                elif hasattr(msg, "role") and hasattr(msg, "content"):
                    text = self._extract_text_from_content(msg.content)
                    messages.append(
                        SessionMessage(
                            role=str(msg.role),
                            content=text,
                        )
                    )

        return messages
