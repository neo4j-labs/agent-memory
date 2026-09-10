"""HTTP transport for the NAMS backend.

Wraps :class:`httpx.AsyncClient` with:

* Wire-protocol auto-selection (REST vs TCK bridge) via
  :func:`endpoints.detect_protocol`.
* Pluggable auth via :class:`AuthProvider`.
* Retry policy: 429 (honors ``Retry-After``), 5xx, network errors;
  exponential backoff; bounded by :attr:`max_retries`.
* HTTP-status → typed-exception mapping (see :data:`_STATUS_MAP`
  and the plan, section F).
* OTel-style spans via :class:`observability.Tracer`.

The transport is generic — it takes an :class:`EndpointSpec` and JSON
body, and dispatches. Phase 3 memory implementations build a registry
of specs and call :meth:`request` for each Protocol method.
"""

from __future__ import annotations

import asyncio
import logging
from types import TracebackType
from typing import TYPE_CHECKING, Any

import httpx

from neo4j_agent_memory.config.settings import NamsConfig
from neo4j_agent_memory.core.exceptions import (
    AuthenticationError,
    NotFoundError,
    NotSupportedError,
    RateLimitError,
    TransportError,
    ValidationError,
)
from neo4j_agent_memory.nams import _telemetry
from neo4j_agent_memory.nams.auth import AuthProvider
from neo4j_agent_memory.nams.endpoints import (
    EndpointSpec,
    TransportMode,
    WireProtocol,
    detect_protocol,
)

if TYPE_CHECKING:
    from neo4j_agent_memory.observability.base import Tracer

logger = logging.getLogger(__name__)


# HTTP status codes that should be retried. 429 has special Retry-After handling.
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


def _is_retryable_exception(exc: BaseException) -> bool:
    """Return True if ``exc`` is a transient network error worth retrying."""
    return isinstance(exc, (httpx.ConnectError, httpx.TimeoutException, httpx.RemoteProtocolError))


def _parse_retry_after(value: str | None) -> float | None:
    """Parse a ``Retry-After`` header. Returns seconds, or None if unparsable.

    The header may be a delta-seconds integer or an HTTP-date; we only
    handle the seconds form (the hosted NAMS sends seconds).
    """
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class HttpTransport:
    """Async HTTP client for the NAMS backend.

    Use via :meth:`from_config` and as an async context manager:

    .. code-block:: python

        async with HttpTransport.from_config(settings.nams, auth=auth) as t:
            data = await t.request(spec, json={"role": "user", "content": "hi"})
    """

    def __init__(
        self,
        *,
        endpoint: str,
        auth: AuthProvider,
        transport_mode: TransportMode = "auto",
        timeout: float = 30.0,
        max_retries: int = 3,
        retry_backoff_seconds: float = 0.5,
        headers: dict[str, str] | None = None,
        workspace_id: str | None = None,
        tracer: Tracer | None = None,
    ) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._auth = auth
        self._protocol: WireProtocol = detect_protocol(endpoint, transport_mode)
        self._timeout = timeout
        self._max_retries = max_retries
        self._backoff = retry_backoff_seconds
        self._user_headers: dict[str, str] = dict(headers or {})
        self._workspace_id = workspace_id
        self._tracer = tracer
        self._client: httpx.AsyncClient | None = None

    @classmethod
    def from_config(
        cls,
        config: NamsConfig,
        *,
        auth: AuthProvider,
        tracer: Tracer | None = None,
    ) -> HttpTransport:
        """Build a transport from a :class:`NamsConfig`."""
        return cls(
            endpoint=config.endpoint,
            auth=auth,
            transport_mode=config.transport_mode,
            timeout=config.timeout,
            max_retries=config.max_retries,
            retry_backoff_seconds=config.retry_backoff_seconds,
            headers=dict(config.headers),
            workspace_id=config.workspace_id,
            tracer=tracer,
        )

    # ------------------------------------------------------------------ lifecycle

    async def __aenter__(self) -> HttpTransport:
        await self._open()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    async def _open(self) -> None:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------ properties

    @property
    def endpoint(self) -> str:
        """The endpoint URL (trailing slash trimmed)."""
        return self._endpoint

    @property
    def protocol(self) -> WireProtocol:
        """The wire protocol in use (``"rest"`` or ``"bridge"``)."""
        return self._protocol

    @property
    def is_open(self) -> bool:
        return self._client is not None

    # ------------------------------------------------------------------ requests

    async def request(
        self,
        spec: EndpointSpec,
        *,
        path_params: dict[str, object] | None = None,
        json: Any = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Issue one NAMS HTTP request and return the parsed response.

        Returns the JSON-decoded body for 2xx responses (or ``None`` for
        204). Raises typed exceptions for 4xx/5xx/network failures per
        :data:`_STATUS_MAP`.

        Args:
            spec: Endpoint specification (REST path + bridge method).
            path_params: Substitutions for ``{name}`` placeholders in
                the REST path (ignored in bridge mode).
            json: Request body (dict / list / pre-serialized payload).
                Sent as JSON.
            params: Query-string parameters for GET requests.
        """
        if self._client is None:
            await self._open()
        assert self._client is not None  # noqa: S101 — narrows for type-checker

        http_method, url = spec.resolve(self._endpoint, self._protocol, path_params)

        # Prepare headers: user-supplied + workspace + auth + json default.
        # Precedence for X-Workspace-Id: an explicit entry in user headers wins
        # (the documented escape hatch); otherwise the configured workspace_id.
        headers: dict[str, str] = dict(self._user_headers)
        if self._workspace_id and "X-Workspace-Id" not in headers:
            headers["X-Workspace-Id"] = self._workspace_id
        if json is not None and "Content-Type" not in headers:
            headers["Content-Type"] = "application/json"
        headers = await self._auth.apply(headers)

        if self._tracer is None:
            return await self._request_with_retry(
                http_method, url, headers=headers, json=json, params=params, spec=spec
            )

        async with self._tracer.async_span(_telemetry.SPAN_NAME) as span:
            _telemetry.set_request_attributes(
                span,
                http_method=http_method,
                url=url,
                nams_method=spec.bridge_method,
                protocol=self._protocol,
            )
            return await self._request_with_retry(
                http_method,
                url,
                headers=headers,
                json=json,
                params=params,
                spec=spec,
                span=span,
            )

    async def _request_with_retry(
        self,
        http_method: str,
        url: str,
        *,
        headers: dict[str, str],
        json: Any,
        params: dict[str, Any] | None,
        spec: EndpointSpec,
        span: Any = None,
    ) -> Any:
        """Inner retry loop. Caller owns the span."""
        assert self._client is not None  # noqa: S101
        last_status: int | None = None
        last_retry_after: float | None = None
        attempt = 0
        while True:
            try:
                response = await self._client.request(
                    http_method,
                    url,
                    headers=headers,
                    json=json,
                    params=params,
                )
            except Exception as exc:
                if _is_retryable_exception(exc) and attempt < self._max_retries:
                    delay = self._backoff_delay(attempt)
                    attempt += 1
                    logger.debug(
                        "NAMS %s %s network error %r — retry %d/%d after %.2fs",
                        http_method,
                        url,
                        exc,
                        attempt,
                        self._max_retries,
                        delay,
                    )
                    await asyncio.sleep(delay)
                    continue
                if span is not None:
                    span.record_exception(exc)
                    span.set_status("ERROR", str(exc))
                raise TransportError(f"NAMS {http_method} {url} network error: {exc}") from exc

            status = response.status_code
            last_status = status

            if 200 <= status < 300:
                if span is not None:
                    _telemetry.set_response_attributes(
                        span, status_code=status, retry_count=attempt
                    )
                return self._decode_body(response)

            if status in _RETRYABLE_STATUS and attempt < self._max_retries:
                retry_after = (
                    _parse_retry_after(response.headers.get("Retry-After"))
                    if status == 429
                    else None
                )
                last_retry_after = retry_after
                delay = retry_after if retry_after is not None else self._backoff_delay(attempt)
                attempt += 1
                logger.debug(
                    "NAMS %s %s → %d, retry %d/%d after %.2fs",
                    http_method,
                    url,
                    status,
                    attempt,
                    self._max_retries,
                    delay,
                )
                await asyncio.sleep(delay)
                continue

            # No more retries — map status → exception.
            if span is not None:
                _telemetry.set_response_attributes(span, status_code=status, retry_count=attempt)
            self._raise_for_status(
                http_method=http_method,
                url=url,
                response=response,
                spec=spec,
                retry_after=last_retry_after if status == 429 else None,
            )
            # _raise_for_status always raises; the next line is unreachable but
            # keeps the type-checker happy.
            raise TransportError(f"NAMS {http_method} {url} unexpected status {last_status}")

    def _backoff_delay(self, attempt: int) -> float:
        """Exponential backoff: ``backoff * 2**attempt`` (so 0.5, 1.0, 2.0, ...)."""
        return float(self._backoff * (2**attempt))

    @staticmethod
    def _decode_body(response: httpx.Response) -> Any:
        """Decode a 2xx response body. Returns None for 204 / empty bodies."""
        if response.status_code == 204 or not response.content:
            return None
        # Defer to httpx's json() which uses the response charset correctly.
        return response.json()

    @staticmethod
    def _raise_for_status(
        *,
        http_method: str,
        url: str,
        response: httpx.Response,
        spec: EndpointSpec,
        retry_after: float | None,
    ) -> None:
        status = response.status_code
        # Best-effort error body extraction.
        body: Any
        try:
            body = response.json() if response.content else None
        except ValueError:
            body = response.text or None
        message = HttpTransport._error_message(status, http_method, url, body)

        if status == 400:
            details = body if isinstance(body, dict) else {"raw": body}
            raise ValidationError(message, details=details)
        if status in (401, 403):
            raise AuthenticationError(message)
        if status == 404:
            raise NotFoundError(message)
        if status in (405, 501):
            raise NotSupportedError(
                backend="nams",
                method=spec.bridge_method,
                message=f"Server rejected with HTTP {status}.",
            )
        if status == 429:
            raise RateLimitError(message, retry_after=retry_after)
        if 500 <= status < 600:
            raise TransportError(message)
        # Anything else (e.g., 418) — treat as transport-level fault.
        raise TransportError(message)

    @staticmethod
    def _error_message(status: int, http_method: str, url: str, body: Any) -> str:
        if isinstance(body, dict) and "error" in body:
            return f"NAMS {http_method} {url} → {status}: {body['error']}"
        if isinstance(body, str) and body:
            return f"NAMS {http_method} {url} → {status}: {body}"
        return f"NAMS {http_method} {url} → {status}"


__all__ = ["HttpTransport"]
