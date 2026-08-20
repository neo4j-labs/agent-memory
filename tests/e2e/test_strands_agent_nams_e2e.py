"""End-to-end: a real Strands ``Agent`` + ``Neo4jMemoryStore`` against live NAMS.

Why this file exists
====================
Every other test of :class:`~neo4j_agent_memory.integrations.strands.memory_store.Neo4jMemoryStore`
drives the store *directly*, on one event loop. That is exactly the blind spot
that let a Critical through review: Strands' **synchronous** entry point runs
``Agent.__init__`` on one throwaway loop and every ``Agent.__call__`` on
another (``strands._async.run_async`` is ``asyncio.run`` in a worker thread),
so a store built from ``settings=`` connected its neo4j/httpx client on the
construction loop and then raised ``RuntimeError: ... attached to a different
loop`` from the first call. ``initialize()`` now rebinds an owned client when
the loop changes; nothing exercised it end-to-end until here.

So this test constructs a real ``Agent``, calls it **twice** synchronously
(construction-loop -> call-loop, then call-loop -> call-loop) and then verifies
the memory actually landed in, and came back out of, hosted NAMS.

A test, not an example
======================
It needs per-run unique names, teardown that runs on assertion failure, and a
skip gate that keeps it out of everyone else's CI. pytest gives all three for
free; a script in ``examples/`` would hand-roll them and never be run.

Running it
==========
Credentials must be in the *process* environment (``MemorySettings``' dotenv
source filters out keys that are not top-level model fields), so::

    uv run --env-file .env pytest tests/e2e/test_strands_agent_nams_e2e.py -v -s

Requires, or it skips cleanly:

* ``MEMORY_API_KEY`` (plus optional ``MEMORY_ENDPOINT`` / ``MEMORY_WORKSPACE_ID``).
* A local Ollama answering on ``OLLAMA_BASE_URL`` (default
  ``http://localhost:11434/v1``) serving a **tool-calling** model.
  ``MemoryManager`` registers ``search_memory`` plus the store's graph tools, so
  a model that rejects ``tools`` cannot drive this test at all.

Environment knobs
=================
``OLLAMA_BASE_URL`` / ``OLLAMA_MODEL_ID``
    Point the test at a different local LLM.
``NAMS_E2E_KEEP=1``
    **Skip teardown** and leave this run's conversation in the workspace, for
    inspecting it in the NAMS web UI. Unset (the default) deletes every
    conversation the run created and asserts they are gone, so the committed
    test stays well-behaved against a shared workspace.

Two things observed while building this, so nobody re-derives them
=================================================================
*Teardown is only as complete as NAMS lets it be.* ``clear_session`` deletes
the conversation; the entities NAMS extracted *from* it survive in the
workspace. ``NamsLongTermMemory`` declares a ``DELETE /entities/{id}`` endpoint
spec but exposes no ``delete_entity()`` method, so there is no public route to
remove them. Hence the deliberately disposable, run-id-suffixed entity names.

*The ``opik`` pytest plugin loads the repo-root ``.env`` into ``os.environ``.*
So under ``uv run pytest`` the credentials are present even without
``--env-file``, and the skip gate looks inert locally. To see it actually skip,
disable that plugin: ``uv run pytest ... -p no:opik``. In CI, where there is no
``.env``, the gate holds either way.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import os
import urllib.error
import urllib.request
import uuid
from collections.abc import AsyncGenerator, Iterator
from typing import Any

import pytest

pytest.importorskip("strands", reason="strands-agents not installed")

from strands import Agent
from strands.memory import (
    ExtractionConfig,
    InvocationTrigger,
    MemoryEntry,
    MemoryInjectionConfig,
    MemoryManager,
)
from strands.models.openai import OpenAIModel
from strands.types.content import Message
from strands.types.streaming import StreamEvent
from strands.types.tools import ToolSpec

from neo4j_agent_memory import MemoryClient
from neo4j_agent_memory.integrations.strands import Neo4jMemoryStore, Neo4jMemoryStoreConfig
from neo4j_agent_memory.integrations.strands.config import (
    build_nams_settings,
    resolve_nams_connection,
)
from neo4j_agent_memory.integrations.strands.memory_store import _STORE_KEY

logger = logging.getLogger(__name__)

# Not `integration`: the root conftest auto-skips that marker whenever Neo4j is
# unreachable, and this test wants NAMS + Ollama, not Neo4j.
pytestmark = pytest.mark.e2e

#: OpenAI-compatible Ollama endpoint. Overridable so this is not hard-wired to a laptop.
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1")

#: Ollama ignores the key but the OpenAI client requires a non-empty one.
OLLAMA_API_KEY = "ollama"

#: Must emit real ``tool_calls``. Verified: ``qwen3.5:9b`` does; ``gemma3:12b``
#: answers "gemma3:12b does not support tools" and cannot drive this test.
OLLAMA_MODEL_ID = os.environ.get("OLLAMA_MODEL_ID", "qwen3.5:9b")

#: Leave this run's NAMS data in place instead of tearing it down.
KEEP_NAMS_DATA = os.environ.get("NAMS_E2E_KEEP", "").strip().lower() in {"1", "true", "yes"}

#: Seconds to wait for NAMS' asynchronous, server-side extraction pipeline.
EXTRACTION_TIMEOUT = 180.0

#: Seconds any single live call may block, so a hung service cannot hang a dev box.
CALL_TIMEOUT = 240.0

#: One run's namespace. Store name is kept slug-safe so the store's tool prefix
#: equals it verbatim (see ``_store_tools._tool_prefix``) and the expected tool
#: name below needs no private helper to compute.
RUN_ID = uuid.uuid4().hex[:10]
STORE_NAME = f"e2e_{RUN_ID}"
USER_ID = f"e2e-user-{RUN_ID}"

#: What the store derives its sink conversation's ``session_id`` / metadata tag
#: from (``Neo4jMemoryStore._sink_name``). Recomputed here so a failing run
#: prints something the owner can search the workspace for.
SINK_NAME = f"strands-memory-store/{USER_ID}/{STORE_NAME}"

#: A token no other workspace data can contain, so recall assertions are about
#: *our* memory rather than a nearest-neighbour hit on someone else's.
TOKEN = f"Zorbium{RUN_ID.upper()}"

#: Seeded via ``store.add()``. Recall is asserted against this rather than
#: against the model's own turn text: NAMS extracts server-side and
#: nearest-neighbour entity search would not let us assert deterministically on
#: whatever a 9B local model happened to say.
SEEDED_MEMORY = (
    f"{TOKEN} is the codename of the quarterly planning ritual "
    f"at Contoso Robotics, run every March in Reykjavik."
)


# ---------------------------------------------------------------------------
# Skip gate
# ---------------------------------------------------------------------------


def _ollama_models() -> list[str] | None:
    """Model names Ollama serves, or ``None`` when it does not answer.

    Short timeout on purpose: a contributor without a local LLM must get a
    skip in a second, not a hang.
    """
    tags_url = OLLAMA_BASE_URL.removesuffix("/v1").rstrip("/") + "/api/tags"
    try:
        with urllib.request.urlopen(tags_url, timeout=5) as response:  # noqa: S310 - fixed localhost URL
            payload = json.loads(response.read())
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None
    models = payload.get("models") if isinstance(payload, dict) else None
    return [str(entry.get("name")) for entry in (models or []) if isinstance(entry, dict)]


def _skip_reason() -> str | None:
    """Why this test cannot run here, or ``None`` when it can."""
    if not os.environ.get("MEMORY_API_KEY"):
        return (
            "MEMORY_API_KEY is not in the process environment. Run with "
            "`uv run --env-file .env pytest ...` -- MemorySettings' dotenv source "
            "drops non-field keys, so a bare .env is not enough."
        )
    served = _ollama_models()
    if served is None:
        return f"No Ollama at {OLLAMA_BASE_URL} (checked /api/tags with a 5s timeout)."
    if OLLAMA_MODEL_ID not in served:
        return f"Ollama does not serve {OLLAMA_MODEL_ID!r}. Pull it or set OLLAMA_MODEL_ID."
    return None


@pytest.fixture(scope="module", autouse=True)
def _gate() -> None:
    reason = _skip_reason()
    if reason:
        pytest.skip(reason)


# ---------------------------------------------------------------------------
# NAMS helpers -- a client of our own, independent of the store's
# ---------------------------------------------------------------------------


def _nams_settings() -> Any:
    endpoint, api_key = resolve_nams_connection()
    return build_nams_settings(endpoint, api_key)


async def _our_conversations(client: MemoryClient) -> list[Any]:
    """Conversations this run's store created, found by its sink metadata tag.

    Matches on ``RUN_ID`` rather than on ``SINK_NAME`` verbatim, so it still
    finds the sink if the store's naming scheme changes -- both the store name
    and the user id embed ``RUN_ID``.
    """
    found = []
    for conversation in await client.short_term.list_conversations(limit=1000):
        tag = (conversation.metadata or {}).get(_STORE_KEY)
        if tag and RUN_ID in str(tag):
            found.append(conversation)
    return found


@pytest.fixture(scope="module")
def nams_teardown() -> Iterator[None]:
    """Delete every conversation this run created, then prove it is gone.

    Module-scoped and ``finally``-guarded so it runs even when an assertion
    fails mid-test -- the point is never to leave data in a shared workspace.
    ``NAMS_E2E_KEEP=1`` opts out, for inspecting the run in the NAMS web UI.
    """
    print(f"\n[run] id={RUN_ID} store={STORE_NAME} user_id={USER_ID} sink_name={SINK_NAME}")
    try:
        yield
    finally:

        async def _cleanup() -> None:
            async with MemoryClient(_nams_settings()) as client:
                doomed = await _our_conversations(client)
                ids = [str(conversation.id) for conversation in doomed]
                print(f"\n[cleanup] conversations created by run {RUN_ID}: {ids}")
                if KEEP_NAMS_DATA:
                    print("[cleanup] NAMS_E2E_KEEP=1 -- leaving the above in place.")
                    return
                for conversation_id in ids:
                    print(f"[cleanup] clear_session({conversation_id})")
                    await client.short_term.clear_session(conversation_id)
                remaining = await _our_conversations(client)
                print(f"[cleanup] remaining after delete: {[str(c.id) for c in remaining]}")
                assert remaining == [], f"cleanup left {len(remaining)} conversation(s) behind"

        asyncio.run(asyncio.wait_for(_cleanup(), timeout=CALL_TIMEOUT))


# ---------------------------------------------------------------------------
# A model that records what it was actually asked -- for the injection assertion
# ---------------------------------------------------------------------------


class RecordingModel(OpenAIModel):
    """``OpenAIModel`` that keeps a copy of every message list it is handed.

    Subclassing and overriding the public ``Model.stream`` is the supported
    extension point, so the injection assertion reads what the model really
    received rather than reaching into ``MemoryManager``'s internals.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.received: list[list[Message]] = []

    async def stream(
        self,
        messages: list[Message],
        tool_specs: list[ToolSpec] | None = None,
        system_prompt: str | None = None,
        **kwargs: Any,
    ) -> AsyncGenerator[StreamEvent, None]:
        self.received.append(copy.deepcopy(messages))
        async for event in super().stream(messages, tool_specs, system_prompt, **kwargs):
            yield event

    def all_text(self) -> str:
        """Every text block from every recorded call, concatenated."""
        chunks: list[str] = []
        for messages in self.received:
            for message in messages:
                for block in message.get("content") or []:
                    text = block.get("text") if isinstance(block, dict) else None
                    if text:
                        chunks.append(str(text))
        return "\n".join(chunks)


# ---------------------------------------------------------------------------
# The test
# ---------------------------------------------------------------------------


async def _seed_and_await_extraction(store: Neo4jMemoryStore) -> str:
    """Write the seed memory, then wait until NAMS has actually extracted it.

    Ordering matters and is the whole reason this is a separate step:
    ``MemoryManager``'s injection *fails open* -- when the store's search comes
    back empty it silently injects nothing. NAMS extracts asynchronously, so a
    seed written immediately before the first turn is not yet searchable and the
    injection assertion would fail for a reason that is not a defect. Await the
    pipeline first; only then is "did the ``<memory>`` block reach the model?" a
    meaningful question.

    Returns the sink conversation id.
    """
    written = await store.add(SEEDED_MEMORY)
    print(f"[seed] store.add -> {written}")

    async with MemoryClient(_nams_settings()) as client:
        conversations = await _our_conversations(client)
        assert len(conversations) == 1, (
            f"expected exactly one sink conversation for run {RUN_ID}, got {len(conversations)}"
        )
        sink_id = str(conversations[0].id)
        print(f"[seed] sink conversation id = {sink_id}")

        settled = await client.long_term.wait_for_extraction(
            session_id=sink_id, timeout=EXTRACTION_TIMEOUT, interval=2.0
        )
        status = await client.short_term.get_extraction_status(sink_id)
        print(f"[seed] extraction settled={settled} status={status.summary}")
        assert settled, f"NAMS extraction did not settle within {EXTRACTION_TIMEOUT}s"

        def _has_token(entities: list[Any]) -> bool:
            return any(TOKEN.lower() in (entity.name or "").lower() for entity in entities)

        searchable = await client.long_term.wait_for_extraction(
            query=TOKEN, predicate=_has_token, timeout=EXTRACTION_TIMEOUT, interval=3.0
        )
        print(f"[seed] {TOKEN} searchable={searchable}")
        assert searchable, f"{TOKEN} never became searchable in NAMS"

        entities = await client.long_term.search_entities(TOKEN, limit=10)
        print(f"[seed] entities matching {TOKEN}: {[e.name for e in entities]}")

    return sink_id


async def _verify_recall(store: Neo4jMemoryStore, sink_id: str) -> None:
    """Assertions 2 and 3: the turns landed in the sink, and recall works."""
    async with MemoryClient(_nams_settings()) as client:
        conversation = await client.short_term.get_conversation(sink_id)
        texts = [message.content for message in conversation.messages]
        print(f"[verify] {len(texts)} message(s) in the sink")
        for text in texts:
            print(f"[verify]   {text[:200]}")
        assert any(SEEDED_MEMORY in text for text in texts), "store.add() text missing from sink"
        assert any(TOKEN in text and SEEDED_MEMORY not in text for text in texts), (
            "no agent turn text in the sink -- extraction/add_messages never ran"
        )

    entries: list[MemoryEntry] = await store.search(TOKEN)
    print(f"[verify] store.search({TOKEN}) -> {len(entries)} entr(y/ies)")
    for entry in entries:
        print(f"[verify]   {entry.content} :: {entry.metadata}")
    assert entries, "store.search returned nothing"
    assert any(TOKEN.lower() in entry.content.lower() for entry in entries), (
        "store.search returned entries, but none derived from the seeded memory"
    )


def test_sync_agent_drives_nams_backed_memory_store(nams_teardown: None) -> None:
    """The whole product path, on the synchronous entry point that broke.

    Deliberately a **sync** test: ``asyncio_mode = "auto"`` would otherwise run
    the body inside a loop, and it is precisely Strands' sync entry point --
    a fresh loop per call -- that this test exists to cover. Each async
    verification step gets its own ``asyncio.run``, which incidentally
    exercises the owned-client rebind once more.
    """
    store = Neo4jMemoryStore(
        Neo4jMemoryStoreConfig(
            name=STORE_NAME,
            settings=_nams_settings(),
            user_id=USER_ID,
            # Server-side extraction: the store implements add_messages, so the
            # manager hands it the filtered turn directly -- no model call.
            extraction=ExtractionConfig(trigger=InvocationTrigger()),
            max_search_results=5,
        )
    )
    model = RecordingModel(
        client_args={"base_url": OLLAMA_BASE_URL, "api_key": OLLAMA_API_KEY},
        model_id=OLLAMA_MODEL_ID,
    )
    manager = MemoryManager(stores=[store], injection=MemoryInjectionConfig(max_entries=5))

    sink_id = asyncio.run(
        asyncio.wait_for(_seed_and_await_extraction(store), timeout=EXTRACTION_TIMEOUT * 2 + 60)
    )

    # --- 1. The synchronous path -------------------------------------------
    # Agent.__init__ initializes the store on one loop...
    agent = Agent(model=model, memory_manager=manager)
    print("[agent] constructed")

    # ...and each __call__ drives it from another. Two calls: construction-loop
    # -> call-loop, then call-loop -> call-loop.
    first = agent(f"In one sentence, what do you know about {TOKEN}?")
    print(f"[turn 1] {str(first)[:400]}")
    second = agent("And where does it happen? One short sentence.")
    print(f"[turn 2] {str(second)[:400]}")

    # --- 4. Namespaced tools ------------------------------------------------
    print(f"[tools] {sorted(agent.tool_names)}")
    assert f"{STORE_NAME}_get_entity_graph" in agent.tool_names
    # NAMS has no preferences endpoint, so the store must not ship this tool.
    assert f"{STORE_NAME}_get_user_preferences" not in agent.tool_names
    assert "search_memory" in agent.tool_names

    # --- 5. Injection reached the model ------------------------------------
    sent = model.all_text()
    assert "<memory>" in sent, "MemoryManager's default <memory> block never reached the model"
    assert TOKEN in sent, f"the seeded memory ({TOKEN}) was not among the injected entries"

    # --- 2 & 3. It landed in NAMS, and comes back out ----------------------
    asyncio.run(asyncio.wait_for(_verify_recall(store, sink_id), timeout=CALL_TIMEOUT))
    asyncio.run(store.aclose())
