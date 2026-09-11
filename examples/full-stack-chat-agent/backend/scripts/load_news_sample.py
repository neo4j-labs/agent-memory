#!/usr/bin/env python
"""Seed the news graph with a small sample dataset.

Why this exists: the agent's differentiator is the *second* graph it queries.
Before this script the example pointed `NEWS_GRAPH_URI` at a third-party host
with shared credentials, which is the wrong default for a reference example —
so the demo now ships its own data.

Creates ~16 `:Article` nodes with `:Topic` / `:Person` / `:Organization` /
`:Geo` neighbours, and (when OPENAI_API_KEY is set) article embeddings plus the
`article_embeddings` vector index that `vector_search_news` queries. Without a
key the text tools all work and `vector_search_news` reports that the index is
missing.

Usage (from `backend/`):

    uv run python scripts/load_news_sample.py
    uv run python scripts/load_news_sample.py --reset   # delete existing sample first
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from neo4j import AsyncGraphDatabase

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config import get_settings  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("load_news_sample")

TODAY = date.today()


def _day(offset: int) -> str:
    """An ISO date `offset` days before today (keeps `get_recent_news` useful)."""
    return (TODAY - timedelta(days=offset)).isoformat()


ARTICLES: list[dict[str, Any]] = [
    {
        "title": "Graph databases move to the centre of AI agent memory",
        "abstract": (
            "Engineering teams building long-running assistants are replacing "
            "flat vector stores with graphs, arguing that relationships between "
            "people, places and events are what make recall useful."
        ),
        "published": _day(1),
        "topics": ["Artificial Intelligence", "Databases"],
        "people": ["Priya Raman"],
        "organizations": ["Neo4j"],
        "geos": ["San Mateo"],
    },
    {
        "title": "Retrieval-augmented generation grows up",
        "abstract": (
            "After two years of prototypes, RAG pipelines are being rebuilt "
            "around structured retrieval, evaluation harnesses and provenance."
        ),
        "published": _day(2),
        "topics": ["Artificial Intelligence"],
        "people": ["Daniel Okafor"],
        "organizations": ["OpenAI"],
        "geos": ["San Francisco"],
    },
    {
        "title": "Europe finalises guidance for high-risk AI systems",
        "abstract": (
            "Regulators published implementation guidance covering transparency, "
            "record keeping and human oversight for high-risk deployments."
        ),
        "published": _day(3),
        "topics": ["Policy", "Artificial Intelligence"],
        "people": ["Margrethe Lindqvist"],
        "organizations": ["European Commission"],
        "geos": ["Brussels"],
    },
    {
        "title": "Offshore wind auction clears at record low price",
        "abstract": (
            "A North Sea auction closed well under forecasts, pushing the "
            "levelised cost of offshore wind below new gas capacity."
        ),
        "published": _day(4),
        "topics": ["Energy", "Climate"],
        "people": ["Sven Halvorsen"],
        "organizations": ["Statkraft"],
        "geos": ["Oslo"],
    },
    {
        "title": "Battery storage buildout outpaces grid connections",
        "abstract": (
            "Developers warn that interconnection queues, not cell supply, are "
            "now the binding constraint on grid-scale storage."
        ),
        "published": _day(5),
        "topics": ["Energy"],
        "people": ["Amara Diallo"],
        "organizations": ["National Grid"],
        "geos": ["London"],
    },
    {
        "title": "Chip packaging becomes the new bottleneck",
        "abstract": (
            "Advanced packaging capacity is sold out through next year, shifting "
            "competition from lithography to assembly and test."
        ),
        "published": _day(6),
        "topics": ["Semiconductors"],
        "people": ["Wei-Lin Chen"],
        "organizations": ["TSMC"],
        "geos": ["Hsinchu"],
    },
    {
        "title": "Open-source models close the gap on reasoning benchmarks",
        "abstract": (
            "Independent evaluations put several open-weight models within a few "
            "points of proprietary leaders on multi-step reasoning tasks."
        ),
        "published": _day(8),
        "topics": ["Artificial Intelligence", "Open Source"],
        "people": ["Lucia Ferreira"],
        "organizations": ["Hugging Face"],
        "geos": ["Paris"],
    },
    {
        "title": "Central bank holds rates as services inflation cools",
        "abstract": (
            "Policymakers kept the benchmark rate unchanged, citing a slower "
            "pace of services price growth and softening wage data."
        ),
        "published": _day(9),
        "topics": ["Economy"],
        "people": ["Helen Maxwell"],
        "organizations": ["Bank of England"],
        "geos": ["London"],
    },
    {
        "title": "Rail operators trial predictive maintenance on freight corridors",
        "abstract": (
            "Sensor networks and graph analytics are being used to anticipate "
            "track faults before they cause cascading delays."
        ),
        "published": _day(11),
        "topics": ["Transport", "Databases"],
        "people": ["Tomasz Wójcik"],
        "organizations": ["Deutsche Bahn"],
        "geos": ["Berlin"],
    },
    {
        "title": "Hospital network publishes results of AI triage pilot",
        "abstract": (
            "A twelve-month pilot reduced median time to specialist review, but "
            "auditors flagged gaps in documentation of model decisions."
        ),
        "published": _day(13),
        "topics": ["Healthcare", "Artificial Intelligence"],
        "people": ["Ruth Adeyemi"],
        "organizations": ["Mayo Clinic"],
        "geos": ["Rochester"],
    },
    {
        "title": "Cities rewrite zoning rules to speed housing approvals",
        "abstract": (
            "Several metropolitan authorities moved to by-right approvals for "
            "mid-rise housing near transit."
        ),
        "published": _day(16),
        "topics": ["Housing", "Policy"],
        "people": ["Carlos Mendes"],
        "organizations": ["City of Austin"],
        "geos": ["Austin"],
    },
    {
        "title": "Container shipping rates fall as new capacity lands",
        "abstract": (
            "A wave of newly delivered vessels pushed spot rates on Asia-Europe "
            "routes to their lowest level in two years."
        ),
        "published": _day(18),
        "topics": ["Trade", "Transport"],
        "people": ["Nadia Haddad"],
        "organizations": ["Maersk"],
        "geos": ["Copenhagen"],
    },
    {
        "title": "Quantum error correction milestone reported",
        "abstract": (
            "Researchers demonstrated a logical qubit with a lower error rate "
            "than its physical components, a long-sought threshold."
        ),
        "published": _day(21),
        "topics": ["Quantum Computing"],
        "people": ["Hiroshi Tanaka"],
        "organizations": ["Delft University of Technology"],
        "geos": ["Delft"],
    },
    {
        "title": "Drought reshapes planting decisions across the grain belt",
        "abstract": (
            "Persistent rainfall deficits are pushing growers toward "
            "shorter-season varieties and wider crop rotations."
        ),
        "published": _day(24),
        "topics": ["Agriculture", "Climate"],
        "people": ["Grace Whitfield"],
        "organizations": ["Food and Agriculture Organization"],
        "geos": ["Des Moines"],
    },
    {
        "title": "Privacy regulator fines data broker over location sales",
        "abstract": (
            "The penalty covered the sale of precise location histories without "
            "a lawful basis, and orders deletion of the underlying data."
        ),
        "published": _day(27),
        "topics": ["Policy", "Privacy"],
        "people": ["Anton Kovalenko"],
        "organizations": ["Information Commissioner's Office"],
        "geos": ["Wilmslow"],
    },
    {
        "title": "Vector and graph search converge in one query language",
        "abstract": (
            "Database vendors are folding vector similarity into their query "
            "languages, letting one statement mix embeddings with traversals."
        ),
        "published": _day(30),
        "topics": ["Databases", "Artificial Intelligence"],
        "people": ["Priya Raman"],
        "organizations": ["Neo4j"],
        "geos": ["Malmö"],
    },
]

CONSTRAINTS = [
    "CREATE CONSTRAINT news_article_url IF NOT EXISTS FOR (a:Article) REQUIRE a.url IS UNIQUE",
    "CREATE CONSTRAINT news_topic_name IF NOT EXISTS FOR (t:Topic) REQUIRE t.name IS UNIQUE",
    "CREATE CONSTRAINT news_person_name IF NOT EXISTS FOR (p:Person) REQUIRE p.name IS UNIQUE",
    "CREATE CONSTRAINT news_org_name IF NOT EXISTS FOR (o:Organization) REQUIRE o.name IS UNIQUE",
    "CREATE CONSTRAINT news_geo_name IF NOT EXISTS FOR (g:Geo) REQUIRE g.name IS UNIQUE",
]

LOAD_ARTICLE = """
MERGE (a:Article {url: $url})
SET a.title = $title,
    a.abstract = $abstract,
    a.published = datetime($published + 'T12:00:00Z'),
    a.embedding = $embedding
WITH a
CALL (a) {
  UNWIND $topics AS name
  MERGE (t:Topic {name: name})
  MERGE (a)-[:HAS_TOPIC]->(t)
}
CALL (a) {
  UNWIND $people AS name
  MERGE (p:Person {name: name})
  MERGE (a)-[:ABOUT_PERSON]->(p)
}
CALL (a) {
  UNWIND $organizations AS name
  MERGE (o:Organization {name: name})
  MERGE (a)-[:ABOUT_ORGANIZATION]->(o)
}
CALL (a) {
  UNWIND $geos AS name
  MERGE (g:Geo {name: name})
  MERGE (a)-[:ABOUT_GEO]->(g)
}
RETURN a.url AS url
"""

RESET = """
MATCH (n)
WHERE n:Article OR n:Topic OR n:Person OR n:Organization OR n:Geo OR n:Photo
DETACH DELETE n
"""


async def _embeddings(texts: list[str]) -> list[list[float]] | None:
    """Embed article text, or return None when no provider is configured."""
    settings = get_settings()
    if not settings.openai_api_key.get_secret_value():
        return None
    from neo4j_agent_memory.llm import from_provider

    embedder = from_provider(
        settings.news_embedding_model,
        kind="embedding",
        api_key=settings.openai_api_key.get_secret_value(),
    )
    vectors = await embedder.embed(texts)
    logger.info("Embedded %d articles with %s", len(vectors), embedder.model)
    return vectors


async def main(reset: bool) -> int:
    settings = get_settings()
    driver = AsyncGraphDatabase.driver(
        settings.news_graph_uri,
        auth=(settings.news_graph_username, settings.news_graph_password.get_secret_value()),
    )
    try:
        await driver.verify_connectivity()
    except Exception as e:
        logger.error("Cannot reach the news graph at %s: %s", settings.news_graph_uri, e)
        await driver.close()
        return 1

    vectors = await _embeddings([f"{a['title']}. {a['abstract']}" for a in ARTICLES])

    async with driver.session(database=settings.news_graph_database) as session:
        if reset:
            await session.run(RESET)
            logger.info("Removed the existing sample news graph")

        for statement in CONSTRAINTS:
            await session.run(statement)

        for index, article in enumerate(ARTICLES):
            slug = article["title"].lower().replace(" ", "-")[:60]
            await session.run(
                LOAD_ARTICLE,
                url=f"https://example.com/news/{slug}",
                title=article["title"],
                abstract=article["abstract"],
                published=article["published"],
                embedding=vectors[index] if vectors else None,
                topics=article["topics"],
                people=article["people"],
                organizations=article["organizations"],
                geos=article["geos"],
            )

        if vectors:
            await session.run(
                "CREATE VECTOR INDEX article_embeddings IF NOT EXISTS "
                "FOR (a:Article) ON a.embedding "
                "OPTIONS {indexConfig: {"
                "`vector.dimensions`: $dims, `vector.similarity_function`: 'cosine'}}",
                dims=len(vectors[0]),
            )
            logger.info("Created the article_embeddings vector index (%d dims)", len(vectors[0]))
        else:
            logger.warning(
                "No OPENAI_API_KEY — articles loaded without embeddings. "
                "Text tools work; vector_search_news will report a missing index."
            )

    await driver.close()
    logger.info("Loaded %d sample articles into %s", len(ARTICLES), settings.news_graph_uri)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--reset",
        action="store_true",
        help="delete existing Article/Topic/Person/Organization/Geo nodes first",
    )
    raise SystemExit(asyncio.run(main(parser.parse_args().reset)))
