"""CLI commands for Neo4j Agent Memory entity extraction.

Usage:
    neo4j-agent-memory extract "John works at Acme Corp"
    echo "John works at Acme Corp" | neo4j-agent-memory extract -
    neo4j-agent-memory extract --file document.txt --format json
    neo4j-agent-memory schemas list
    neo4j-agent-memory stats
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import click
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from neo4j_agent_memory.extraction import (
    ExtractionResult,
    ExtractorBuilder,
)
from neo4j_agent_memory.schema import (
    EntitySchemaConfig,
    EntityTypeConfig,
    load_schema_from_file,
)

console = Console()
error_console = Console(stderr=True)


def run_async(coro):
    """Run an async coroutine synchronously."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        # Already in an async context — create a new loop in a thread
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor() as pool:
            return pool.submit(asyncio.run, coro).result()
    else:
        return asyncio.run(coro)


def format_entities_table(result: ExtractionResult) -> Table:
    """Format extraction result as a Rich table."""
    table = Table(title="Extracted Entities")
    table.add_column("Type", style="cyan")
    table.add_column("Name", style="green")
    table.add_column("Confidence", style="yellow", justify="right")
    table.add_column("Attributes", style="dim")

    for entity in result.entities:
        attrs = entity.attributes or {}
        attrs_str = json.dumps(attrs) if attrs else ""
        table.add_row(
            entity.type,
            entity.name,
            f"{entity.confidence:.2f}" if entity.confidence else "N/A",
            attrs_str[:50] + "..." if len(attrs_str) > 50 else attrs_str,
        )

    return table


def format_relations_table(result: ExtractionResult) -> Table:
    """Format extraction relations as a Rich table."""
    table = Table(title="Extracted Relations")
    table.add_column("Source", style="cyan")
    table.add_column("Relation", style="magenta")
    table.add_column("Target", style="green")
    table.add_column("Confidence", style="yellow", justify="right")

    for rel in result.relations:
        table.add_row(
            rel.source,
            rel.relation_type,
            rel.target,
            f"{rel.confidence:.2f}" if rel.confidence else "N/A",
        )

    return table


def format_preferences_table(result: ExtractionResult) -> Table:
    """Format extraction preferences as a Rich table."""
    table = Table(title="Extracted Preferences")
    table.add_column("Category", style="cyan")
    table.add_column("Preference", style="green")
    table.add_column("Confidence", style="yellow", justify="right")

    for pref in result.preferences:
        table.add_row(
            pref.category,
            pref.preference,
            f"{pref.confidence:.2f}" if pref.confidence else "N/A",
        )

    return table


def result_to_dict(result: ExtractionResult) -> dict[str, Any]:
    """Convert extraction result to a dictionary for JSON output."""
    return {
        "entities": [
            {
                "type": e.type,
                "name": e.name,
                "confidence": e.confidence,
                "attributes": e.attributes,
            }
            for e in result.entities
        ],
        "relations": [
            {
                "source": r.source,
                "relation_type": r.relation_type,
                "target": r.target,
                "confidence": r.confidence,
                "attributes": r.attributes if hasattr(r, "attributes") else {},
            }
            for r in result.relations
        ],
        "preferences": [
            {
                "category": p.category,
                "preference": p.preference,
                "confidence": p.confidence,
            }
            for p in result.preferences
        ],
        "source_text": result.source_text,
    }


@click.group()
@click.version_option()
def cli():
    """Neo4j Agent Memory - Entity Extraction CLI.

    Extract entities, relations, and preferences from text using
    GLiNER and LLM-based extractors.
    """
    pass


@cli.command()
@click.argument("text", required=False)
@click.option(
    "-f",
    "--file",
    type=click.Path(exists=True, path_type=Path),
    help="Read text from a file instead of argument.",
)
@click.option(
    "--format",
    "-o",
    type=click.Choice(["table", "json", "jsonl"]),
    default="table",
    help="Output format (default: table).",
)
@click.option(
    "--schema",
    type=click.Path(exists=True, path_type=Path),
    help="Path to a schema YAML file.",
)
@click.option(
    "--entity-types",
    "-e",
    multiple=True,
    help="Entity types to extract (can be specified multiple times).",
)
@click.option(
    "--extractor",
    type=click.Choice(["gliner", "llm", "hybrid"]),
    default="gliner",
    help="Extractor to use (default: gliner).",
)
@click.option(
    "--model",
    default=None,
    help="Model name for GLiNER or LLM extractor.",
)
@click.option(
    "--relations/--no-relations",
    default=True,
    help="Extract relations between entities.",
)
@click.option(
    "--preferences/--no-preferences",
    default=False,
    help="Extract preferences/sentiments.",
)
@click.option(
    "--confidence-threshold",
    type=float,
    default=0.5,
    help="Minimum confidence threshold (default: 0.5).",
)
@click.option(
    "--quiet",
    "-q",
    is_flag=True,
    help="Suppress progress output.",
)
def extract(
    text: str | None,
    file: Path | None,
    format: str,
    schema: Path | None,
    entity_types: tuple[str, ...],
    extractor: str,
    model: str | None,
    relations: bool,
    preferences: bool,
    confidence_threshold: float,
    quiet: bool,
):
    """Extract entities from text.

    TEXT can be provided as an argument, from a file (--file), or piped via stdin.
    Use "-" as TEXT to read from stdin.

    Examples:

        neo4j-agent-memory extract "John works at Acme Corp"

        echo "John works at Acme Corp" | neo4j-agent-memory extract -

        neo4j-agent-memory extract --file document.txt --format json

        neo4j-agent-memory extract "..." --entity-types Person --entity-types Organization
    """
    # Get text from argument, file, or stdin
    if text == "-" or (text is None and file is None and not sys.stdin.isatty()):
        text = sys.stdin.read()
    elif file:
        text = file.read_text()
    elif text is None:
        error_console.print("[red]Error:[/red] No text provided. Use --help for usage.")
        sys.exit(1)

    if not text.strip():
        error_console.print("[red]Error:[/red] Empty text provided.")
        sys.exit(1)

    async def do_extract():
        # Build the extractor
        builder = ExtractorBuilder()

        if schema:
            schema_config = load_schema_from_file(schema)
            builder = builder.with_schema(schema_config)
        elif entity_types:
            # Create a simple schema with the specified types
            schema_config = EntitySchemaConfig(
                name="cli_schema",
                entity_types=[EntityTypeConfig(name=et) for et in entity_types],
            )
            builder = builder.with_schema(schema_config)

        # Configure extractor type
        if extractor == "gliner":
            if model:
                builder = builder.with_gliner(model_name=model)
            else:
                builder = builder.with_gliner()
        elif extractor == "llm":
            if model:
                builder = builder.with_llm(model=model)
            else:
                builder = builder.with_llm()
        elif extractor == "hybrid":
            builder = builder.with_gliner()
            if model:
                builder = builder.with_llm(model=model)
            else:
                builder = builder.with_llm()

        # Set confidence threshold
        builder = builder.with_confidence_threshold(confidence_threshold)

        ext = builder.build()

        # Run extraction
        result = await ext.extract(
            text,
            extract_relations=relations,
            extract_preferences=preferences,
        )

        return result

    # Run with progress indicator
    if quiet or format in ("json", "jsonl"):
        result = run_async(do_extract())
    else:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
            transient=True,
        ) as progress:
            progress.add_task("Extracting entities...", total=None)
            result = run_async(do_extract())

    # Output results
    if format == "json":
        click.echo(json.dumps(result_to_dict(result), indent=2))
    elif format == "jsonl":
        # Output each entity as a separate JSON line
        for entity in result.entities:
            click.echo(
                json.dumps(
                    {
                        "type": "entity",
                        "data": {
                            "type": entity.type,
                            "name": entity.name,
                            "confidence": entity.confidence,
                            "attributes": entity.attributes,
                        },
                    }
                )
            )
        for rel in result.relations:
            click.echo(
                json.dumps(
                    {
                        "type": "relation",
                        "data": {
                            "source": rel.source,
                            "relation_type": rel.relation_type,
                            "target": rel.target,
                            "confidence": rel.confidence,
                        },
                    }
                )
            )
        for pref in result.preferences:
            click.echo(
                json.dumps(
                    {
                        "type": "preference",
                        "data": {
                            "category": pref.category,
                            "preference": pref.preference,
                            "confidence": pref.confidence,
                        },
                    }
                )
            )
    else:
        # Table format
        if result.entities:
            console.print(format_entities_table(result))
        else:
            console.print("[dim]No entities extracted.[/dim]")

        if relations and result.relations:
            console.print()
            console.print(format_relations_table(result))

        if preferences and result.preferences:
            console.print()
            console.print(format_preferences_table(result))

        # Summary
        console.print()
        console.print(
            f"[dim]Extracted {len(result.entities)} entities, "
            f"{len(result.relations)} relations, "
            f"{len(result.preferences)} preferences[/dim]"
        )


@cli.group()
def schemas():
    """Manage extraction schemas."""
    pass


@schemas.command("list")
@click.option(
    "--format",
    "-o",
    type=click.Choice(["table", "json"]),
    default="table",
    help="Output format.",
)
@click.option(
    "--uri",
    envvar="NEO4J_URI",
    default="bolt://localhost:7687",
    help="Neo4j URI (default: bolt://localhost:7687 or NEO4J_URI env var).",
)
@click.option(
    "--user",
    envvar="NEO4J_USER",
    default="neo4j",
    help="Neo4j username (default: neo4j or NEO4J_USER env var).",
)
@click.option(
    "--password",
    envvar="NEO4J_PASSWORD",
    help="Neo4j password (or NEO4J_PASSWORD env var).",
)
def schemas_list(format: str, uri: str, user: str, password: str | None):
    """List saved schemas from Neo4j."""
    if not password:
        error_console.print(
            "[red]Error:[/red] Neo4j password required. Set NEO4J_PASSWORD or use --password."
        )
        sys.exit(1)

    from pydantic import SecretStr

    from neo4j_agent_memory.config.settings import Neo4jConfig
    from neo4j_agent_memory.graph.client import Neo4jClient
    from neo4j_agent_memory.schema import SchemaManager

    async def do_list():
        config = Neo4jConfig(uri=uri, username=user, password=SecretStr(password))
        async with Neo4jClient(config) as client:
            manager = SchemaManager(client)
            return await manager.list_schemas()

    try:
        schema_list = run_async(do_list())
    except Exception as e:
        error_console.print(f"[red]Error connecting to Neo4j:[/red] {e}")
        sys.exit(1)

    if format == "json":
        click.echo(
            json.dumps(
                [
                    {
                        "name": s.name,
                        "latest_version": s.latest_version,
                        "is_active": s.is_active,
                        "version_count": s.version_count,
                        "description": s.description,
                    }
                    for s in schema_list
                ],
                indent=2,
            )
        )
    else:
        if not schema_list:
            console.print("[dim]No schemas found.[/dim]")
            return

        table = Table(title="Saved Schemas")
        table.add_column("Name", style="cyan")
        table.add_column("Version", style="green")
        table.add_column("Active", style="yellow")
        table.add_column("Versions", style="dim")

        for s in schema_list:
            table.add_row(
                s.name,
                s.latest_version,
                "✓" if s.is_active else "",
                str(s.version_count),
            )

        console.print(table)


@schemas.command("show")
@click.argument("name")
@click.option(
    "--version",
    "-v",
    help="Schema version (default: active version).",
)
@click.option(
    "--format",
    "-o",
    type=click.Choice(["yaml", "json"]),
    default="yaml",
    help="Output format.",
)
@click.option(
    "--uri",
    envvar="NEO4J_URI",
    default="bolt://localhost:7687",
    help="Neo4j URI.",
)
@click.option(
    "--user",
    envvar="NEO4J_USER",
    default="neo4j",
    help="Neo4j username.",
)
@click.option(
    "--password",
    envvar="NEO4J_PASSWORD",
    help="Neo4j password.",
)
def schemas_show(
    name: str, version: str | None, format: str, uri: str, user: str, password: str | None
):
    """Show details of a saved schema."""
    if not password:
        error_console.print("[red]Error:[/red] Neo4j password required.")
        sys.exit(1)

    from pydantic import SecretStr

    from neo4j_agent_memory.config.settings import Neo4jConfig
    from neo4j_agent_memory.graph.client import Neo4jClient
    from neo4j_agent_memory.schema import SchemaManager

    async def do_show():
        config = Neo4jConfig(uri=uri, username=user, password=SecretStr(password))
        async with Neo4jClient(config) as client:
            manager = SchemaManager(client)
            if version:
                return await manager.load_schema_version(name, version)
            return await manager.load_schema(name)

    try:
        schema_config = run_async(do_show())
    except Exception as e:
        error_console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)

    if not schema_config:
        error_console.print(f"[red]Error:[/red] Schema '{name}' not found.")
        sys.exit(1)

    if format == "json":
        click.echo(json.dumps(schema_config.to_dict(), indent=2))
    else:
        # YAML output
        import yaml

        click.echo(yaml.dump(schema_config.to_dict(), default_flow_style=False, sort_keys=False))


@schemas.command("validate")
@click.argument("file", type=click.Path(exists=True, path_type=Path))
def schemas_validate(file: Path):
    """Validate a schema YAML file."""
    try:
        schema = load_schema_from_file(file)
        console.print(f"[green]✓[/green] Schema '{schema.name}' is valid.")
        entity_type_names = [et.name for et in schema.entity_types]
        console.print(f"  Entity types: {', '.join(entity_type_names)}")
        if schema.relation_types:
            relation_type_names = [rt.name for rt in schema.relation_types]
            console.print(f"  Relation types: {', '.join(relation_type_names)}")
    except Exception as e:
        error_console.print(f"[red]✗ Invalid schema:[/red] {e}")
        sys.exit(1)


@cli.command()
@click.option(
    "--format",
    "-o",
    type=click.Choice(["table", "json"]),
    default="table",
    help="Output format.",
)
@click.option(
    "--uri",
    envvar="NEO4J_URI",
    default="bolt://localhost:7687",
    help="Neo4j URI.",
)
@click.option(
    "--user",
    envvar="NEO4J_USER",
    default="neo4j",
    help="Neo4j username.",
)
@click.option(
    "--password",
    envvar="NEO4J_PASSWORD",
    help="Neo4j password.",
)
def stats(format: str, uri: str, user: str, password: str | None):
    """Show extraction statistics from Neo4j.

    Displays counts of entities, relations, and extractors stored in the database.
    """
    if not password:
        error_console.print(
            "[red]Error:[/red] Neo4j password required. Set NEO4J_PASSWORD or use --password."
        )
        sys.exit(1)

    from pydantic import SecretStr

    from neo4j_agent_memory.config.settings import Neo4jConfig
    from neo4j_agent_memory.graph.client import Neo4jClient
    from neo4j_agent_memory.memory import LongTermMemory

    async def do_stats():
        config = Neo4jConfig(uri=uri, username=user, password=SecretStr(password))
        async with Neo4jClient(config) as client:
            memory = LongTermMemory(client)
            extraction_stats = await memory.get_extraction_stats()
            extractor_stats = await memory.get_extractor_stats()
            return extraction_stats, extractor_stats

    try:
        extraction_stats, extractor_stats = run_async(do_stats())
    except Exception as e:
        error_console.print(f"[red]Error connecting to Neo4j:[/red] {e}")
        sys.exit(1)

    if format == "json":
        click.echo(
            json.dumps(
                {
                    "extraction_stats": extraction_stats,
                    "extractor_stats": extractor_stats,
                },
                indent=2,
            )
        )
    else:
        # Overview panel
        console.print(
            Panel(
                f"[cyan]Entities:[/cyan] {extraction_stats.get('total_entities', 0)}\n"
                f"[cyan]With Provenance:[/cyan] {extraction_stats.get('entities_with_provenance', 0)}\n"
                f"[cyan]Extractors:[/cyan] {extraction_stats.get('total_extractors', 0)}",
                title="Extraction Statistics",
            )
        )

        # Entity types breakdown
        entity_types = extraction_stats.get("entity_types", {})
        if entity_types:
            console.print()
            table = Table(title="Entities by Type")
            table.add_column("Type", style="cyan")
            table.add_column("Count", style="green", justify="right")

            for etype, count in sorted(entity_types.items(), key=lambda x: -x[1]):
                table.add_row(etype, str(count))

            console.print(table)

        # Extractor breakdown
        if extractor_stats:
            console.print()
            table = Table(title="Extractors")
            table.add_column("Name", style="cyan")
            table.add_column("Version", style="dim")
            table.add_column("Entities", style="green", justify="right")

            for ext in extractor_stats:
                table.add_row(
                    ext.get("name", "Unknown"),
                    ext.get("version") or "N/A",
                    str(ext.get("entity_count", 0)),
                )

            console.print(table)


@cli.group()
def mcp():
    """MCP (Model Context Protocol) server commands.

    Start an MCP server that exposes memory tools, resources, and prompts
    for Claude Desktop, Claude Code, Cursor, and other MCP-compatible hosts.
    """
    pass


@mcp.command("serve")
@click.option(
    "--uri",
    envvar="NEO4J_URI",
    default="bolt://localhost:7687",
    help="Neo4j connection URI.",
)
@click.option(
    "--user",
    envvar="NEO4J_USER",
    default="neo4j",
    help="Neo4j username.",
)
@click.option(
    "--password",
    envvar="NEO4J_PASSWORD",
    help="Neo4j password (or NEO4J_PASSWORD env var).",
)
@click.option(
    "--database",
    envvar="NEO4J_DATABASE",
    default="neo4j",
    help="Neo4j database name.",
)
@click.option(
    "--transport",
    type=click.Choice(["stdio", "sse", "http"]),
    default="stdio",
    help="MCP transport type (default: stdio).",
)
@click.option(
    "--host",
    default="127.0.0.1",
    help="Host for network transports.",
)
@click.option(
    "--port",
    type=int,
    default=8080,
    help="Port for network transports.",
)
@click.option(
    "--profile",
    type=click.Choice(["core", "extended"]),
    default="extended",
    help="Tool profile: core (6 tools) or extended (16 tools).",
)
@click.option(
    "--session-strategy",
    type=click.Choice(["per_conversation", "per_day", "persistent"]),
    default="per_conversation",
    help="Session identity strategy.",
)
@click.option(
    "--user-id",
    envvar="MCP_USER_ID",
    default=None,
    help="User ID for per_day/persistent session strategies.",
)
@click.option(
    "--observation-threshold",
    type=int,
    default=30000,
    help="Token threshold for observational memory compression.",
)
@click.option(
    "--no-auto-preferences",
    is_flag=True,
    default=False,
    help="Disable automatic preference detection.",
)
def mcp_serve(
    uri: str,
    user: str,
    password: str | None,
    database: str,
    transport: str,
    host: str,
    port: int,
    profile: str,
    session_strategy: str,
    user_id: str | None,
    observation_threshold: int,
    no_auto_preferences: bool,
):
    """Start the MCP server for Claude Desktop and other MCP hosts.

    The server exposes memory tools, resources, and prompts via the
    Model Context Protocol. Use stdio transport for Claude Desktop
    or SSE/HTTP for network deployments.

    \b
    Examples:
        # Start with stdio transport (for Claude Desktop)
        neo4j-agent-memory mcp serve --password mypassword

    \b
        # Start with SSE transport on port 8080
        neo4j-agent-memory mcp serve --transport sse --port 8080

    \b
        # Start with core profile (fewer tools, less context overhead)
        neo4j-agent-memory mcp serve --profile core
    """
    if not password:
        error_console.print(
            "[red]Error:[/red] Neo4j password required. Set NEO4J_PASSWORD or use --password."
        )
        sys.exit(1)

    try:
        from neo4j_agent_memory.mcp.server import run_server
    except ImportError:
        error_console.print(
            "[red]Error:[/red] MCP dependencies not installed. "
            "Install with: pip install neo4j-agent-memory[mcp]"
        )
        sys.exit(1)

    asyncio.run(
        run_server(
            neo4j_uri=uri,
            neo4j_user=user,
            neo4j_password=password,
            neo4j_database=database,
            transport=transport,
            host=host,
            port=port,
            profile=profile,
            session_strategy=session_strategy,
            user_id=user_id,
            observation_threshold=observation_threshold,
            auto_preferences=not no_auto_preferences,
        )
    )


def main():
    """Entry point for the CLI."""
    cli()


if __name__ == "__main__":
    main()
