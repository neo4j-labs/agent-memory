"""Shared helpers for the Lenny's Memory data pipeline scripts.

Every script in this directory needs the same four things: terminal colors, a
progress bar, Neo4j CLI arguments, and a ``MemorySettings`` built from the
environment. Keeping them here means the five scripts cannot disagree about
which embedding model to use -- which matters, because messages embedded with
one model and queried with another silently return nothing.

Import it as a sibling module::

    sys.path.insert(0, str(Path(__file__).parent))
    from _common import Colors, ProgressBar, add_neo4j_args, build_memory_settings
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from pydantic import SecretStr

from neo4j_agent_memory import MemorySettings, Neo4jConfig
from neo4j_agent_memory.llm import from_provider

BACKEND_ENV = Path(__file__).parent.parent / "backend" / ".env"

# Defaults mirror backend/src/config.py so the pipeline and the running app
# agree on one embedding space.
DEFAULT_EMBEDDING_MODEL = "openai/text-embedding-3-small"
DEFAULT_LLM_MODEL = "openai/gpt-5-mini"


def load_backend_env() -> None:
    """Load ``backend/.env`` so scripts and the app read the same settings."""
    load_dotenv(BACKEND_ENV)


# ── Terminal output ───────────────────────────────────────────────────


class Colors:
    """ANSI escape codes used by the pipeline scripts."""

    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    CYAN = "\033[96m"
    BLUE = "\033[94m"
    MAGENTA = "\033[95m"


def supports_color() -> bool:
    """True when stdout is a TTY and NO_COLOR is unset."""
    if os.environ.get("NO_COLOR"):
        return False
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


USE_COLORS = supports_color()


def color(text: str, color_code: str) -> str:
    """Wrap ``text`` in ``color_code`` when the terminal supports it."""
    if USE_COLORS:
        return f"{color_code}{text}{Colors.RESET}"
    return text


def format_duration(seconds: float) -> str:
    """Format a duration as ``1h 02m 03s`` / ``2m 03s`` / ``4.1s``."""
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, secs = divmod(int(seconds), 60)
    if minutes < 60:
        return f"{minutes}m {secs:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m {secs:02d}s"


class ProgressBar:
    """A single-line progress bar with a rate and ETA.

    ``total`` may be revised upward with :meth:`set_total` when the real count is
    only known after the first page of results.
    """

    def __init__(self, total: int, *, label: str = "", width: int = 24) -> None:
        self.total = max(total, 0)
        self.label = label
        self.width = width
        self.count = 0
        self.start = time.time()

    def set_total(self, total: int) -> None:
        self.total = max(total, 0)

    def advance(self, step: int = 1, *, suffix: str = "") -> None:
        self.count += step
        self.render(suffix=suffix)

    def render(self, *, suffix: str = "") -> None:
        elapsed = time.time() - self.start
        fraction = (self.count / self.total) if self.total else 0.0
        filled = int(fraction * self.width)
        bar = f"[{'=' * filled}{' ' * (self.width - filled)}]"
        rate = self.count / elapsed if elapsed > 0 else 0.0
        remaining = max(self.total - self.count, 0)
        eta = format_duration(remaining / rate) if rate > 0 else "--"
        line = (
            f"\r{self.label} {bar} {self.count}/{self.total} ({fraction * 100:5.1f}%) "
            f"| {rate:.1f}/s | ETA {eta}"
        )
        if suffix:
            line += f" | {suffix}"
        print(f"{line}   ", end="", flush=True)

    def close(self) -> None:
        self.render()
        print()

    @property
    def elapsed(self) -> float:
        return time.time() - self.start


# ── CLI arguments ─────────────────────────────────────────────────────


def add_neo4j_args(parser: argparse.ArgumentParser) -> None:
    """Add the connection flags every script shares.

    Defaults come from the environment (``backend/.env`` is loaded first), so
    ``NEO4J_USERNAME`` is honoured -- it used to be ignored by the geocoder,
    which had no ``--neo4j-user`` flag at all.
    """
    group = parser.add_argument_group("Neo4j connection")
    group.add_argument(
        "--neo4j-uri",
        default=os.getenv("NEO4J_URI", "bolt://localhost:7687"),
        help="Neo4j connection URI (env: NEO4J_URI)",
    )
    group.add_argument(
        "--neo4j-user",
        default=os.getenv("NEO4J_USERNAME", "neo4j"),
        help="Neo4j username (env: NEO4J_USERNAME)",
    )
    group.add_argument(
        "--neo4j-password",
        default=os.getenv("NEO4J_PASSWORD", "password"),
        help="Neo4j password (env: NEO4J_PASSWORD)",
    )
    group.add_argument(
        "--neo4j-database",
        default=os.getenv("NEO4J_DATABASE", "neo4j"),
        help="Neo4j database name (env: NEO4J_DATABASE)",
    )


def add_model_args(parser: argparse.ArgumentParser) -> None:
    """Add the provider-string flags (mirroring the backend's settings)."""
    group = parser.add_argument_group("Providers")
    group.add_argument(
        "--embedding-model",
        default=os.getenv("EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL),
        help=(
            "Embedding provider string (env: EMBEDDING_MODEL). Use a "
            "sentence-transformers id such as BAAI/bge-small-en-v1.5 to stay "
            "local. MUST match what the backend queries with."
        ),
    )
    group.add_argument(
        "--llm-model",
        default=os.getenv("LLM_MODEL", DEFAULT_LLM_MODEL),
        help="LLM provider string for extraction fallbacks (env: LLM_MODEL)",
    )


def neo4j_config(args: argparse.Namespace) -> Neo4jConfig:
    """Build a ``Neo4jConfig`` from parsed CLI args."""
    return Neo4jConfig(
        uri=args.neo4j_uri,
        username=args.neo4j_user,
        password=SecretStr(args.neo4j_password),
        database=getattr(args, "neo4j_database", "neo4j"),
    )


def build_memory_settings(
    args: argparse.Namespace,
    *,
    with_llm: bool = False,
    quiet: bool = False,
    **extra: Any,
) -> MemorySettings:
    """Build ``MemorySettings`` that agree with the backend's configuration.

    Resolves ``EMBEDDING_MODEL`` / ``LLM_MODEL`` through
    :func:`neo4j_agent_memory.llm.from_provider` -- the loader used to skip this
    and silently fall back to the default OpenAI embedder, which put message
    vectors in a different space than the backend queried with.

    Args:
        args: Parsed args carrying the ``--neo4j-*`` (and optionally
            ``--embedding-model`` / ``--llm-model``) flags.
        with_llm: Also resolve an LLM provider (needed for LLM extraction or
            summarisation; skip it for embedding-only jobs).
        quiet: Suppress the "resolved providers" banner.
        **extra: Extra ``MemorySettings`` fields (``extraction=``,
            ``geocoding=``, ``enrichment=``, ...).
    """
    embedding_model = getattr(args, "embedding_model", None) or os.getenv(
        "EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL
    )
    llm_model = getattr(args, "llm_model", None) or os.getenv("LLM_MODEL", DEFAULT_LLM_MODEL)

    embedder = from_provider(embedding_model, kind="embedding", **_provider_kwargs(embedding_model))
    settings_kwargs: dict[str, Any] = {
        "neo4j": neo4j_config(args),
        "embedding": embedder,
        **extra,
    }
    if with_llm:
        settings_kwargs["llm"] = from_provider(llm_model, kind="llm", **_provider_kwargs(llm_model))

    if not quiet:
        print(f"{color('Neo4j', Colors.DIM)}      {args.neo4j_uri} (user: {args.neo4j_user})")
        print(f"{color('Embeddings', Colors.DIM)} {embedding_model}")
        if with_llm:
            print(f"{color('LLM', Colors.DIM)}        {llm_model}")
        print()

    return MemorySettings(**settings_kwargs)


def _provider_kwargs(model: str) -> dict[str, Any]:
    """API-key kwargs for a provider string, taken from the environment."""
    if model.startswith("openai/") or model.startswith("text-embedding-"):
        key = os.getenv("OPENAI_API_KEY")
        return {"api_key": key} if key else {}
    if model.startswith("anthropic/"):
        key = os.getenv("ANTHROPIC_API_KEY")
        return {"api_key": key} if key else {}
    return {}


__all__ = [
    "BACKEND_ENV",
    "Colors",
    "ProgressBar",
    "add_model_args",
    "add_neo4j_args",
    "build_memory_settings",
    "color",
    "format_duration",
    "load_backend_env",
    "neo4j_config",
    "supports_color",
]
