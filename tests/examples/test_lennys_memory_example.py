"""Smoke tests for the Lenny's Memory example (backend + data pipeline).

Three layers, cheapest first:

* **Structure** — the pipeline is runnable from a clean clone: sample
  transcripts exist, ``data/README.md`` documents the format, the Makefile
  targets resolve, and the duplicate ``requirements.txt`` is gone.
* **Pure functions** — ``parse_transcript`` over fixtures that cover the
  continuation-turn and Unicode cases, plus the ``slugify`` <->
  ``_guest_to_session_id`` contract the whole demo's session-id scheme rests on
  (two independent implementations that must agree).
* **Loader** — ``load_transcripts.py --dry-run`` over the synthetic samples as a
  subprocess, and (when ``NEO4J_URI`` points at a throwaway database) a real
  ``--sample 1`` ingest followed by ``--embeddings-only``.

No API key is needed: the loader runs with a local sentence-transformers
embedder and entity extraction disabled.
"""

from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from tests.examples._manifests import assert_library_pin

EXAMPLES_DIR = Path(__file__).parent.parent.parent / "examples"
APP_DIR = EXAMPLES_DIR / "lennys-memory"
BACKEND_DIR = APP_DIR / "backend"
SCRIPTS_DIR = APP_DIR / "scripts"
DATA_DIR = APP_DIR / "data"
SAMPLES_DIR = DATA_DIR / "samples"


# =============================================================================
# Structure
# =============================================================================


class TestPipelineIsRunnableFromACleanClone:
    """The documented Quick Start used to fail at step 4: data/ was empty."""

    def test_sample_transcripts_are_tracked(self):
        samples = sorted(SAMPLES_DIR.glob("*.txt"))
        assert samples, (
            "data/samples/ must contain synthetic transcripts so `make load-sample` "
            "works without acquiring the (non-redistributable) real corpus"
        )
        assert len(samples) >= 2, "keep at least two samples: one per parser edge case"

    def test_data_readme_documents_the_format(self):
        readme = DATA_DIR / "README.md"
        assert readme.exists(), "data/README.md must explain where transcripts come from"
        text = readme.read_text()
        for needle in ("(HH:MM:SS):", "lenny-podcast-", "data/samples"):
            assert needle in text, f"data/README.md should document {needle!r}"

    def test_loader_resolves_a_data_dir_with_at_least_one_file(self):
        """`make load-sample` passes no --data-dir; the loader must still find files."""
        loader = _load_loader_module()
        resolved = loader.resolve_data_dir(None)
        assert sorted(resolved.glob("*.txt")), f"no transcripts under {resolved}"

    def test_no_duplicate_requirements_manifest(self):
        assert not (BACKEND_DIR / "requirements.txt").exists(), (
            "the backend declares dependencies once, in pyproject.toml + uv.lock"
        )

    def test_backend_pins_the_library_with_an_upper_bound(self):
        pin = assert_library_pin(BACKEND_DIR / "pyproject.toml")
        for extra in ("extraction", "openai", "fuzzy", "mcp"):
            assert extra in pin.extras, f"backend needs the [{extra}] extra"

    def test_makefile_targets_exist(self):
        makefile = (APP_DIR / "Makefile").read_text()
        for target in (
            "load-sample:",
            "load-full:",
            "extract-entities:",
            "backfill-message-embeddings:",
            "repair-links:",
            "backfill-embeddings:",
            "backfill-relationships:",
            "geocode-locations:",
            "enrich:",
            "mcp-server:",
        ):
            assert target in makefile, f"Makefile is missing the {target} target"

    def test_env_example_does_not_enable_debug(self):
        env_example = (BACKEND_DIR / ".env.example").read_text()
        assert "DEBUG=false" in env_example, "shipping DEBUG=true turns on auto-reload"
        assert "AGENT_MODEL=" in env_example, "the chat model must be env-driven"

    def test_no_retired_model_ids(self):
        """gpt-4o and text-embedding-004 are retired; defaults must not name them."""
        for path in [
            BACKEND_DIR / "src" / "config.py",
            BACKEND_DIR / "src" / "agent" / "agent.py",
            BACKEND_DIR / ".env.example",
        ]:
            text = path.read_text()
            for retired in ("gpt-4o", "text-embedding-004", "textembedding-gecko"):
                assert retired not in text, f"{path.name} still references {retired}"


class TestPublicApiOnly:
    """The example must consume the library through its supported surface."""

    def test_no_private_client_access(self):
        offenders = []
        for path in list((BACKEND_DIR / "src").rglob("*.py")) + list(SCRIPTS_DIR.glob("*.py")):
            text = path.read_text()
            for pattern in ("_client.execute_read", "_client.execute_write", "long_term._embedder"):
                if pattern in text:
                    offenders.append(f"{path}: {pattern}")
        assert not offenders, (
            "use client.query.cypher() for reads and a public writer (or "
            "client.graph.execute_write) for writes:\n" + "\n".join(offenders)
        )

    def test_scripts_do_not_import_internal_query_constants(self):
        offenders = [
            str(path)
            for path in SCRIPTS_DIR.glob("*.py")
            if "from neo4j_agent_memory.graph.queries import" in path.read_text()
        ]
        assert not offenders, (
            "graph.queries is internal with no stability guarantee; inline the "
            f"Cypher instead: {offenders}"
        )

    def test_scripts_share_one_settings_builder(self):
        """All five scripts must agree on the embedding space (scripts/_common.py)."""
        for script in SCRIPTS_DIR.glob("*.py"):
            if script.name == "_common.py":
                continue
            text = script.read_text()
            assert "build_memory_settings" in text, (
                f"{script.name} builds its own MemorySettings; use _common."
                " build_memory_settings() so the pipeline and the backend use the"
                " same embedding model"
            )


class TestReadmeClaims:
    """The README is part of the example; wrong numbers are bugs."""

    def test_tool_count_matches_the_registered_tools(self):
        registered = _count_agent_tools()
        assert registered == 28, f"expected 28 @agent.tool functions, found {registered}"
        readme = (APP_DIR / "README.md").read_text()
        stale = re.findall(r"\b19\s+(?:agent\s+)?tools\b", readme)
        assert not stale, f"README still claims 19 tools in {len(stale)} place(s)"

    def test_relative_image_paths_resolve(self):
        readme_path = APP_DIR / "README.md"
        readme = readme_path.read_text()
        missing = []
        for match in re.finditer(r"!\[[^\]]*\]\(([^)\s]+)\)", readme):
            target = match.group(1)
            if target.startswith(("http://", "https://", "data:", "#")):
                continue
            if not (readme_path.parent / target).exists():
                missing.append(target)
        assert not missing, f"README references missing images: {missing}"

    def test_no_streaming_extraction_claim_without_an_implementation(self):
        readme = (APP_DIR / "README.md").read_text()
        loader = (SCRIPTS_DIR / "load_transcripts.py").read_text()
        if "StreamingExtractor" in readme:
            assert "StreamingExtractor" in loader or "create_streaming_extractor" in loader, (
                "the README advertises streaming extraction but the loader does not "
                "implement it (the dead function was removed in the 2026-09 pass)"
            )


# =============================================================================
# Pure functions
# =============================================================================


def _load_loader_module():
    """Import ``scripts/load_transcripts.py`` as a module."""
    import importlib

    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    return importlib.import_module("load_transcripts")


def _load_backend_tools_module():
    """Import the backend's ``src.agent.tools`` module."""
    import importlib

    if str(BACKEND_DIR) not in sys.path:
        sys.path.insert(0, str(BACKEND_DIR))
    return importlib.import_module("src.agent.tools")


def _count_agent_tools() -> int:
    """Count ``@agent.tool`` decorated functions in the backend agent module."""
    tree = ast.parse((BACKEND_DIR / "src" / "agent" / "agent.py").read_text())
    count = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
            continue
        for decorator in node.decorator_list:
            if isinstance(decorator, ast.Attribute) and decorator.attr == "tool":
                count += 1
    return count


class TestTranscriptParser:
    """``parse_transcript`` is a pure function and the pipeline's front door."""

    @pytest.fixture
    def loader(self):
        return _load_loader_module()

    def test_parses_speaker_turns(self, loader, tmp_path):
        path = tmp_path / "Sample Guest.txt"
        path.write_text(
            "Host (00:00:00):\n"
            "First question.\n"
            "\n"
            "Sample Guest (00:00:10):\n"
            "First answer,\n"
            "spanning two lines.\n",
            encoding="utf-8",
        )
        turns = loader.parse_transcript(path)
        assert [t.speaker for t in turns] == ["Host", "Sample Guest"]
        assert turns[0].timestamp == "00:00:00"
        assert turns[1].content == "First answer,\nspanning two lines."
        assert all(t.episode_guest == "Sample Guest" for t in turns)

    def test_continuation_turn_keeps_the_previous_speaker(self, loader, tmp_path):
        path = tmp_path / "Sample Guest.txt"
        path.write_text(
            "Sample Guest (00:00:00):\nOne.\n\n(00:00:30):\nStill me.\n",
            encoding="utf-8",
        )
        turns = loader.parse_transcript(path)
        assert [t.speaker for t in turns] == ["Sample Guest", "Sample Guest"]
        assert [t.timestamp for t in turns] == ["00:00:00", "00:00:30"]

    def test_unicode_speaker_names_are_recognised(self, loader, tmp_path):
        """An ASCII-only speaker class swallowed the turn marker as body text."""
        path = tmp_path / "Renee Delacroix.txt"
        path.write_text(
            "Host (00:00:00):\nWelcome.\n\nRenée Delacroix (00:00:14):\nThank you.\n",
            encoding="utf-8",
        )
        turns = loader.parse_transcript(path)
        assert [t.speaker for t in turns] == ["Host", "Renée Delacroix"]
        assert turns[1].content == "Thank you."

    def test_turns_without_body_text_are_dropped(self, loader, tmp_path):
        path = tmp_path / "Sample Guest.txt"
        path.write_text("Host (00:00:00):\n\nHost (00:00:05):\nSomething.\n", encoding="utf-8")
        turns = loader.parse_transcript(path)
        assert len(turns) == 1

    def test_shipped_samples_parse(self, loader):
        for path in sorted(SAMPLES_DIR.glob("*.txt")):
            turns = loader.parse_transcript(path)
            assert len(turns) >= 4, f"{path.name} parsed into {len(turns)} turns"
            assert len({t.speaker for t in turns}) >= 2, f"{path.name} lost speaker attribution"


class TestSessionIdContract:
    """The loader and the agent must derive the same session id for a guest."""

    @pytest.mark.parametrize(
        "guest",
        [
            "Brian Chesky",
            "Renée Delacroix",
            "Tobi Lütke",
            "Mei Takahashi",
            "O'Brien  Smith-Jones",
        ],
    )
    def test_slugify_matches_guest_to_session_id(self, guest):
        loader = _load_loader_module()
        tools = _load_backend_tools_module()
        assert f"lenny-podcast-{loader.slugify(guest)}" == tools._guest_to_session_id(guest), (
            "scripts/load_transcripts.py::slugify and "
            "backend/src/agent/tools.py::_guest_to_session_id are two independent "
            "implementations of one rule; they must agree or the agent cannot find "
            "the sessions the loader created"
        )


# =============================================================================
# Loader
# =============================================================================

_LOADER = SCRIPTS_DIR / "load_transcripts.py"


def _run_loader(*args: str, env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    env.setdefault("TOKENIZERS_PARALLELISM", "false")
    env["NO_COLOR"] = "1"
    # Never fall through to the loader's bolt://localhost:7687 default: that is
    # a developer's own database. Tests that need Neo4j pass it explicitly.
    env["NEO4J_URI"] = env_extra.get("NEO4J_URI", "") if env_extra else ""
    if not env["NEO4J_URI"]:
        env["NEO4J_URI"] = "bolt://127.0.0.1:1"
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(_LOADER), *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=600,
    )


class TestLoaderDryRun:
    """The dry run touches no database, so it always runs."""

    def test_dry_run_over_the_samples(self):
        result = _run_loader("--dry-run", "--data-dir", str(SAMPLES_DIR))
        assert result.returncode == 0, result.stdout + result.stderr
        assert "DRY RUN" in result.stdout
        for path in sorted(SAMPLES_DIR.glob("*.txt")):
            assert path.name in result.stdout, f"{path.name} missing from the dry-run plan"

    def test_dry_run_reports_turn_counts(self):
        result = _run_loader("--dry-run", "--data-dir", str(SAMPLES_DIR))
        assert re.search(r"Total: \d+ files, \d+ turns", result.stdout), result.stdout


@pytest.mark.requires_neo4j
class TestLoaderAgainstNeo4j:
    """Ingest one synthetic transcript for real, then backfill its embeddings."""

    @pytest.fixture(autouse=True)
    def _require_neo4j(self, neo4j_connection):
        self.env = {
            "NEO4J_URI": neo4j_connection["uri"],
            "NEO4J_USERNAME": neo4j_connection["username"],
            "NEO4J_PASSWORD": neo4j_connection["password"],
            # 384-dim local embedder: no API key, and cached by other suites.
            "EMBEDDING_MODEL": "all-MiniLM-L6-v2",
        }

    def _skip_on_dimension_mismatch(self, result: subprocess.CompletedProcess) -> None:
        blob = result.stdout + result.stderr
        if "EmbeddingDimensionMismatch" in blob or "dimensions" in blob and result.returncode != 0:
            pytest.skip("shared test database has vector indexes at another dimension")

    def test_loads_then_embeds(self):
        load = _run_loader(
            "--data-dir",
            str(SAMPLES_DIR),
            "--sample",
            "1",
            "--no-entities",
            "--no-embeddings",
            env_extra=self.env,
        )
        self._skip_on_dimension_mismatch(load)
        assert load.returncode == 0, load.stdout + load.stderr
        assert "Loading Complete" in load.stdout

        embed = _run_loader(
            "--data-dir",
            str(SAMPLES_DIR),
            "--sample",
            "1",
            "--embeddings-only",
            env_extra=self.env,
        )
        self._skip_on_dimension_mismatch(embed)
        assert embed.returncode == 0, embed.stdout + embed.stderr
        assert "messages embedded" in embed.stdout

    def test_resume_skips_an_already_loaded_transcript(self):
        first = _run_loader(
            "--data-dir",
            str(SAMPLES_DIR),
            "--sample",
            "1",
            "--no-entities",
            "--no-embeddings",
            env_extra=self.env,
        )
        self._skip_on_dimension_mismatch(first)
        assert first.returncode == 0, first.stdout + first.stderr

        again = _run_loader(
            "--data-dir",
            str(SAMPLES_DIR),
            "--sample",
            "1",
            "--no-entities",
            "--no-embeddings",
            "--resume",
            env_extra=self.env,
        )
        assert again.returncode == 0, again.stdout + again.stderr
        assert "already loaded" in again.stdout.lower() or "skipp" in again.stdout.lower()
