"""Offline contracts for the Aura connection used by the seven Bolt tutorials.

Only synthetic exports are used. Provider resolution and driver I/O are doubled;
the authored configuration expressions still construct the real SDK settings.
"""

import ast
import asyncio
import importlib
import inspect
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from neo4j.exceptions import AuthError, ServiceUnavailable, SessionExpired

from neo4j_agent_memory import BoltSettings, MemorySettings

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / "docs/modules/ROOT/examples"
EXPORTS = {
    "NEO4J_URI": "neo4j+s://tutorial.databases.neo4j.io",
    "NEO4J_USERNAME": "tutorial-user",
    "NEO4J_PASSWORD": "synthetic-tutorial-password",
    "NEO4J_DATABASE": "tutorial-database",
}
SETTINGS_PROGRAMS = [
    "core_memory_settings",
    "anthropic_local_memory",
    "strands_memory_tutorial",
    "microsoft_shopping_tutorial",
    "mcp_local_tutorial",
]


@pytest.fixture
def aura(monkeypatch, tmp_path):
    # Do not inspect the developer's .env or inherit database/provider settings.
    monkeypatch.setattr(os, "environ", dict(EXPORTS))
    monkeypatch.chdir(tmp_path)
    monkeypatch.syspath_prepend(str(EXAMPLES))
    return importlib.import_module("aura_connection")


class OfflineEmbedding:
    model = "offline/test"
    dimensions = 384

    async def embed(self, _texts):
        raise AssertionError("Aura settings checks must not request embeddings")

    async def embed_one(self, _text):
        raise AssertionError("Aura settings checks must not request embeddings")


class OfflineLLM:
    model = "offline/test"

    async def complete(self, _messages, **_kwargs):
        raise AssertionError("Aura settings checks must not request completions")


def settings_expression(name):
    """Execute the authored settings call without importing framework runtimes."""
    path = EXAMPLES / f"{name}.py"
    tree = ast.parse(path.read_text())
    assert any(
        isinstance(node, ast.ImportFrom)
        and node.module == "aura_connection"
        and any(alias.name == "aura_config" for alias in node.names)
        for node in tree.body
    ), f"{name} must import the shared Aura configuration"
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in {"MemorySettings", "BoltSettings"}
    ]
    assert len(calls) == 1, f"Review changed settings construction in {name}"
    return compile(ast.Expression(body=calls[0]), str(path), "eval")


@pytest.mark.parametrize("name", SETTINGS_PROGRAMS)
def test_authored_settings_pass_aura_credentials_to_real_sdk(name, aura, monkeypatch):
    provider = OfflineEmbedding()
    monkeypatch.setattr("neo4j_agent_memory.llm.from_provider", lambda *_a, **_k: provider)
    config = eval(
        settings_expression(name),
        {
            "MemorySettings": MemorySettings,
            "BoltSettings": BoltSettings,
            "aura_config": aura.aura_config,
            "embedding": provider,
            "llm": OfflineLLM(),
        },
    )
    assert isinstance(config, MemorySettings)
    assert config.backend == "bolt"
    assert config.neo4j.uri == EXPORTS["NEO4J_URI"]
    assert config.neo4j.username == EXPORTS["NEO4J_USERNAME"]
    assert config.neo4j.password.get_secret_value() == EXPORTS["NEO4J_PASSWORD"]
    assert config.neo4j.database == EXPORTS["NEO4J_DATABASE"]


@pytest.mark.parametrize("name", SETTINGS_PROGRAMS)
def test_missing_credentials_stop_before_sdk_settings_or_provider_resolution(name, aura):
    os.environ.pop("NEO4J_PASSWORD")
    constructor = Mock(side_effect=AssertionError("Must not construct SDK settings"))
    with pytest.raises(aura.AuraConfigurationError, match="NEO4J_PASSWORD"):
        eval(
            settings_expression(name),
            {
                "MemorySettings": constructor,
                "BoltSettings": constructor,
                "aura_config": aura.aura_config,
                "embedding": OfflineEmbedding(),
                "llm": OfflineLLM(),
            },
        )
    constructor.assert_not_called()


@pytest.mark.parametrize("key", ["NEO4J_URI", "NEO4J_USERNAME", "NEO4J_PASSWORD"])
@pytest.mark.parametrize("value", [None, "", " \t "])
def test_required_exports_reject_missing_and_blank_values(key, value, aura):
    if value is None:
        os.environ.pop(key)
    else:
        os.environ[key] = value
    with pytest.raises(aura.AuraConfigurationError) as caught:
        aura.aura_config()
    assert key in str(caught.value)
    assert EXPORTS["NEO4J_PASSWORD"] not in str(caught.value)
    assert EXPORTS["NEO4J_URI"] not in str(caught.value)


def test_only_database_has_a_default_and_dotenv_is_not_an_implicit_fallback(aura, tmp_path):
    os.environ.pop("NEO4J_DATABASE")
    assert aura.aura_config()["database"] == "neo4j"
    (tmp_path / ".env").write_text("\n".join(f"{key}={value}" for key, value in EXPORTS.items()))
    os.environ.clear()
    with pytest.raises(aura.AuraConfigurationError) as caught:
        aura.aura_config()
    assert all(
        name in str(caught.value) for name in ["NEO4J_URI", "NEO4J_USERNAME", "NEO4J_PASSWORD"]
    )


@pytest.mark.parametrize("database", ["", " \t "])
def test_explicit_blank_database_does_not_fall_back(aura, database):
    os.environ["NEO4J_DATABASE"] = database
    with pytest.raises(aura.AuraConfigurationError, match="NEO4J_DATABASE"):
        aura.aura_config()


@pytest.mark.parametrize("scheme", ["bolt", "neo4j", "neo4j+ssc", "https"])
def test_aura_connection_requires_verified_encryption(aura, scheme):
    os.environ["NEO4J_URI"] = f"{scheme}://synthetic-sensitive-host.invalid"
    with pytest.raises(aura.AuraConfigurationError) as caught:
        aura.aura_config()
    assert "neo4j+s://" in str(caught.value)
    assert "synthetic-sensitive-host" not in str(caught.value)
    assert EXPORTS["NEO4J_PASSWORD"] not in str(caught.value)


class ProbeDriver:
    def __init__(self):
        self.verify_connectivity = AsyncMock()
        self.execute_query = AsyncMock(return_value=([{"ready": 1}], None, None))
        self.close = AsyncMock()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        await self.close()


@pytest.fixture
def readiness(aura, monkeypatch):
    module = importlib.import_module("wait_for_tutorial_neo4j")
    driver = ProbeDriver()
    factory = Mock(return_value=driver)
    monkeypatch.setattr(module.AsyncGraphDatabase, "driver", factory)
    return module, driver, factory


@pytest.mark.asyncio
async def test_readiness_checks_connectivity_and_selected_database_then_closes(readiness):
    module, driver, factory = readiness
    assert inspect.signature(module.wait_until_ready).parameters["timeout"].default == 180
    await module.wait_until_ready(timeout=1)
    factory.assert_called_once_with(
        EXPORTS["NEO4J_URI"],
        auth=(EXPORTS["NEO4J_USERNAME"], EXPORTS["NEO4J_PASSWORD"]),
        connection_timeout=1,
        connection_acquisition_timeout=1,
    )
    driver.verify_connectivity.assert_awaited_once_with()
    driver.execute_query.assert_awaited_once_with(
        "RETURN 1 AS ready", database_=EXPORTS["NEO4J_DATABASE"], routing_="r"
    )
    driver.close.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_missing_aura_credentials_prevent_driver_creation(readiness):
    module, _driver, factory = readiness
    os.environ.pop("NEO4J_PASSWORD")
    with pytest.raises(ValueError, match="NEO4J_PASSWORD"):
        await module.wait_until_ready()
    factory.assert_not_called()


@pytest.mark.parametrize("error_type", [ServiceUnavailable, SessionExpired])
@pytest.mark.asyncio
async def test_readiness_retries_transient_failures_and_closes(readiness, monkeypatch, error_type):
    module, driver, _factory = readiness
    driver.verify_connectivity.side_effect = [error_type("transient fixture failure"), None]
    sleep = AsyncMock()
    monkeypatch.setattr(module.asyncio, "sleep", sleep)
    await module.wait_until_ready(timeout=1)
    assert driver.verify_connectivity.await_count == 2
    driver.execute_query.assert_awaited_once()
    sleep.assert_awaited_once_with(2)
    driver.close.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_readiness_deadline_cancels_inflight_probe_and_closes_driver(readiness):
    module, driver, _factory = readiness
    cancelled = asyncio.Event()

    async def never_ready():
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    driver.verify_connectivity.side_effect = never_ready
    with pytest.raises(asyncio.TimeoutError):
        await module.wait_until_ready(timeout=0.01)
    assert cancelled.is_set()
    driver.execute_query.assert_not_awaited()
    driver.close.assert_awaited_once_with()


@pytest.mark.parametrize("records", [[], [{"ready": 0}], [{"ready": 1}, {"ready": 1}]])
@pytest.mark.asyncio
async def test_readiness_rejects_unexpected_query_results(readiness, records):
    module, driver, _factory = readiness
    driver.execute_query.return_value = (records, None, None)
    with pytest.raises(RuntimeError, match="Unexpected readiness query result"):
        await module.wait_until_ready(timeout=1)
    driver.close.assert_awaited_once_with()


def test_auth_failure_stops_without_retry_closes_and_cli_sanitizes(readiness, monkeypatch, capsys):
    module, driver, _factory = readiness
    driver.verify_connectivity.side_effect = AuthError(
        f"sensitive raw error {EXPORTS['NEO4J_URI']} {EXPORTS['NEO4J_PASSWORD']}"
    )
    sleep = AsyncMock()
    monkeypatch.setattr(module.asyncio, "sleep", sleep)
    with pytest.raises(SystemExit) as caught:
        module.main()
    assert caught.value.code == 1
    driver.verify_connectivity.assert_awaited_once()
    driver.execute_query.assert_not_awaited()
    sleep.assert_not_awaited()
    driver.close.assert_awaited_once_with()
    output = capsys.readouterr()
    assert not output.out
    assert "AuthError" in output.err and "NEO4J_*" in output.err
    assert EXPORTS["NEO4J_PASSWORD"] not in output.err
    assert EXPORTS["NEO4J_URI"] not in output.err
    assert "sensitive raw error" not in output.err


def test_readiness_cli_sanitizes_timeout(readiness, monkeypatch, capsys):
    module, _driver, _factory = readiness
    monkeypatch.setattr(
        module,
        "wait_until_ready",
        AsyncMock(side_effect=asyncio.TimeoutError(EXPORTS["NEO4J_PASSWORD"])),
    )
    with pytest.raises(SystemExit) as caught:
        module.main()
    assert caught.value.code == 1
    output = capsys.readouterr()
    assert "TimeoutError" in output.err
    assert EXPORTS["NEO4J_PASSWORD"] not in output.err
    assert not output.out


def test_mcp_generated_config_supplies_child_process_aura_environment(aura, monkeypatch, capsys):
    module = importlib.import_module("mcp_local_tutorial")
    monkeypatch.setattr(sys, "argv", [str(EXAMPLES / "mcp_local_tutorial.py"), "--config"])
    module.main()
    config = json.loads(capsys.readouterr().out)["mcpServers"]["neo4j-docs"]
    assert config["command"] == sys.executable
    assert config["args"] == [str(EXAMPLES / "mcp_local_tutorial.py")]
    assert config["env"] == EXPORTS
    # Launch a fresh interpreter with ONLY the generated child environment.
    # The actual authored build_server constructs real MemorySettings; the
    # provider and MCP transport factories are doubled before they can do I/O.
    program = r"""
import os, runpy, sys, types
sys.path.insert(0, os.path.dirname(sys.argv[1]))
from neo4j_agent_memory import MemorySettings
import neo4j_agent_memory.llm
class OfflineEmbedding:
    model = "offline/test"
    dimensions = 384
    async def embed(self, texts):
        raise AssertionError("No model call expected")
    async def embed_one(self, text):
        raise AssertionError("No model call expected")
neo4j_agent_memory.llm.from_provider = lambda *args, **kwargs: OfflineEmbedding()
server = types.ModuleType("neo4j_agent_memory.mcp.server")
def create_mcp_server(settings, *, profile, auto_preferences):
    assert isinstance(settings, MemorySettings)
    assert settings.backend == "bolt"
    assert settings.neo4j.uri == os.environ["NEO4J_URI"]
    assert settings.neo4j.username == os.environ["NEO4J_USERNAME"]
    assert settings.neo4j.password.get_secret_value() == os.environ["NEO4J_PASSWORD"]
    assert settings.neo4j.database == os.environ["NEO4J_DATABASE"]
    assert profile == "core" and auto_preferences is False
    return "verified"
server.create_mcp_server = create_mcp_server
sys.modules[server.__name__] = server
assert runpy.run_path(sys.argv[1])["build_server"]() == "verified"
print("Verified generated MCP child configuration")
"""
    result = subprocess.run(
        [config["command"], "-I", "-c", program, *config["args"]],
        env=config["env"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "Verified generated MCP child configuration"


def test_mcp_config_includes_default_database(aura, monkeypatch, capsys):
    os.environ.pop("NEO4J_DATABASE")
    module = importlib.import_module("mcp_local_tutorial")
    monkeypatch.setattr(sys, "argv", ["mcp_local_tutorial.py", "--config"])
    module.main()
    config = json.loads(capsys.readouterr().out)
    assert config["mcpServers"]["neo4j-docs"]["env"]["NEO4J_DATABASE"] == "neo4j"


def test_all_seven_bolt_tutorials_include_aura_setup_and_cleanup():
    pages = [
        "first-agent-memory",
        "conversation-memory",
        "knowledge-graph",
        "anthropic-and-local-embeddings",
        "strands-agent-quickstart",
        "microsoft-agent-memory",
        "mcp-server",
    ]
    for name in pages:
        text = (ROOT / "docs/modules/ROOT/pages/tutorials" / f"{name}.adoc").read_text()
        assert "include::partial$aura-tutorial-setup.adoc[]" in text, name
        assert "include::partial$aura-tutorial-cleanup.adoc[]" in text, name
        assert "docker run" not in text and "docker exec" not in text, name
    setup = (ROOT / "docs/modules/ROOT/partials/aura-tutorial-setup.adoc").read_text()
    cleanup = (ROOT / "docs/modules/ROOT/partials/aura-tutorial-cleanup.adoc").read_text()
    assert "python wait_for_tutorial_neo4j.py\n" in setup
    for partial, filename in (
        ("python-aura-connection-file.adoc", "aura_connection.py"),
        ("python-aura-readiness-file.adoc", "wait_for_tutorial_neo4j.py"),
    ):
        include = f"include::partial${partial}[]"
        assert setup.index(include) < setup.index("python wait_for_tutorial_neo4j.py")
        file_block = (ROOT / "docs/modules/ROOT/partials" / partial).read_text()
        assert f".Save as `{filename}`" in file_block
        assert f"include::example${filename}[]" in file_block
    for name in EXPORTS:
        assert f"export {name}=" in setup
        assert name in cleanup
