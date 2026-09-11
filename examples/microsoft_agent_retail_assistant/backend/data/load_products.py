"""Load sample product data into Neo4j for the retail assistant demo.

Creates 16 ``:Product`` nodes plus ``:Category`` / ``:Brand`` / ``:Attribute``
nodes, the ``IN_CATEGORY`` / ``MADE_BY`` / ``HAS_ATTRIBUTE`` / ``SIMILAR_TO`` /
``BOUGHT_TOGETHER`` relationships between them, and the ``product_embedding``
vector index.

Usage::

    python -m data.load_products            # idempotent MERGE, keeps everything else
    python -m data.load_products --reset     # DETACH DELETE the whole database first

Every write is a ``MERGE``, so the default run is **non-destructive** and safe
to repeat — including on a database that already holds agent memory. Only
``--reset`` deletes, and it asks for confirmation unless ``--yes`` is given.

Embedding generation needs ``OPENAI_API_KEY``. Without it the products are
still created and the backend falls back to text search.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

from dotenv import load_dotenv
from neo4j import AsyncGraphDatabase

load_dotenv()

#: Embedding model used for product vectors. Must match the dimensions of the
#: ``product_embedding`` index below and ``OPENAI_EMBEDDING_MODEL`` in the app.
EMBEDDING_MODEL = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
#: Dimensions of :data:`EMBEDDING_MODEL`.
EMBEDDING_DIMENSIONS = 1536

# Sample product catalog
PRODUCTS = [
    # Running Shoes
    {
        "id": "nike-pegasus-40",
        "name": "Nike Pegasus 40",
        "description": "Versatile everyday running shoe with responsive React foam cushioning. Great for road running and daily training.",
        "price": 130.00,
        "category": "Running Shoes",
        "brand": "Nike",
        "in_stock": True,
        "inventory": 45,
        "popularity": 0.95,
        "style": "athletic",
        "image_url": "",
        "attributes": {"cushion": "medium", "weight": "272g", "drop": "10mm", "surface": "road"},
    },
    {
        "id": "adidas-ultraboost-24",
        "name": "Adidas Ultraboost 24",
        "description": "Premium running shoe with Boost midsole for energy return. Primeknit upper for adaptive fit.",
        "price": 190.00,
        "category": "Running Shoes",
        "brand": "Adidas",
        "in_stock": True,
        "inventory": 30,
        "popularity": 0.90,
        "style": "athletic",
        "image_url": "",
        "attributes": {"cushion": "high", "weight": "310g", "drop": "10mm", "surface": "road"},
    },
    {
        "id": "nb-990v6",
        "name": "New Balance 990v6",
        "description": "Heritage running shoe combining classic style with modern performance. Made in USA with premium materials.",
        "price": 200.00,
        "category": "Running Shoes",
        "brand": "New Balance",
        "in_stock": True,
        "inventory": 20,
        "popularity": 0.85,
        "style": "classic",
        "image_url": "",
        "attributes": {"cushion": "medium", "weight": "340g", "drop": "12mm", "surface": "road"},
    },
    {
        "id": "asics-gel-nimbus-26",
        "name": "ASICS Gel-Nimbus 26",
        "description": "Maximum cushion neutral running shoe with FF Blast Plus cushioning and PureGEL technology.",
        "price": 160.00,
        "category": "Running Shoes",
        "brand": "ASICS",
        "in_stock": True,
        "inventory": 35,
        "popularity": 0.80,
        "style": "athletic",
        "image_url": "",
        "attributes": {"cushion": "high", "weight": "290g", "drop": "8mm", "surface": "road"},
    },
    {
        "id": "brooks-ghost-16",
        "name": "Brooks Ghost 16",
        "description": "Smooth and balanced neutral running shoe with DNA Loft v2 cushioning for a soft, smooth ride.",
        "price": 140.00,
        "category": "Running Shoes",
        "brand": "Brooks",
        "in_stock": True,
        "inventory": 40,
        "popularity": 0.82,
        "style": "athletic",
        "image_url": "",
        "attributes": {"cushion": "medium", "weight": "280g", "drop": "12mm", "surface": "road"},
    },
    # Casual Shoes
    {
        "id": "nike-air-max-90",
        "name": "Nike Air Max 90",
        "description": "Iconic lifestyle sneaker with visible Air cushioning. A timeless streetwear classic.",
        "price": 130.00,
        "category": "Casual Shoes",
        "brand": "Nike",
        "in_stock": True,
        "inventory": 50,
        "popularity": 0.92,
        "style": "streetwear",
        "image_url": "",
        "attributes": {"cushion": "medium", "weight": "340g", "occasion": "casual"},
    },
    {
        "id": "adidas-stan-smith",
        "name": "Adidas Stan Smith",
        "description": "Minimalist leather tennis shoe turned everyday classic. Clean white design with green heel tab.",
        "price": 100.00,
        "category": "Casual Shoes",
        "brand": "Adidas",
        "in_stock": True,
        "inventory": 60,
        "popularity": 0.88,
        "style": "minimalist",
        "image_url": "",
        "attributes": {"material": "leather", "occasion": "casual", "closure": "lace"},
    },
    {
        "id": "nb-574",
        "name": "New Balance 574",
        "description": "Classic retro sneaker with ENCAP midsole cushioning. Versatile design for everyday wear.",
        "price": 90.00,
        "category": "Casual Shoes",
        "brand": "New Balance",
        "in_stock": True,
        "inventory": 55,
        "popularity": 0.84,
        "style": "retro",
        "image_url": "",
        "attributes": {"cushion": "medium", "material": "suede/mesh", "occasion": "casual"},
    },
    # Apparel
    {
        "id": "nike-drifit-tee",
        "name": "Nike Dri-FIT Running Shirt",
        "description": "Lightweight moisture-wicking running shirt with Dri-FIT technology. Keeps you dry during intense workouts.",
        "price": 35.00,
        "category": "Apparel",
        "brand": "Nike",
        "in_stock": True,
        "inventory": 100,
        "popularity": 0.75,
        "style": "athletic",
        "image_url": "",
        "attributes": {"material": "polyester", "fit": "standard", "technology": "Dri-FIT"},
    },
    {
        "id": "adidas-running-shorts",
        "name": "Adidas Running Shorts",
        "description": "Lightweight running shorts with built-in brief liner. AEROREADY moisture management for comfort.",
        "price": 30.00,
        "category": "Apparel",
        "brand": "Adidas",
        "in_stock": True,
        "inventory": 80,
        "popularity": 0.70,
        "style": "athletic",
        "image_url": "",
        "attributes": {"material": "recycled polyester", "fit": "regular", "inseam": "5 inch"},
    },
    {
        "id": "ua-coldgear",
        "name": "Under Armour ColdGear Base Layer",
        "description": "Warm base layer for cold-weather running. Dual-layer fabric traps heat without bulk.",
        "price": 55.00,
        "category": "Apparel",
        "brand": "Under Armour",
        "in_stock": True,
        "inventory": 40,
        "popularity": 0.72,
        "style": "athletic",
        "image_url": "",
        "attributes": {
            "material": "polyester/elastane",
            "fit": "compression",
            "technology": "ColdGear",
        },
    },
    # Accessories
    {
        "id": "garmin-forerunner-265",
        "name": "Garmin Forerunner 265",
        "description": "GPS running smartwatch with AMOLED display. Tracks pace, heart rate, training status, and recovery.",
        "price": 450.00,
        "category": "Accessories",
        "brand": "Garmin",
        "in_stock": True,
        "inventory": 15,
        "popularity": 0.88,
        "style": "tech",
        "image_url": "",
        "attributes": {"battery_life": "13 days", "gps": "multi-band", "display": "AMOLED"},
    },
    {
        "id": "nike-running-socks",
        "name": "Nike Multiplier Running Socks (2-Pack)",
        "description": "Cushioned running socks with Dri-FIT moisture wicking. Arch band support and reinforced heel and toe.",
        "price": 18.00,
        "category": "Accessories",
        "brand": "Nike",
        "in_stock": True,
        "inventory": 200,
        "popularity": 0.65,
        "style": "athletic",
        "image_url": "",
        "attributes": {"material": "polyester blend", "cushion": "medium", "pack_size": "2"},
    },
    {
        "id": "hydration-belt",
        "name": "Nathan Trail Mix Plus Hydration Belt",
        "description": "Adjustable hydration belt with two 10oz flasks. Zippered pocket for phone and essentials.",
        "price": 40.00,
        "category": "Accessories",
        "brand": "Nathan",
        "in_stock": False,
        "inventory": 0,
        "popularity": 0.60,
        "style": "athletic",
        "image_url": "",
        "attributes": {"capacity": "20oz", "pockets": "1 zippered", "bottles": "2"},
    },
    # Equipment
    {
        "id": "foam-roller",
        "name": "TriggerPoint GRID Foam Roller",
        "description": "Multi-density foam roller for muscle recovery and self-massage. Patented GRID surface for targeted relief.",
        "price": 35.00,
        "category": "Equipment",
        "brand": "TriggerPoint",
        "in_stock": True,
        "inventory": 25,
        "popularity": 0.68,
        "style": "recovery",
        "image_url": "",
        "attributes": {"length": "13 inch", "density": "multi", "material": "EVA foam"},
    },
    {
        "id": "resistance-bands",
        "name": "Theraband Resistance Bands Set",
        "description": "Set of 5 resistance bands for strength training and injury prevention. Progressive resistance levels.",
        "price": 25.00,
        "category": "Equipment",
        "brand": "Theraband",
        "in_stock": True,
        "inventory": 50,
        "popularity": 0.62,
        "style": "training",
        "image_url": "",
        "attributes": {"pieces": "5", "resistance_levels": "light to heavy", "material": "latex"},
    },
]

# Categories with descriptions
CATEGORIES = {
    "Running Shoes": "Performance footwear designed for running and jogging",
    "Casual Shoes": "Everyday lifestyle footwear for casual wear",
    "Apparel": "Athletic clothing for running and training",
    "Accessories": "Watches, socks, hydration, and other running accessories",
    "Equipment": "Recovery and training equipment for runners",
}

# Manually defined "bought together" pairs (product_id_1, product_id_2, frequency, confidence)
BOUGHT_TOGETHER = [
    ("nike-pegasus-40", "nike-running-socks", 85, 0.72),
    ("nike-pegasus-40", "nike-drifit-tee", 60, 0.55),
    ("adidas-ultraboost-24", "adidas-running-shorts", 50, 0.48),
    ("brooks-ghost-16", "hydration-belt", 30, 0.35),
    ("garmin-forerunner-265", "nike-pegasus-40", 40, 0.42),
    ("foam-roller", "resistance-bands", 55, 0.60),
    ("nike-air-max-90", "adidas-stan-smith", 20, 0.15),
    ("ua-coldgear", "nike-running-socks", 35, 0.38),
]

# Attribute nodes to create (name, value pairs shared across products)
SHARED_ATTRIBUTES = [
    ("Cushion Level", "medium"),
    ("Cushion Level", "high"),
    ("Surface", "road"),
    ("Occasion", "casual"),
    ("Fit", "standard"),
    ("Fit", "compression"),
    ("Material", "polyester"),
]


async def load_sample_data(*, reset: bool = False) -> None:
    """Load all sample data into Neo4j.

    Args:
        reset: When True, delete every node in the database first. Off by
            default — the loader only MERGEs, so repeat runs are harmless.
    """
    uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    user = os.getenv("NEO4J_USER", "neo4j")
    password = os.getenv("NEO4J_PASSWORD")
    if not password:
        print("NEO4J_PASSWORD is not set. Copy .env.example to .env and fill it in.")
        raise SystemExit(2)

    driver = AsyncGraphDatabase.driver(uri, auth=(user, password))

    async with driver.session() as session:
        if reset:
            print("Clearing existing data (--reset)...")
            await _clear_database(session)

        print("Creating products...")
        await _create_products(session)

        print("Creating categories and brands...")
        await _create_categories_and_brands(session)

        print("Creating similarity relationships...")
        await _create_similarity_relationships(session)

        print("Creating bought-together relationships...")
        await _create_bought_together(session)

        print("Creating attribute nodes and relationships...")
        await _create_attributes(session)

        print("Creating vector index...")
        await _create_vector_index(session)

        print("Generating embeddings...")
        await _generate_embeddings(session)

    await driver.close()
    print("\nSample data loaded successfully!")
    print(f"  Products: {len(PRODUCTS)}")
    print(f"  Categories: {len(CATEGORIES)}")
    print(f"  Bought-together pairs: {len(BOUGHT_TOGETHER)}")


async def _clear_database(session):
    """Delete all nodes and relationships."""
    await session.run("MATCH (n) DETACH DELETE n")


async def _create_products(session):
    """Create Product nodes with all properties."""
    await session.run(
        """
        UNWIND $products AS product
        MERGE (p:Product {id: product.id})
        SET p.name = product.name,
            p.description = product.description,
            p.price = product.price,
            p.category = product.category,
            p.brand = product.brand,
            p.in_stock = product.in_stock,
            p.inventory = product.inventory,
            p.popularity = product.popularity,
            p.style = product.style,
            p.image_url = product.image_url
        """,
        {"products": PRODUCTS},
    )


async def _create_categories_and_brands(session):
    """Create Category and Brand nodes with relationships."""
    # Create categories
    await session.run(
        """
        UNWIND $categories AS cat
        MERGE (c:Category {name: cat.name})
        SET c.description = cat.description
        """,
        {"categories": [{"name": k, "description": v} for k, v in CATEGORIES.items()]},
    )

    # Create brands from products
    await session.run(
        """
        MATCH (p:Product)
        WITH DISTINCT p.brand AS brand_name
        WHERE brand_name IS NOT NULL
        MERGE (b:Brand {name: brand_name})
        """
    )

    # Create relationships
    await session.run(
        """
        MATCH (p:Product), (c:Category {name: p.category})
        MERGE (p)-[:IN_CATEGORY]->(c)
        """
    )

    await session.run(
        """
        MATCH (p:Product), (b:Brand {name: p.brand})
        MERGE (p)-[:MADE_BY]->(b)
        """
    )


async def _create_similarity_relationships(session):
    """Create SIMILAR_TO relationships between products in the same category."""
    await session.run(
        """
        MATCH (p1:Product)-[:IN_CATEGORY]->(c)<-[:IN_CATEGORY]-(p2:Product)
        WHERE p1 <> p2
        MERGE (p1)-[:SIMILAR_TO]-(p2)
        """
    )


async def _create_bought_together(session):
    """Create BOUGHT_TOGETHER relationships."""
    await session.run(
        """
        UNWIND $pairs AS pair
        MATCH (p1:Product {id: pair.id1}), (p2:Product {id: pair.id2})
        MERGE (p1)-[r:BOUGHT_TOGETHER]-(p2)
        SET r.frequency = pair.frequency,
            r.confidence = pair.confidence
        """,
        {
            "pairs": [
                {"id1": p[0], "id2": p[1], "frequency": p[2], "confidence": p[3]}
                for p in BOUGHT_TOGETHER
            ]
        },
    )


async def _create_attributes(session):
    """Create Attribute nodes and HAS_ATTRIBUTE relationships."""
    # Create Attribute nodes
    await session.run(
        """
        UNWIND $attrs AS attr
        MERGE (a:Attribute {name: attr.name, value: attr.value})
        """,
        {"attrs": [{"name": a[0], "value": a[1]} for a in SHARED_ATTRIBUTES]},
    )

    # Build product-to-attribute links from the Python-side attributes data
    # Map: attribute_name -> (product_attributes_key, attr_node_name)
    attr_mappings = [
        ("cushion", "Cushion Level"),
        ("surface", "Surface"),
        ("occasion", "Occasion"),
        ("fit", "Fit"),
        ("material", "Material"),
    ]

    links = []
    for product in PRODUCTS:
        attrs = product.get("attributes", {})
        for attr_key, attr_name in attr_mappings:
            if attr_key in attrs:
                links.append(
                    {
                        "product_id": product["id"],
                        "attr_name": attr_name,
                        "attr_value": attrs[attr_key],
                    }
                )

    await session.run(
        """
        UNWIND $links AS link
        MATCH (p:Product {id: link.product_id})
        MATCH (a:Attribute {name: link.attr_name, value: link.attr_value})
        MERGE (p)-[:HAS_ATTRIBUTE]->(a)
        """,
        {"links": links},
    )


async def _create_vector_index(session):
    """Create vector index for product embeddings."""
    try:
        # Index options cannot be parameterised, so the dimension is
        # interpolated — it is an int constant defined in this module, never
        # user input.
        await session.run(
            f"""
            CREATE VECTOR INDEX product_embedding IF NOT EXISTS
            FOR (p:Product)
            ON (p.embedding)
            OPTIONS {{indexConfig: {{
                `vector.dimensions`: {int(EMBEDDING_DIMENSIONS)},
                `vector.similarity_function`: 'cosine'
            }}}}
            """
        )
        print("  Vector index created (or already exists)")
    except Exception as e:
        print(f"  Vector index creation note: {e}")


async def _generate_embeddings(session):
    """Generate and store embeddings for products using OpenAI."""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("  OPENAI_API_KEY not set — skipping embedding generation.")
        print("  Products will work with text search fallback.")
        return

    try:
        from openai import AsyncOpenAI

        openai_client = AsyncOpenAI(api_key=api_key)

        # Build texts to embed
        texts = [f"{p['name']}: {p['description']}" for p in PRODUCTS]

        # Batch embed
        response = await openai_client.embeddings.create(
            input=texts,
            model=EMBEDDING_MODEL,
        )

        # Store embeddings on products
        for i, product in enumerate(PRODUCTS):
            embedding = response.data[i].embedding
            await session.run(
                """
                MATCH (p:Product {id: $product_id})
                SET p.embedding = $embedding
                """,
                {"product_id": product["id"], "embedding": embedding},
            )

        print(f"  Generated embeddings for {len(PRODUCTS)} products ({EMBEDDING_MODEL})")

    except ImportError:
        print("  openai package not installed — skipping embedding generation.")
        print("  Install with: pip install openai")
    except Exception as e:
        print(f"  Embedding generation failed: {e}")
        print("  Products will work with text search fallback.")


def main(argv: list[str] | None = None) -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Load the sample product catalog into Neo4j.",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="DELETE every node in the database before loading (destructive).",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip the confirmation prompt for --reset.",
    )
    args = parser.parse_args(argv)

    if args.reset and not args.yes:
        target = os.getenv("NEO4J_URI", "bolt://localhost:7687")
        answer = input(f"--reset will DETACH DELETE every node in {target}. Continue? [y/N] ")
        if answer.strip().lower() not in {"y", "yes"}:
            print("Aborted.")
            sys.exit(1)

    asyncio.run(load_sample_data(reset=args.reset))


if __name__ == "__main__":
    main()
