"""Read the Aura connection exported by the documentation setup commands."""

import os


class AuraConfigurationError(ValueError):
    """Missing or invalid tutorial settings, with no credential values in errors."""


def aura_config():
    required = ("NEO4J_URI", "NEO4J_USERNAME", "NEO4J_PASSWORD")
    missing = [name for name in required if not os.environ.get(name, "").strip()]
    if missing:
        raise AuraConfigurationError("Export the Aura connection settings: " + ", ".join(missing))
    uri = os.environ["NEO4J_URI"]
    if not uri.startswith("neo4j+s://"):
        raise AuraConfigurationError("NEO4J_URI must use the Aura neo4j+s:// connection scheme")
    database = os.environ.get("NEO4J_DATABASE", "neo4j")
    if not database.strip():
        raise AuraConfigurationError("NEO4J_DATABASE must not be empty")
    return {
        "uri": uri,
        "username": os.environ["NEO4J_USERNAME"],
        "password": os.environ["NEO4J_PASSWORD"],
        "database": database,
    }
