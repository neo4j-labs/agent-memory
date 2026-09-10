"""Tests for nams/transport.py — HttpTransport mechanics.

Covers:

* Auto-protocol detection (REST vs bridge).
* Auth header application.
* Happy path: 200, 201, 204 (empty body).
* Error mapping: 400→Validation, 401→Auth, 403→Auth, 404→NotFoundError,
  405/501→NotSupportedError, 429→RateLimitError, 5xx→TransportError,
  network failures→TransportError.
* Retry policy: 429 honors Retry-After, 5xx uses exponential backoff,
  retries don't exceed max_retries.
* Custom headers + bridge protocol routing.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import httpx
import pytest
import respx
from pydantic import SecretStr

from neo4j_agent_memory.config.settings import NamsConfig
from neo4j_agent_memory.core.exceptions import (
    AuthenticationError,
    MemoryError,
    NotFoundError,
    NotSupportedError,
    RateLimitError,
    TransportError,
    ValidationError,
)
from neo4j_agent_memory.nams import EndpointSpec, HttpTransport, StaticApiKeyAuth

# Suppress real waits in retry-path tests. With backoff=0.01 and max 2 retries,
# the synthetic delays are still ~30ms total — acceptable for unit tests.

ADD_MESSAGE_SPEC = EndpointSpec(
    rest_method="POST",
    rest_path="/conversations/{session_id}/messages",
    bridge_method="add_message",
)
LIST_SESSIONS_SPEC = EndpointSpec(
    rest_method="GET",
    rest_path="/sessions",
    bridge_method="list_sessions",
)


# ----------------------------------------------------------------- construction


class TestConstruction:
    def test_from_config_picks_rest_protocol(self, nams_config):
        auth = StaticApiKeyAuth.from_config(nams_config)
        t = HttpTransport.from_config(nams_config, auth=auth)
        assert t.protocol == "rest"
        assert t.endpoint == "https://memory.test/v1"

    def test_from_config_picks_bridge_protocol(self, bridge_config):
        auth = StaticApiKeyAuth.from_config(bridge_config)
        t = HttpTransport.from_config(bridge_config, auth=auth)
        assert t.protocol == "bridge"
        assert t.endpoint == "https://memory.test"

    def test_explicit_transport_mode_overrides_auto(self):
        config = NamsConfig(
            endpoint="https://memory.test/v1",
            api_key=SecretStr("k"),
            transport_mode="bridge",
        )
        t = HttpTransport.from_config(config, auth=StaticApiKeyAuth.from_config(config))
        assert t.protocol == "bridge"

    def test_endpoint_trailing_slash_stripped(self):
        config = NamsConfig(
            endpoint="https://memory.test/v1/",
            api_key=SecretStr("k"),
        )
        t = HttpTransport.from_config(config, auth=StaticApiKeyAuth.from_config(config))
        assert t.endpoint == "https://memory.test/v1"


# -------------------------------------------------------------- async lifecycle


class TestLifecycle:
    async def test_context_manager_opens_and_closes(self, nams_config, auth):
        t = HttpTransport.from_config(nams_config, auth=auth)
        assert not t.is_open
        async with t:
            assert t.is_open
        assert not t.is_open

    async def test_close_idempotent(self, nams_config, auth):
        t = HttpTransport.from_config(nams_config, auth=auth)
        async with t:
            pass
        await t.close()  # second close should not raise


# ----------------------------------------------------------------- happy paths


class TestHappyPath:
    @respx.mock
    async def test_post_returns_parsed_json(self, nams_config, auth):
        route = respx.post("https://memory.test/v1/conversations/abc/messages").respond(
            200, json={"id": "m1", "role": "user", "content": "hi"}
        )

        async with HttpTransport.from_config(nams_config, auth=auth) as t:
            result = await t.request(
                ADD_MESSAGE_SPEC,
                path_params={"session_id": "abc"},
                json={"role": "user", "content": "hi"},
            )
        assert result == {"id": "m1", "role": "user", "content": "hi"}
        assert route.called

    @respx.mock
    async def test_get_with_query_params(self, nams_config, auth):
        route = respx.get("https://memory.test/v1/sessions").respond(
            200, json=[{"session_id": "s1"}]
        )

        async with HttpTransport.from_config(nams_config, auth=auth) as t:
            result = await t.request(LIST_SESSIONS_SPEC, params={"limit": 10})
        assert isinstance(result, list)
        assert result[0]["session_id"] == "s1"
        # respx normalizes query strings.
        assert route.calls[0].request.url.params["limit"] == "10"

    @respx.mock
    async def test_204_returns_none(self, nams_config, auth):
        respx.post("https://memory.test/v1/conversations/abc/messages").respond(204)

        async with HttpTransport.from_config(nams_config, auth=auth) as t:
            result = await t.request(
                ADD_MESSAGE_SPEC,
                path_params={"session_id": "abc"},
                json={"role": "user", "content": "hi"},
            )
        assert result is None

    @respx.mock
    async def test_200_empty_body_returns_none(self, nams_config, auth):
        # Some servers send 200 with no body — be tolerant.
        respx.post("https://memory.test/v1/conversations/abc/messages").respond(200, content=b"")

        async with HttpTransport.from_config(nams_config, auth=auth) as t:
            result = await t.request(
                ADD_MESSAGE_SPEC,
                path_params={"session_id": "abc"},
                json={"role": "user", "content": "hi"},
            )
        assert result is None

    @respx.mock
    async def test_auth_header_applied(self, nams_config, auth):
        route = respx.post("https://memory.test/v1/conversations/abc/messages").respond(
            200, json={}
        )

        async with HttpTransport.from_config(nams_config, auth=auth) as t:
            await t.request(
                ADD_MESSAGE_SPEC,
                path_params={"session_id": "abc"},
                json={"role": "user", "content": "hi"},
            )
        assert route.calls[0].request.headers["Authorization"] == "Bearer nams_test_key"

    @respx.mock
    async def test_custom_headers_passed_through(self, auth):
        config = NamsConfig(
            endpoint="https://memory.test/v1",
            api_key=SecretStr("nams_test_key"),
            headers={"X-Trace-Id": "t1"},
            validate_on_connect=False,
        )
        route = respx.post("https://memory.test/v1/conversations/abc/messages").respond(
            200, json={}
        )
        async with HttpTransport.from_config(
            config, auth=StaticApiKeyAuth.from_config(config)
        ) as t:
            await t.request(
                ADD_MESSAGE_SPEC,
                path_params={"session_id": "abc"},
                json={"role": "user", "content": "hi"},
            )
        assert route.calls[0].request.headers["X-Trace-Id"] == "t1"
        # Auth header still present alongside.
        assert route.calls[0].request.headers["Authorization"] == "Bearer nams_test_key"


class TestBridgeProtocol:
    @respx.mock
    async def test_bridge_routing(self, bridge_config):
        # Bridge protocol = POST /<snake_case_method>
        route = respx.post("https://memory.test/add_message").respond(200, json={"id": "m1"})
        auth = StaticApiKeyAuth.from_config(bridge_config)
        async with HttpTransport.from_config(bridge_config, auth=auth) as t:
            result = await t.request(
                ADD_MESSAGE_SPEC,
                path_params={"session_id": "abc"},  # ignored in bridge mode
                json={"role": "user", "content": "hi"},
            )
        assert result == {"id": "m1"}
        assert route.called


# ----------------------------------------------------------------- error paths


class TestErrorMapping:
    @respx.mock
    async def test_400_raises_validation_error_with_details(self, nams_config, auth):
        respx.post("https://memory.test/v1/conversations/abc/messages").respond(
            400, json={"error": "missing field", "field": "role"}
        )

        async with HttpTransport.from_config(nams_config, auth=auth) as t:
            with pytest.raises(ValidationError) as exc_info:
                await t.request(
                    ADD_MESSAGE_SPEC,
                    path_params={"session_id": "abc"},
                    json={"content": "hi"},
                )
        assert "missing field" in str(exc_info.value)
        assert exc_info.value.details.get("field") == "role"

    @respx.mock
    async def test_401_raises_authentication_error(self, nams_config, auth):
        respx.post("https://memory.test/v1/conversations/abc/messages").respond(
            401, json={"error": "invalid api key"}
        )

        async with HttpTransport.from_config(nams_config, auth=auth) as t:
            with pytest.raises(AuthenticationError):
                await t.request(
                    ADD_MESSAGE_SPEC,
                    path_params={"session_id": "abc"},
                    json={"role": "user", "content": "hi"},
                )

    @respx.mock
    async def test_403_raises_authentication_error(self, nams_config, auth):
        respx.post("https://memory.test/v1/conversations/abc/messages").respond(
            403, json={"error": "forbidden"}
        )

        async with HttpTransport.from_config(nams_config, auth=auth) as t:
            with pytest.raises(AuthenticationError):
                await t.request(
                    ADD_MESSAGE_SPEC,
                    path_params={"session_id": "abc"},
                    json={"role": "user", "content": "hi"},
                )

    @respx.mock
    async def test_404_raises_not_found_error(self, nams_config, auth):
        respx.post("https://memory.test/v1/conversations/abc/messages").respond(
            404, json={"error": "session not found"}
        )

        async with HttpTransport.from_config(nams_config, auth=auth) as t:
            with pytest.raises(NotFoundError, match="not found") as exc_info:
                await t.request(
                    ADD_MESSAGE_SPEC,
                    path_params={"session_id": "abc"},
                    json={"role": "user", "content": "hi"},
                )
            assert isinstance(exc_info.value, MemoryError)

    @respx.mock
    async def test_405_raises_not_supported(self, nams_config, auth):
        respx.post("https://memory.test/v1/conversations/abc/messages").respond(
            405, json={"error": "method not allowed"}
        )

        async with HttpTransport.from_config(nams_config, auth=auth) as t:
            with pytest.raises(NotSupportedError) as exc_info:
                await t.request(
                    ADD_MESSAGE_SPEC,
                    path_params={"session_id": "abc"},
                    json={"role": "user", "content": "hi"},
                )
        assert exc_info.value.backend == "nams"
        assert exc_info.value.method == "add_message"

    @respx.mock
    async def test_501_raises_not_supported(self, nams_config, auth):
        respx.post("https://memory.test/v1/conversations/abc/messages").respond(501)

        async with HttpTransport.from_config(nams_config, auth=auth) as t:
            with pytest.raises(NotSupportedError):
                await t.request(
                    ADD_MESSAGE_SPEC,
                    path_params={"session_id": "abc"},
                    json={"role": "user", "content": "hi"},
                )


class TestRetryBehavior:
    @respx.mock
    async def test_5xx_retried_then_succeeds(self, nams_config, auth):
        # First two calls fail with 503, third succeeds.
        route = respx.post("https://memory.test/v1/conversations/abc/messages").mock(
            side_effect=[
                httpx.Response(503, json={"error": "busy"}),
                httpx.Response(503, json={"error": "busy"}),
                httpx.Response(200, json={"id": "m1"}),
            ]
        )

        async with HttpTransport.from_config(nams_config, auth=auth) as t:
            result = await t.request(
                ADD_MESSAGE_SPEC,
                path_params={"session_id": "abc"},
                json={"role": "user", "content": "hi"},
            )
        assert result == {"id": "m1"}
        assert route.call_count == 3

    @respx.mock
    async def test_5xx_exhausts_retries_then_raises_transport_error(self, nams_config, auth):
        # max_retries=2 from fixture → 3 attempts total, all 503.
        route = respx.post("https://memory.test/v1/conversations/abc/messages").respond(
            503, json={"error": "down"}
        )

        async with HttpTransport.from_config(nams_config, auth=auth) as t:
            with pytest.raises(TransportError, match="503"):
                await t.request(
                    ADD_MESSAGE_SPEC,
                    path_params={"session_id": "abc"},
                    json={"role": "user", "content": "hi"},
                )
        assert route.call_count == 3  # 1 + 2 retries

    @respx.mock
    async def test_429_with_retry_after_honored(self, nams_config, auth, monkeypatch):
        # Capture sleep durations so we can verify Retry-After is honored.
        sleeps: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            sleeps.append(seconds)

        monkeypatch.setattr(asyncio, "sleep", fake_sleep)

        respx.post("https://memory.test/v1/conversations/abc/messages").mock(
            side_effect=[
                httpx.Response(429, headers={"Retry-After": "0.05"}),
                httpx.Response(200, json={"id": "m1"}),
            ]
        )

        async with HttpTransport.from_config(nams_config, auth=auth) as t:
            result = await t.request(
                ADD_MESSAGE_SPEC,
                path_params={"session_id": "abc"},
                json={"role": "user", "content": "hi"},
            )
        assert result == {"id": "m1"}
        # First (and only) sleep used the Retry-After value (0.05),
        # not the exponential backoff (would be 0.01 * 2^0 = 0.01).
        assert sleeps == [0.05]

    @respx.mock
    async def test_429_without_retry_after_uses_backoff(self, nams_config, auth, monkeypatch):
        sleeps: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            sleeps.append(seconds)

        monkeypatch.setattr(asyncio, "sleep", fake_sleep)

        respx.post("https://memory.test/v1/conversations/abc/messages").mock(
            side_effect=[
                httpx.Response(429),  # no Retry-After header
                httpx.Response(200, json={"id": "m1"}),
            ]
        )

        async with HttpTransport.from_config(nams_config, auth=auth) as t:
            await t.request(
                ADD_MESSAGE_SPEC,
                path_params={"session_id": "abc"},
                json={"role": "user", "content": "hi"},
            )
        # backoff = 0.01 (from fixture), attempt 0 → 0.01 * 2^0 = 0.01.
        assert sleeps == [0.01]

    @respx.mock
    async def test_429_exhausts_retries_raises_rate_limit(self, nams_config, auth):
        respx.post("https://memory.test/v1/conversations/abc/messages").respond(
            429, headers={"Retry-After": "0.001"}
        )

        async with HttpTransport.from_config(nams_config, auth=auth) as t:
            with pytest.raises(RateLimitError) as exc_info:
                await t.request(
                    ADD_MESSAGE_SPEC,
                    path_params={"session_id": "abc"},
                    json={"role": "user", "content": "hi"},
                )
        # retry_after carried through to the raised exception.
        assert exc_info.value.retry_after == 0.001

    @respx.mock
    async def test_network_error_retried(self, nams_config, auth):
        # Two ConnectErrors, then success.
        route = respx.post("https://memory.test/v1/conversations/abc/messages").mock(
            side_effect=[
                httpx.ConnectError("connection refused"),
                httpx.ConnectError("connection refused"),
                httpx.Response(200, json={"id": "m1"}),
            ]
        )

        async with HttpTransport.from_config(nams_config, auth=auth) as t:
            result = await t.request(
                ADD_MESSAGE_SPEC,
                path_params={"session_id": "abc"},
                json={"role": "user", "content": "hi"},
            )
        assert result == {"id": "m1"}
        assert route.call_count == 3

    @respx.mock
    async def test_timeout_error_retried(self, nams_config, auth):
        route = respx.post("https://memory.test/v1/conversations/abc/messages").mock(
            side_effect=[
                httpx.WriteTimeout("timed out"),
                httpx.Response(200, json={"id": "m1"}),
            ]
        )

        async with HttpTransport.from_config(nams_config, auth=auth) as t:
            result = await t.request(
                ADD_MESSAGE_SPEC,
                path_params={"session_id": "abc"},
                json={"role": "user", "content": "hi"},
            )
        assert result == {"id": "m1"}
        assert route.call_count == 2

    @respx.mock
    async def test_network_error_exhausts_raises_transport_error(self, nams_config, auth):
        respx.post("https://memory.test/v1/conversations/abc/messages").mock(
            side_effect=httpx.ConnectError("net down")
        )

        async with HttpTransport.from_config(nams_config, auth=auth) as t:
            with pytest.raises(TransportError, match="network error"):
                await t.request(
                    ADD_MESSAGE_SPEC,
                    path_params={"session_id": "abc"},
                    json={"role": "user", "content": "hi"},
                )

    @respx.mock
    async def test_400_not_retried(self, nams_config, auth):
        # ValidationError is non-retryable — should hit the server exactly once.
        route = respx.post("https://memory.test/v1/conversations/abc/messages").respond(
            400, json={"error": "bad"}
        )

        async with HttpTransport.from_config(nams_config, auth=auth) as t:
            with pytest.raises(ValidationError):
                await t.request(
                    ADD_MESSAGE_SPEC,
                    path_params={"session_id": "abc"},
                    json={"role": "user", "content": "hi"},
                )
        assert route.call_count == 1


# ----------------------------------------------------------- tracer integration


class TestTracerIntegration:
    @respx.mock
    async def test_span_attributes_set_on_happy_path(self, nams_config, auth):
        # Use a real Tracer (NoOpTracer) and capture span method calls.
        span = MagicMock()
        tracer = MagicMock()
        tracer.async_span.return_value.__aenter__ = MagicMock(return_value=_AsyncReturn(span))
        tracer.async_span.return_value.__aexit__ = MagicMock(return_value=_AsyncReturn(None))

        respx.post("https://memory.test/v1/conversations/abc/messages").respond(
            200, json={"id": "m1"}
        )

        t = HttpTransport.from_config(nams_config, auth=auth, tracer=tracer)
        async with t:
            await t.request(
                ADD_MESSAGE_SPEC,
                path_params={"session_id": "abc"},
                json={"role": "user", "content": "hi"},
            )

        # set_attribute called multiple times — verify the key ones landed.
        attr_calls = {call.args[0]: call.args[1] for call in span.set_attribute.call_args_list}
        assert attr_calls["http.method"] == "POST"
        assert attr_calls["http.url"] == ("https://memory.test/v1/conversations/abc/messages")
        assert attr_calls["nams.method"] == "add_message"
        assert attr_calls["nams.protocol"] == "rest"
        assert attr_calls["http.status_code"] == 200


# ----------------------------------------------------------------- test helpers


class _AsyncReturn:
    """Trivial awaitable wrapper for MagicMock-based async context managers."""

    def __init__(self, value):
        self._value = value

    def __await__(self):
        async def _coro():
            return self._value

        return _coro().__await__()
