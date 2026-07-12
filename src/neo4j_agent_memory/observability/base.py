"""Base observability interfaces and utilities.

Provides abstract interfaces for tracing that can be implemented
by different providers (OpenTelemetry, Opik, etc.).
"""

from __future__ import annotations

import functools
import time
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, TypeVar

F = TypeVar("F", bound=Callable[..., Any])


class TracingProvider(str, Enum):
    """Available tracing providers."""

    OPENTELEMETRY = "opentelemetry"
    OPIK = "opik"
    NOOP = "noop"
    AUTO = "auto"


def is_opentelemetry_available() -> bool:
    """Check if OpenTelemetry is available."""
    try:
        import opentelemetry.trace  # noqa: F401

        return True
    except ImportError:
        return False


def is_opik_available() -> bool:
    """Check if Opik is available."""
    try:
        import opik  # noqa: F401

        return True
    except ImportError:
        return False


@dataclass
class SpanAttributes:
    """Attributes that can be set on a span."""

    name: str
    attributes: dict[str, Any] = field(default_factory=dict)
    start_time: float | None = None
    end_time: float | None = None
    status: str = "OK"
    error: str | None = None


class Span(ABC):
    """Abstract span interface."""

    @abstractmethod
    def set_attribute(self, key: str, value: Any) -> None:
        """Set an attribute on the span."""
        ...

    @abstractmethod
    def set_status(self, status: str, description: str | None = None) -> None:
        """Set the span status."""
        ...

    @abstractmethod
    def record_exception(self, exception: Exception) -> None:
        """Record an exception on the span."""
        ...

    @abstractmethod
    def end(self) -> None:
        """End the span."""
        ...


class NoOpSpan(Span):
    """No-op span implementation for when tracing is disabled."""

    def set_attribute(self, key: str, value: Any) -> None:
        """No-op."""
        pass

    def set_status(self, status: str, description: str | None = None) -> None:
        """No-op."""
        pass

    def record_exception(self, exception: Exception) -> None:
        """No-op."""
        pass

    def end(self) -> None:
        """No-op."""
        pass


class Tracer(ABC):
    """Abstract tracer interface.

    Provides methods for creating spans and tracing functions.
    """

    @abstractmethod
    def start_span(self, name: str, attributes: dict[str, Any] | None = None) -> Span:
        """Start a new span.

        Args:
            name: Name of the span
            attributes: Optional attributes to set on the span

        Returns:
            A Span object
        """
        ...

    @contextmanager
    def span(self, name: str, attributes: dict[str, Any] | None = None) -> Iterator[Span]:
        """Context manager for creating a span.

        Args:
            name: Name of the span
            attributes: Optional attributes to set on the span

        Yields:
            A Span object
        """
        span = self.start_span(name, attributes)
        try:
            yield span
        except Exception as e:
            span.record_exception(e)
            span.set_status("ERROR", str(e))
            raise
        finally:
            span.end()

    @asynccontextmanager
    async def async_span(
        self, name: str, attributes: dict[str, Any] | None = None
    ) -> AsyncIterator[Span]:
        """Async context manager for creating a span.

        Args:
            name: Name of the span
            attributes: Optional attributes to set on the span

        Yields:
            A Span object
        """
        span = self.start_span(name, attributes)
        try:
            yield span
        except Exception as e:
            span.record_exception(e)
            span.set_status("ERROR", str(e))
            raise
        finally:
            span.end()

    def trace(
        self,
        name: str | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> Callable[[F], F]:
        """Decorator for tracing a function.

        Args:
            name: Optional span name (defaults to function name)
            attributes: Optional attributes to set on the span

        Returns:
            Decorated function
        """

        def decorator(func: F) -> F:
            span_name = name or func.__name__

            if _is_async_callable(func):

                @functools.wraps(func)
                async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                    async with self.async_span(span_name, attributes) as span:
                        # Add function arguments as attributes
                        span.set_attribute("function.name", func.__name__)
                        span.set_attribute("function.module", func.__module__)
                        start = time.perf_counter()
                        try:
                            result = await func(*args, **kwargs)
                            span.set_attribute(
                                "function.duration_ms", (time.perf_counter() - start) * 1000
                            )
                            return result
                        except Exception:
                            span.set_attribute(
                                "function.duration_ms", (time.perf_counter() - start) * 1000
                            )
                            raise

                # functools.wraps copies func's metadata but the static type of
                # the wrapper is (...) -> Any, not the input F; the decorator
                # contract preserves F's signature at runtime.
                return async_wrapper  # type: ignore[return-value]
            else:

                @functools.wraps(func)
                def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                    with self.span(span_name, attributes) as span:
                        span.set_attribute("function.name", func.__name__)
                        span.set_attribute("function.module", func.__module__)
                        start = time.perf_counter()
                        try:
                            result = func(*args, **kwargs)
                            span.set_attribute(
                                "function.duration_ms", (time.perf_counter() - start) * 1000
                            )
                            return result
                        except Exception:
                            span.set_attribute(
                                "function.duration_ms", (time.perf_counter() - start) * 1000
                            )
                            raise

                # See async_wrapper above: functools.wraps preserves F at
                # runtime; the wrapper's static type is (...) -> Any.
                return sync_wrapper  # type: ignore[return-value]

        return decorator


class NoOpTracer(Tracer):
    """No-op tracer implementation for when tracing is disabled."""

    def start_span(self, name: str, attributes: dict[str, Any] | None = None) -> Span:
        """Return a no-op span."""
        return NoOpSpan()


def _is_async_callable(func: Callable[..., Any]) -> bool:
    """Check if a callable is async."""
    import asyncio
    import inspect

    if asyncio.iscoroutinefunction(func):
        return True
    if hasattr(func, "__call__"):
        return asyncio.iscoroutinefunction(func.__call__)
    return inspect.iscoroutinefunction(func)


# Global tracer instance
_global_tracer: Tracer | None = None


def get_tracer(
    provider: TracingProvider | str = TracingProvider.AUTO,
    *,
    service_name: str = "neo4j-agent-memory",
    **kwargs: Any,
) -> Tracer:
    """Get a tracer instance.

    Args:
        provider: Tracing provider to use. Options:
            - "auto": Auto-detect available provider (Opik > OpenTelemetry > NoOp)
            - "opentelemetry": Use OpenTelemetry
            - "opik": Use Opik
            - "noop": Use no-op tracer (disables tracing)
        service_name: Service name for the tracer
        **kwargs: Additional provider-specific configuration

    Returns:
        A Tracer instance
    """
    global _global_tracer

    if isinstance(provider, str):
        provider = TracingProvider(provider.lower())

    if provider == TracingProvider.AUTO:
        # Try Opik first, then OpenTelemetry, then NoOp
        if is_opik_available():
            provider = TracingProvider.OPIK
        elif is_opentelemetry_available():
            provider = TracingProvider.OPENTELEMETRY
        else:
            provider = TracingProvider.NOOP

    if provider == TracingProvider.OPIK:
        from neo4j_agent_memory.observability.opik import OpikTracer

        _global_tracer = OpikTracer(service_name=service_name, **kwargs)
    elif provider == TracingProvider.OPENTELEMETRY:
        from neo4j_agent_memory.observability.otel import OpenTelemetryTracer

        _global_tracer = OpenTelemetryTracer(service_name=service_name, **kwargs)
    else:
        _global_tracer = NoOpTracer()

    return _global_tracer


def get_current_tracer() -> Tracer:
    """Get the current global tracer instance.

    Returns:
        The current global tracer, or a NoOpTracer if none is configured
    """
    global _global_tracer
    if _global_tracer is None:
        return NoOpTracer()
    return _global_tracer
