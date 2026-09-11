"""Smoke tests for the hello-memory example — the repository's front door.

Four tiers, cheapest first:

1. ``@pytest.mark.syntax`` — the files exist, ``main.py`` parses, and its
   PEP 723 header is valid TOML that pins the library the way every other
   example manifest does (asserted with the shared
   :mod:`tests.examples._manifests` helper, so the floor lives in one place).
2. ``@pytest.mark.imports`` — the names the example imports really exist, and
   ``settings_from_env()`` resolves both backends from the environment alone.
3. An offline NAMS run: the whole ``main()`` body against a ``respx``-mocked
   service, no API key and no network. This is the only coverage the hosted
   path gets — the repo has no key.
4. ``@pytest.mark.integration`` — the same body against a real Neo4j with a
   local embedder, asserting the context block contains what was written.

``main.py`` carries a PEP 723 header, so in production ``uv`` resolves the
library from PyPI. These tests deliberately exercise the *working tree* copy
instead: a release-install test would need network and would not catch a break
in unreleased surface.
"""

from __future__ import annotations

import ast
import importlib.util
import os
import re
import sys
from pathlib import Path
from types import ModuleType

import pytest
import tomllib

pytest.importorskip("respx", reason="respx not installed")

import httpx  # noqa: E402
import respx  # noqa: E402

from tests.examples._manifests import assert_library_pin  # noqa: E402

EXAMPLE_DIR = Path(__file__).parent.parent.parent / "examples" / "hello-memory"
MAIN_PY = EXAMPLE_DIR / "main.py"
MODULE_NAME = "hello_memory_main"

ENDPOINT = "https://memory.test/v1"
CONVERSATION_ID = "00000000-0000-0000-0000-0000000000aa"
ENTITY_ID = "00000000-0000-0000-0000-0000000000e1"

# PEP 723: the first ``# /// script`` block, per the spec's reference regex.
_PEP723_RE = re.compile(r"(?m)^# /// (?P<type>[a-zA-Z0-9-]+)$\s(?P<content>(^#(| .*)$\s)+)^# ///$")


def _load_module() -> ModuleType:
    """Exec ``main.py`` as a module (callers must pop it from sys.modules)."""
    spec = importlib.util.spec_from_file_location(MODULE_NAME, MAIN_PY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _pep723_metadata() -> dict[str, object]:
    """Parse the PEP 723 ``script`` block out of ``main.py`` as TOML."""
    blocks = [
        match
        for match in _PEP723_RE.finditer(MAIN_PY.read_text(encoding="utf-8"))
        if match.group("type") == "script"
    ]
    assert len(blocks) == 1, (
        f"expected exactly one PEP 723 `# /// script` block in {MAIN_PY}, found {len(blocks)} — "
        "without it `uv run examples/hello-memory/main.py` cannot build an environment"
    )
    content = "".join(
        line[2:] if line.startswith("# ") else line[1:]
        for line in blocks[0].group("content").splitlines(keepends=True)
    )
    return tomllib.loads(content)


@pytest.mark.syntax
class TestHelloMemoryStructure:
    def test_required_files_exist(self):
        for filename in ("main.py", "README.md", ".env.example"):
            assert (EXAMPLE_DIR / filename).exists(), f"Missing: {filename}"

    def test_main_compiles(self):
        ast.parse(MAIN_PY.read_text(encoding="utf-8"))

    def test_pep723_header_parses_and_requires_python_310(self):
        metadata = _pep723_metadata()
        assert metadata.get("requires-python") == ">=3.10"

    def test_pep723_header_pins_the_library(self, tmp_path):
        """Same pin rules as any other example manifest, via the shared helper.

        The helper reads manifests from disk, so the header's dependency list
        is written out as a requirements file first — cheaper than duplicating
        the floor/cap logic here, and it keeps ``MIN_EXAMPLE_PIN`` the only
        place a release bump has to touch.
        """
        dependencies = _pep723_metadata().get("dependencies")
        assert isinstance(dependencies, list) and dependencies

        shim = tmp_path / "requirements.txt"
        shim.write_text("\n".join(str(item) for item in dependencies), encoding="utf-8")
        pin = assert_library_pin(shim)
        assert "nams" in pin.extras, (
            "the hosted path needs the [nams] extra (httpx) — without it the "
            "MEMORY_API_KEY branch cannot connect"
        )

    def test_body_stays_small(self):
        """The whole point of this example: it must stay paste-able.

        Counts statements inside ``main()``. The front door earns its place by
        being readable in one screen; a feature belongs in another example.
        """
        tree = ast.parse(MAIN_PY.read_text(encoding="utf-8"))
        main_fn = next(
            node
            for node in tree.body
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "main"
        )
        statements = sum(1 for _ in ast.walk(main_fn) if isinstance(_, ast.stmt)) - 1
        assert statements <= 20, (
            f"main() is {statements} statements — keep hello-memory under 20 or move "
            "the new material into its own example"
        )

    def test_sticks_to_released_surface(self):
        """``uv run`` resolves the *published* library, not this working tree.

        The PEP 723 header pins ``>=0.5.0``, and 0.5.0 exports neither
        ``BoltSettings``/``NamsSettings``/``connect`` nor a bolt-side
        ``create_conversation`` — a stranger's first run would end in an
        ``ImportError`` or ``AttributeError``. Every other example may use
        unreleased surface; this one may not until the pin floor moves.
        """
        source = MAIN_PY.read_text(encoding="utf-8")
        for unreleased in ("BoltSettings", "NamsSettings", "connect("):
            assert unreleased not in source, (
                f"{unreleased} is not in the released line the PEP 723 header pins; "
                "use MemorySettings(backend=...) until the floor is bumped"
            )
        # create_conversation is NAMS-only before 0.6 — it must stay guarded.
        if "create_conversation" in source:
            assert "if client.is_nams:" in source

    def test_no_bolt_localhost_password_drift(self):
        """The script default must match the container the README documents."""
        source = MAIN_PY.read_text(encoding="utf-8")
        readme = (EXAMPLE_DIR / "README.md").read_text(encoding="utf-8")
        assert 'os.getenv("NEO4J_PASSWORD", "test-password")' in source
        assert "NEO4J_AUTH=neo4j/test-password" in readme

    def test_readme_follows_labs_conventions(self):
        readme = (EXAMPLE_DIR / "README.md").read_text(encoding="utf-8")
        assert "Neo4j-Labs" in readme, "missing the Labs badge"
        assert "Neo4j Labs Project" in readme, "missing the Labs disclaimer"
        assert "## Prerequisites" in readme
        assert "## Run" in readme
        assert "## Expected output" in readme
        assert "## Support" in readme
        assert "Verified against" in readme
        assert "MEMORY_API_KEY" in readme, "the live hosted command must be documented"
        assert "docs/.../" not in readme, "no placeholder doc paths"

    def test_env_example_has_placeholders_only(self):
        content = (EXAMPLE_DIR / ".env.example").read_text(encoding="utf-8")
        assert "MEMORY_API_KEY=nams_xxxx" in content
        assert "NEO4J_PASSWORD" in content
        assert "EMBEDDING" in content
        assert "sk-xxxx" in content, "the OpenAI key must be a placeholder"


@pytest.mark.imports
class TestHelloMemoryImports:
    def test_required_imports_resolve(self):
        from neo4j_agent_memory import (  # noqa: F401
            ExtractionConfig,
            ExtractorType,
            MemoryClient,
            MemorySettings,
            Neo4jConfig,
        )

    def test_settings_from_env_selects_nams_when_a_key_is_present(self, monkeypatch):
        monkeypatch.setenv("MEMORY_API_KEY", "nams_test_key")
        monkeypatch.setenv("MEMORY_ENDPOINT", ENDPOINT)
        try:
            settings = _load_module().settings_from_env()
            assert settings.backend == "nams"
            assert settings.nams.endpoint == ENDPOINT
        finally:
            sys.modules.pop(MODULE_NAME, None)

    def test_settings_from_env_falls_back_to_bolt(self, monkeypatch):
        monkeypatch.delenv("MEMORY_API_KEY", raising=False)
        monkeypatch.setenv("NEO4J_URI", "bolt://example.test:7687")
        monkeypatch.setenv("NEO4J_PASSWORD", "hunter2")
        monkeypatch.setenv("EMBEDDING", "openai/text-embedding-3-small")
        try:
            settings = _load_module().settings_from_env()
            assert settings.backend == "bolt"
            assert settings.neo4j.uri == "bolt://example.test:7687"
            assert settings.neo4j.password.get_secret_value() == "hunter2"
            # No LLM and no extractor: the example writes its entity by hand,
            # so a fresh run never needs a second API key.
            assert settings.llm is None
            assert settings.extraction.extractor_type.value == "none"
        finally:
            sys.modules.pop(MODULE_NAME, None)


class TestHelloMemoryOnNams:
    """Run ``main()`` end to end against a mocked NAMS — no key, no network."""

    def _mock_nams(self, router: respx.Router) -> None:
        conversation = {
            "id": CONVERSATION_ID,
            "userId": "demo",
            "createdAt": "2026-09-10T12:00:00Z",
            "updatedAt": "2026-09-10T12:00:00Z",
        }
        messages = [
            {
                "id": f"00000000-0000-0000-0000-00000000000{index}",
                "conversationId": CONVERSATION_ID,
                "role": role,
                "content": content,
                "createdAt": f"2026-09-10T12:00:0{index}Z",
            }
            for index, (role, content) in enumerate(
                [("user", "I'm allergic to shellfish."), ("assistant", "Noted — no shellfish.")],
                start=1,
            )
        ]
        # connect() probe -> list_conversations(limit=1)
        router.get(f"{ENDPOINT}/conversations").respond(200, json={"conversations": []})
        router.post(f"{ENDPOINT}/conversations").respond(201, json=conversation)
        router.post(f"{ENDPOINT}/conversations/{CONVERSATION_ID}/messages").mock(
            side_effect=[httpx.Response(201, json=payload) for payload in messages]
        )
        router.post(f"{ENDPOINT}/entities").respond(
            201,
            json={
                "id": ENTITY_ID,
                "name": "Shellfish",
                "type": "object",
                "description": "A food allergen.",
                "createdAt": "2026-09-10T12:00:03Z",
                "updatedAt": "2026-09-10T12:00:03Z",
            },
        )
        router.get(f"{ENDPOINT}/conversations/{CONVERSATION_ID}/context").respond(
            200,
            json={
                "reflections": [],
                "observations": [{"content": "Allergic to shellfish."}],
                "recentMessages": messages,
            },
        )

    async def test_main_runs_against_mocked_nams(self, monkeypatch, capsys):
        monkeypatch.setenv("MEMORY_API_KEY", "nams_test_key")
        monkeypatch.setenv("MEMORY_ENDPOINT", ENDPOINT)
        monkeypatch.delenv("MEMORY_WORKSPACE_ID", raising=False)

        try:
            module = _load_module()
            with respx.mock(assert_all_called=False) as router:
                self._mock_nams(router)
                await module.main()
        finally:
            sys.modules.pop(MODULE_NAME, None)

        out = capsys.readouterr().out
        assert "backend: nams" in out
        assert "## Observations" in out
        assert "Allergic to shellfish." in out
        assert "[user] I'm allergic to shellfish." in out

    async def test_the_nams_run_must_skip_the_preferences_call(self, monkeypatch):
        """The ``client.is_nams`` guard is load-bearing, not decorative.

        ``add_preference`` raises ``NotSupportedError`` on the hosted backend,
        so without the guard the hosted run would crash on its fifth call.
        Assert both halves: the guard is in the source, and the call really
        does raise against a (mocked) NAMS client.
        """
        monkeypatch.setenv("MEMORY_API_KEY", "nams_test_key")
        monkeypatch.setenv("MEMORY_ENDPOINT", ENDPOINT)

        source = MAIN_PY.read_text(encoding="utf-8")
        assert "if not client.is_nams:" in source
        assert "add_preference" in source

        from neo4j_agent_memory import NamsSettings, connect
        from neo4j_agent_memory.core.exceptions import NotSupportedError

        with respx.mock(assert_all_called=False) as router:
            self._mock_nams(router)
            client = await connect(NamsSettings())
            try:
                with pytest.raises(NotSupportedError):
                    await client.long_term.add_preference("diet", "Avoids shellfish")
            finally:
                await client.close()


@pytest.mark.requires_neo4j
@pytest.mark.integration
@pytest.mark.skipif(
    not os.getenv("NEO4J_URI"),
    reason="set NEO4J_URI to run the example end-to-end (the quick job has no database)",
)
class TestHelloMemoryOnBolt:
    """Run ``main()`` against a real Neo4j with a local embedder (no API keys)."""

    @pytest.fixture
    def module(self, neo4j_env, monkeypatch):
        pytest.importorskip("sentence_transformers")
        monkeypatch.delenv("MEMORY_API_KEY", raising=False)
        monkeypatch.setenv("EMBEDDING", "sentence-transformers/all-MiniLM-L6-v2")
        try:
            yield _load_module()
        finally:
            sys.modules.pop(MODULE_NAME, None)

    async def _cleanup(self, module) -> None:
        """Remove only this example's nodes — the database may be shared.

        ``client.buffered.submit`` is the public write path (inline in the
        default ``sync`` write mode); ``client.query.cypher`` is read-only.
        """
        from neo4j_agent_memory import MemoryClient

        async with MemoryClient(module.settings_from_env()) as client:
            await client.buffered.submit(
                """
                MATCH (c:Conversation {session_id: $session})
                OPTIONAL MATCH (c)-[:HAS_MESSAGE]->(m:Message)
                DETACH DELETE c, m
                """,
                {"session": module.SESSION},
            )
            await client.buffered.submit("MATCH (e:Entity {name: 'Shellfish'}) DETACH DELETE e")
            await client.buffered.submit(
                """
                MATCH (p:Preference {category: 'diet'})
                WHERE p.preference = 'Avoids shellfish'
                DETACH DELETE p
                """
            )
            await client.flush()

    async def test_main_writes_and_reads_back_the_whole_round_trip(self, module, capsys):
        try:
            await self._cleanup(module)
            await module.main()
            out = capsys.readouterr().out

            assert "backend: bolt" in out
            # Short-term: both turns came back in the conversation block.
            assert "**user**: I'm allergic to shellfish." in out
            assert "**assistant**: Noted — no shellfish." in out
            # Long-term: the preference and the entity the script wrote.
            assert "[diet] Avoids shellfish" in out
            assert "Shellfish (OBJECT): A food allergen." in out
        finally:
            await self._cleanup(module)

    async def test_rerunning_is_safe(self, module):
        """The fixed session id makes this idempotent for entity/preference."""
        from neo4j_agent_memory import MemoryClient

        try:
            await self._cleanup(module)
            await module.main()
            await module.main()

            async with MemoryClient(module.settings_from_env()) as client:
                rows = await client.query.cypher(
                    "MATCH (e:Entity {name: 'Shellfish'}) RETURN count(e) AS cnt"
                )
                assert rows[0]["cnt"] == 1, "add_entity must upsert, not duplicate"
                rows = await client.query.cypher(
                    """
                    MATCH (p:Preference {category: 'diet'})
                    WHERE p.preference = 'Avoids shellfish'
                    RETURN count(p) AS cnt
                    """
                )
                assert rows[0]["cnt"] == 1, "add_preference deduplicates by similarity"
        finally:
            await self._cleanup(module)
