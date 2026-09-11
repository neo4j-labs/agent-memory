#!/usr/bin/env python3
"""Back a Google ADK agent with neo4j-agent-memory.

Runs a real ADK loop — ``LlmAgent`` + ``Runner`` + ``InMemorySessionService`` —
with :class:`~neo4j_agent_memory.integrations.google_adk.Neo4jMemoryService` as
the ``memory_service``. Two turns in **two different ADK sessions** prove the
point: session 1 is committed to Neo4j, then session 2 (which has no
conversation history of its own) recalls it through ADK's ``load_memory`` tool.

What runs, in order:

1. **Setup** — print the resolved backend (``bolt`` or hosted ``nams``), the
   embedder and the extractor, so a degraded install is visible immediately.
2. **Turn 1** through the ``Runner``; the ADK ``Session`` is then handed to
   ``add_session_to_memory()``, which writes one ``:Message`` per event and
   extracts entities.
3. **Curate two entities** with ``long_term.add_entity()`` so they carry
   embeddings and become semantically searchable workspace-wide.
4. **Preference learning** via ``MemoryIntegration(auto_preferences=True)`` and
   ``SessionStrategy.PER_DAY`` — the library's regex ``PreferenceDetector``, no
   LLM call.
5. **Turn 2 in a fresh ADK session**, where the model calls ``load_memory`` and
   answers from Neo4j.
6. **Direct search** through ``search_memory()``, showing what is
   session-scoped (messages) and what is workspace-wide (entities,
   preferences).
7. **Session recall** with ``get_memories_for_session()``.

No Google credentials required: without ``GOOGLE_API_KEY`` (or
``GEMINI_API_KEY`` / ``GOOGLE_GENAI_USE_VERTEXAI``) the demo drives the same
``Runner`` with a small scripted model, so the ADK plumbing — including the
``load_memory`` tool call — still executes offline. Set ``ADK_MODEL`` to pick
the Gemini model when you do have a key. ``--no-agent`` skips the model
entirely and ingests a plain dict session instead.

Requirements::

    pip install "neo4j-agent-memory[google-adk]"
    # plus one embedding/extraction path:
    #   OPENAI_API_KEY=sk-...                         (hosted models)
    #   or pip install "neo4j-agent-memory[extraction,sentence-transformers]"
    #   or MEMORY_API_KEY=nams_...                    (hosted NAMS backend)

Copy ``.env.example`` to ``.env`` to set the Neo4j connection.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from collections.abc import AsyncGenerator
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import SecretStr

from neo4j_agent_memory import MemoryClient, MemorySettings, Neo4jConfig
from neo4j_agent_memory.config.settings import ExtractionConfig, ExtractorType
from neo4j_agent_memory.extraction import create_extractor, is_gliner_available
from neo4j_agent_memory.integration import MemoryIntegration, SessionStrategy
from neo4j_agent_memory.integrations.google_adk import Neo4jMemoryService

APP_NAME = "neo4j-agent-memory-adk-demo"
USER_ID = os.getenv("ADK_USER_ID", "demo-user")
# Gemini 2.5 Flash is the current default; override with ADK_MODEL.
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
LOCAL_EMBEDDING_MODEL = os.getenv("LOCAL_EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
OPENAI_EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
OPENAI_LLM_MODEL = os.getenv("MEMORY_LLM", "openai/gpt-5-mini")

RUN_ID = datetime.now().strftime("%Y%m%d%H%M%S")
SESSION_1_STATE_KEY = "demo_session_1"

TURN_1 = (
    "I'm working on Project Alpha with Sarah Chen and John Park. "
    "The deadline is next Friday and I prefer morning meetings."
)
TURN_2 = "Who am I working with on Project Alpha?"
# Phase 3 stores this through MemoryIntegration, where the regex
# PreferenceDetector turns it into a :Preference node.
PREFERENCE_TEXT = "I prefer morning meetings and I like short written status updates."
PREFERENCE_PROBE = "morning meetings"

AGENT_INSTRUCTION = (
    "You are a project assistant with long-term memory. "
    "Whenever the user asks about something they told you earlier, call the "
    "load_memory tool first and answer only from what it returns."
)


# ── Environment / settings ───────────────────────────────────────────────


def load_env() -> None:
    """Load ``.env`` from this directory (then ``examples/.env``) if present."""
    here = Path(__file__).resolve().parent
    for env_file in (here / ".env", here.parent / ".env"):
        if not env_file.exists():
            continue
        try:
            from dotenv import load_dotenv
        except ImportError:  # python-dotenv is optional
            for raw in env_file.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip().strip("\"'"))
        else:
            load_dotenv(env_file)
        print(f"Loaded environment from {env_file}")


def _local_extraction() -> ExtractionConfig:
    """A local (no-LLM) extraction pipeline, downgraded to what is installed."""
    try:
        import spacy

        has_spacy = bool(spacy.util.is_package("en_core_web_sm"))
    except ImportError:
        has_spacy = False

    has_gliner = is_gliner_available()
    if not (has_spacy or has_gliner):
        # Nothing local to extract with. NONE is explicit: better a visibly
        # empty knowledge graph than a silent NoOpExtractor fallback.
        return ExtractionConfig(extractor_type=ExtractorType.NONE, enable_llm_fallback=False)

    return ExtractionConfig(
        extractor_type=ExtractorType.PIPELINE,
        enable_spacy=has_spacy,
        enable_gliner=has_gliner,
        enable_llm_fallback=False,
    )


def build_settings() -> MemorySettings:
    """Resolve settings from the environment.

    Three paths, in priority order:

    * ``MEMORY_API_KEY`` → the hosted NAMS backend (embeddings and extraction
      run server-side).
    * ``OPENAI_API_KEY`` → bolt, with OpenAI embeddings and the LLM extraction
      fallback enabled.
    * neither → bolt, ``llm=None``, a local sentence-transformers embedder and
      a local spaCy/GLiNER pipeline, so the demo needs no API key at all.
    """
    if os.getenv("MEMORY_API_KEY"):
        # MemorySettings() reads MEMORY_API_KEY / MEMORY_ENDPOINT itself.
        return MemorySettings()

    neo4j = Neo4jConfig(
        uri=os.getenv("NEO4J_URI", "bolt://localhost:7687"),
        username=os.getenv("NEO4J_USERNAME", os.getenv("NEO4J_USER", "neo4j")),
        password=SecretStr(os.getenv("NEO4J_PASSWORD", "test-password")),
        database=os.getenv("NEO4J_DATABASE", "neo4j"),
    )

    if os.getenv("OPENAI_API_KEY"):
        return MemorySettings(
            backend="bolt",
            neo4j=neo4j,
            llm=OPENAI_LLM_MODEL,
            embedding=f"openai/{OPENAI_EMBEDDING_MODEL}",
        )

    return MemorySettings(
        backend="bolt",
        neo4j=neo4j,
        llm=None,
        embedding=LOCAL_EMBEDDING_MODEL,
        extraction=_local_extraction(),
    )


# ── The model ────────────────────────────────────────────────────────────


def _has_gemini_credentials() -> bool:
    return bool(
        os.getenv("GOOGLE_API_KEY")
        or os.getenv("GEMINI_API_KEY")
        or os.getenv("GOOGLE_GENAI_USE_VERTEXAI")
    )


def build_model() -> Any:
    """Return a Gemini model id, or a scripted stand-in when no key is set.

    ``LlmAgent(model=...)`` accepts either a model id string or a ``BaseLlm``
    instance, which is what keeps this example runnable (and CI-testable)
    without Google credentials.
    """
    if _has_gemini_credentials():
        model_id = os.getenv("ADK_MODEL", DEFAULT_GEMINI_MODEL)
        print(f"model: {model_id} (live Gemini)")
        return model_id

    from google.adk.models.base_llm import BaseLlm
    from google.adk.models.llm_request import LlmRequest
    from google.adk.models.llm_response import LlmResponse
    from google.genai import types

    class ScriptedLlm(BaseLlm):
        """Deterministic stand-in: acknowledge, then call ``load_memory``."""

        calls: int = 0

        async def generate_content_async(
            self, llm_request: LlmRequest, stream: bool = False
        ) -> AsyncGenerator[LlmResponse, None]:
            self.calls += 1
            last = ""
            for content in reversed(llm_request.contents or []):
                if content.role == "user" and content.parts:
                    last = "".join(part.text or "" for part in content.parts)
                    break

            has_tool_result = any(
                part.function_response is not None
                for content in (llm_request.contents or [])
                for part in (content.parts or [])
            )

            if has_tool_result:
                yield LlmResponse(
                    content=types.Content(
                        role="model",
                        parts=[types.Part(text="From memory: Sarah Chen and John Park.")],
                    )
                )
            elif "?" in last:
                yield LlmResponse(
                    content=types.Content(
                        role="model",
                        parts=[
                            types.Part(
                                function_call=types.FunctionCall(
                                    name="load_memory",
                                    args={"query": "Project Alpha collaborators"},
                                )
                            )
                        ],
                    )
                )
            else:
                yield LlmResponse(
                    content=types.Content(
                        role="model",
                        parts=[
                            types.Part(
                                text=(
                                    "Noted — Project Alpha with Sarah Chen and John Park, "
                                    "deadline next Friday, morning meetings preferred."
                                )
                            )
                        ],
                    )
                )

    print("model: scripted stand-in (no GOOGLE_API_KEY / GEMINI_API_KEY set)")
    return ScriptedLlm(model="scripted-demo-model")


# ── Helpers ──────────────────────────────────────────────────────────────


def entry_text(entry: Any) -> str:
    """Read the text out of an ADK ``MemoryEntry``.

    ``search_memory()`` returns a ``SearchMemoryResponse``; each
    ``response.memories`` item is an ADK ``MemoryEntry`` whose ``content`` is a
    ``google.genai.types.Content`` — a list of parts, not a string.
    """
    content = getattr(entry, "content", None)
    parts = getattr(content, "parts", None) or []
    return "".join(getattr(part, "text", None) or "" for part in parts).strip()


def describe(entry: Any) -> str:
    """One printable line per ADK memory entry."""
    meta = getattr(entry, "custom_metadata", None) or {}
    kind = meta.get("memory_type", "?")
    author = getattr(entry, "author", None) or "?"
    text = entry_text(entry).replace("\n", " ")
    return f"[{kind}/{author}] {text[:80]}"


async def print_search(
    memory_service: Neo4jMemoryService,
    query: str,
    *,
    session_id: str | None = None,
    limit: int = 5,
) -> int:
    """Run one ``search_memory`` call and print its entries."""
    scope = f"session={session_id}" if session_id else "no session scope"
    print(f"\n  query={query!r} ({scope})")
    response = await memory_service.search_memory(query=query, session_id=session_id, limit=limit)
    if not response.memories:
        print("    (no matches)")
        return 0
    for entry in response.memories:
        print(f"    {describe(entry)}")
    return len(response.memories)


def build_runner(memory_service: Neo4jMemoryService) -> tuple[Any, Any]:
    """Wire an ADK agent + ``Runner`` to this memory service.

    This is the whole integration: ``memory_service=`` on the ``Runner`` is what
    makes ``load_memory`` read from Neo4j. ``LlmAgent`` has no ``memory=``
    parameter — memory is a Runner-level service, not an agent field.
    """
    from google.adk.agents import LlmAgent
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.adk.tools import load_memory

    session_service = InMemorySessionService()
    agent = LlmAgent(
        name="memory_demo",
        model=build_model(),
        instruction=AGENT_INSTRUCTION,
        tools=[load_memory],
    )
    runner = Runner(
        app_name=APP_NAME,
        agent=agent,
        session_service=session_service,
        memory_service=memory_service,
    )
    return runner, session_service


async def run_turn(runner: Any, session_id: str, text: str) -> list[str]:
    """Drive one ADK turn and collect the model's text output.

    ``Runner.run_async()`` is an async *generator* — iterate it, never await it.
    """
    from google.genai import types

    said: list[str] = []
    async for event in runner.run_async(
        user_id=USER_ID,
        session_id=session_id,
        new_message=types.Content(role="user", parts=[types.Part(text=text)]),
    ):
        for part in (event.content.parts or []) if event.content else []:
            if part.text:
                said.append(part.text)
            if part.function_call:
                print(f"    → tool call: {part.function_call.name}({part.function_call.args})")
            if part.function_response:
                print(f"    ← tool result from {part.function_response.name}")
    return said


# ── Phases ───────────────────────────────────────────────────────────────


async def ingest_without_agent(memory_service: Neo4jMemoryService, session_id: str) -> None:
    """``--no-agent``: ingest a plain dict session, no model involved."""
    await memory_service.add_session_to_memory(
        {
            "id": session_id,
            "messages": [
                {"role": "user", "content": TURN_1},
                {
                    "role": "assistant",
                    "content": "Noted — Project Alpha with Sarah Chen and John Park.",
                },
            ],
        }
    )
    print(f"  stored dict session {session_id} (no agent)")


async def _stored_preferences(client: MemoryClient) -> list[Any]:
    """Read preferences back semantically (works on bolt and on NAMS).

    The detector picks the category itself (``work`` / ``communication`` /
    ``technology`` depending on phrasing), so searching on the preference text
    is more robust than guessing a category.
    """
    return list(await client.long_term.search_preferences(PREFERENCE_PROBE, limit=5))


async def learn_preferences(client: MemoryClient, text: str) -> None:
    """Preference learning through ``MemoryIntegration``.

    ``auto_preferences=True`` runs the library's regex ``PreferenceDetector``
    on user messages in a background task — no LLM call, no extra latency on
    the response path. ``Neo4jMemoryService`` itself does *not* detect
    preferences, so this is the layer that makes "learned preferences" real.
    """
    async with MemoryIntegration(
        client,
        session_strategy=SessionStrategy.PER_DAY,
        user_id=USER_ID,
        auto_extract=True,
        auto_preferences=True,
    ) as integration:
        session_id = integration.resolve_session_id()
        print(f"  MemoryIntegration session (PER_DAY): {session_id}")
        await integration.store_message("user", text)

        # Detection is a background task; poll briefly so the demo prints it.
        prefs: list[Any] = []
        for _ in range(20):
            prefs = await _stored_preferences(client)
            if prefs:
                break
            await asyncio.sleep(0.25)

    if prefs:
        for pref in prefs[:5]:
            print(f"  detected preference: [{pref.category}] {pref.preference}")
    else:
        print("  no preferences detected (the detector favours precision over recall)")


# ── Main ─────────────────────────────────────────────────────────────────


def describe_extractor(extractor: Any) -> str:
    """Name the extractor and its pipeline stages.

    Printed at startup so a degraded install is obvious: with none of the
    extraction extras present the pipeline silently falls back to
    ``NoOpExtractor`` and the knowledge graph stays empty.
    """
    name = type(extractor).__name__
    stages = getattr(extractor, "stages", None)
    if stages:
        labels = [getattr(stage, "name", None) or type(stage).__name__ for stage in stages]
        name += " (" + ", ".join(labels) + ")"
    return name


async def main(*, use_agent: bool = True) -> None:
    """Run the Google ADK memory demo."""
    load_env()

    settings = build_settings()
    print("=" * 66)
    print("Neo4j Agent Memory — Google ADK demo")
    print("=" * 66)

    print(f"embedding: {settings.embedding}")
    print(f"llm: {settings.llm}")

    # On NAMS, extraction and embedding run server-side — nothing to build.
    extractor = None
    if settings.backend != "nams":
        extractor = create_extractor(settings.extraction, None, settings.llm)
        print(f"extractor: {describe_extractor(extractor)}")

    async with MemoryClient(settings, extractor=extractor) as client:
        print(f"backend: {client.backend}")

        memory_service = Neo4jMemoryService(
            memory_client=client,
            user_id=USER_ID,
            include_entities=True,
            include_preferences=True,
        )

        session_1 = f"adk-demo-{RUN_ID}-1"
        session_2 = f"adk-demo-{RUN_ID}-2"

        if use_agent:
            print("\n1. First turn — ADK Runner writing into Neo4j")
        else:
            print("\n1. Ingesting a dict session (--no-agent)")
        print("-" * 66)

        runner: Any = None
        session_service: Any = None
        try:
            if use_agent:
                runner, session_service = build_runner(memory_service)
                await session_service.create_session(
                    app_name=APP_NAME, user_id=USER_ID, session_id=session_1
                )
                print(f"  user: {TURN_1}")
                for text in await run_turn(runner, session_1, TURN_1):
                    print(f"  agent: {text.strip()}")

                # Commit the finished ADK session to Neo4j. ADK keeps the turn
                # history in its own session service; this is the hand-off.
                adk_session = await session_service.get_session(
                    app_name=APP_NAME, user_id=USER_ID, session_id=session_1
                )
                await memory_service.add_session_to_memory(adk_session)
                print(f"  stored ADK session {session_1} in Neo4j")
            else:
                await ingest_without_agent(memory_service, session_1)

            print("\n2. Curating entities so they are searchable workspace-wide")
            print("-" * 66)
            # Extraction MERGEs :Entity nodes from the message text, but NER
            # gives a name and a type, not a vector. add_entity() fills in the
            # embedding and description, which is what makes semantic entity
            # recall work.
            for name, entity_type, description in (
                ("Sarah Chen", "PERSON", "Frontend lead on Project Alpha"),
                ("Project Alpha", "EVENT", "Internal project, deadline next Friday"),
            ):
                entity, dedup = await client.long_term.add_entity(
                    name, entity_type, description=description
                )
                print(f"  {entity.name} ({entity.full_type}) — dedup: {dedup.action}")

            print("\n3. Preference learning (MemoryIntegration + PreferenceDetector)")
            print("-" * 66)
            await learn_preferences(client, PREFERENCE_TEXT)

            if runner is not None:
                print("\n4. Second turn in a NEW ADK session — recall via load_memory")
                print("-" * 66)
                await session_service.create_session(
                    app_name=APP_NAME, user_id=USER_ID, session_id=session_2
                )
                print(f"  user: {TURN_2}")
                for text in await run_turn(runner, session_2, TURN_2):
                    print(f"  agent: {text.strip()}")
                print(
                    "  (the new ADK session has no history of its own — everything "
                    "the agent said came back out of Neo4j)"
                )
        finally:
            if runner is not None:
                await runner.close()

        print("\n5. Direct search through the memory service")
        print("-" * 66)
        print(
            "  Pass session_id= explicitly rather than relying on the last session\n"
            "  the service happened to write. Hosted NAMS requires it for message\n"
            "  search; on bolt today it is accepted but message search still spans\n"
            "  every session (see the README's 'Known gaps'). Entity and preference\n"
            "  recall is workspace-wide on both backends by design."
        )
        await print_search(memory_service, "Project Alpha deadline", session_id=session_1)
        # session_2 holds no messages of its own — whatever comes back here is
        # long-term memory, which is workspace-wide.
        await print_search(memory_service, "Sarah Chen", session_id=session_2)

        print("\n6. Session recall")
        print("-" * 66)
        history = await memory_service.get_memories_for_session(session_1)
        print(f"  {session_1} has {len(history)} messages")
        for entry in history:
            role = (entry.metadata or {}).get("role", "?")
            print(f"    [{role}] {entry.content[:60]}")

        print("\n" + "=" * 66)
        print("Demo complete")
        print("=" * 66)
        print("Next steps:")
        print("  • Inspect the graph in Neo4j Browser:")
        print("      MATCH (c:Conversation)-[:HAS_MESSAGE]->(m:Message)-[:MENTIONS]->(e:Entity)")
        print(f"      WHERE c.session_id = '{session_1}' RETURN *")
        print("  • Swap in a real Gemini model: export GOOGLE_API_KEY=... ADK_MODEL=gemini-2.5-pro")
        print("  • Run the same memory behind the MCP server, or on the hosted")
        print("    NAMS backend by setting MEMORY_API_KEY.")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--no-agent",
        action="store_true",
        help="skip the ADK Runner/model entirely and ingest a dict session",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = parse_args()
    asyncio.run(main(use_agent=not args.no_agent))
