#!/usr/bin/env python3
"""Vertex AI embeddings with Neo4j Agent Memory.

Demonstrates:

- ``VertexAIEmbedder`` with ``gemini-embedding-001`` (``text-embedding-004`` was
  shut down on 2026-01-14 and now raises at construction).
- ``output_dimensionality`` — the model's native size is 3072; the embedder
  truncates to 768 by default so Neo4j vector indexes sized for the old default
  keep working.
- Single and batch embedding, plus cosine similarity over the results.
- Task types (``RETRIEVAL_DOCUMENT`` vs ``RETRIEVAL_QUERY``) for asymmetric
  search.
- Wiring the provider into ``MemoryClient`` and writing **multi-tenant**
  messages (``memory.multi_tenant=True`` + ``user_identifier=``).

Requirements::

    pip install "neo4j-agent-memory[vertex-ai]"
    gcloud auth application-default login
    export GOOGLE_CLOUD_PROJECT=your-project-id

The embedding phases need real Google Cloud credentials and are skipped without
``GOOGLE_CLOUD_PROJECT``; the MemoryClient phase runs on whatever embedder
``_common.build_settings()`` resolves, so it also works with OpenAI or a local
sentence-transformers model.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime

from _common import (
    VERTEX_EMBEDDING_MODEL,
    build_settings,
    describe_settings,
    load_env,
    use_vertex_embeddings,
)

USER_ID = os.getenv("DEMO_USER_ID", "demo-user")

TEXTS = [
    "Graph databases excel at relationship queries.",
    "Vector search enables semantic similarity matching.",
    "Agent memory combines short-term and long-term storage.",
    "Entity extraction identifies people, places, and organizations.",
    "The Model Context Protocol enables tool-based AI interactions.",
]


def vertex_available() -> bool:
    """Vertex phases need a GCP project and the optional extra."""
    if not os.getenv("GOOGLE_CLOUD_PROJECT"):
        print("Skipping Vertex AI phases: GOOGLE_CLOUD_PROJECT is not set.")
        print("  export GOOGLE_CLOUD_PROJECT=your-project-id")
        return False
    return True


def cosine_similarity(a: list[float], b: list[float]) -> float:
    dot_product: float = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a: float = sum(x * x for x in a) ** 0.5
    norm_b: float = sum(x * x for x in b) ** 0.5
    return float(dot_product / (norm_a * norm_b))


async def demo_basic_embeddings() -> None:
    """Single and batch embedding, plus similarity ranking."""
    from neo4j_agent_memory.embeddings.vertex_ai import VertexAIEmbedder

    print("=" * 60)
    print("Vertex AI Embeddings - Basic Usage")
    print("=" * 60)
    print()

    embedder = VertexAIEmbedder(
        model=VERTEX_EMBEDDING_MODEL,
        project_id=os.getenv("GOOGLE_CLOUD_PROJECT"),
        location=os.getenv("VERTEX_AI_LOCATION", "us-central1"),
    )

    print(f"Model: {embedder.model}")
    print(f"Dimensions: {embedder.dimensions}")
    print(f"Output dimensionality: {embedder.output_dimensionality} (native: 3072)")
    print(f"Task type: {embedder.task_type}")
    print()

    print("1. Single Text Embedding")
    print("-" * 40)
    text = "Neo4j is a graph database that stores and manages connected data."
    embedding = await embedder.embed(text)
    print(f"   Text: {text[:50]}...")
    print(f"   Embedding dimensions: {len(embedding)}")
    print(f"   First 5 values: {embedding[:5]}")
    print()

    print("2. Batch Embedding")
    print("-" * 40)
    embeddings = await embedder.embed_batch(TEXTS)
    print(f"   Processed {len(TEXTS)} texts")
    for i, (text, emb) in enumerate(zip(TEXTS, embeddings, strict=False)):
        print(f"   [{i + 1}] {text[:40]}... → {len(emb)} dims")
    print()

    print("3. Semantic Similarity")
    print("-" * 40)
    query = "How do graph databases handle relationships?"
    query_embedding = await embedder.embed(query)
    print(f"   Query: {query}")
    print()
    similarities = [
        (text, cosine_similarity(query_embedding, emb))
        for text, emb in zip(TEXTS, embeddings, strict=False)
    ]
    similarities.sort(key=lambda pair: pair[1], reverse=True)
    for text, sim in similarities:
        print(f"   {sim:.4f} - {text[:50]}...")
    print()


async def demo_task_types() -> None:
    """Asymmetric search: embed documents and queries with matched task types."""
    from neo4j_agent_memory.embeddings.vertex_ai import VertexAIEmbedder

    print("=" * 60)
    print("Vertex AI Embeddings - Task Types")
    print("=" * 60)
    print()

    for task_type, description in (
        ("RETRIEVAL_DOCUMENT", "For indexing documents to be searched"),
        ("RETRIEVAL_QUERY", "For search queries"),
        ("SEMANTIC_SIMILARITY", "For comparing text similarity"),
        ("CLASSIFICATION", "For text classification tasks"),
        ("CLUSTERING", "For clustering similar texts"),
    ):
        print(f"  • {task_type}\n    {description}")

    print()
    print("Example: different task types for query vs document")
    print("-" * 40)

    doc_embedder = VertexAIEmbedder(
        model=VERTEX_EMBEDDING_MODEL,
        task_type="RETRIEVAL_DOCUMENT",
        project_id=os.getenv("GOOGLE_CLOUD_PROJECT"),
    )
    query_embedder = VertexAIEmbedder(
        model=VERTEX_EMBEDDING_MODEL,
        task_type="RETRIEVAL_QUERY",
        project_id=os.getenv("GOOGLE_CLOUD_PROJECT"),
    )

    doc_embedding = await doc_embedder.embed(
        "Neo4j provides native graph storage and processing capabilities."
    )
    query_embedding = await query_embedder.embed("What are Neo4j's core features?")

    print(f"  Document ({doc_embedder.task_type}): {len(doc_embedding)} dims")
    print(f"  Query ({query_embedder.task_type}): {len(query_embedding)} dims")
    print()
    print("  Matched task types improve retrieval quality for asymmetric search.")
    print()


async def demo_with_memory_client() -> None:
    """Store and search multi-tenant messages through MemoryClient."""
    from neo4j_agent_memory import MemoryClient

    print("=" * 60)
    print("Vertex AI Embeddings - With MemoryClient")
    print("=" * 60)
    print()

    # multi_tenant=True makes ``user_identifier=`` mandatory on writes, which is
    # what scopes messages to a tenant (there is no ``user_id=`` parameter on
    # add_message).
    settings = build_settings(multi_tenant=True)
    print("Configuration:")
    describe_settings(settings)
    if not use_vertex_embeddings():
        print("  (set EMBEDDING_PROVIDER=vertex_ai + GOOGLE_CLOUD_PROJECT for Vertex AI)")
    print()

    async with MemoryClient(settings) as client:
        print(f"Connected backend: {client.backend}")
        session_id = f"vertex-demo-{datetime.now().strftime('%Y%m%d%H%M%S')}"

        # One :User node per tenant; messages are linked to it.
        await client.users.upsert_user(identifier=USER_ID)

        print()
        print("1. Storing messages (scoped to one tenant)...")
        print("-" * 40)
        messages = [
            ("user", "Tell me about graph databases and their use cases."),
            (
                "assistant",
                "Graph databases like Neo4j excel at managing connected data. "
                "They're ideal for social networks, recommendation engines, "
                "fraud detection, and knowledge graphs.",
            ),
            ("user", "How does vector search integrate with graphs?"),
            (
                "assistant",
                "Neo4j combines vector indexes with graph traversal. "
                "You can find semantically similar nodes and then explore "
                "their relationships for richer context.",
            ),
        ]
        for role, content in messages:
            await client.short_term.add_message(
                session_id=session_id,
                role=role,
                content=content,
                user_identifier=USER_ID,
            )
            print(f"  [{role}] {content[:50]}...")
        print()

        print("2. Semantic search over the stored messages...")
        print("-" * 40)
        for query in (
            "graph database applications",
            "combining vectors and graphs",
            "Neo4j features",
        ):
            print(f"\n  Query: '{query}'")
            results = await client.short_term.search_messages(
                query=query,
                session_id=session_id,
                limit=2,
            )
            if not results:
                print("    (no matches above the similarity threshold)")
            for msg in results:
                print(f"    → {msg.content[:60]}...")
        print()

        print("3. Confirming the tenant link")
        print("-" * 40)
        rows = await client.query.cypher(
            "MATCH (u:User {identifier: $user})-[:HAS_CONVERSATION]->"
            "(c:Conversation {session_id: $session}) "
            "RETURN count(c) AS conversations",
            {"user": USER_ID, "session": session_id},
        )
        print(f"  :User {USER_ID} → {rows[0]['conversations']} conversation(s)")
        print()


async def main() -> None:
    """Run the Vertex AI embedding demos."""
    load_env()

    print("\n" + "=" * 60)
    print("Neo4j Agent Memory - Vertex AI Embeddings Demo")
    print("=" * 60 + "\n")

    if vertex_available():
        await demo_basic_embeddings()
        await demo_task_types()

    await demo_with_memory_client()

    print("\n" + "=" * 60)
    print("Demo complete!")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
