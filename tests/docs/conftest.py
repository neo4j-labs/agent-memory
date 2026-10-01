"""Pytest fixtures for documentation tests."""

from __future__ import annotations

from pathlib import Path

import pytest


def get_project_root() -> Path:
    """Get the project root directory."""
    # tests/docs/conftest.py -> project_root
    return Path(__file__).parent.parent.parent


def get_docs_root() -> Path:
    """Get the docs root directory (contains antora.yml)."""
    return get_project_root() / "docs"


def get_pages_dir() -> Path:
    """Get the Antora pages directory (contains content files)."""
    return get_docs_root() / "modules" / "ROOT" / "pages"


def get_module_root() -> Path:
    """Get the Antora module root (contains nav.adoc)."""
    return get_docs_root() / "modules" / "ROOT"


@pytest.fixture(scope="session")
def project_root() -> Path:
    """Fixture providing the project root directory."""
    return get_project_root()


@pytest.fixture(scope="session")
def docs_dir() -> Path:
    """Fixture providing the docs pages directory (where content files live)."""
    pages = get_pages_dir()
    if not pages.exists():
        pytest.skip("Docs pages directory not found")
    return pages


@pytest.fixture(scope="session")
def docs_root() -> Path:
    """Fixture providing the docs root directory."""
    return get_docs_root()


@pytest.fixture(scope="session")
def site_dir() -> Path:
    """Fixture providing the built site directory (Antora output)."""
    return get_docs_root() / "build" / "site"


@pytest.fixture(scope="session")
def all_adoc_files(docs_dir: Path) -> list[Path]:
    """Fixture providing all AsciiDoc files in docs (pages + nav.adoc)."""
    files = []
    # Get all page files
    for adoc_file in docs_dir.rglob("*.adoc"):
        files.append(adoc_file)
    # Also include nav.adoc from module root
    nav_file = get_module_root() / "nav.adoc"
    if nav_file.exists():
        files.append(nav_file)
    return sorted(files)


@pytest.fixture(scope="session")
def quadrant_dirs(docs_dir: Path) -> dict[str, Path]:
    """Fixture providing paths to Diataxis quadrant directories."""
    return {
        "tutorials": docs_dir / "tutorials",
        "how-to": docs_dir / "how-to",
        "reference": docs_dir / "reference",
        "explanation": docs_dir / "explanation",
    }


@pytest.fixture(scope="session")
def python_snippets(docs_dir: Path):
    """Fixture providing all Python code snippets from docs pages."""
    from tests.docs.utils import extract_python_snippets

    return extract_python_snippets(docs_dir)


@pytest.fixture(scope="session")
def complete_snippets(python_snippets):
    """Fixture providing only complete (runnable) Python snippets."""
    return [s for s in python_snippets if s.is_complete]


# =============================================================================
# Live runs of the maintained docs programs (docs/modules/ROOT/examples)
# =============================================================================

EXAMPLES_DIR = get_module_root() / "examples"

# Fictional facts the tutorials and recipes write about. The stub LLM below
# "extracts" exactly these, so a run is deterministic and needs no API key.
KNOWN_ENTITIES = {
    "Maya Chen": "PERSON",
    "Ravi Shah": "PERSON",
    "Northstar Robotics": "ORGANIZATION",
    "Summit Research": "ORGANIZATION",
    "Denver": "LOCATION",
    "Boulder": "LOCATION",
}
KNOWN_RELATIONS = [
    ("Maya Chen", "is CEO of", "Northstar Robotics", "CEO_OF"),
    ("Maya Chen", "works at", "Northstar Robotics", "WORKS_AT"),
    ("Northstar Robotics", "is in", "Denver", "LOCATED_IN"),
    ("Northstar Robotics", "partners with", "Summit Research", "PARTNER_OF"),
    ("Ravi Shah", "works at", "Summit Research", "WORKS_AT"),
    ("Ravi Shah", "joined", "Summit Research", "WORKS_AT"),
]


class LocalEmbeddingProvider:
    """Deterministic ``EmbeddingProvider`` sized like ``openai/text-embedding-3-small``.

    It keeps the vector indexes at 1536 dimensions, the size the other
    integration tests use, so docs runs can share their database.
    """

    model = "docs-test/token-bag"
    dimensions = 1536

    def __init__(self) -> None:
        from tests.conftest import MockEmbedder

        self._embedder = MockEmbedder(self.dimensions)

    async def embed(self, texts):
        return [await self._embedder.embed(text) for text in texts]

    async def embed_one(self, text):
        return await self._embedder.embed(text)


class StubExtractionLLM:
    """``LLMProvider`` and ``StructuredExtractor`` that extracts ``KNOWN_*`` facts."""

    model = "docs-test/stub-llm"

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def _payload(self, messages) -> dict:
        text = "\n".join(message.content for message in messages)
        self.prompts.append(text)
        return {
            "entities": [
                {"name": name, "type": entity_type, "confidence": 0.95}
                for name, entity_type in KNOWN_ENTITIES.items()
                if name in text
            ],
            "relations": [
                {"source": source, "target": target, "relation_type": kind, "confidence": 0.9}
                for source, phrase, target, kind in KNOWN_RELATIONS
                if f"{source} {phrase} {target}" in text
            ],
            "preferences": [],
        }

    async def complete(self, messages, **_kwargs):
        import json

        from neo4j_agent_memory.llm.types import Completion

        return Completion(content=json.dumps(self._payload(messages)), model=self.model)

    async def complete_structured(self, messages, response_model, **_kwargs):
        return response_model.model_validate(self._payload(messages))


class StubChatClient:
    """Stands in for ``openai.AsyncOpenAI`` in the phases that call a chat model."""

    def __init__(self, *_args, **_kwargs) -> None:
        from types import SimpleNamespace

        self.requests: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, *, model, messages, **_kwargs):
        from types import SimpleNamespace

        self.requests.append({"model": model, "messages": messages})
        reply = "Based on the saved context, Trail Starter fits a wide-fit budget."
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=reply))])

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False


class LivePrograms:
    """Imports docs programs with their Aura connection pointed at the test Neo4j."""

    def __init__(self, connection: dict, monkeypatch) -> None:
        self.connection = connection
        self.llm = StubExtractionLLM()
        self.chat = StubChatClient()
        self._monkeypatch = monkeypatch

    def load(self, name: str):
        """Import a program and point every imported ``aura_config`` at the test database."""
        import importlib
        import sys

        module = importlib.import_module(name)
        connection = importlib.import_module("aura_connection")
        examples = EXAMPLES_DIR.resolve()
        for loaded in list(sys.modules.values()):
            # Only docs programs are patched, and vars() avoids firing the lazy
            # module __getattr__ hooks of third-party packages (transformers
            # imports optional backends such as torchvision from them).
            # aura_connection itself stays unpatched, so later imports still match.
            try:
                namespace = vars(loaded)
            except TypeError:  # sys.modules may hold objects without a __dict__.
                continue
            path = namespace.get("__file__")
            if (
                loaded is connection
                or not isinstance(path, str)
                or not Path(path).resolve().is_relative_to(examples)
            ):
                continue
            if namespace.get("aura_config") is connection.aura_config:
                self._monkeypatch.setattr(loaded, "aura_config", lambda: dict(self.connection))
        return module

    async def run(self, module, *argv: str):
        """Run ``module.main()`` with the command line a reader would type."""
        import inspect
        import sys

        self._monkeypatch.setattr(sys, "argv", [f"{module.__name__}.py", *argv])
        result = module.main()
        if inspect.isawaitable(result):
            await result

    def memory_client(self):
        """A client built from the tutorials' shared ``core_memory_settings``."""
        from neo4j_agent_memory import MemoryClient

        return MemoryClient(self.load("core_memory_settings").settings())

    def wipe(self) -> None:
        """Empty the database; the tutorials use fixed session names."""
        from neo4j import GraphDatabase

        auth = (self.connection["username"], self.connection["password"])
        with GraphDatabase.driver(self.connection["uri"], auth=auth) as driver:
            driver.execute_query("MATCH (n) DETACH DELETE n")


@pytest.fixture
def live_programs(neo4j_connection_info, monkeypatch, tmp_path) -> LivePrograms:
    """Run maintained docs programs against the Neo4j test database.

    Only the connection, the embedding provider and chat or extraction model
    calls are replaced. Every SDK call the programs make runs for real.
    """
    connection = {
        "uri": neo4j_connection_info["uri"],
        "username": neo4j_connection_info["username"],
        "password": neo4j_connection_info["password"],
        "database": "neo4j",
    }
    programs = LivePrograms(connection, monkeypatch)
    monkeypatch.syspath_prepend(str(EXAMPLES_DIR / "integrations"))
    monkeypatch.syspath_prepend(str(EXAMPLES_DIR))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("NEO4J_URI", connection["uri"])
    monkeypatch.setenv("NEO4J_USERNAME", connection["username"])
    monkeypatch.setenv("NEO4J_PASSWORD", connection["password"])
    monkeypatch.setenv("NEO4J_DATABASE", connection["database"])
    monkeypatch.setenv("OPENAI_MODEL", "docs-test-model")
    monkeypatch.setenv("ANTHROPIC_MODEL", "docs-test-model")

    def from_provider(spec, kind="llm", **_kwargs):
        return LocalEmbeddingProvider() if kind == "embedding" else programs.llm

    monkeypatch.setattr("neo4j_agent_memory.llm.from_provider", from_provider)
    try:
        import openai
    except ImportError:
        pass
    else:
        monkeypatch.setattr(openai, "AsyncOpenAI", lambda *_a, **_k: programs.chat)
    return programs
