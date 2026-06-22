"""Observational Memory - context compression and observation extraction.

The MemoryObserver monitors accumulated context per session and extracts
high-level observations (key facts, decisions, topic shifts) when the
token count exceeds a configurable threshold.

This implements the three-tier context hierarchy from the Observational
Memory PRD:
1. Reflections - high-level session summaries (generated when threshold exceeded)
2. Observations - extracted facts, decisions, preferences from messages
3. Recent messages - the most recent messages in the session

The Observer does NOT use MCP sampling. When summarization is needed, it
uses direct LLM API calls if a provider is configured, or falls back to
keyword/entity-based extraction.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from neo4j_agent_memory import MemoryClient
    from neo4j_agent_memory.llm.protocol import LLMProvider

logger = logging.getLogger(__name__)

# Approximate tokens per character (rough estimate for English text)
_CHARS_PER_TOKEN = 4


@dataclass
class Observation:
    """A single observation extracted from conversation context."""

    type: str
    """Observation type: 'fact', 'decision', 'preference', 'topic', 'entity'."""

    content: str
    """The observation text."""

    source_message_id: str | None = None
    """ID of the message this was extracted from."""

    timestamp: str | None = None
    """ISO timestamp of when the observation was created."""

    confidence: float = 0.8
    """Confidence score for this observation."""


@dataclass
class SessionContext:
    """Accumulated context state for a single session."""

    session_id: str
    total_chars: int = 0
    message_count: int = 0
    observations: list[Observation] = field(default_factory=list)
    reflections: list[str] = field(default_factory=list)
    last_compression_at: int = 0  # message_count when last compressed
    entity_names: set[str] = field(default_factory=set)
    topics: list[str] = field(default_factory=list)


class MemoryObserver:
    """Observes memory operations and extracts observations.

    Monitors accumulated context per session. When the token threshold is
    exceeded, triggers observation generation to compress context while
    preserving important information.

    Args:
        client: MemoryClient for querying conversation history.
        threshold_tokens: Token count at which to trigger compression.
        recent_message_window: Number of recent messages to always keep
            in full detail (not compressed).
    """

    def __init__(
        self,
        client: MemoryClient,
        *,
        threshold_tokens: int = 30000,
        recent_message_window: int = 20,
        llm_provider: LLMProvider | None = None,
    ):
        """Initialize the observer.

        Args:
            client: MemoryClient for querying conversation history.
            threshold_tokens: Token count at which to trigger compression.
            recent_message_window: Number of recent messages to always keep
                in full detail (not compressed).
            llm_provider: Optional :class:`LLMProvider` used to summarize
                older messages when generating reflections. When ``None``,
                the observer falls back to keyword/entity extraction (the
                v0.2 behavior). Wiring this argument is what enables
                Anthropic/Bedrock/Ollama-powered reflection summaries.
        """
        self._client = client
        self._threshold_tokens = threshold_tokens
        self._recent_window = recent_message_window
        self._sessions: dict[str, SessionContext] = {}
        self._llm_provider = llm_provider

    def _get_session(self, session_id: str) -> SessionContext:
        """Get or create session context tracker."""
        if session_id not in self._sessions:
            self._sessions[session_id] = SessionContext(session_id=session_id)
        return self._sessions[session_id]

    async def on_message_stored(
        self,
        session_id: str,
        content: str,
        message_id: str | None = None,
        role: str = "user",
    ) -> None:
        """Hook called after a message is stored.

        Updates accumulated token count and triggers compression if
        the threshold is exceeded.

        Args:
            session_id: Session the message belongs to.
            content: Message text content.
            message_id: Optional message ID for provenance.
            role: Message role ('user', 'assistant', 'system').
        """
        ctx = self._get_session(session_id)
        ctx.total_chars += len(content)
        ctx.message_count += 1

        # Extract inline observations from user messages
        if role == "user":
            observations = self._extract_inline_observations(content, message_id)
            ctx.observations.extend(observations)

        # Check if we need to compress
        approx_tokens = ctx.total_chars // _CHARS_PER_TOKEN
        if approx_tokens > self._threshold_tokens:
            messages_since_compression = ctx.message_count - ctx.last_compression_at
            if messages_since_compression >= self._recent_window:
                await self._generate_reflection(session_id)

    async def _generate_reflection(self, session_id: str) -> None:
        """Generate a high-level reflection for the session.

        This compresses older messages into a summary reflection. When an
        :class:`LLMProvider` is configured, uses a single ``complete`` call
        to produce a one-paragraph summary. Otherwise falls back to
        keyword/entity extraction (the v0.2 behavior).
        """
        ctx = self._get_session(session_id)

        try:
            # Get conversation messages
            conversation = await self._client.short_term.get_conversation(
                session_id=session_id,
                limit=100,
            )

            if not conversation.messages:
                return

            # Extract topics and entities from older messages
            # (beyond the recent window)
            older_messages = conversation.messages[: -self._recent_window]
            if not older_messages:
                return

            reflection: str | None
            if self._llm_provider is not None:
                reflection = await self._llm_summarize(older_messages, ctx)
            else:
                reflection = self._keyword_reflection(older_messages, ctx)

            if reflection:
                ctx.reflections.append(reflection)

            ctx.last_compression_at = ctx.message_count
            logger.debug(
                f"Generated reflection for session {session_id}: "
                f"{len(ctx.reflections)} reflections, "
                f"{len(ctx.observations)} observations"
            )

        except Exception as e:
            logger.warning(f"Error generating reflection for session {session_id}: {e}")

    async def _llm_summarize(
        self,
        older_messages: list[Any],
        ctx: SessionContext,
    ) -> str | None:
        """Use the configured :class:`LLMProvider` to summarize older messages.

        On any provider error we silently fall through to the keyword path
        — a reflection failure must never break the message-store hot path.
        """
        if self._llm_provider is None:  # pragma: no cover - guarded by caller
            return None
        from neo4j_agent_memory.llm.types import ChatMessage

        transcript_parts: list[str] = []
        for msg in older_messages[-60:]:  # cap transcript size
            role = getattr(msg, "role", None)
            if role is None:
                role_name: str = "user"
            elif hasattr(role, "value"):
                role_name = str(role.value)
            else:
                # Match the original `role or "user"`: a falsy role (e.g. "") -> "user".
                role_name = str(role) if role else "user"
            transcript_parts.append(f"{role_name}: {msg.content}")
        transcript = "\n".join(transcript_parts)

        system = ChatMessage(
            role="system",
            content=(
                "You are a session summarizer. Produce a single concise "
                "paragraph capturing decisions, facts, and unresolved "
                "questions. No bullet lists, no headers, under 120 words."
            ),
        )
        user = ChatMessage(
            role="user",
            content=(
                f"Summarize this conversation so future turns have the "
                f"essential context. There are {ctx.message_count} total "
                f"messages; the following is the older portion:\n\n{transcript}"
            ),
        )
        try:
            completion = await self._llm_provider.complete(
                [system, user],
                temperature=0.0,
                max_tokens=400,
            )
        except Exception as exc:  # pragma: no cover - provider errors logged
            logger.warning("LLM summarization failed, falling back to keywords: %s", exc)
            return self._keyword_reflection(older_messages, ctx)
        summary = completion.content.strip()
        if not summary:
            return self._keyword_reflection(older_messages, ctx)
        return f"Session summary ({ctx.message_count} messages): {summary}"

    def _keyword_reflection(
        self,
        older_messages: list[Any],
        ctx: SessionContext,
    ) -> str | None:
        """Fallback summarizer that uses capitalized phrases as entity proxies.

        Identical to the v0.2 behavior; lifted into its own method so the
        LLM path can fall back to it on error.
        """
        entities: set[str] = set()
        for msg in older_messages:
            words = msg.content.split()
            for i, word in enumerate(words):
                if word and word[0].isupper() and len(word) > 2:
                    entity_parts = [word]
                    for j in range(i + 1, min(i + 4, len(words))):
                        if words[j] and words[j][0].isupper():
                            entity_parts.append(words[j])
                        else:
                            break
                    if len(entity_parts) > 1:
                        entities.add(" ".join(entity_parts))

        reflection_parts: list[str] = []
        if ctx.observations:
            obs_summary = "; ".join(o.content for o in ctx.observations[-10:])
            reflection_parts.append(f"Key observations: {obs_summary}")
        if entities:
            top_entities = sorted(entities)[:10]
            reflection_parts.append(f"Entities discussed: {', '.join(top_entities)}")
        if not reflection_parts:
            return None
        return f"Session summary ({ctx.message_count} messages): " + ". ".join(reflection_parts)

    def _extract_inline_observations(
        self,
        content: str,
        message_id: str | None = None,
    ) -> list[Observation]:
        """Extract observations from a single message.

        Identifies statements of fact, decisions, and notable claims.
        This is a lightweight extraction -- not intended to replace
        full entity extraction.
        """
        observations: list[Observation] = []
        now = datetime.now(tz=timezone.utc).isoformat()

        # Look for decision/action statements
        decision_markers = [
            "I decided",
            "I've decided",
            "let's go with",
            "I'll go with",
            "I chose",
            "I've chosen",
            "we should",
            "I want to",
            "I'm going to",
            "I plan to",
        ]
        for marker in decision_markers:
            if marker.lower() in content.lower():
                # Extract the sentence containing the marker
                sentence = _extract_sentence_containing(content, marker)
                if sentence:
                    observations.append(
                        Observation(
                            type="decision",
                            content=sentence,
                            source_message_id=message_id,
                            timestamp=now,
                            confidence=0.75,
                        )
                    )
                break  # One decision per message

        # Look for factual statements with "is/are" patterns
        fact_patterns = [
            "the answer is",
            "it turns out",
            "actually,",
            "I found out",
            "I learned that",
            "it seems like",
            "the reason is",
        ]
        for marker in fact_patterns:
            if marker.lower() in content.lower():
                sentence = _extract_sentence_containing(content, marker)
                if sentence:
                    observations.append(
                        Observation(
                            type="fact",
                            content=sentence,
                            source_message_id=message_id,
                            timestamp=now,
                            confidence=0.70,
                        )
                    )
                break

        return observations

    async def get_observations(self, session_id: str) -> dict[str, Any]:
        """Get accumulated observations for a session.

        Returns the three-tier context hierarchy:
        - reflections: high-level session summaries
        - observations: extracted facts, decisions, preferences
        - session_stats: message count, approximate token usage

        Args:
            session_id: Session to get observations for.

        Returns:
            Dict with observations, reflections, and stats.
        """
        ctx = self._get_session(session_id)

        return {
            "session_id": session_id,
            "message_count": ctx.message_count,
            "approximate_tokens": ctx.total_chars // _CHARS_PER_TOKEN,
            "threshold_tokens": self._threshold_tokens,
            "threshold_exceeded": (ctx.total_chars // _CHARS_PER_TOKEN) > self._threshold_tokens,
            "reflections": ctx.reflections,
            "observations": [
                {
                    "type": o.type,
                    "content": o.content,
                    "confidence": o.confidence,
                    "timestamp": o.timestamp,
                }
                for o in ctx.observations
            ],
            "entity_names": sorted(ctx.entity_names),
            "topics": ctx.topics,
        }

    def reset_session(self, session_id: str) -> None:
        """Clear accumulated state for a session."""
        self._sessions.pop(session_id, None)


def _extract_sentence_containing(text: str, marker: str) -> str | None:
    """Extract the sentence containing a marker phrase."""
    lower = text.lower()
    idx = lower.find(marker.lower())
    if idx == -1:
        return None

    # Find sentence boundaries
    # Look backward for sentence start
    start = max(0, idx)
    for i in range(idx - 1, -1, -1):
        if text[i] in ".!?\n":
            start = i + 1
            break
    else:
        start = 0

    # Look forward for sentence end
    end = len(text)
    for i in range(idx + len(marker), len(text)):
        if text[i] in ".!?\n":
            end = i + 1
            break

    sentence = text[start:end].strip()
    # Cap at reasonable length
    if len(sentence) > 300:
        sentence = sentence[:300].rsplit(" ", 1)[0] + "..."
    return sentence if len(sentence) > 10 else None
