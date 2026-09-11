"""Smoke tests for the NAMS + LangChain example.

The example talks to the hosted service over HTTP, so the whole script is
exercised offline against a ``respx``-mocked NAMS: no API key, no network, no
Neo4j. That is deliberate — the defect this test exists to catch (two invented
adapter method names shipped to ``main``) would have been found by *executing*
``main()`` once, which nothing did.

The chat model is the example's own offline fallback (``FakeListChatModel``),
selected automatically because no ``OPENAI_API_KEY`` is set here.
"""

from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

import pytest

pytest.importorskip("langchain", reason="langchain (create_agent) not installed")
pytest.importorskip("respx", reason="respx not installed")

import respx  # noqa: E402

from tests.examples._manifests import assert_library_pin  # noqa: E402

EXAMPLES_DIR = Path(__file__).parent.parent.parent / "examples"
EXAMPLE_DIR = EXAMPLES_DIR / "nams-langchain"
MAIN_PY = EXAMPLE_DIR / "main.py"

ENDPOINT = "https://memory.test/v1"
CONVERSATION_ID = "00000000-0000-0000-0000-0000000000aa"

# NAMS speaks camelCase; these payloads mirror tests/unit/nams fixtures.
CONVERSATION = {
    "id": CONVERSATION_ID,
    "userId": "demo",
    "createdAt": "2026-09-10T12:00:00Z",
    "updatedAt": "2026-09-10T12:00:00Z",
}
USER_MESSAGE = {
    "id": "00000000-0000-0000-0000-000000000001",
    "conversationId": CONVERSATION_ID,
    "role": "user",
    "content": "I prefer dark mode in all my apps. Remember that.",
    "createdAt": "2026-09-10T12:00:01Z",
}
ASSISTANT_MESSAGE = {
    "id": "00000000-0000-0000-0000-000000000002",
    "conversationId": CONVERSATION_ID,
    "role": "assistant",
    "content": "Noted — dark mode everywhere.",
    "createdAt": "2026-09-10T12:00:02Z",
}
ENTITY = {
    "id": "00000000-0000-0000-0000-0000000000e1",
    "name": "Dark Mode",
    "type": "concept",
    "description": "UI colour-scheme preference",
    "createdAt": "2026-09-10T12:00:03Z",
    "updatedAt": "2026-09-10T12:00:03Z",
}


def _load_main_module():
    """Import ``examples/nams-langchain/main.py`` (a dashed, non-package dir)."""
    spec = importlib.util.spec_from_file_location("nams_langchain_main", MAIN_PY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _mock_nams(router: respx.Router) -> None:
    """Register every route the example drives, in call order."""
    # connect() validation probe
    router.get(f"{ENDPOINT}/conversations").respond(200, json={"conversations": []})
    router.post(f"{ENDPOINT}/conversations").respond(201, json=CONVERSATION)
    # middleware: persist user turn, then the assistant reply
    router.post(f"{ENDPOINT}/conversations/{CONVERSATION_ID}/messages").respond(
        201, json=USER_MESSAGE
    )
    # middleware: context injection (short-term only; long-term returns "")
    router.get(f"{ENDPOINT}/conversations/{CONVERSATION_ID}/context").respond(
        200, json={"recentMessages": [USER_MESSAGE]}
    )
    # read-back
    router.get(f"{ENDPOINT}/conversations/{CONVERSATION_ID}").respond(200, json=CONVERSATION)
    router.get(f"{ENDPOINT}/conversations/{CONVERSATION_ID}/messages").respond(
        200, json={"messages": [USER_MESSAGE, ASSISTANT_MESSAGE]}
    )
    # wait_for_extraction: status poll, then entity search
    router.get(f"{ENDPOINT}/conversations/{CONVERSATION_ID}/extraction-status").respond(
        200, json={"summary": {"completed": 2}}
    )
    router.post(f"{ENDPOINT}/entities/search").respond(
        200, json={"entities": [ENTITY], "searchType": "vector"}
    )
    # retriever: conversation-scoped message search
    router.post(f"{ENDPOINT}/conversations/{CONVERSATION_ID}/search").respond(
        200, json={"messages": [USER_MESSAGE], "searchType": "vector"}
    )


@pytest.mark.syntax
class TestNamsLangchainStructure:
    def test_required_files_exist(self):
        for filename in ("main.py", "README.md", "requirements.txt", ".env.example"):
            assert (EXAMPLE_DIR / filename).exists(), f"Missing: {filename}"

    def test_main_compiles(self):
        ast.parse(MAIN_PY.read_text(encoding="utf-8"))

    def test_requirements_pin_the_library_and_langchain_1x(self):
        requirements = EXAMPLE_DIR / "requirements.txt"
        pin = assert_library_pin(requirements)
        assert "nams" in pin.extras
        assert "langchain-agents" in pin.extras, (
            "the create_agent middleware needs the [langchain-agents] extra"
        )

        content = requirements.read_text(encoding="utf-8")
        assert "langchain>=1.0,<2" in content, "LangChain must be pinned to the 1.x line"
        assert "langchain-openai>=1.0,<2" in content

    def test_main_uses_the_langchain_1x_idiom(self):
        source = MAIN_PY.read_text(encoding="utf-8")
        assert "create_agent(" in source
        assert "Neo4jMemoryMiddleware" in source
        assert "NamsSettings" in source
        # Phantom methods that shipped once (nams-langchain-F01) must not return.
        assert "memory.aadd_messages" not in source
        assert "aload_memory_variables({})" not in source
        # Legacy LangChain 0.x shapes moved to langchain-classic.
        assert "ConversationChain" not in source
        assert "AgentExecutor" not in source

    def test_env_example_documents_workspace_id(self):
        content = (EXAMPLE_DIR / ".env.example").read_text(encoding="utf-8")
        assert "MEMORY_API_KEY" in content
        assert "MEMORY_WORKSPACE_ID" in content

    def test_readme_follows_labs_conventions(self):
        readme = (EXAMPLE_DIR / "README.md").read_text(encoding="utf-8")
        assert "Neo4j-Labs" in readme, "missing the Labs badge"
        assert "Neo4j Labs Project" in readme, "missing the Labs disclaimer"
        assert "## Support" in readme
        assert "Verified against" in readme
        assert "Expected output" in readme
        # The old README claimed full backend parity; the adapter's NAMS limits
        # must be stated instead (nams-langchain-F06).
        assert "NotSupportedError" in readme


@pytest.mark.imports
class TestNamsLangchainImports:
    def test_adapters_the_example_imports_exist(self):
        from neo4j_agent_memory import MemoryClient, NamsSettings  # noqa: F401
        from neo4j_agent_memory.integrations.langchain import (  # noqa: F401
            Neo4jMemoryMiddleware,
            Neo4jMemoryRetriever,
        )

    def test_build_model_falls_back_to_the_offline_model(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        try:
            module = _load_main_module()
            model = module.build_model()
        finally:
            sys.modules.pop("nams_langchain_main", None)

        from langchain_core.language_models.fake_chat_models import FakeListChatModel

        assert isinstance(model, FakeListChatModel)


class TestNamsLangchainRun:
    """Execute ``main()`` end to end against a mocked NAMS."""

    async def test_main_runs_against_mocked_nams(self, monkeypatch, capsys):
        monkeypatch.setenv("MEMORY_API_KEY", "nams_test_key")
        monkeypatch.setenv("MEMORY_ENDPOINT", ENDPOINT)
        monkeypatch.delenv("MEMORY_WORKSPACE_ID", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        try:
            module = _load_main_module()
            # A developer's own examples/nams-langchain/.env must not leak in.
            monkeypatch.setattr(module, "load_env", lambda: None)
            with respx.mock(assert_all_called=False) as router:
                _mock_nams(router)
                await module.main()
        finally:
            sys.modules.pop("nams_langchain_main", None)

        out = capsys.readouterr().out
        assert ENDPOINT in out
        assert CONVERSATION_ID in out
        assert "2 messages persisted on NAMS" in out
        assert "Dark Mode" in out
        assert "Retriever returned" in out

    async def test_main_exits_without_an_api_key(self, monkeypatch):
        monkeypatch.delenv("MEMORY_API_KEY", raising=False)

        try:
            module = _load_main_module()
            monkeypatch.setattr(module, "load_env", lambda: None)
            with pytest.raises(SystemExit, match="MEMORY_API_KEY"):
                await module.main()
        finally:
            sys.modules.pop("nams_langchain_main", None)
