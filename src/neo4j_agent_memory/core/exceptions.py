"""Custom exceptions for neo4j-agent-memory."""

from __future__ import annotations


class MemoryError(Exception):
    """Base exception for all memory-related errors."""

    pass


class ConnectionError(MemoryError):
    """Raised when there's a problem connecting to Neo4j."""

    pass


class SchemaError(MemoryError):
    """Raised when there's a problem with the database schema."""

    pass


class ExtractionError(MemoryError):
    """Raised when entity extraction fails."""

    pass


class ResolutionError(MemoryError):
    """Raised when entity resolution fails."""

    pass


class EmbeddingError(MemoryError):
    """Raised when embedding generation fails."""

    pass


class ConfigurationError(MemoryError):
    """Raised when there's a configuration problem."""

    pass


class NotConnectedError(MemoryError):
    """Raised when attempting operations without an active connection."""

    pass


class TransportError(ConnectionError):
    """Raised when an HTTP transport call fails (NAMS backend).

    Covers network errors, connect/read timeouts, and 5xx responses that
    survived the retry policy. Subclass of :class:`ConnectionError` so
    existing ``except ConnectionError`` blocks still catch transport
    failures.
    """

    pass


class AuthenticationError(MemoryError):
    """Raised when authentication with the NAMS backend fails.

    Triggered by 401/403 responses (invalid or missing API key, forbidden
    workspace).
    """

    pass


class NotFoundError(MemoryError):
    """Raised when the NAMS backend cannot find a resource (HTTP 404)."""

    pass


class RateLimitError(MemoryError):
    """Raised when the NAMS backend rate-limits the client.

    Triggered by 429 responses that survive the retry policy. The
    ``retry_after`` attribute (seconds) carries the server-provided
    ``Retry-After`` header value when available.
    """

    def __init__(self, message: str = "Rate limit exceeded", *, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class ValidationError(MemoryError):
    """Raised when the NAMS backend rejects a request as invalid (HTTP 400).

    The ``details`` attribute carries the server's structured error body
    (when present) for programmatic inspection.
    """

    def __init__(self, message: str, *, details: dict[str, object] | None = None):
        super().__init__(message)
        self.details: dict[str, object] = details or {}


class NotSupportedError(MemoryError):
    """Raised when a method is not supported on the active backend.

    Use cases:

    * Bolt-only features called on a NAMS-backed client
      (e.g. ``client.consolidation.dedupe_entities()``,
      ``client.graph``, ``client.schema.adopt_existing_graph()``).
    * Platinum-tier methods called on a bolt-backed client
      (e.g. ``client.long_term.set_entity_feedback(...)``).
    * Server-declared unsupported operations (HTTP 405/501 from NAMS).

    The structured fields (``backend``, ``method``, ``workaround``) allow
    callers to introspect and surface remediation hints.
    """

    def __init__(
        self,
        *,
        backend: str,
        method: str,
        message: str | None = None,
        workaround: str | None = None,
    ) -> None:
        self.backend = backend
        self.method = method
        self.workaround = workaround
        parts = [f"{method} is not supported on the {backend!r} backend."]
        if message:
            parts.append(message)
        if workaround:
            parts.append(f"Workaround: {workaround}")
        super().__init__(" ".join(parts))
