"""OpenTelemetry tracing provider.

Provides OpenTelemetry-based tracing for the extraction pipeline.

Usage:
    from neo4j_agent_memory.observability import get_tracer

    tracer = get_tracer(provider="opentelemetry", service_name="my-service")

    @tracer.trace("extract_entities")
    async def extract(text: str):
        ...
"""

from __future__ import annotations

from typing import Any

from neo4j_agent_memory.observability.base import Span, Tracer


class OpenTelemetrySpan(Span):
    """OpenTelemetry span wrapper."""

    def __init__(self, otel_span: Any) -> None:
        """Initialize with an OpenTelemetry span.

        Args:
            otel_span: The underlying OpenTelemetry span
        """
        self._span = otel_span

    def set_attribute(self, key: str, value: Any) -> None:
        """Set an attribute on the span."""
        # OpenTelemetry only supports certain value types
        if isinstance(value, (str, int, float, bool)):
            self._span.set_attribute(key, value)
        elif isinstance(value, (list, tuple)):
            # Convert list elements to supported types
            converted = [str(v) if not isinstance(v, (str, int, float, bool)) else v for v in value]
            self._span.set_attribute(key, converted)
        else:
            # Convert unsupported types to string
            self._span.set_attribute(key, str(value))

    def set_status(self, status: str, description: str | None = None) -> None:
        """Set the span status."""
        from opentelemetry.trace import Status, StatusCode

        if status.upper() == "OK":
            self._span.set_status(Status(StatusCode.OK, description))
        elif status.upper() == "ERROR":
            self._span.set_status(Status(StatusCode.ERROR, description))
        else:
            self._span.set_status(Status(StatusCode.UNSET, description))

    def record_exception(self, exception: Exception) -> None:
        """Record an exception on the span."""
        self._span.record_exception(exception)

    def end(self) -> None:
        """End the span."""
        self._span.end()


class OpenTelemetryTracer(Tracer):
    """OpenTelemetry-based tracer implementation.

    Provides tracing via OpenTelemetry SDK. Supports both automatic
    and manual instrumentation.

    Args:
        service_name: Name of the service for tracing
        endpoint: Optional OTLP endpoint for exporting traces
        headers: Optional headers for OTLP exporter authentication
    """

    def __init__(
        self,
        service_name: str = "neo4j-agent-memory",
        endpoint: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        """Initialize the OpenTelemetry tracer.

        Args:
            service_name: Name of the service
            endpoint: OTLP endpoint URL (if None, uses env vars or defaults)
            headers: Optional headers for authentication
        """
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        # Create resource with service name
        resource = Resource.create({"service.name": service_name})

        # Create tracer provider
        provider = TracerProvider(resource=resource)

        # Add exporter if endpoint provided
        if endpoint:
            try:
                from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
                    OTLPSpanExporter,
                )

                grpc_exporter = OTLPSpanExporter(endpoint=endpoint, headers=headers)
                provider.add_span_processor(BatchSpanProcessor(grpc_exporter))
            except ImportError:
                # Try HTTP exporter
                try:
                    from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                        OTLPSpanExporter as HTTPSpanExporter,
                    )

                    http_exporter = HTTPSpanExporter(endpoint=endpoint, headers=headers)
                    provider.add_span_processor(BatchSpanProcessor(http_exporter))
                except ImportError:
                    pass  # No exporter available, traces will be collected but not exported

        # Set as global provider
        trace.set_tracer_provider(provider)

        # Get tracer
        self._tracer = trace.get_tracer(service_name)
        self._service_name = service_name

    def start_span(self, name: str, attributes: dict[str, Any] | None = None) -> Span:
        """Start a new span.

        Args:
            name: Name of the span
            attributes: Optional attributes to set on the span

        Returns:
            An OpenTelemetrySpan wrapping the underlying span
        """
        otel_span = self._tracer.start_span(name)

        # Set attributes if provided
        if attributes:
            for key, value in attributes.items():
                if isinstance(value, (str, int, float, bool)):
                    otel_span.set_attribute(key, value)
                else:
                    otel_span.set_attribute(key, str(value))

        return OpenTelemetrySpan(otel_span)

    @property
    def service_name(self) -> str:
        """Get the service name."""
        return self._service_name
