"""NAMS + FastAPI — the production wiring for a memory-backed HTTP service.

What this shows, and why each piece is here:

* **One lifespan-managed client.** ``MemoryClient`` owns an HTTP connection
  pool; opening it once per process (not once per request) is the single most
  important production detail.
* **Honest tenancy.** NAMS scopes a *workspace* by API key. Scoping a *user*
  is the application's job: the caller's id comes from a verified credential,
  the conversation is created under that ``userId``, and ownership is
  re-checked on every request. NAMS silently ignores ``user_identifier`` on
  ``add_message``, so this example does not pass it there.
* **An error taxonomy.** App-level handlers map the library's exceptions onto
  HTTP status codes (429 with ``Retry-After``, 400, 404, 501, 502) and log the
  backend message server-side instead of echoing it to the caller.
* **A real turn.** ``/chat`` reads assembled context back out of memory,
  hands it to the agent stub, and stores the assistant reply — so memory is
  doing work, not just accumulating writes.
* **Liveness vs readiness.** ``/health`` does no I/O; ``/ready`` makes one
  cheap authenticated NAMS call.

Run:

    cp .env.example .env      # set MEMORY_API_KEY
    uv pip install -r requirements.txt
    uv run uvicorn main:app --reload

See the README for the two-step curl, the auth modes, and the production notes.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

from neo4j_agent_memory import (
    AuthenticationError,
    NamsConfig,
    NamsMemoryClient,
    NamsSettings,
    NotFoundError,
    NotSupportedError,
    RateLimitError,
    TransportError,
    connect,
)
from neo4j_agent_memory import MemoryError as MemoryBackendError

# The library's ValidationError is *not* pydantic's. Aliasing it keeps the two
# distinguishable in a module that also imports pydantic — FastAPI registers a
# handler for pydantic's, and shadowing that name would be a silent regression.
from neo4j_agent_memory import ValidationError as MemoryValidationError

logger = logging.getLogger("nams_fastapi")


def load_env() -> None:
    """Load this directory's ``.env`` so the documented setup step has an effect.

    Existing process environment wins, which is what you want in a container:
    the ``.env`` file is a developer convenience, not a config source that can
    override what the orchestrator injected.
    """
    env_file = Path(__file__).parent / ".env"
    if not env_file.exists():
        return
    try:
        from dotenv import load_dotenv
    except ImportError:  # python-dotenv is optional; parse the file ourselves
        for raw in env_file.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))
    else:
        load_dotenv(env_file)


load_env()


# --------------------------------------------------------------------- lifespan


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Open one shared MemoryClient for the whole app lifetime.

    A single HTTP transport pool is reused across every request, so there is no
    per-request connection churn, and ``close()`` in ``finally`` drains it on
    shutdown (uvicorn runs this on SIGTERM).
    """
    if not os.environ.get("MEMORY_API_KEY"):
        # Fail at boot, not on the first request: a misconfigured deployment
        # never reaches the load balancer's healthy set.
        raise RuntimeError(
            "Set MEMORY_API_KEY to your NAMS API key. Sign up at https://memory.neo4jlabs.com."
        )

    # The knobs a real deployment tunes. MEMORY_API_KEY, MEMORY_ENDPOINT and
    # MEMORY_WORKSPACE_ID are picked up from the environment by NamsSettings.
    settings = NamsSettings(
        nams=NamsConfig(
            timeout=float(os.getenv("MEMORY_TIMEOUT", "30")),
            max_retries=int(os.getenv("MEMORY_MAX_RETRIES", "3")),
            # Costs one request at boot and turns bad credentials into a failed
            # start instead of a 500 on the first user request. Disable it for
            # serverless cold starts where boot latency is user-visible.
            validate_on_connect=os.getenv("MEMORY_VALIDATE_ON_CONNECT", "1") != "0",
        )
    )

    # `connect()` returns an already-connected, NAMS-typed client, so the
    # hosted-backend methods are statically visible. We own the lifecycle.
    client = await connect(settings)
    logger.info(
        "NAMS connected: endpoint=%s workspace=%s",
        settings.nams.endpoint,
        settings.nams.workspace_id or "(scoped by API key)",
    )
    app.state.memory = client
    try:
        yield
    finally:
        await client.close()
        app.state.memory = None


app = FastAPI(
    title="NAMS + FastAPI",
    summary="Conversation memory for an HTTP service, backed by the hosted NAMS.",
    lifespan=lifespan,
)


# ------------------------------------------------------------------ dependencies


def get_memory(request: Request) -> NamsMemoryClient:
    """Dependency: the process-wide MemoryClient, read off the live app.

    Reading ``request.app`` rather than the module-global ``app`` keeps this
    working when the app is built by a factory or mounted as a sub-app.
    """
    memory: NamsMemoryClient | None = getattr(request.app.state, "memory", None)
    if memory is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Memory backend not initialised",
        )
    return memory


bearer_scheme = HTTPBearer(auto_error=False, description="Bearer token for the calling user")


def insecure_user_header_enabled() -> bool:
    """Whether the unauthenticated ``X-User-Id`` dev path is allowed."""
    return os.getenv("ALLOW_INSECURE_USER_HEADER") == "1"


def verify_token(token: str) -> str:
    """STUB — return the subject of a verified credential.

    In production this is your JWT/session verification: check the signature,
    issuer, audience and expiry, then return the subject claim. **Never** trust
    a client-supplied tenant id. To keep the example runnable without an
    identity provider, this stub accepts ``demo-<user-id>`` tokens.
    """
    subject = token.removeprefix("demo-") if token.startswith("demo-") else ""
    if not subject:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return subject


async def current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)] = None,
    insecure_user_id: Annotated[str | None, Header(alias="X-User-Id")] = None,
) -> str:
    """The tenant key for this request, derived from a verified credential.

    Set ``ALLOW_INSECURE_USER_HEADER=1`` to also accept a plain ``X-User-Id``
    header — convenient for a curl-driven demo, never acceptable in production,
    and logged as a warning every time it is used.
    """
    if credentials is not None:
        return verify_token(credentials.credentials)
    if insecure_user_id and insecure_user_header_enabled():
        logger.warning(
            "Unauthenticated X-User-Id=%r accepted (ALLOW_INSECURE_USER_HEADER=1)",
            insecure_user_id,
        )
        return insecure_user_id
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Missing bearer credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )


MemoryDep = Annotated[NamsMemoryClient, Depends(get_memory)]
UserDep = Annotated[str, Depends(current_user)]


# ----------------------------------------------------------------------- models


class ChatRequest(BaseModel):
    """Per-turn request shape.

    ``extra="forbid"`` so a mistyped field (``session_id`` instead of
    ``conversation_id``, say) is a 422 rather than a silently new conversation
    on every call.
    """

    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, description="The user's turn.")
    conversation_id: str | None = Field(
        default=None,
        description="Omit on the first call; send the id the first call returned afterwards.",
    )


class ChatResponse(BaseModel):
    """One completed turn."""

    conversation_id: str
    reply: str
    user_message_id: str
    context_used: bool = Field(description="Whether memory returned any context for this turn.")


class ConversationInfo(BaseModel):
    """One of the caller's conversations."""

    conversation_id: str
    created_at: str | None = None


class ConversationDetail(BaseModel):
    """Conversation header plus its message count (two NAMS reads)."""

    conversation_id: str
    message_count: int


class MessageHit(BaseModel):
    """A conversation-scoped search hit."""

    message_id: str
    role: str
    content: str


# ------------------------------------------------------------------- agent stub


def generate_reply(context: str, message: str) -> str:
    """Stand-in for your agent call.

    Replace the body with your LLM/agent invocation: ``context`` is the memory
    half of the prompt (NAMS assembles reflections, observations and recent
    messages server-side), ``message`` is the current turn. Keeping it a pure
    function means this example needs no model key.
    """
    remembered = "with memory" if context.strip() else "no memory yet"
    return f"({remembered}) echo: {message}"


# ----------------------------------------------------------------- conversations


async def owned_conversation_ids(memory: NamsMemoryClient, user_id: str) -> set[str]:
    """Conversation ids NAMS has recorded under this caller's ``userId``.

    One request per call, and capped at 100: a tenant with more conversations
    than that would see a legitimate id rejected. A high-volume service should
    keep its own user→conversation mapping (in its own database, or in the
    session) and use this only to verify a single id rather than to enumerate.
    """
    conversations = await memory.short_term.list_conversations(user_identifier=user_id, limit=100)
    return {str(conversation.id) for conversation in conversations}


async def assert_owned(memory: NamsMemoryClient, user_id: str, conversation_id: str) -> None:
    """404 unless this caller owns ``conversation_id``.

    Re-asserted on *every* request: a conversation id is not a credential, so
    ownership established at create time has to be checked again on each use.
    """
    if conversation_id not in await owned_conversation_ids(memory, user_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Unknown conversation for this user",
        )


async def resolve_conversation(
    memory: NamsMemoryClient, user_id: str, requested_id: str | None
) -> str:
    """Return a conversation id this caller owns, creating one when needed.

    ``create_conversation`` is where NAMS *does* honour ownership: the
    ``user_identifier`` kwarg is sent as ``userId``, and the server mints the
    canonical conversation id we return to the client.
    """
    if requested_id is not None:
        await assert_owned(memory, user_id, requested_id)
        return requested_id

    conversation = await memory.short_term.create_conversation(
        f"chat-{user_id}", user_identifier=user_id
    )
    return str(conversation.id)


# ----------------------------------------------------------------------- routes


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest, memory: MemoryDep, user_id: UserDep) -> ChatResponse:
    """One turn: read context, store the user message, reply, store the reply."""
    if not request.message.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Empty message")

    conversation_id = await resolve_conversation(memory, user_id, request.conversation_id)

    # The assembled three-tier read (reflections + observations + recent
    # messages). NAMS's endpoint takes no query — it always returns the same
    # view for the conversation — so `request.message` is passed only for
    # signature parity with the bolt backend, which does use it.
    context = await memory.short_term.get_context(request.message, session_id=conversation_id)

    # No `user_identifier=` here: NAMS accepts only {content, role} on this
    # endpoint and drops anything else silently. The conversation already
    # carries the caller's userId from create_conversation().
    user_message = await memory.short_term.add_message(conversation_id, "user", request.message)

    reply = generate_reply(context, request.message)
    await memory.short_term.add_message(conversation_id, "assistant", reply)

    return ChatResponse(
        conversation_id=conversation_id,
        reply=reply,
        user_message_id=str(user_message.id),
        context_used=bool(context.strip()),
    )


@app.get("/conversations", response_model=list[ConversationInfo])
async def list_conversations(memory: MemoryDep, user_id: UserDep) -> list[ConversationInfo]:
    """The caller's conversations — how a returning client finds its id again."""
    conversations = await memory.short_term.list_conversations(user_identifier=user_id, limit=50)
    return [
        ConversationInfo(
            conversation_id=str(conversation.id),
            created_at=conversation.created_at.isoformat() if conversation.created_at else None,
        )
        for conversation in conversations
    ]


@app.get("/conversations/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(
    conversation_id: str, memory: MemoryDep, user_id: UserDep
) -> ConversationDetail:
    """Message count for one conversation.

    Deliberately *not* part of ``/chat``: ``get_conversation`` costs two NAMS
    requests (header + messages) and materialises the whole history, which is
    the point on this route and pure overhead on the hot path.
    """
    await assert_owned(memory, user_id, conversation_id)
    conversation = await memory.short_term.get_conversation(conversation_id)
    return ConversationDetail(
        conversation_id=conversation_id,
        message_count=len(conversation.messages),
    )


@app.get("/conversations/{conversation_id}/search", response_model=list[MessageHit])
async def search_conversation(
    conversation_id: str,
    memory: MemoryDep,
    user_id: UserDep,
    q: Annotated[str, Query(min_length=1, description="Search query")],
    limit: Annotated[int, Query(ge=1, le=50)] = 5,
) -> list[MessageHit]:
    """Semantic recall inside one conversation (NAMS search is conversation-scoped)."""
    await assert_owned(memory, user_id, conversation_id)
    hits = await memory.short_term.search_messages(q, session_id=conversation_id, limit=limit)
    return [
        MessageHit(message_id=str(hit.id), role=hit.role.value, content=hit.content) for hit in hits
    ]


@app.get("/health")
async def health(memory: MemoryDep) -> dict[str, str]:
    """Liveness — no I/O. 503 when the transport is closed, so probes drain us."""
    if not memory.is_connected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Memory backend disconnected",
        )
    return {"status": "ok"}


@app.get("/ready")
async def ready(memory: MemoryDep) -> dict[str, str]:
    """Readiness — one cheap authenticated NAMS call.

    Failures land in the typed handlers below (401/403 → 500, network → 502),
    so a bad key or an unreachable backend keeps this instance out of rotation.
    """
    await memory.short_term.list_conversations(limit=1)
    return {"status": "ready"}


# ------------------------------------------------------------- error taxonomy
#
# App-level handlers, not a per-route `try/except`: every route (including the
# read paths) is covered, and the backend's message is logged rather than
# echoed to the caller — it can carry endpoint and configuration detail.


def _log_backend_error(request: Request, exc: Exception) -> None:
    logger.error(
        "memory backend error on %s %s: %s",
        request.method,
        request.url.path,
        exc,
        exc_info=True,
    )


@app.exception_handler(RateLimitError)
async def handle_rate_limit(request: Request, exc: RateLimitError) -> JSONResponse:
    """429, passing the server's own ``Retry-After`` through to the caller."""
    _log_backend_error(request, exc)
    headers = {"Retry-After": str(int(exc.retry_after))} if exc.retry_after else {}
    return JSONResponse(
        {"detail": "Upstream rate limit — retry later"},
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        headers=headers,
    )


@app.exception_handler(AuthenticationError)
async def handle_auth_error(request: Request, exc: AuthenticationError) -> JSONResponse:
    """Our credentials, not the caller's: a config error, so 500 (never 401)."""
    _log_backend_error(request, exc)
    return JSONResponse(
        {"detail": "Memory backend credentials rejected"},
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
    )


@app.exception_handler(MemoryValidationError)
async def handle_validation_error(request: Request, exc: MemoryValidationError) -> JSONResponse:
    """400 — the backend rejected the request body; its details are structured."""
    _log_backend_error(request, exc)
    return JSONResponse(
        {"detail": "Memory backend rejected the request", "errors": exc.details},
        status_code=status.HTTP_400_BAD_REQUEST,
    )


@app.exception_handler(NotFoundError)
async def handle_not_found(request: Request, exc: NotFoundError) -> JSONResponse:
    """404 — the conversation or entity is gone on the backend."""
    _log_backend_error(request, exc)
    return JSONResponse({"detail": "Not found"}, status_code=status.HTTP_404_NOT_FOUND)


@app.exception_handler(NotSupportedError)
async def handle_not_supported(request: Request, exc: NotSupportedError) -> JSONResponse:
    """501 — a bolt-only call reached the hosted backend (or vice versa)."""
    _log_backend_error(request, exc)
    return JSONResponse(
        {"detail": f"{exc.method} is not supported on the {exc.backend} backend"},
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
    )


@app.exception_handler(TransportError)
async def handle_transport_error(request: Request, exc: TransportError) -> JSONResponse:
    """502 — network failure or 5xx that survived the transport's retries."""
    _log_backend_error(request, exc)
    return JSONResponse(
        {"detail": "Memory backend unavailable"}, status_code=status.HTTP_502_BAD_GATEWAY
    )


@app.exception_handler(MemoryBackendError)
async def handle_memory_error(request: Request, exc: MemoryBackendError) -> JSONResponse:
    """Catch-all for the library's base error, so nothing leaks a stack trace."""
    _log_backend_error(request, exc)
    return JSONResponse({"detail": "Memory backend error"}, status_code=status.HTTP_502_BAD_GATEWAY)


if __name__ == "__main__":
    # The canonical entry point is `uvicorn main:app --reload`; this keeps
    # `python main.py` working with uvicorn's own signal handling intact.
    import uvicorn

    uvicorn.run("main:app", host="127.0.0.1", port=8000)
