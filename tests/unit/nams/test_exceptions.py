"""Phase 1 unit tests: new exception classes for the NAMS backend.

Covers the HTTP/backend-specific exceptions:

* ``TransportError`` (subclass of ``ConnectionError``)
* ``AuthenticationError``
* ``NotFoundError``
* ``NotSupportedError`` — structured (backend, method, workaround)
* ``RateLimitError`` — carries ``retry_after``
* ``ValidationError`` — carries ``details``
"""

from __future__ import annotations

import pytest

from neo4j_agent_memory import (
    AuthenticationError,
    ConnectionError,
    MemoryError,
    NotFoundError,
    NotSupportedError,
    RateLimitError,
    TransportError,
    ValidationError,
)


class TestExceptionHierarchy:
    """All new exceptions extend MemoryError; TransportError extends ConnectionError."""

    def test_transport_error_is_connection_error(self):
        # Existing `except ConnectionError` blocks must still catch transport failures.
        assert issubclass(TransportError, ConnectionError)
        assert issubclass(TransportError, MemoryError)

    def test_authentication_error_is_memory_error(self):
        assert issubclass(AuthenticationError, MemoryError)

    def test_not_found_error_is_memory_error(self):
        assert issubclass(NotFoundError, MemoryError)

    def test_not_found_error_is_exported_from_core(self):
        from neo4j_agent_memory.core import NotFoundError as CoreNotFoundError

        assert CoreNotFoundError is NotFoundError

    def test_not_supported_error_is_memory_error(self):
        assert issubclass(NotSupportedError, MemoryError)

    def test_rate_limit_error_is_memory_error(self):
        assert issubclass(RateLimitError, MemoryError)

    def test_validation_error_is_memory_error(self):
        assert issubclass(ValidationError, MemoryError)


class TestTransportError:
    def test_raises_with_message(self):
        with pytest.raises(TransportError, match="connect timeout"):
            raise TransportError("connect timeout after 30s")

    def test_caught_by_connection_error(self):
        with pytest.raises(ConnectionError):
            raise TransportError("net down")


class TestAuthenticationError:
    def test_raises_with_message(self):
        with pytest.raises(AuthenticationError, match="invalid"):
            raise AuthenticationError("invalid API key")


class TestRateLimitError:
    def test_default_message(self):
        err = RateLimitError()
        assert "Rate limit" in str(err)
        assert err.retry_after is None

    def test_with_retry_after(self):
        err = RateLimitError("Rate limit exceeded", retry_after=12.5)
        assert err.retry_after == 12.5

    def test_raises(self):
        with pytest.raises(RateLimitError) as exc_info:
            raise RateLimitError(retry_after=5.0)
        assert exc_info.value.retry_after == 5.0


class TestValidationError:
    def test_with_details(self):
        err = ValidationError(
            "Invalid request", details={"field": "session_id", "reason": "missing"}
        )
        assert err.details == {"field": "session_id", "reason": "missing"}

    def test_details_default_empty(self):
        err = ValidationError("Bad request")
        assert err.details == {}

    def test_raises(self):
        with pytest.raises(ValidationError, match="bad"):
            raise ValidationError("bad", details={"foo": "bar"})


class TestNotSupportedError:
    def test_required_fields_only(self):
        err = NotSupportedError(backend="nams", method="client.users.upsert_user")
        assert err.backend == "nams"
        assert err.method == "client.users.upsert_user"
        assert err.workaround is None
        assert "client.users.upsert_user" in str(err)
        assert "'nams'" in str(err)

    def test_with_message_and_workaround(self):
        err = NotSupportedError(
            backend="nams",
            method="client.graph",
            message="Direct Neo4j driver access is bolt-only.",
            workaround="Use client.query.cypher() for portable read-only queries.",
        )
        msg = str(err)
        assert "bolt-only" in msg
        assert "Workaround:" in msg
        assert "client.query.cypher" in msg
        assert err.workaround is not None
        assert "cypher" in err.workaround

    def test_with_bolt_backend(self):
        """Bolt-side: Platinum-only methods raise NotSupportedError(backend='bolt')."""
        err = NotSupportedError(
            backend="bolt",
            method="LongTermMemory.set_entity_feedback",
            message="Entity feedback is a Platinum-tier feature.",
        )
        assert err.backend == "bolt"
        assert "'bolt'" in str(err)

    def test_keyword_only_args(self):
        """All fields must be keyword-only to avoid positional confusion."""
        with pytest.raises(TypeError):
            NotSupportedError("nams", "method")  # type: ignore[misc]

    def test_caught_by_memory_error(self):
        with pytest.raises(MemoryError):
            raise NotSupportedError(backend="nams", method="x")
