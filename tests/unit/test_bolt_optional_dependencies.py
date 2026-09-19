"""Bolt initialization must not import the optional hosted backend or httpx."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path


def test_bolt_initializes_without_importing_nams_or_httpx(tmp_path):
    # A fresh interpreter prevents another test's NAMS imports from hiding the
    # dependency boundary. The SDK under test is explicitly the current source.
    source_root = Path(__file__).resolve().parents[2] / "src"
    script = textwrap.dedent(
        """
        import asyncio
        import importlib.abc
        import sys
        from unittest.mock import patch

        sys.path.insert(0, sys.argv[1])
        forbidden = ("httpx", "neo4j_agent_memory.nams")

        def is_forbidden(name):
            return any(name == prefix or name.startswith(prefix + ".")
                       for prefix in forbidden)

        assert not any(is_forbidden(name) for name in sys.modules)

        class BlockOptionalBackend(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if is_forbidden(fullname):
                    raise ModuleNotFoundError(
                        "Blocked optional backend import: " + fullname,
                        name=fullname,
                    )

        sys.meta_path.insert(0, BlockOptionalBackend())

        def forbid_network(event, args):
            if event in {"socket.connect", "socket.getaddrinfo"}:
                raise AssertionError("This import-boundary test must not use the network")

        sys.addaudithook(forbid_network)

        import neo4j_agent_memory as sdk
        from neo4j_agent_memory.core.exceptions import NotSupportedError
        from neo4j_agent_memory.memory.long_term import LongTermMemory
        from neo4j_agent_memory.memory.reasoning import ReasoningMemory
        from neo4j_agent_memory.memory.short_term import ShortTermMemory

        class GraphClient:
            def __init__(self, config):
                self.is_connected = False

            async def connect(self):
                self.is_connected = True

            async def close(self):
                self.is_connected = False

        # Minimal EmbeddingProvider, so the test needs no provider extra: the
        # unit-test CI job installs the dev group only, where a provider string
        # like "openai/text-embedding-3-small" has no adapter to resolve to. An
        # instance still exercises the real provider -> legacy-embedder
        # adaptation inside MemoryClient.
        class OfflineEmbeddingProvider:
            model = "offline-test-embedder"
            dimensions = 1536

            async def embed(self, texts):
                return [[0.0] * self.dimensions for _ in texts]

            async def embed_one(self, text):
                return [0.0] * self.dimensions

        class SchemaManager:
            def __init__(self, client, vector_dimensions):
                self.client = client
                self.vector_dimensions = vector_dimensions
                self.setup_completed = False
                self.dimensions_checked = False

            async def setup_all(self):
                assert self.client.is_connected
                self.setup_completed = True

            async def validate_vector_index_dimensions(self, dimensions):
                assert dimensions == self.vector_dimensions == 1536
                self.dimensions_checked = True

        async def run():
            settings = sdk.MemorySettings(
                _env_file=None,
                backend="bolt",
                neo4j={"password": "unused-offline-test-password"},
                embedding=OfflineEmbeddingProvider(),
                llm=None,
                extraction={"extractor_type": "none"},
                resolution={"strategy": "none"},
                geocoding={"enabled": False},
                enrichment={"enabled": False},
            )
            # Replace only external database I/O and schema I/O. Exercise real
            # MemoryClient.connect(), memory constructors, sentinels and close().
            with patch.object(sdk, "Neo4jClient", GraphClient), \\
                 patch.object(sdk, "SchemaManager", SchemaManager):
                async with sdk.MemoryClient(settings) as client:
                    assert client.is_connected
                    assert isinstance(client.short_term, ShortTermMemory)
                    assert isinstance(client.long_term, LongTermMemory)
                    assert isinstance(client.reasoning, ReasoningMemory)
                    assert client.query is not None
                    assert client.users is not None
                    assert client.buffered is not None
                    assert client.consolidation is not None
                    assert client.schema.setup_completed
                    assert client.schema.dimensions_checked
                    for name, method in [("ontology", "list"), ("auth", "list_keys")]:
                        sentinel = getattr(client, name)
                        assert bool(sentinel)
                        try:
                            getattr(sentinel, method)()
                        except NotSupportedError as error:
                            assert error.method == name + "." + method
                            assert error.workaround
                        else:
                            raise AssertionError("Unsupported accessor did not raise")
                assert not client.is_connected
            assert not any(is_forbidden(name) for name in sys.modules)

        asyncio.run(run())
        print("Bolt lifecycle completed without NAMS or httpx imports")
        """
    )
    environment = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith(("NAM_", "MEMORY_", "NAMS_", "OPENAI_"))
    }
    result = subprocess.run(
        [sys.executable, "-I", "-c", script, str(source_root)],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Bolt lifecycle completed without NAMS or httpx imports" in result.stdout
