"""Run the how-to recipe programs against Neo4j.

The how-to pages show tagged regions of three maintained programs in
``docs/modules/ROOT/examples``: ``core_memory_recipes.py`` (messages, entities,
preferences, reasoning traces, deduplication, audit), ``extraction_recipes.py``
(entity extraction) and ``integrations/hybrid_recipe.py``. These tests run those
programs through the command line a reader types, against the Neo4j test
database. The ``live_programs`` fixture replaces only the Aura connection and the
embedding provider, so every SDK call and Cypher query runs for real.

``test_core_python_tutorials.py`` keeps the offline API-binding and failure-path
checks; this module is what proves the recipes pass against a database.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

pytestmark = [pytest.mark.docs, pytest.mark.integration]

CORE_COMMANDS = ["messages", "entities", "preferences", "reasoning", "deduplication", "audit"]

# The message recipe reads a batch back in order, which depends on the stored
# timestamps. One passing run proves little, so every recipe runs repeatedly
# and the message recipe runs often enough to catch an order that is not
# guaranteed.
REPEATS = {"messages": 10}


@pytest.mark.parametrize("command", CORE_COMMANDS)
async def test_core_recipe_command_runs_against_neo4j(live_programs, command, capsys):
    """``python core_memory_recipes.py <command>`` completes and prints its check."""
    recipes = live_programs.load("core_memory_recipes")

    await live_programs.run(recipes, command)

    assert "Verified:" in capsys.readouterr().out


@pytest.mark.parametrize("command", CORE_COMMANDS)
async def test_core_recipe_is_repeatable(live_programs, command):
    """Each recipe passes on every run, not just once, in a database it already wrote to."""
    recipes = live_programs.load("core_memory_recipes")
    recipe = getattr(recipes, command)
    failures = []
    async with live_programs.memory_client() as client:
        for attempt in range(REPEATS.get(command, 3)):
            try:
                await recipe(client, uuid4().hex[:8])
            except AssertionError as error:
                failures.append(f"run {attempt}: {error!r}")
    assert not failures, f"{command} failed {len(failures)} time(s): {failures}"


async def test_hybrid_recipe_runs_against_neo4j(live_programs, capsys):
    """``python hybrid_recipe.py`` stores, reads back and routes a message search."""
    recipe = live_programs.load("hybrid_recipe")

    await live_programs.run(recipe)

    assert "Verified stored message" in capsys.readouterr().out


@pytest.mark.parametrize("command", ["extract", "schema", "relations", "batch", "streaming"])
async def test_extraction_recipe_command_runs_with_the_local_model(live_programs, command, capsys):
    """``python extraction_recipes.py <command>`` runs the real GLiNER2.5 model."""
    pytest.importorskip("gliner2", reason="the extraction recipes need the gliner2 extra")
    recipes = live_programs.load("extraction_recipes")

    await live_programs.run(recipes, command)

    assert "Verified:" in capsys.readouterr().out
