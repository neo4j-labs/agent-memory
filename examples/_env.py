"""Shared ``.env`` loading for the single-file examples in ``examples/``.

Each top-level example script starts with::

    from _env import NEO4J_PASSWORD, NEO4J_URI, NEO4J_USERNAME, OPENAI_API_KEY

Importing this module loads ``examples/.env`` (copy ``examples/.env.example``)
using `python-dotenv <https://pypi.org/project/python-dotenv/>`_ when it is
installed, and falls back to a tiny manual parser otherwise so the examples also
run under a bare ``python`` with no extras. Variables already present in the
process environment always win, so ``NEO4J_URI=... python examples/...`` keeps
working.

The defaults point at the throwaway Neo4j that ``make neo4j-start`` launches
(see ``docker-compose.test.yml``), so a fresh clone runs with nothing exported.
"""

from __future__ import annotations

import os
from pathlib import Path

#: ``examples/.env`` — the per-example environment file (git-ignored).
ENV_FILE = Path(__file__).resolve().parent / ".env"
#: Repository-root ``.env``, loaded second so ``examples/.env`` takes precedence.
ROOT_ENV_FILE = ENV_FILE.parent.parent / ".env"

DEFAULT_NEO4J_URI = "bolt://localhost:7687"
DEFAULT_NEO4J_USERNAME = "neo4j"
# Matches docker-compose.test.yml, i.e. what `make neo4j-start` creates.
DEFAULT_NEO4J_PASSWORD = "test-password"


def _parse_env_file(path: Path) -> None:
    """Minimal ``KEY=value`` parser used when python-dotenv is not installed."""
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("\"'")
        if key and key not in os.environ:
            os.environ[key] = value


def load_example_env(*, verbose: bool = True) -> list[Path]:
    """Load ``examples/.env`` then the repository-root ``.env``, if present.

    Returns the list of files that were loaded (possibly empty).
    """
    loaded: list[Path] = []
    for path in (ENV_FILE, ROOT_ENV_FILE):
        if not path.exists():
            continue
        try:
            from dotenv import load_dotenv
        except ImportError:
            _parse_env_file(path)
        else:
            load_dotenv(path)
        loaded.append(path)
        if verbose:
            print(f"Loaded environment from {path}")
    return loaded


load_example_env()

NEO4J_URI = os.getenv("NEO4J_URI", DEFAULT_NEO4J_URI)
NEO4J_USERNAME = os.getenv("NEO4J_USERNAME", DEFAULT_NEO4J_USERNAME)
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", DEFAULT_NEO4J_PASSWORD)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY") or None
MEMORY_API_KEY = os.getenv("MEMORY_API_KEY") or None

__all__ = [
    "DEFAULT_NEO4J_PASSWORD",
    "DEFAULT_NEO4J_URI",
    "DEFAULT_NEO4J_USERNAME",
    "ENV_FILE",
    "MEMORY_API_KEY",
    "NEO4J_PASSWORD",
    "NEO4J_URI",
    "NEO4J_USERNAME",
    "OPENAI_API_KEY",
    "ROOT_ENV_FILE",
    "load_example_env",
]
