"""Opik tracing provider.

Provides Opik-based tracing for LLM and extraction pipeline observability.

Opik (https://github.com/comet-ml/opik) is an LLM-focused observability platform
by Comet that provides:
- Nested function call tracing
- Feedback scores and evaluation metrics
- Hallucination detection
- Token consumption tracking
- Dashboard for trace visualization

Usage:
    from neo4j_agent_memory.observability import get_tracer

    tracer = get_tracer(provider="opik", project_name="my-project")

    @tracer.trace("extract_entities")
    async def extract(text: str):
        ...
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from neo4j_agent_memory.observability.base import Span, Tracer


@dataclass
class OpikSpanData:
    """Data collected during span execution."""

    name: str
    attributes: dict[str, Any] = field(default_factory=dict)
    start_time: float = field(default_factory=time.time)
    end_time: float | None = None
    status: str = "OK"
    error: str | None = None
    trace_id: str | None = None


class OpikSpan(Span):
    """Opik span wrapper.

    Wraps Opik's @track decorator functionality in a span interface.
    """

    def __init__(
        self,
        opik_client: Any,
        name: str,
        trace_id: str | None = None,
        parent_span_id: str | None = None,
    ) -> None:
        """Initialize the Opik span.

        Args:
            opik_client: The Opik client instance
            name: Name of the span
            trace_id: Optional trace ID for correlation
            parent_span_id: Optional parent span ID for nesting
        """
        self._client = opik_client
        self._name = name
        self._trace_id = trace_id
        self._parent_span_id = parent_span_id
        self._start_time = time.time()
        self._attributes: dict[str, Any] = {}
        self._status = "OK"
        self._error: str | None = None
        self._span: Any = None

        # Create the span using Opik's API
        try:
            if hasattr(opik_client, "trace"):
                # Using Opik's trace context
                self._span = opik_client.trace(name=name)
            elif hasattr(opik_client, "start_span"):
                self._span = opik_client.start_span(name=name)
        except Exception:
            # If Opik API fails, continue without tracing
            pass

    def set_attribute(self, key: str, value: Any) -> None:
        """Set an attribute on the span."""
        self._attributes[key] = value
        if self._span and hasattr(self._span, "set_metadata"):
            try:
                self._span.set_metadata({key: value})
            except Exception:
                pass

    def set_status(self, status: str, description: str | None = None) -> None:
        """Set the span status."""
        self._status = status
        self._error = description if status == "ERROR" else None
        if self._span and hasattr(self._span, "set_status"):
            try:
                self._span.set_status(status)
            except Exception:
                pass

    def record_exception(self, exception: Exception) -> None:
        """Record an exception on the span."""
        self._status = "ERROR"
        self._error = str(exception)
        if self._span and hasattr(self._span, "record_exception"):
            try:
                self._span.record_exception(exception)
            except Exception:
                pass

    def end(self) -> None:
        """End the span."""
        self._end_time = time.time()
        duration_ms = (self._end_time - self._start_time) * 1000
        self._attributes["duration_ms"] = duration_ms

        if self._span:
            try:
                if hasattr(self._span, "end"):
                    self._span.end(
                        metadata=self._attributes,
                        output={"status": self._status, "error": self._error},
                    )
                elif hasattr(self._span, "__exit__"):
                    self._span.__exit__(None, None, None)
            except Exception:
                pass


class OpikTracer(Tracer):
    """Opik-based tracer implementation.

    Provides LLM-focused observability via Opik SDK. Features:
    - Nested trace tracking for extraction pipelines
    - Token usage monitoring
    - Feedback score integration
    - Dashboard visualization

    Args:
        service_name: Name of the service for tracing
        project_name: Opik project name (creates if doesn't exist)
        api_key: Optional Opik API key (uses env var if not provided)
        workspace: Optional Opik workspace name
    """

    def __init__(
        self,
        service_name: str = "neo4j-agent-memory",
        project_name: str | None = None,
        api_key: str | None = None,
        workspace: str | None = None,
    ) -> None:
        """Initialize the Opik tracer.

        Args:
            service_name: Name of the service
            project_name: Opik project name (defaults to service_name)
            api_key: Opik API key (or set OPIK_API_KEY env var)
            workspace: Opik workspace (or set OPIK_WORKSPACE env var)
        """
        import opik

        self._service_name = service_name
        self._project_name = project_name or service_name

        # Configure Opik
        config_kwargs: dict[str, Any] = {}
        if api_key:
            config_kwargs["api_key"] = api_key
        if workspace:
            config_kwargs["workspace"] = workspace

        if config_kwargs:
            opik.configure(**config_kwargs)

        # Get client
        self._client = opik.Opik(project_name=self._project_name)
        self._opik = opik

    def start_span(self, name: str, attributes: dict[str, Any] | None = None) -> Span:
        """Start a new span.

        Args:
            name: Name of the span
            attributes: Optional attributes to set on the span

        Returns:
            An OpikSpan wrapping the Opik trace
        """
        span = OpikSpan(self._client, name)

        if attributes:
            for key, value in attributes.items():
                span.set_attribute(key, value)

        return span

    def track(
        self,
        name: str | None = None,
        capture_input: bool = True,
        capture_output: bool = True,
    ) -> Any:
        """Get Opik's native @track decorator.

        This provides direct access to Opik's @track decorator for
        more advanced use cases like capturing LLM inputs/outputs.

        Args:
            name: Optional name for the trace
            capture_input: Whether to capture function inputs
            capture_output: Whether to capture function outputs

        Returns:
            Opik's @track decorator
        """
        return self._opik.track(
            name=name,
            capture_input=capture_input,
            capture_output=capture_output,
            project_name=self._project_name,
        )

    @property
    def service_name(self) -> str:
        """Get the service name."""
        return self._service_name

    @property
    def project_name(self) -> str:
        """Get the Opik project name."""
        return self._project_name

    def log_feedback(
        self,
        trace_id: str,
        name: str,
        value: float,
        reason: str | None = None,
    ) -> None:
        """Log feedback for a trace.

        Args:
            trace_id: ID of the trace to add feedback to
            name: Feedback metric name (e.g., "accuracy", "relevance")
            value: Feedback score (typically 0-1)
            reason: Optional explanation for the score
        """
        try:
            # log_feedback is present in some Opik releases but absent from the
            # published stubs.  Use getattr so both mypy and ty see a safe dynamic
            # dispatch rather than a missing-attribute error.
            log_fb = getattr(self._client, "log_feedback", None)
            if callable(log_fb):
                log_fb(
                    trace_id=trace_id,
                    name=name,
                    value=value,
                    reason=reason,
                )
        except Exception:
            pass  # Don't fail if feedback logging fails
