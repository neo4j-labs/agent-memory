"""The app's memory client, and swapping it after an ontology activation.

A bolt client resolves its ontology **once, at connect time**: the GLiNER2.5
schema, relation validation, the strict write paths and the labels new nodes
get all come from the document it resolved then. ``client.ontology.activate()``
changes what the *database* has bound, not what a connected client extracts
against. So after every activation :class:`MemoryService` connects a new
client, swaps it in under a lock, and closes the old one after a grace period
(a request that started before the swap may still be using it).

The settings object is built once and reused for every connection, so the
sentence-transformers embedder it carries loads its weights once per process.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from datetime import date, datetime
from typing import Any
from uuid import UUID

from neo4j_agent_memory import BoltMemoryClient, Neo4jConfig, connect
from neo4j_agent_memory.config.settings import (
    BoltSettings,
    ExtractionConfig,
    ExtractorType,
    SchemaConfig,
)
from src.config import Settings

logger = logging.getLogger(__name__)

#: How long a replaced client stays open for requests that still hold it.
RETIRE_GRACE_SECONDS = 30.0


class MemoryUnavailableError(RuntimeError):
    """Neo4j could not be reached when the app started (see ``/api/health``)."""


def build_memory_settings(settings: Settings) -> BoltSettings:
    """Keyless bolt settings whose ontology is whatever the database has active.

    The same shape as ``examples/ontology-lifecycle-bolt``: no ontology file or
    template, so ``use_active_ontology=True`` makes the client adopt the
    version this database has active when it connects.
    """
    return BoltSettings(
        neo4j=Neo4jConfig(
            uri=settings.neo4j_uri,
            username=settings.neo4j_username,
            password=settings.neo4j_password,
            database=settings.neo4j_database,
        ),
        llm=None,
        embedding=settings.local_embedding_model,
        schema_config=SchemaConfig(use_active_ontology=True),
        extraction=ExtractionConfig(
            extractor_type=ExtractorType.PIPELINE,
            enable_spacy=False,
            enable_gliner=True,
            enable_llm_fallback=False,
        ),
    )


def resolved_binding(client: BoltMemoryClient) -> dict[str, Any]:
    """What a connected client resolved: ``{"domain_id", "validation_mode"}``."""
    document = client.ontology_document
    return {
        "domain_id": document.domain.id if document is not None else None,
        "validation_mode": client.validation_mode,
    }


class MemoryService:
    """Owns the connected client; :meth:`reconnect` swaps in a fresh one."""

    def __init__(self, settings: Settings) -> None:
        self._memory_settings = build_memory_settings(settings)
        self._client: BoltMemoryClient | None = None
        self._lock = asyncio.Lock()
        self._retiring: dict[asyncio.Task[None], BoltMemoryClient] = {}
        self.error: str | None = None

    @property
    def connected(self) -> bool:
        return self._client is not None

    @property
    def client(self) -> BoltMemoryClient:
        """The current client. Read it per request; it changes on reconnect."""
        if self._client is None:
            raise MemoryUnavailableError(self.error or "The memory client is not connected.")
        return self._client

    def resolved(self) -> dict[str, Any]:
        """What the app's current client resolved (``domain_id`` + ``validation_mode``)."""
        if self._client is None:
            return {"domain_id": None, "validation_mode": None}
        return resolved_binding(self._client)

    async def connect(self) -> None:
        """Connect the first client. Failures are recorded, not raised."""
        try:
            self._client = await connect(self._memory_settings)
            self.error = None
        except Exception as exc:  # the API still starts; /health reports it
            self.error = f"{type(exc).__name__}: {exc}"
            logger.error("Could not connect to Neo4j: %s", self.error)

    async def get_client(self) -> BoltMemoryClient:
        """The current client, connecting first if Neo4j was down at startup."""
        if self._client is None:
            async with self._lock:
                if self._client is None:
                    await self.connect()
        return self.client

    async def reconnect(self) -> BoltMemoryClient:
        """Connect a new client (it resolves the now-active ontology) and swap it in."""
        async with self._lock:
            fresh = await connect(self._memory_settings)
            previous, self._client = self._client, fresh
            self.error = None
        if previous is not None:
            task = asyncio.create_task(self._retire(previous))
            self._retiring[task] = previous
            task.add_done_callback(lambda done: self._retiring.pop(done, None))
        return fresh

    @staticmethod
    async def _retire(client: BoltMemoryClient) -> None:
        await asyncio.sleep(RETIRE_GRACE_SECONDS)
        await _close_quietly(client)

    async def close(self) -> None:
        """Close the current client and every retired one still in its grace period."""
        for task, retired in list(self._retiring.items()):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            await _close_quietly(retired)
        self._retiring.clear()
        if self._client is not None:
            await _close_quietly(self._client)
            self._client = None


async def _close_quietly(client: BoltMemoryClient) -> None:
    """Close ``client``; a failure here is logged, never raised."""
    try:
        await client.close()
    except Exception:
        logger.warning("Could not close a memory client", exc_info=True)


def jsonable(value: Any) -> Any:
    """Turn a value read from Neo4j into JSON-safe data.

    Neo4j temporal values become ISO-8601 strings, UUIDs strings, and models
    their ``model_dump()``. Embedding vectors are dropped from maps: they are
    large and mean nothing in a UI.
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    to_native = getattr(value, "to_native", None)
    if callable(to_native):  # neo4j.time.DateTime / Date / Time / Duration
        return jsonable(to_native())
    if isinstance(value, dict):
        return {
            str(key): jsonable(item) for key, item in value.items() if "embedding" not in str(key)
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [jsonable(item) for item in value]
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return jsonable(model_dump(mode="json"))
    return str(value)


def iso(value: Any) -> str | None:
    """An ISO-8601 string for a Neo4j or Python datetime, or ``None``."""
    converted = jsonable(value)
    return converted if isinstance(converted, str) else None


def parse_json_map(raw: Any) -> dict[str, Any]:
    """Parse a JSON-serialised map property (``metadata``, ``metrics_json``)."""
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}
