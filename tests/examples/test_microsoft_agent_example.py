"""Validation tests for the microsoft_agent_retail_assistant example.

These tests validate that the example app:
- Has correct structure
- Has required files
- Python files have valid syntax
- Key imports are present
- Declares dependency ranges that can actually resolve to something importable
- Does not reach for library attributes that do not exist

Note: These are NOT runtime tests. Running the full app requires separate
infrastructure (Neo4j, an OpenAI or Azure OpenAI key, and the
``agent-framework-openai`` chat-client distribution). ``backend/smoke_check.py``
is the live-server check; it is deliberately not named ``test_*`` so pytest
does not collect it.
"""

import ast
import io
import re
import tokenize
from pathlib import Path

import pytest

from neo4j_agent_memory import MemoryClient
from tests.examples._manifests import assert_library_pin

EXAMPLES_DIR = Path(__file__).parent.parent.parent / "examples"
APP_DIR = EXAMPLES_DIR / "microsoft_agent_retail_assistant"
BACKEND_DIR = APP_DIR / "backend"

#: Floor the library's [microsoft-agent] extra declares for the Agent Framework.
#: Keep in step with ``MICROSOFT_AGENT_FRAMEWORK_MIN_VERSION``. The example
#: needs ``agent-framework-openai`` too, which itself requires core >= 1.17.
MIN_AGENT_FRAMEWORK_PIN = "1.17"
#: Floor for the chat-client distribution that provides ``OpenAIChatClient``.
MIN_AGENT_FRAMEWORK_OPENAI_PIN = "1.14"

#: Backend python files, relative to ``backend/``.
BACKEND_PY_FILES = [
    "main.py",
    "agent.py",
    "memory_config.py",
    "smoke_check.py",
    "data/load_products.py",
    "tools/__init__.py",
    "tools/product_search.py",
    "tools/recommendations.py",
    "tools/inventory.py",
    "tools/cart.py",
]


def _backend_sources() -> dict[str, str]:
    """Every backend Python file's source, keyed by relative path."""
    return {
        name: (BACKEND_DIR / name).read_text(encoding="utf-8")
        for name in BACKEND_PY_FILES
        if (BACKEND_DIR / name).exists()
    }


def _code_only(source: str) -> str:
    """Drop comments, docstrings and string literals from ``source``.

    The checks below look for *code* that does something wrong. Without this,
    a docstring that explains "do not call ``client.embeddings``" would trip
    the very assertion that documents it.
    """
    pieces: list[str] = []
    try:
        tokens = tokenize.generate_tokens(io.StringIO(source).readline)
        for token in tokens:
            if token.type in (tokenize.COMMENT, tokenize.STRING):
                continue
            pieces.append(token.string)
    except tokenize.TokenError:  # pragma: no cover - syntax is asserted elsewhere
        return source
    return "\n".join(pieces)


def _backend_code() -> dict[str, str]:
    """Backend sources with comments and string literals removed."""
    return {name: _code_only(source) for name, source in _backend_sources().items()}


class TestMicrosoftAgentExampleStructure:
    """Validate directory structure of the Microsoft Agent retail assistant."""

    def test_app_directory_exists(self):
        """Verify the example directory exists."""
        assert APP_DIR.exists(), f"Example directory not found: {APP_DIR}"

    def test_readme_exists(self):
        """Verify README.md exists."""
        readme = APP_DIR / "README.md"
        assert readme.exists(), f"README.md not found: {readme}"

    def test_backend_directory_exists(self):
        """Verify the backend directory exists."""
        assert (BACKEND_DIR).exists()

    def test_frontend_directory_exists(self):
        """Verify the frontend directory exists."""
        assert (APP_DIR / "frontend").exists()

    @pytest.mark.parametrize("name", BACKEND_PY_FILES)
    def test_backend_file_exists(self, name):
        """Every backend module the README describes is present."""
        assert (BACKEND_DIR / name).exists(), f"missing backend file: {name}"

    def test_backend_requirements_exists(self):
        """Verify backend requirements.txt exists."""
        assert (BACKEND_DIR / "requirements.txt").exists()

    def test_env_example_exists_and_documents_the_required_variables(self):
        """A `.env.example` the README can tell readers to copy."""
        env_example = BACKEND_DIR / ".env.example"
        assert env_example.exists(), "backend/.env.example is missing"
        content = env_example.read_text(encoding="utf-8")
        for variable in ("NEO4J_URI", "NEO4J_PASSWORD", "OPENAI_API_KEY", "OPENAI_MODEL"):
            assert variable in content, f"{variable} not documented in .env.example"

    def test_no_pytest_collectable_module_in_the_backend(self):
        """The live-server checks must not be collected as unit tests.

        ``backend/test_backend.py`` defined ten ``test_*`` functions that each
        take a ``base_url`` argument, which pytest reports as errors whenever
        the examples tree is collected. It is now ``smoke_check.py``.
        """
        stray = sorted(p.name for p in BACKEND_DIR.rglob("test_*.py"))
        assert not stray, f"pytest would collect these live-server scripts: {stray}"
        assert (BACKEND_DIR / "smoke_check.py").exists()

    def test_frontend_package_json_exists(self):
        """Verify frontend package.json exists."""
        assert (APP_DIR / "frontend" / "package.json").exists()


class TestMicrosoftAgentExampleDependencies:
    """Validate that the example declares correct dependencies."""

    def test_requirements_has_agent_framework(self):
        """Verify requirements.txt includes agent-framework."""
        content = (BACKEND_DIR / "requirements.txt").read_text(encoding="utf-8")
        assert "agent-framework" in content

    def test_requirements_pins_ga_agent_framework(self):
        """Verify the agent-framework pin targets the GA line, capped at the next major.

        The preview floor (``>=1.0.0b260212``) resolved to whatever GA release
        was current, and the 1.x GA line renamed the two provider base classes
        the library adapter extends — so an uncapped preview floor is how this
        example came to be broken in the first place. The library's
        ``[microsoft-agent]`` extra requires ``agent-framework-core>=1.13,<2``;
        this example needs ``>=1.17`` because ``agent-framework-openai`` does.
        """
        assert_library_pin(
            BACKEND_DIR / "requirements.txt",
            minimum=MIN_AGENT_FRAMEWORK_PIN,
            package="agent-framework-core",
        )

    def test_requirements_pins_the_openai_chat_client_distribution(self):
        """``agent_framework.openai`` is a lazy re-export of a separate package.

        Importing ``OpenAIChatClient`` from ``agent-framework-core`` alone
        raises ``ModuleNotFoundError: The package agent-framework-openai is
        required``, so the example must declare it.
        """
        assert_library_pin(
            BACKEND_DIR / "requirements.txt",
            minimum=MIN_AGENT_FRAMEWORK_OPENAI_PIN,
            package="agent-framework-openai",
        )

    def test_requirements_has_version_pin(self):
        """Verify requirements.txt pins a current, capped neo4j-agent-memory range."""
        assert_library_pin(BACKEND_DIR / "requirements.txt")

    def test_requirements_declares_the_extras_the_code_configures(self):
        """``get_extraction_config()`` enables spaCy + GLiNER; the install must supply them."""
        pin = assert_library_pin(BACKEND_DIR / "requirements.txt")
        for extra in ("openai", "microsoft-agent", "extraction", "fuzzy"):
            assert extra in pin.extras, (
                f"requirements.txt does not request the [{extra}] extra, but the backend "
                f"configures the feature it provides. Declared: {pin.extras}"
            )

    def test_requirements_has_fastapi(self):
        """Verify requirements.txt includes fastapi."""
        content = (BACKEND_DIR / "requirements.txt").read_text(encoding="utf-8")
        assert "fastapi" in content


class TestMicrosoftAgentExampleSyntax:
    """Validate Python files have valid syntax."""

    @pytest.fixture(params=BACKEND_PY_FILES)
    def python_file(self, request):
        """Parameterized fixture for each Python file."""
        return BACKEND_DIR / request.param

    def test_python_file_has_valid_syntax(self, python_file):
        """Verify Python file can be parsed without syntax errors."""
        if not python_file.exists():
            pytest.skip(f"File not found: {python_file}")
        source = python_file.read_text(encoding="utf-8")
        try:
            ast.parse(source, filename=str(python_file))
        except SyntaxError as e:
            pytest.fail(f"Syntax error in {python_file.name}: {e}")


class TestMicrosoftAgentExampleImports:
    """Validate that key imports are present in the example code."""

    def test_agent_imports_neo4j_memory(self):
        """Verify agent.py imports neo4j_agent_memory integration."""
        content = (BACKEND_DIR / "agent.py").read_text(encoding="utf-8")
        assert "neo4j_agent_memory" in content or "microsoft_agent" in content

    def test_memory_config_imports_memory_client(self):
        """Verify memory_config.py imports MemoryClient."""
        content = (BACKEND_DIR / "memory_config.py").read_text(encoding="utf-8")
        assert "MemoryClient" in content

    def test_main_imports_fastapi(self):
        """Verify main.py imports FastAPI."""
        content = (BACKEND_DIR / "main.py").read_text(encoding="utf-8")
        assert "fastapi" in content or "FastAPI" in content

    def test_agent_uses_create_memory_tools(self):
        """Verify agent.py uses create_memory_tools."""
        content = (BACKEND_DIR / "agent.py").read_text(encoding="utf-8")
        assert "create_memory_tools" in content

    def test_agent_uses_context_provider(self):
        """Verify agent.py uses context provider pattern."""
        content = (BACKEND_DIR / "agent.py").read_text(encoding="utf-8")
        assert (
            "context_provider" in content
            or "Neo4jContextProvider" in content
            or "Neo4jMicrosoftMemory" in content
        )

    def test_catalog_tools_have_one_implementation(self):
        """``agent.py`` adapts ``backend/tools/``; it must not re-implement them.

        The example used to carry two copies of every catalog query — one
        inline in ``agent.py`` and one in ``tools/`` that nothing imported.
        """
        content = (BACKEND_DIR / "agent.py").read_text(encoding="utf-8")
        assert "from tools import" in content, "agent.py should import the shared tool functions"
        assert "queryNodes" not in content, (
            "agent.py contains catalog Cypher again — it belongs in tools/ only"
        )

    def test_context_provider_is_the_only_message_writer(self):
        """``Neo4jContextProvider.after_run()`` persists the turn.

        Calling ``memory.save_message()`` as well stores every message twice.
        """
        content = _code_only((BACKEND_DIR / "agent.py").read_text(encoding="utf-8"))
        assert not re.search(r"\bmemory\s*\.\s*save_message\b", content), (
            "agent.py writes messages by hand as well as through the context "
            "provider — one chat turn would create four :Message nodes"
        )


class TestMicrosoftAgentExampleUsesRealLibraryApi:
    """Guard against phantom attributes and private reach-through."""

    CLIENT_ATTR_RE = re.compile(r"\bclient\s*\.\s*(?P<attr>[A-Za-z_][A-Za-z0-9_]*)")

    def test_every_client_attribute_exists_on_memory_client(self):
        """``client.embeddings`` does not exist; the failure used to be swallowed."""
        public = {name for name in dir(MemoryClient) if not name.startswith("_")}
        problems: list[str] = []
        for name, source in _backend_code().items():
            for match in self.CLIENT_ATTR_RE.finditer(source):
                attr = match.group("attr")
                if attr.startswith("_"):
                    problems.append(f"{name}: client.{attr} reaches into private state")
                elif attr not in public:
                    problems.append(f"{name}: client.{attr} is not a MemoryClient attribute")
        assert not problems, "phantom or private client attributes:\n  " + "\n  ".join(
            sorted(set(problems))
        )

    def test_no_embedder_reach_through(self):
        """The embedder is constructed explicitly, not taken from client internals."""
        private_attr = re.compile(r"\.\s*_embedder\b")
        for name, source in _backend_code().items():
            assert not private_attr.search(source), (
                f"{name} reaches for a private embedder attribute"
            )
        assert "OpenAIEmbedder" in (BACKEND_DIR / "memory_config.py").read_text(encoding="utf-8")

    def test_reads_use_the_portable_cypher_accessor(self):
        """Reads go through ``client.query.cypher`` rather than ``graph.execute_read``."""
        offenders = [
            name
            for name, source in _backend_code().items()
            if re.search(r"\.\s*execute_read\b", source)
        ]
        assert not offenders, (
            "these modules still use the deprecated bolt-only read accessor: "
            f"{offenders}. Use client.query.cypher()."
        )


class TestMicrosoftAgentExampleFeatures:
    """Feature validation for Microsoft Agent retail assistant."""

    def test_uses_dedup_config(self):
        """Verify example uses DeduplicationConfig."""
        content = (BACKEND_DIR / "memory_config.py").read_text(encoding="utf-8")
        assert "DeduplicationConfig" in content, "memory_config.py should use DeduplicationConfig"

    def test_dedup_config_is_actually_wired(self):
        """A config nobody passes anywhere is documentation, not a feature.

        ``MemoryClient`` never forwards a ``DeduplicationConfig`` to
        ``LongTermMemory``, so the example has to construct the layer itself —
        and something has to call the factory that does.
        """
        config_src = (BACKEND_DIR / "memory_config.py").read_text(encoding="utf-8")
        assert "deduplication=get_deduplication_config()" in config_src, (
            "get_deduplication_config() is never passed to LongTermMemory"
        )
        main_src = (BACKEND_DIR / "main.py").read_text(encoding="utf-8")
        assert "create_long_term_memory(" in main_src, (
            "nothing in main.py builds the deduplication-aware LongTermMemory"
        )

    def test_dedup_review_loop_is_reachable(self):
        """The review queue is exposed, which is what makes dedup demonstrable."""
        main_src = (BACKEND_DIR / "main.py").read_text(encoding="utf-8")
        for symbol in (
            "find_potential_duplicates",
            "review_duplicate",
            "get_deduplication_stats",
        ):
            assert symbol in main_src, f"/memory/duplicates does not use {symbol}"

    def test_uses_extraction_config(self):
        """Verify example uses ExtractionConfig."""
        content = (BACKEND_DIR / "memory_config.py").read_text(encoding="utf-8")
        assert "ExtractionConfig" in content, "memory_config.py should use ExtractionConfig"

    def test_records_failed_traces_and_audit_edges(self):
        """Traces carry structured outcomes and TOUCHED edges, not a hard-coded success."""
        content = (BACKEND_DIR / "agent.py").read_text(encoding="utf-8")
        for symbol in ("TraceOutcome", "touched_entities", "triggered_by_message_id"):
            assert symbol in content, f"agent.py does not use {symbol}"
        assert 'outcome="success"' not in content, "the trace outcome is still hard-coded"

    def test_preferences_have_a_write_path(self):
        """Something in the backend writes a :Preference, not just reads them."""
        main_src = (BACKEND_DIR / "main.py").read_text(encoding="utf-8")
        assert "add_preference(" in main_src, "no endpoint records a preference"

    def test_model_ids_come_from_the_environment(self):
        """No retired model id is hard-coded."""
        sources = _backend_sources()
        sources["requirements.txt"] = (BACKEND_DIR / "requirements.txt").read_text(encoding="utf-8")
        for retired in ("gpt-4-turbo-preview", "gpt-4o", "text-embedding-004"):
            for name, source in sources.items():
                assert retired not in source, f"{name} hard-codes the retired model id {retired}"
        config_src = sources["memory_config.py"]
        assert "openai_model: str = DEFAULT_CHAT_MODEL" in config_src


class TestMicrosoftAgentExampleDataLoader:
    """The README's Quick Start step 3 has to exist, and must not wipe the database."""

    def test_data_loader_exists(self):
        """``python -m data.load_products`` is step 3 of the Quick Start."""
        assert (BACKEND_DIR / "data" / "__init__.py").exists()
        assert (BACKEND_DIR / "data" / "load_products.py").exists()

    def test_loader_default_run_is_not_destructive(self):
        """``MATCH (n) DETACH DELETE n`` must be behind an explicit flag."""
        source = (BACKEND_DIR / "data" / "load_products.py").read_text(encoding="utf-8")
        assert "--reset" in source, "the loader has no opt-in reset flag"
        assert "if reset:" in source, "the loader clears the database unconditionally"

    def test_loader_creates_the_vector_index_the_app_queries(self):
        """The index name and dimensions must match the app's configuration."""
        loader = (BACKEND_DIR / "data" / "load_products.py").read_text(encoding="utf-8")
        search = (BACKEND_DIR / "tools" / "product_search.py").read_text(encoding="utf-8")
        assert "product_embedding" in loader
        assert "product_embedding" in search
        assert "EMBEDDING_DIMENSIONS = 1536" in loader
