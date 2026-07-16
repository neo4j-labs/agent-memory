"""Type-level test: backend-typed construction via ``connect()``.

``connect(BoltSettings(...))`` statically returns ``BoltMemoryClient``;
``connect(NamsSettings(...))`` statically returns ``NamsMemoryClient``. This
is the shipped mechanism (a module-level overloaded factory) — an
overloaded ``MemoryClient.__new__`` was tried first, but both mypy --strict
and ty reject specializing a generic's return type through ``__new__``
overloads, so overload resolution happens on a plain function instead. See
the module-level comment in ``neo4j_agent_memory/__init__.py`` above
``connect()`` for the full rationale.
"""

from __future__ import annotations

from typing_extensions import assert_type

from neo4j_agent_memory import (
    BoltMemoryClient,
    BoltSettings,
    NamsMemoryClient,
    NamsSettings,
    connect,
)


async def bolt_settings_gives_bolt_client() -> None:
    client = await connect(BoltSettings())
    assert_type(client, BoltMemoryClient)
    # Bolt-only method type-checks on the bolt-typed client.
    await client.long_term.search_locations_near(latitude=0.0, longitude=0.0, radius_km=1.0)


async def nams_settings_gives_nams_client() -> None:
    client = await connect(NamsSettings())
    assert_type(client, NamsMemoryClient)
    # NamsLongTermMemory has no search_locations_near — this must be a type error.
    await client.long_term.search_locations_near(  # type: ignore[attr-defined]  # intentional: NAMS rejects bolt-only
        latitude=0.0, longitude=0.0, radius_km=1.0
    )
