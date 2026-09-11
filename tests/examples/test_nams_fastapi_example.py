"""Smoke tests for the NAMS + FastAPI example.

The example is an HTTP service in front of the hosted memory service, so both
halves are faked: the app is driven through ``httpx.ASGITransport`` (no uvicorn,
no port) while its outbound NAMS calls hit a small stateful ``respx`` double
(no API key, no network, no Neo4j).

What this exists to catch: nothing previously instantiated the app, so a
renamed ``add_message``, a changed NAMS conversation-create body, a README that
documents a field the model does not have, or a regression in the error-status
mapping would all have shipped silently.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import sys
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("fastapi", reason="fastapi not installed")
pytest.importorskip("respx", reason="respx not installed")

import httpx  # noqa: E402
import respx  # noqa: E402

from tests.examples._manifests import assert_library_pin  # noqa: E402

EXAMPLES_DIR = Path(__file__).parent.parent.parent / "examples"
EXAMPLE_DIR = EXAMPLES_DIR / "nams-fastapi"
MAIN_PY = EXAMPLE_DIR / "main.py"

ENDPOINT = "https://memory.test/v1"
CONVERSATION_ID = "00000000-0000-0000-0000-0000000000aa"
USER_ID = "alice"
BEARER = {"Authorization": "Bearer demo-alice"}

CONVERSATION = {
    "id": CONVERSATION_ID,
    "userId": USER_ID,
    "createdAt": "2026-09-10T12:00:00Z",
    "updatedAt": "2026-09-10T12:00:00Z",
}


def _load_main_module() -> Any:
    """Import ``examples/nams-fastapi/main.py`` (a dashed, non-package dir)."""
    spec = importlib.util.spec_from_file_location("nams_fastapi_main", MAIN_PY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeNams:
    """Stateful NAMS double: create/list conversations, messages, context, search.

    Stateful on purpose — the example's ownership check (``list_conversations``
    filtered by ``userId``) and its "second turn sees context" behaviour only
    mean something against a backend that remembers the first turn.
    """

    def __init__(self) -> None:
        self.created = False
        self.messages: list[dict[str, Any]] = []
        self.list_route: Any = None
        self.create_route: Any = None

    def install(self, router: respx.Router) -> None:
        self.list_route = router.get(f"{ENDPOINT}/conversations").mock(
            side_effect=self._list_conversations
        )
        self.create_route = router.post(f"{ENDPOINT}/conversations").mock(
            side_effect=self._create_conversation
        )
        router.get(f"{ENDPOINT}/conversations/{CONVERSATION_ID}").respond(200, json=CONVERSATION)
        router.get(f"{ENDPOINT}/conversations/{CONVERSATION_ID}/messages").mock(
            side_effect=self._list_messages
        )
        router.post(f"{ENDPOINT}/conversations/{CONVERSATION_ID}/messages").mock(
            side_effect=self._add_message
        )
        router.get(f"{ENDPOINT}/conversations/{CONVERSATION_ID}/context").mock(
            side_effect=self._context
        )
        router.post(f"{ENDPOINT}/conversations/{CONVERSATION_ID}/search").mock(
            side_effect=self._search
        )

    # -- handlers ---------------------------------------------------------

    def _create_conversation(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        self.created = True
        # NAMS accepts only {userId?, metadata?} and mints the id itself.
        return httpx.Response(201, json={**CONVERSATION, "userId": body.get("userId")})

    def _list_conversations(self, request: httpx.Request) -> httpx.Response:
        user = request.url.params.get("userId")
        if not self.created or (user is not None and user != USER_ID):
            return httpx.Response(200, json={"conversations": []})
        return httpx.Response(200, json={"conversations": [CONVERSATION]})

    def _add_message(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        assert set(body) == {"content", "role"}, (
            f"NAMS accepts only content/role on this endpoint, got {sorted(body)}"
        )
        message = {
            "id": str(uuid.uuid4()),
            "conversationId": CONVERSATION_ID,
            "role": body["role"],
            "content": body["content"],
            "createdAt": "2026-09-10T12:00:01Z",
        }
        self.messages.append(message)
        return httpx.Response(201, json=message)

    def _list_messages(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"messages": self.messages})

    def _context(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"reflections": [], "observations": [], "recentMessages": self.messages},
        )

    def _search(self, request: httpx.Request) -> httpx.Response:
        query = json.loads(request.content or b"{}").get("query", "").lower()
        hits = [m for m in self.messages if query in m["content"].lower()]
        return httpx.Response(200, json={"messages": hits, "searchType": "vector"})


@pytest.fixture
def nams_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """A NAMS config pointing at the mock, with retries off for speed."""
    monkeypatch.setenv("MEMORY_API_KEY", "nams_test_key")
    monkeypatch.setenv("MEMORY_ENDPOINT", ENDPOINT)
    monkeypatch.setenv("MEMORY_MAX_RETRIES", "0")
    monkeypatch.delenv("MEMORY_WORKSPACE_ID", raising=False)
    monkeypatch.delenv("ALLOW_INSECURE_USER_HEADER", raising=False)


@pytest.fixture
def module(nams_env: None) -> Iterator[Any]:
    module = _load_main_module()
    yield module
    sys.modules.pop("nams_fastapi_main", None)


@asynccontextmanager
async def running_app(module: Any) -> AsyncIterator[httpx.AsyncClient]:
    """Run the app's own lifespan, then drive it over ASGI."""
    async with module.lifespan(module.app):
        transport = httpx.ASGITransport(app=module.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://svc") as client:
            yield client


@asynccontextmanager
async def mocked_service(module: Any) -> AsyncIterator[tuple[httpx.AsyncClient, FakeNams]]:
    with respx.mock(assert_all_called=False) as router:
        fake = FakeNams()
        fake.install(router)
        async with running_app(module) as client:
            yield client, fake


@pytest.mark.syntax
class TestNamsFastapiStructure:
    def test_required_files_exist(self) -> None:
        for filename in ("main.py", "README.md", "requirements.txt", ".env.example"):
            assert (EXAMPLE_DIR / filename).exists(), f"Missing: {filename}"

    def test_main_compiles(self) -> None:
        ast.parse(MAIN_PY.read_text(encoding="utf-8"))

    def test_requirements_pin_the_library_fastapi_and_uvicorn(self) -> None:
        requirements = EXAMPLE_DIR / "requirements.txt"
        pin = assert_library_pin(requirements)
        assert "nams" in pin.extras

        content = requirements.read_text(encoding="utf-8")
        assert "fastapi>=0.141,<1" in content
        # [standard] is what supplies --reload's watcher, which the README uses.
        assert "uvicorn[standard]>=0.52,<1" in content

    def test_env_example_documents_workspace_id(self) -> None:
        content = (EXAMPLE_DIR / ".env.example").read_text(encoding="utf-8")
        assert "MEMORY_API_KEY" in content
        assert "MEMORY_WORKSPACE_ID" in content

    def test_main_does_not_repeat_the_dropped_tenancy_claim(self) -> None:
        source = MAIN_PY.read_text(encoding="utf-8")
        # NAMS silently drops user_identifier on add_message (nams-fastapi-F01):
        # ownership must flow through create_conversation instead.
        assert "create_conversation(" in source
        add_message_calls = [
            line
            for line in source.splitlines()
            if "add_message(" in line and "user_identifier" in line
        ]
        assert not add_message_calls, (
            f"user_identifier on add_message is a no-op: {add_message_calls}"
        )
        # The hand-rolled literal id that 404'd on the next call is gone.
        assert '"fastapi-chat"' not in source
        # Modern entry point, not asyncio.run(uvicorn.Server(...)).
        assert 'uvicorn.run("main:app"' in source

    def test_readme_follows_labs_conventions(self) -> None:
        readme = (EXAMPLE_DIR / "README.md").read_text(encoding="utf-8")
        assert "Neo4j-Labs" in readme, "missing the Labs badge"
        assert "Neo4j Labs Project" in readme, "missing the Labs disclaimer"
        assert "## Support" in readme
        assert "Verified against" in readme
        assert "Expected output" in readme
        # The documented curl must use the real request field (nams-fastapi-F04).
        assert "conversation_id" in readme
        assert '"session_id"' not in readme
        # The impossible OTel instruction must not come back (nams-fastapi-F12).
        assert "pass it to `MemorySettings`" not in readme


@pytest.mark.imports
class TestNamsFastapiImports:
    def test_names_the_example_imports_exist(self) -> None:
        from neo4j_agent_memory import (  # noqa: F401
            AuthenticationError,
            NamsConfig,
            NamsSettings,
            NotFoundError,
            NotSupportedError,
            RateLimitError,
            TransportError,
            connect,
        )
        from neo4j_agent_memory import MemoryError as MemoryBackendError  # noqa: F401
        from neo4j_agent_memory import ValidationError as MemoryValidationError  # noqa: F401

    def test_app_exposes_the_documented_routes(self, module: Any) -> None:
        paths = {route.path for route in module.app.routes}
        assert {
            "/chat",
            "/conversations",
            "/conversations/{conversation_id}",
            "/conversations/{conversation_id}/search",
            "/health",
            "/ready",
        } <= paths

    def test_chat_request_forbids_unknown_fields(self, module: Any) -> None:
        assert module.ChatRequest.model_config.get("extra") == "forbid"


class TestNamsFastapiRoutes:
    """Drive the app end to end against the mocked service."""

    async def test_health_and_ready(self, module: Any) -> None:
        async with mocked_service(module) as (client, _):
            assert (await client.get("/health")).json() == {"status": "ok"}
            assert (await client.get("/ready")).json() == {"status": "ready"}

    async def test_health_reports_503_when_the_transport_is_closed(self, module: Any) -> None:
        async with mocked_service(module) as (client, _):
            await module.app.state.memory.close()
            response = await client.get("/health")
        assert response.status_code == 503

    async def test_chat_requires_credentials(self, module: Any) -> None:
        async with mocked_service(module) as (client, _):
            response = await client.post("/chat", json={"message": "hi"})
        assert response.status_code == 401
        # The insecure header alone is not identity unless the dev flag is set.
        async with mocked_service(module) as (client, _):
            response = await client.post(
                "/chat", json={"message": "hi"}, headers={"X-User-Id": USER_ID}
            )
        assert response.status_code == 401

    async def test_insecure_header_mode_is_opt_in(
        self, module: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ALLOW_INSECURE_USER_HEADER", "1")
        async with mocked_service(module) as (client, _):
            response = await client.post(
                "/chat", json={"message": "hi"}, headers={"X-User-Id": USER_ID}
            )
        assert response.status_code == 200
        assert response.json()["conversation_id"] == CONVERSATION_ID

    async def test_two_turns_search_and_count(self, module: Any) -> None:
        async with mocked_service(module) as (client, fake):
            first = await client.post("/chat", json={"message": "Hello there!"}, headers=BEARER)
            assert first.status_code == 200, first.text
            body = first.json()
            assert body["conversation_id"] == CONVERSATION_ID
            assert body["reply"] == "(no memory yet) echo: Hello there!"
            assert body["context_used"] is False
            assert uuid.UUID(body["user_message_id"])

            second = await client.post(
                "/chat",
                json={"message": "Remember my name is Alice.", "conversation_id": CONVERSATION_ID},
                headers=BEARER,
            )
            assert second.status_code == 200, second.text
            # The second turn reads the first turn back out of memory.
            assert second.json()["context_used"] is True

            # One user + one assistant message per turn, stored server-side.
            assert len(fake.messages) == 4
            detail = await client.get(f"/conversations/{CONVERSATION_ID}", headers=BEARER)
            assert detail.json() == {
                "conversation_id": CONVERSATION_ID,
                "message_count": 4,
            }

            listing = await client.get("/conversations", headers=BEARER)
            assert [c["conversation_id"] for c in listing.json()] == [CONVERSATION_ID]

            hits = await client.get(
                f"/conversations/{CONVERSATION_ID}/search",
                params={"q": "Alice"},
                headers=BEARER,
            )
            assert hits.status_code == 200, hits.text
            contents = [h["content"] for h in hits.json()]
            assert "Remember my name is Alice." in contents

    async def test_chat_rejects_an_unknown_field(self, module: Any) -> None:
        async with mocked_service(module) as (client, _):
            response = await client.post(
                "/chat", json={"message": "hi", "session_id": "demo"}, headers=BEARER
            )
        assert response.status_code == 422

    async def test_conversation_owned_by_another_user_is_404(self, module: Any) -> None:
        async with mocked_service(module) as (client, _):
            await client.post("/chat", json={"message": "mine"}, headers=BEARER)
            response = await client.post(
                "/chat",
                json={"message": "yours", "conversation_id": CONVERSATION_ID},
                headers={"Authorization": "Bearer demo-bob"},
            )
        assert response.status_code == 404


class TestNamsFastapiErrorMapping:
    """Backend failures must become typed HTTP statuses, not 500s or 502-for-all."""

    @pytest.mark.parametrize(
        ("status_code", "expected"),
        [
            (401, 500),  # our API key, not the caller's → config error
            (400, 400),  # backend rejected the request body
            (501, 501),  # unsupported on this backend
            (503, 502),  # upstream unavailable
        ],
    )
    async def test_backend_status_maps_to_http_status(
        self, module: Any, status_code: int, expected: int
    ) -> None:
        async with mocked_service(module) as (client, fake):
            fake.create_route.mock(return_value=httpx.Response(status_code, json={"error": "nope"}))
            response = await client.post("/chat", json={"message": "hi"}, headers=BEARER)
        assert response.status_code == expected
        if status_code == 400:
            # A 400 is the caller's problem, so the structured details help them.
            assert response.json()["errors"] == {"error": "nope"}
        else:
            # Everything else is ours: the backend's message stays in the log.
            assert "nope" not in response.text

    async def test_rate_limit_maps_to_429_with_retry_after(
        self, module: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MEMORY_MAX_RETRIES", "1")  # so Retry-After is captured
        async with mocked_service(module) as (client, fake):
            fake.create_route.mock(
                return_value=httpx.Response(429, headers={"Retry-After": "1"}, json={})
            )
            response = await client.post("/chat", json={"message": "hi"}, headers=BEARER)
        assert response.status_code == 429
        assert response.headers["Retry-After"] == "1"

    async def test_network_failure_maps_to_502(self, module: Any) -> None:
        async with mocked_service(module) as (client, fake):
            fake.create_route.mock(side_effect=httpx.ConnectError("connection refused"))
            response = await client.post("/chat", json={"message": "hi"}, headers=BEARER)
        assert response.status_code == 502
        assert response.json() == {"detail": "Memory backend unavailable"}


class TestNamsFastapiStartup:
    async def test_lifespan_fails_fast_without_an_api_key(
        self, module: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("MEMORY_API_KEY", raising=False)
        with pytest.raises(RuntimeError, match="MEMORY_API_KEY"):
            async with module.lifespan(module.app):
                pass  # pragma: no cover — lifespan raises before yielding
