"""Podcast search tools for the chat agent.

This module provides tools for:
- Podcast content search (transcripts, speakers, episodes)
- Entity queries (people, organizations, topics)
- Location queries with geospatial features
- User preferences and reasoning memory
"""

import json
import logging
import math
import re
import unicodedata
from typing import Any

from pydantic_ai import RunContext

from src.agent.dependencies import AgentDeps

logger = logging.getLogger(__name__)

# Preference categories this demo writes (see ``api/routes/chat.py``) and reads
# back in ``get_user_preferences``. Keeping the list in one place means the
# write path and the read path cannot drift apart.
PREFERENCE_CATEGORIES: tuple[str, ...] = ("content", "format", "topics", "general")


def _guest_to_session_id(guest_name: str) -> str:
    """Convert a guest name to a session_id format.

    Handles Unicode characters by normalizing to ASCII equivalents
    (e.g., "Tobi Lütke" -> "tobi-lutke", not "tobi-l-tke").

    Must stay in lockstep with ``slugify()`` in
    ``examples/lennys-memory/scripts/load_transcripts.py`` -- the loader names
    every podcast session with that rule and these tools look sessions up by it.
    ``tests/examples/test_lennys_memory_example.py`` asserts the two agree.
    """
    # Normalize Unicode characters to their ASCII equivalents
    # NFD decomposes characters (ü -> u + combining umlaut)
    # Then we strip combining characters
    normalized = unicodedata.normalize("NFD", guest_name)
    ascii_name = normalized.encode("ascii", "ignore").decode("ascii")

    # Convert to lowercase and replace non-alphanumeric with hyphens
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")
    return f"lenny-podcast-{slug}"


def _haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate the great-circle distance between two points in kilometers."""
    R = 6371  # Earth's radius in kilometers
    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    delta_lat = math.radians(lat2 - lat1)
    delta_lon = math.radians(lon2 - lon1)

    a = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(delta_lon / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


async def search_podcast_content(
    ctx: RunContext[AgentDeps],
    query: str,
    limit: int = 10,
    context_messages: int = 0,
) -> list[dict[str, Any]]:
    """Search podcast transcripts using semantic search.

    Args:
        ctx: The agent run context.
        query: Search terms or topic to find.
        limit: Maximum number of results to return.
        context_messages: Number of surrounding messages to include for context (0-3).
            When > 0, includes messages before/after each result for fuller context.

    Returns:
        List of matching transcript segments with speaker, episode, and content.
        When context_messages > 0, includes surrounding conversation context.
    """
    if not ctx.deps.client:
        return [{"error": "Memory client not available"}]

    # Clamp context_messages to reasonable range
    context_messages = max(0, min(3, context_messages))

    try:
        messages = await ctx.deps.client.short_term.search_messages(
            query=query,
            limit=limit,
            threshold=0.5,
            metadata_filters={"source": "lenny_podcast"},
        )

        results = []
        for msg in messages:
            result = {
                "content": (msg.content[:500] + "..." if len(msg.content) > 500 else msg.content),
                "speaker": msg.metadata.get("speaker", "Unknown"),
                "episode_guest": msg.metadata.get("episode_guest", "Unknown"),
                "timestamp": msg.metadata.get("timestamp", ""),
                "relevance": round(msg.metadata.get("similarity", 0), 3),
            }

            # Fetch surrounding context if requested
            if context_messages > 0 and hasattr(msg, "id") and msg.id:
                try:
                    context = await _get_message_context(ctx, str(msg.id), context_messages)
                    if context:
                        result["context_before"] = context.get("before", [])
                        result["context_after"] = context.get("after", [])
                except Exception:
                    pass  # Silently skip context on error

            results.append(result)

        return results
    except Exception as e:
        logger.exception("search_podcast_content failed")
        return [{"error": f"Search failed: {str(e)}"}]


async def _get_message_context(
    ctx: RunContext[AgentDeps],
    message_id: str,
    context_size: int,
) -> dict[str, list[dict[str, str]]]:
    """Get surrounding messages for context.

    Uses NEXT_MESSAGE relationships if available, otherwise falls back to
    timestamp-based ordering.
    """
    try:
        # Try to get context using message relationships
        query = """
        MATCH (m:Message)
        WHERE elementId(m) = $message_id
        OPTIONAL MATCH (before:Message)-[:NEXT_MESSAGE*1..{context}]->(m)
        OPTIONAL MATCH (m)-[:NEXT_MESSAGE*1..{context}]->(after:Message)
        WITH m,
             collect(DISTINCT before)[0..{context}] AS before_msgs,
             collect(DISTINCT after)[0..{context}] AS after_msgs
        RETURN [b IN before_msgs | {{
            content: left(b.content, 200),
            speaker: b.metadata.speaker
        }}] AS context_before,
        [a IN after_msgs | {{
            content: left(a.content, 200),
            speaker: a.metadata.speaker
        }}] AS context_after
        """.replace("{context}", str(context_size))

        results = await ctx.deps.client.query.cypher(query, {"message_id": message_id})

        if results and results[0]:
            return {
                "before": results[0].get("context_before", []),
                "after": results[0].get("context_after", []),
            }
        return {"before": [], "after": []}
    except Exception:
        return {"before": [], "after": []}


async def search_by_speaker(
    ctx: RunContext[AgentDeps],
    speaker: str,
    topic: str | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Search for what a specific speaker said.

    Args:
        ctx: The agent run context.
        speaker: Name of the speaker (e.g., "Brian Chesky", "Lenny").
        topic: Optional topic to filter by.
        limit: Maximum number of results to return.

    Returns:
        List of transcript segments from the specified speaker.
    """
    if not ctx.deps.client:
        return [{"error": "Memory client not available"}]

    try:
        # If topic is provided, use vector search with speaker filter for best results
        if topic:
            messages = await ctx.deps.client.short_term.search_messages(
                query=topic,
                limit=limit * 2,  # Fetch extra to filter by speaker
                threshold=0.5,  # Lower threshold for better recall
                metadata_filters={"source": "lenny_podcast"},
            )

            # Filter by speaker name (case-insensitive, partial match)
            speaker_lower = speaker.lower()
            results = []
            for msg in messages:
                msg_speaker = msg.metadata.get("speaker", "").lower()
                if speaker_lower in msg_speaker or msg_speaker in speaker_lower:
                    results.append(
                        {
                            "content": (
                                msg.content[:500] + "..." if len(msg.content) > 500 else msg.content
                            ),
                            "speaker": msg.metadata.get("speaker", "Unknown"),
                            "episode_guest": msg.metadata.get("episode_guest", "Unknown"),
                            "relevance": round(msg.metadata.get("similarity", 0), 3),
                        }
                    )
                    if len(results) >= limit:
                        break

            if results:
                return results

        # Fallback: Use Cypher with fuzzy speaker matching
        query = """
        MATCH (c:Conversation)-[:HAS_MESSAGE]->(m:Message)
        WHERE c.session_id STARTS WITH 'lenny-podcast-'
        AND m.metadata IS NOT NULL
        AND (
            m.metadata CONTAINS $speaker_pattern
            OR toLower(m.metadata) CONTAINS toLower($speaker_lower)
        )
        """
        params: dict[str, Any] = {
            "speaker_pattern": f'"speaker": "{speaker}"',
            "speaker_lower": speaker.lower(),
        }

        if topic:
            query += " AND toLower(m.content) CONTAINS toLower($topic)"
            params["topic"] = topic

        query += """
        RETURN m.content AS content,
               m.metadata AS metadata,
               c.session_id AS session_id
        ORDER BY m.created_at DESC
        LIMIT $limit
        """
        params["limit"] = limit

        results = await ctx.deps.client.query.cypher(query, params)

        formatted_results = []
        for r in results:
            # Parse metadata JSON to extract speaker and episode info
            metadata = {}
            if r.get("metadata"):
                try:
                    metadata = json.loads(r["metadata"])
                except (json.JSONDecodeError, TypeError):
                    metadata = {}

            # Extract episode guest from session_id (format: lenny-podcast-guest-name)
            session_id = r.get("session_id", "")
            episode_guest = metadata.get("episode_guest", "")
            if not episode_guest and session_id.startswith("lenny-podcast-"):
                # Convert session_id back to guest name (e.g., "lenny-podcast-julie-zhuo" -> "Julie Zhuo")
                guest_part = session_id.replace("lenny-podcast-", "")
                episode_guest = " ".join(word.capitalize() for word in guest_part.split("-"))

            formatted_results.append(
                {
                    "content": (
                        r["content"][:500] + "..." if len(r["content"]) > 500 else r["content"]
                    ),
                    "speaker": metadata.get("speaker", "Unknown"),
                    "episode_guest": episode_guest or "Unknown",
                }
            )

        return formatted_results
    except Exception as e:
        logger.exception("search_by_speaker failed")
        return [{"error": f"Search failed: {str(e)}"}]


async def search_by_episode(
    ctx: RunContext[AgentDeps],
    guest_name: str,
    topic: str | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Search within a specific episode by guest name.

    Args:
        ctx: The agent run context.
        guest_name: Name of the podcast guest.
        topic: Optional topic to search for within the episode.
        limit: Maximum number of results to return.

    Returns:
        List of transcript segments from the specified episode.
    """
    if not ctx.deps.client:
        return [{"error": "Memory client not available"}]

    try:
        # Convert guest name to session_id format (handles Unicode like "Lütke" -> "lutke")
        session_id = _guest_to_session_id(guest_name)

        if topic:
            # Search within episode for specific topic
            messages = await ctx.deps.client.short_term.search_messages(
                query=topic,
                session_id=session_id,
                limit=limit,
            )
        else:
            # Get conversation from episode
            conv = await ctx.deps.client.short_term.get_conversation(
                session_id=session_id,
                limit=limit,
            )
            messages = conv.messages if conv else []

        return [
            {
                "content": (msg.content[:500] + "..." if len(msg.content) > 500 else msg.content),
                "speaker": msg.metadata.get("speaker", "Unknown"),
                "timestamp": msg.metadata.get("timestamp", ""),
            }
            for msg in messages
        ]
    except Exception as e:
        logger.exception("search_by_episode failed")
        return [{"error": f"Search failed: {str(e)}"}]


async def get_episode_list(ctx: RunContext[AgentDeps]) -> list[dict[str, Any]]:
    """Get list of all podcast episodes.

    Args:
        ctx: The agent run context.

    Returns:
        List of episodes with guest names and message counts.
    """
    if not ctx.deps.client:
        return [{"error": "Memory client not available"}]

    try:
        query = """
        MATCH (c:Conversation)
        WHERE c.session_id STARTS WITH 'lenny-podcast-'
        OPTIONAL MATCH (c)-[:HAS_MESSAGE]->(m:Message)
        WITH c, count(m) AS message_count
        RETURN c.session_id AS session_id,
               message_count
        ORDER BY c.session_id
        """

        results = await ctx.deps.client.query.cypher(query)

        return [
            {
                "guest": r["session_id"].replace("lenny-podcast-", "").replace("-", " ").title(),
                "session_id": r["session_id"],
                "message_count": r["message_count"],
            }
            for r in results
        ]
    except Exception as e:
        logger.exception("get_episode_list failed")
        return [{"error": f"Query failed: {str(e)}"}]


async def get_speaker_list(ctx: RunContext[AgentDeps]) -> list[dict[str, Any]]:
    """Get list of all unique speakers across episodes.

    Args:
        ctx: The agent run context.

    Returns:
        List of speakers with their appearance counts.
    """
    if not ctx.deps.client:
        return [{"error": "Memory client not available"}]

    try:
        # Query to extract unique speakers from message metadata
        # Note: This assumes metadata is stored as JSON string
        query = """
        MATCH (c:Conversation)-[:HAS_MESSAGE]->(m:Message)
        WHERE c.session_id STARTS WITH 'lenny-podcast-'
        AND m.metadata IS NOT NULL
        WITH m.metadata AS meta
        WITH meta
        WHERE meta CONTAINS '"speaker":'
        RETURN DISTINCT meta
        LIMIT 500
        """

        results = await ctx.deps.client.query.cypher(query)

        # Parse speaker names from metadata
        import json

        speakers: dict[str, int] = {}
        for r in results:
            try:
                if isinstance(r["meta"], str):
                    metadata = json.loads(r["meta"])
                else:
                    metadata = r["meta"]
                speaker = metadata.get("speaker", "Unknown")
                speakers[speaker] = speakers.get(speaker, 0) + 1
            except (json.JSONDecodeError, TypeError):
                pass

        return [
            {"speaker": name, "appearance_count": count}
            for name, count in sorted(speakers.items(), key=lambda x: -x[1])
        ]
    except Exception as e:
        logger.exception("get_speaker_list failed")
        return [{"error": f"Query failed: {str(e)}"}]


async def get_memory_stats(ctx: RunContext[AgentDeps]) -> dict[str, Any]:
    """Get statistics about the loaded podcast data.

    Args:
        ctx: The agent run context.

    Returns:
        Dictionary with counts of episodes, messages, speakers, etc.
    """
    if not ctx.deps.client:
        return {"error": "Memory client not available"}

    try:
        stats = await ctx.deps.client.get_stats()
        return {
            "conversations": stats.get("conversations", 0),
            "messages": stats.get("messages", 0),
            "entities": stats.get("entities", 0),
            "note": "These are podcast transcript segments loaded into memory",
        }
    except Exception as e:
        logger.exception("get_memory_stats failed")
        return {"error": f"Failed to get stats: {str(e)}"}


# =============================================================================
# Entity Query Tools
# =============================================================================


async def search_entities(
    ctx: RunContext[AgentDeps],
    query: str,
    entity_type: str | None = None,
    limit: int = 10,
    collapse_duplicates: bool = True,
) -> list[dict[str, Any]]:
    """Search for entities (people, organizations, topics) mentioned in podcasts.

    Args:
        ctx: The agent run context.
        query: Search term (e.g., "product-market fit", "Y Combinator").
        entity_type: Filter by type - PERSON, ORGANIZATION, LOCATION, EVENT, CONCEPT.
        limit: Maximum number of results to return.
        collapse_duplicates: If True, collapse entities linked via SAME_AS relationships
            to show only the canonical entity. This prevents duplicate results.

    Returns:
        List of entities with descriptions and mention counts.
    """
    if not ctx.deps.client:
        return [{"error": "Memory client not available"}]

    try:
        # Two public calls instead of reaching for the private embedder:
        #   1. the library's vector search ranks the entities, and
        #   2. one read fetches the enrichment properties the Entity model does
        #      not surface (enriched_description / wikipedia_url live as
        #      top-level Neo4j properties, not in the metadata blob).
        matches = await ctx.deps.client.long_term.search_entities(
            query,
            entity_types=[entity_type.upper()] if entity_type else None,
            limit=limit * 2 if collapse_duplicates else limit,
        )
        if not matches:
            return []

        enrichment = await ctx.deps.client.query.cypher(
            """
            MATCH (e:Entity)
            WHERE e.id IN $ids
            RETURN e.id AS id,
                   e.enriched_description AS enriched_description,
                   e.wikipedia_url AS wikipedia_url
            """,
            {"ids": [str(entity.id) for entity in matches]},
        )
        extra = {row["id"]: row for row in enrichment}

        results = [
            {
                "id": str(entity.id),
                "name": entity.name,
                "type": entity.type,
                "subtype": entity.subtype,
                "enriched_description": extra.get(str(entity.id), {}).get("enriched_description"),
                "wikipedia_url": extra.get(str(entity.id), {}).get("wikipedia_url"),
            }
            for entity in matches
        ]

        if collapse_duplicates and results:
            # Collapse SAME_AS clusters to canonical entities
            results = await _collapse_duplicate_entity_results(ctx, results, limit)

        return [
            {
                "name": r["name"],
                "type": r["type"],
                "subtype": r.get("subtype"),
                "description": r.get("enriched_description") or "",
                "wikipedia_url": r.get("wikipedia_url"),
                "enriched": bool(r.get("enriched_description")),
            }
            for r in results[:limit]
        ]
    except Exception as e:
        logger.exception("search_entities failed")
        return [{"error": f"Entity search failed: {str(e)}"}]


async def _collapse_duplicate_entity_results(
    ctx: RunContext[AgentDeps],
    results: list[dict[str, Any]],
    limit: int,
) -> list[dict[str, Any]]:
    """Deduplicate entity results by name.

    Note: SAME_AS relationship support is not yet implemented in the database.
    This function currently just deduplicates by exact name match.
    """
    if not results:
        return results

    # Simple deduplication by name
    seen_names = set()
    deduped = []
    for r in results:
        name = r.get("name")
        if name and name not in seen_names:
            seen_names.add(name)
            deduped.append(r)
            if len(deduped) >= limit:
                break

    return deduped


async def _collapse_duplicate_entities(
    ctx: RunContext[AgentDeps],
    entities: list,
    limit: int,
) -> list:
    """Deduplicate entities by name.

    Note: SAME_AS relationship support is not yet implemented in the database.
    This function currently just deduplicates by exact name match.
    """
    if not entities:
        return entities

    # Simple deduplication by name
    seen_names = set()
    deduped = []
    for e in entities:
        name = getattr(e, "name", None)
        if name and name not in seen_names:
            seen_names.add(name)
            deduped.append(e)
            if len(deduped) >= limit:
                break

    return deduped


async def get_entity_context(
    ctx: RunContext[AgentDeps],
    entity_name: str,
) -> dict[str, Any]:
    """Get full context about an entity including enrichment data and status.

    Uses vector search to find entities even with partial or fuzzy name matches.

    Args:
        ctx: The agent run context.
        entity_name: Name of entity (e.g., "Brian Chesky", "Airbnb").

    Returns:
        Entity details, enrichment status, Wikipedia summary (if available),
        and podcast mentions. The enrichment_status field indicates:
        - "enriched": Entity has Wikipedia/external data
        - "pending": Enrichment was attempted but no data found
        - "not_attempted": Entity has not been enriched yet
    """
    logger.info(f"[get_entity_context] Called with entity_name='{entity_name}'")

    if not ctx.deps.client:
        logger.warning("[get_entity_context] Memory client not available!")
        return {"error": "Memory client not available"}

    try:
        # First try exact name match
        logger.info(f"[get_entity_context] Trying exact name match for '{entity_name}'")
        entity = await ctx.deps.client.long_term.get_entity_by_name(entity_name)
        logger.info(f"[get_entity_context] Exact match result: {entity is not None}")

        # If not found, try vector search for fuzzy matching
        if not entity:
            entities = await ctx.deps.client.long_term.search_entities(
                query=entity_name,
                limit=5,
                threshold=0.5,  # Lower threshold for name matching
            )
            if entities:
                # Use the best match
                entity = entities[0]

        # Still not found? Try case-insensitive Cypher search
        if not entity:
            query = """
            MATCH (e:Entity)
            WHERE toLower(e.name) CONTAINS toLower($name)
               OR toLower($name) CONTAINS toLower(e.name)
            RETURN e
            ORDER BY size(e.name) ASC
            LIMIT 1
            """
            results = await ctx.deps.client.query.cypher(query, {"name": entity_name})
            if results:
                # Parse the entity from Cypher result
                entity = await ctx.deps.client.long_term.get_entity_by_name(results[0]["e"]["name"])

        if not entity:
            return {
                "error": f"Entity '{entity_name}' not found. Try searching with search_entities tool first."
            }

        # Get mentions from the graph (filter to podcast sessions only)
        query = """
        MATCH (e:Entity {name: $name})<-[:MENTIONS]-(m:Message)<-[:HAS_MESSAGE]-(c:Conversation)
        WHERE c.session_id STARTS WITH 'lenny-podcast-'
        RETURN m.content AS content,
               m.metadata AS metadata,
               c.session_id AS session_id
        LIMIT 5
        """
        mentions = await ctx.deps.client.query.cypher(query, {"name": entity_name})

        mention_list = []
        for m in mentions:
            metadata = m.get("metadata", {})
            if isinstance(metadata, str):
                try:
                    metadata = json.loads(metadata)
                except json.JSONDecodeError:
                    metadata = {}
            mention_list.append(
                {
                    "content": m["content"][:300] + "..."
                    if len(m["content"]) > 300
                    else m["content"],
                    "speaker": metadata.get("speaker", "Unknown"),
                    "episode": m["session_id"]
                    .replace("lenny-podcast-", "")
                    .replace("-", " ")
                    .title(),
                }
            )

        # Determine enrichment status
        enrichment_status = _get_enrichment_status(entity)

        # Get enrichment fields - check both direct attributes and metadata dict
        # The Entity model stores extra properties in metadata, not as direct attributes
        metadata = getattr(entity, "metadata", {}) or {}

        enriched_description = getattr(entity, "enriched_description", None) or metadata.get(
            "enriched_description"
        )
        wikipedia_url = getattr(entity, "wikipedia_url", None) or metadata.get("wikipedia_url")
        image_url = getattr(entity, "image_url", None) or metadata.get("image_url")
        enrichment_provider = getattr(entity, "enrichment_provider", None) or metadata.get(
            "enrichment_provider"
        )
        enriched_at = metadata.get("enriched_at")
        wikidata_id = getattr(entity, "wikidata_id", None) or metadata.get("wikidata_id")

        # Return in format expected by frontend EntityCard
        # Frontend expects: { entity: {...}, mentions: [...] } with enrichment fields at entity level
        return {
            "entity": {
                "id": str(entity.id) if hasattr(entity, "id") else entity.name,
                "name": entity.name,
                "type": entity.type,
                "subtype": entity.subtype,
                "description": entity.description,
                # Enrichment fields at top level for frontend compatibility
                "enriched_description": enriched_description,
                "wikipedia_url": wikipedia_url,
                "image_url": image_url,
                "wikidata_id": wikidata_id,
                # Additional enrichment metadata
                "enrichment_status": enrichment_status,
                "enrichment_provider": enrichment_provider,
                "enriched_at": str(enriched_at) if enriched_at else None,
            },
            "mentions": mention_list,
            "mention_count": len(mention_list),
        }
    except Exception as e:
        logger.exception(f"[get_entity_context] Exception for entity '{entity_name}': {e}")
        return {"error": f"Failed to get entity context: {str(e)}"}


def _get_enrichment_status(entity) -> str:
    """Determine the enrichment status of an entity."""
    # Get metadata dict for checking enrichment properties
    metadata = getattr(entity, "metadata", {}) or {}

    # Check if entity has enriched data
    enriched_description = getattr(entity, "enriched_description", None) or metadata.get(
        "enriched_description"
    )
    wikipedia_url = getattr(entity, "wikipedia_url", None) or metadata.get("wikipedia_url")

    if enriched_description or wikipedia_url:
        return "enriched"

    # Check if enrichment was attempted but failed
    if metadata.get("enrichment_attempted"):
        return "pending"  # Attempted but no data found

    return "not_attempted"


async def find_related_entities(
    ctx: RunContext[AgentDeps],
    entity_name: str,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Find entities related to a given entity via the knowledge graph.

    Uses fuzzy name matching to find the entity, then returns co-occurring entities.

    Args:
        ctx: The agent run context.
        entity_name: Starting entity (e.g., "Airbnb").
        limit: Maximum number of related entities to return.

    Returns:
        List of related entities with co-occurrence counts.
    """
    if not ctx.deps.client:
        return [{"error": "Memory client not available"}]

    try:
        # First, resolve the entity name using fuzzy matching
        resolved_name = await _resolve_entity_name(ctx, entity_name)
        if not resolved_name:
            return [{"error": f"Entity '{entity_name}' not found"}]

        # Find entities that co-occur in the same messages (podcast sessions only)
        query = """
        MATCH (e1:Entity {name: $name})<-[:MENTIONS]-(m:Message)<-[:HAS_MESSAGE]-(c:Conversation)
        WHERE c.session_id STARTS WITH 'lenny-podcast-'
        MATCH (m)-[:MENTIONS]->(e2:Entity)
        WHERE e1 <> e2
        WITH e2, count(m) AS co_occurrences
        ORDER BY co_occurrences DESC
        LIMIT $limit
        RETURN e2.name AS name, e2.type AS type, e2.subtype AS subtype,
               e2.enriched_description AS enriched_description, co_occurrences
        """
        results = await ctx.deps.client.query.cypher(query, {"name": resolved_name, "limit": limit})

        return [
            {
                "name": r["name"],
                "type": r["type"],
                "subtype": r["subtype"],
                "enriched_description": r["enriched_description"],
                "co_occurrences": r["co_occurrences"],
            }
            for r in results
        ]
    except Exception as e:
        logger.exception("find_related_entities failed")
        return [{"error": f"Failed to find related entities: {str(e)}"}]


async def _resolve_entity_name(
    ctx: RunContext[AgentDeps],
    entity_name: str,
) -> str | None:
    """Resolve an entity name using multiple matching strategies.

    Returns the canonical entity name or None if not found.
    """
    # Try exact match first
    entity = await ctx.deps.client.long_term.get_entity_by_name(entity_name)
    if entity:
        return entity.name

    # Try vector search
    entities = await ctx.deps.client.long_term.search_entities(
        query=entity_name,
        limit=3,
        threshold=0.5,
    )
    if entities:
        return entities[0].name

    # Try case-insensitive substring match
    query = """
    MATCH (e:Entity)
    WHERE toLower(e.name) CONTAINS toLower($name)
       OR toLower($name) CONTAINS toLower(e.name)
    RETURN e.name AS name
    ORDER BY size(e.name) ASC
    LIMIT 1
    """
    results = await ctx.deps.client.query.cypher(query, {"name": entity_name})
    if results:
        return results[0]["name"]

    return None


async def get_most_mentioned_entities(
    ctx: RunContext[AgentDeps],
    entity_type: str | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Get the most frequently mentioned entities across all podcasts.

    Args:
        ctx: The agent run context.
        entity_type: Filter by PERSON, ORGANIZATION, CONCEPT, LOCATION, etc.
        limit: Number of results (default 10).

    Returns:
        List of top entities with mention counts.
    """
    if not ctx.deps.client:
        return [{"error": "Memory client not available"}]

    try:
        # Count mentions from podcast sessions only
        query = """
        MATCH (e:Entity)<-[r:MENTIONS]-(m:Message)<-[:HAS_MESSAGE]-(c:Conversation)
        WHERE c.session_id STARTS WITH 'lenny-podcast-'
        AND ($type IS NULL OR e.type = $type)
        WITH e, count(r) AS mentions
        ORDER BY mentions DESC
        LIMIT $limit
        RETURN e.name AS name, e.type AS type,
               e.subtype AS subtype,
               e.enriched_description AS enriched_description,
               e.wikipedia_url AS wikipedia_url,
               mentions
        """
        results = await ctx.deps.client.query.cypher(query, {"type": entity_type, "limit": limit})

        return [
            {
                "name": r["name"],
                "type": r["type"],
                "subtype": r["subtype"],
                "description": r.get("enriched_description") or "",
                "wikipedia_url": r["wikipedia_url"],
                "mentions": r["mentions"],
            }
            for r in results
        ]
    except Exception as e:
        logger.exception("get_most_mentioned_entities failed")
        return [{"error": f"Failed to get top entities: {str(e)}"}]


# =============================================================================
# Location Query Tools (Map View Integration)
# =============================================================================


async def search_locations(
    ctx: RunContext[AgentDeps],
    query: str | None = None,
    episode_guest: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Search for locations mentioned in podcasts.

    Args:
        ctx: The agent run context.
        query: Optional search term (e.g., "Silicon Valley", "Europe").
        episode_guest: Optional guest name to filter by episode.
        limit: Maximum number of results to return.

    Returns:
        Locations mentioned in podcasts. Includes coordinates if available for map visualization.
    """
    if not ctx.deps.client:
        return [{"error": "Memory client not available"}]

    try:
        session_id = None
        if episode_guest:
            session_id = _guest_to_session_id(episode_guest)

        # Query LOCATION entities - coordinates stored in location point property
        cypher_query = """
        MATCH (e:Entity)
        WHERE e.type = 'LOCATION'
        AND e.location IS NOT NULL
        """
        params: dict[str, Any] = {}

        if session_id:
            cypher_query += """
            AND EXISTS {
                MATCH (c:Conversation)-[:HAS_MESSAGE]->(m:Message)-[:MENTIONS]->(e)
                WHERE c.session_id = $session_id
            }
            """
            params["session_id"] = session_id

        if query:
            cypher_query += """
            AND toLower(e.name) CONTAINS toLower($query)
            """
            params["query"] = query

        cypher_query += """
        RETURN e.id AS id, e.name AS name, e.type AS type, e.subtype AS subtype,
               e.location.y AS latitude, e.location.x AS longitude,
               e.enriched_description AS enriched_description
        LIMIT $limit
        """
        params["limit"] = limit

        locations = await ctx.deps.client.query.cypher(cypher_query, params)

        return [
            {
                "name": loc.get("name"),
                "type": loc.get("type", "LOCATION"),
                "subtype": loc.get("subtype"),
                "latitude": loc.get("latitude"),
                "longitude": loc.get("longitude"),
                "description": loc.get("enriched_description") or "",
            }
            for loc in locations
        ]
    except Exception as e:
        logger.exception("search_locations failed")
        return [{"error": f"Location search failed: {str(e)}"}]


async def find_locations_near(
    ctx: RunContext[AgentDeps],
    location_name: str,
    radius_km: float = 500.0,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Find other locations mentioned near a given location.

    Args:
        ctx: The agent run context.
        location_name: Reference location (e.g., "San Francisco").
        radius_km: Search radius in kilometers (default 500km).
        limit: Maximum number of results to return.

    Returns:
        Nearby locations with distance information.
        Results can be visualized on the map with the radius overlay.
    """
    if not ctx.deps.client:
        return [{"error": "Memory client not available"}]

    try:
        # First get the reference location
        ref_locations = await ctx.deps.client.get_locations(has_coordinates=True, limit=500)
        ref_loc = None
        for loc in ref_locations:
            if location_name.lower() in loc.get("name", "").lower():
                ref_loc = loc
                break

        if not ref_loc or ref_loc.get("latitude") is None or ref_loc.get("longitude") is None:
            return [{"error": f"Location '{location_name}' not found with coordinates"}]

        # Geospatial search lives on the long-term memory layer and returns
        # Entity objects with distance_km stashed in ``metadata``.
        nearby = await ctx.deps.client.long_term.search_locations_near(
            latitude=ref_loc["latitude"],
            longitude=ref_loc["longitude"],
            radius_km=radius_km,
            limit=limit,
        )

        results: list[dict[str, Any]] = []
        for entity in nearby:
            if entity.name.lower() == str(ref_loc.get("name", "")).lower():
                continue  # Exclude the reference location itself
            coords = await ctx.deps.client.long_term.get_location_coordinates(entity.id)
            results.append(
                {
                    "name": entity.name,
                    "type": entity.full_type,
                    "latitude": coords[0] if coords else None,
                    "longitude": coords[1] if coords else None,
                    "distance_km": round(entity.metadata["distance_km"], 1)
                    if entity.metadata.get("distance_km") is not None
                    else None,
                }
            )
        return results
    except Exception as e:
        logger.exception("find_locations_near failed")
        return [{"error": f"Nearby location search failed: {str(e)}"}]


async def get_episode_locations(
    ctx: RunContext[AgentDeps],
    episode_guest: str,
) -> list[dict[str, Any]]:
    """Get all locations mentioned in a specific episode.

    Args:
        ctx: The agent run context.
        episode_guest: Guest name (e.g., "Brian Chesky").

    Returns:
        A geographic profile of the episode's content with all locations mentioned.
        Includes coordinates if available for map visualization.
    """
    if not ctx.deps.client:
        return [{"error": "Memory client not available"}]

    try:
        session_id = _guest_to_session_id(episode_guest)

        # Query LOCATION entities for this episode - coordinates in location point property
        cypher_query = """
        MATCH (c:Conversation)-[:HAS_MESSAGE]->(m:Message)-[:MENTIONS]->(e:Entity)
        WHERE c.session_id = $session_id
        AND e.type = 'LOCATION'
        AND e.location IS NOT NULL
        WITH e, count(m) AS mention_count
        RETURN e.id AS id, e.name AS name, e.type AS type, e.subtype AS subtype,
               e.location.y AS latitude, e.location.x AS longitude,
               e.enriched_description AS enriched_description,
               mention_count
        ORDER BY mention_count DESC
        LIMIT 100
        """
        locations = await ctx.deps.client.query.cypher(cypher_query, {"session_id": session_id})

        return [
            {
                "name": loc.get("name"),
                "type": loc.get("type", "LOCATION"),
                "subtype": loc.get("subtype"),
                "latitude": loc.get("latitude"),
                "longitude": loc.get("longitude"),
                "description": loc.get("enriched_description") or "",
                "mentions": loc.get("mention_count"),
            }
            for loc in locations
        ]
    except Exception as e:
        logger.exception("get_episode_locations failed")
        return [{"error": f"Failed to get episode locations: {str(e)}"}]


async def find_location_path(
    ctx: RunContext[AgentDeps],
    from_location: str,
    to_location: str,
) -> dict[str, Any]:
    """Find the shortest path between two locations in the knowledge graph.

    Args:
        ctx: The agent run context.
        from_location: Starting location name.
        to_location: Destination location name.

    Returns:
        The graph path showing how locations are connected through
        entities, messages, and conversations. The frontend map can visualize
        this as a polyline overlay connecting the locations.
    """
    if not ctx.deps.client:
        return {"error": "Memory client not available"}

    try:
        query = """
        MATCH (start:Entity {type: 'LOCATION'})
        WHERE toLower(start.name) CONTAINS toLower($from_loc)
        WITH start LIMIT 1
        MATCH (end:Entity {type: 'LOCATION'})
        WHERE toLower(end.name) CONTAINS toLower($to_loc)
        WITH start, end LIMIT 1
        // Neo4j raises when shortestPath's endpoints are the same node, which
        // happens whenever both names match one location.
        WHERE start <> end
        MATCH path = shortestPath((start)-[*..6]-(end))
        RETURN start.name AS from_location,
               start.location.latitude AS from_lat,
               start.location.longitude AS from_lon,
               end.name AS to_location,
               end.location.latitude AS to_lat,
               end.location.longitude AS to_lon,
               [n IN nodes(path) |
                CASE
                    WHEN n:Entity THEN {type: 'entity', name: n.name, entity_type: n.type}
                    WHEN n:Message THEN {type: 'message', content: left(n.content, 100)}
                    WHEN n:Conversation THEN {type: 'conversation', id: n.session_id}
                    ELSE {type: 'unknown'}
                END
               ] AS path_nodes,
               length(path) AS path_length
        """
        results = await ctx.deps.client.query.cypher(
            query, {"from_loc": from_location, "to_loc": to_location}
        )

        if not results:
            return {"error": f"No path found between '{from_location}' and '{to_location}'"}

        r = results[0]
        return {
            "from_location": {
                "name": r["from_location"],
                "latitude": r["from_lat"],
                "longitude": r["from_lon"],
            },
            "to_location": {
                "name": r["to_location"],
                "latitude": r["to_lat"],
                "longitude": r["to_lon"],
            },
            "path_length": r["path_length"],
            "path_nodes": r["path_nodes"],
        }
    except Exception as e:
        logger.exception("find_location_path failed")
        return {"error": f"Path finding failed: {str(e)}"}


async def get_location_clusters(
    ctx: RunContext[AgentDeps],
    episode_guest: str | None = None,
) -> list[dict[str, Any]]:
    """Analyze geographic clusters of locations mentioned in podcasts.

    Args:
        ctx: The agent run context.
        episode_guest: Optional guest to filter by.

    Returns:
        Location density information useful for understanding
        which geographic regions are most discussed. Works with the
        frontend heatmap visualization.
    """
    if not ctx.deps.client:
        return [{"error": "Memory client not available"}]

    try:
        session_id = None
        if episode_guest:
            session_id = _guest_to_session_id(episode_guest)

        locations = await ctx.deps.client.get_locations(
            session_id=session_id,
            has_coordinates=True,
            limit=500,
        )

        # Group by country for cluster analysis
        country_clusters: dict[str, list[dict]] = {}
        for loc in locations:
            country = loc.get("country") or "Unknown"
            if country not in country_clusters:
                country_clusters[country] = []
            country_clusters[country].append(
                {
                    "name": loc.get("name"),
                    "latitude": loc.get("latitude"),
                    "longitude": loc.get("longitude"),
                }
            )

        return [
            {
                "country": country,
                "location_count": len(locs),
                "locations": locs[:5],  # Top 5 locations per country
                "center_lat": sum(loc["latitude"] for loc in locs if loc["latitude"]) / len(locs)
                if locs
                else None,
                "center_lon": sum(loc["longitude"] for loc in locs if loc["longitude"]) / len(locs)
                if locs
                else None,
            }
            for country, locs in sorted(country_clusters.items(), key=lambda x: -len(x[1]))
        ]
    except Exception as e:
        logger.exception("get_location_clusters failed")
        return [{"error": f"Cluster analysis failed: {str(e)}"}]


async def calculate_location_distances(
    ctx: RunContext[AgentDeps],
    locations: list[str],
) -> list[dict[str, Any]]:
    """Calculate distances between multiple locations.

    Args:
        ctx: The agent run context.
        locations: List of location names to measure between.

    Returns:
        Pairwise distances in kilometers using great-circle calculation.
        Useful for understanding the geographic scope of discussions.
    """
    if not ctx.deps.client:
        return [{"error": "Memory client not available"}]

    if len(locations) < 2:
        return [{"error": "At least 2 locations are required"}]

    try:
        # Get all locations with coordinates
        all_locs = await ctx.deps.client.get_locations(has_coordinates=True, limit=500)

        # Find matching locations
        found_locs: list[dict] = []
        for loc_name in locations:
            for loc in all_locs:
                if (
                    loc_name.lower() in loc.get("name", "").lower()
                    and loc.get("latitude")
                    and loc.get("longitude")
                ):
                    found_locs.append(
                        {
                            "name": loc.get("name"),
                            "latitude": loc.get("latitude"),
                            "longitude": loc.get("longitude"),
                        }
                    )
                    break

        if len(found_locs) < 2:
            return [
                {"error": f"Need at least 2 locations with coordinates, found {len(found_locs)}"}
            ]

        # Calculate pairwise distances
        distances = []
        for i in range(len(found_locs)):
            for j in range(i + 1, len(found_locs)):
                loc1 = found_locs[i]
                loc2 = found_locs[j]
                dist = _haversine_distance(
                    loc1["latitude"],
                    loc1["longitude"],
                    loc2["latitude"],
                    loc2["longitude"],
                )
                distances.append(
                    {
                        "from": loc1["name"],
                        "to": loc2["name"],
                        "distance_km": round(dist, 1),
                    }
                )

        return sorted(distances, key=lambda x: x["distance_km"])
    except Exception as e:
        logger.exception("calculate_location_distances failed")
        return [{"error": f"Distance calculation failed: {str(e)}"}]


# =============================================================================
# User Preferences and Reasoning Memory Tools
# =============================================================================


async def get_user_preferences(
    ctx: RunContext[AgentDeps],
) -> list[dict[str, Any]]:
    """Get the current user's stored preferences.

    Args:
        ctx: The agent run context.

    Returns:
        Preferences including content interests, format preferences,
        and historical patterns. Use this to personalize responses.
    """
    if not ctx.deps.client:
        return [{"error": "Memory client not available"}]

    try:
        # ``search_preferences`` is a vector search -- an empty query embeds the
        # empty string and then filters by cosine similarity, which is not a
        # "list everything" operation. Enumerate the categories this app writes
        # instead (see ``PREFERENCE_CATEGORIES`` in api/routes/chat.py).
        preferences = []
        for category in PREFERENCE_CATEGORIES:
            preferences.extend(
                await ctx.deps.client.long_term.get_preferences_by_category(category, limit=20)
            )

        return [
            {
                "category": p.category,
                "preference": p.preference,
                "confidence": p.confidence,
                # Preference has no ``source`` field -- the chat route records
                # provenance in metadata when it detects a preference.
                "source": (p.metadata or {}).get("source"),
                "context": p.context,
            }
            for p in preferences[:20]
        ]
    except Exception as e:
        logger.exception("get_user_preferences failed")
        return [{"error": f"Failed to get preferences: {str(e)}"}]


async def find_similar_past_queries(
    ctx: RunContext[AgentDeps],
    current_query: str,
    limit: int = 3,
) -> list[dict[str, Any]]:
    """Find similar queries from past conversations and their successful resolutions.

    Args:
        ctx: The agent run context.
        current_query: The current user query.
        limit: Maximum number of similar traces to return.

    Returns:
        Past reasoning traces that solved similar problems.
        Useful for learning from successful past interactions.
    """
    if not ctx.deps.client:
        return [{"error": "Memory client not available"}]

    try:
        traces = await ctx.deps.client.reasoning.get_similar_traces(
            task=current_query,
            limit=limit,
            success_only=True,
        )

        return [
            {
                "task": t.task,
                "outcome": t.outcome,
                "success": t.success,
                "steps_count": len(t.steps) if t.steps else 0,
            }
            for t in traces
        ]
    except Exception as e:
        logger.exception("find_similar_past_queries failed")
        return [{"error": f"Failed to find similar traces: {str(e)}"}]


# =============================================================================
# Enhanced Reasoning Memory Tools (NEW)
# =============================================================================


async def learn_from_similar_task(
    ctx: RunContext[AgentDeps],
    task_description: str,
    limit: int = 1,
) -> list[dict[str, Any]]:
    """Get full reasoning traces from similar past tasks for few-shot learning.

    Unlike find_similar_past_queries which returns summaries, this returns
    the complete reasoning steps so the agent can learn the approach.

    Args:
        ctx: The agent run context.
        task_description: Description of the current task.
        limit: Number of similar traces to return.

    Returns:
        Complete reasoning traces including all steps and tool calls.
    """
    if not ctx.deps.client:
        return [{"error": "Memory client not available"}]

    try:
        traces = await ctx.deps.client.reasoning.get_similar_traces(
            task=task_description,
            limit=limit,
            success_only=True,
            threshold=0.6,  # Lower threshold to find more potential matches
        )

        results = []
        for trace in traces:
            # Get the full trace with steps
            full_trace = await ctx.deps.client.reasoning.get_trace_with_steps(trace.id)
            if not full_trace:
                continue

            steps_data = []
            for step in full_trace.steps or []:
                step_info = {
                    "step_number": step.step_number,
                    "thought": step.thought,
                    "action": step.action,
                    "observation": step.observation,
                }
                # Include tool calls if present
                if step.tool_calls:
                    step_info["tool_calls"] = [
                        {
                            "tool_name": tc.tool_name,
                            "arguments": tc.arguments,
                            "status": tc.status.value if tc.status else "unknown",
                            "duration_ms": tc.duration_ms,
                        }
                        for tc in step.tool_calls
                    ]
                steps_data.append(step_info)

            results.append(
                {
                    "task": full_trace.task,
                    "outcome": full_trace.outcome,
                    "success": full_trace.success,
                    "similarity": trace.metadata.get("similarity", 0) if trace.metadata else 0,
                    "steps": steps_data,
                }
            )

        return results
    except Exception as e:
        logger.exception("learn_from_similar_task failed")
        return [{"error": f"Failed to get similar traces: {str(e)}"}]


async def get_tool_usage_patterns(
    ctx: RunContext[AgentDeps],
    tool_name: str | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    """Analyze tool usage patterns to understand which tools are most effective.

    Returns statistics about tool calls including success rates, average durations,
    and common tool sequences.

    Args:
        ctx: The agent run context.
        tool_name: Optional specific tool to analyze.
        limit: Maximum number of tools to include in analysis.

    Returns:
        Tool usage statistics and patterns.
    """
    if not ctx.deps.client:
        return {"error": "Memory client not available"}

    try:
        # Get pre-aggregated tool stats
        stats = await ctx.deps.client.reasoning.get_tool_stats(tool_name=tool_name)

        tool_data = []
        for s in stats[:limit]:
            # ToolStats fields are `name` / `successful_calls` / `failed_calls`
            # (this read used `tool_name` / `success_count` / `failure_count`
            # and raised AttributeError on every call).
            tool_info = {
                "tool_name": s.name,
                "description": s.description,
                "total_calls": s.total_calls,
                "success_count": s.successful_calls,
                "failure_count": s.failed_calls,
                "success_rate": round(s.success_rate * 100, 1) if s.success_rate else 0,
                "avg_duration_ms": round(s.avg_duration_ms, 1) if s.avg_duration_ms else None,
            }
            tool_data.append(tool_info)

        # Sort by success rate and usage
        tool_data.sort(key=lambda x: (x["success_rate"], x["total_calls"]), reverse=True)

        return {
            "tools": tool_data,
            "total_tools_analyzed": len(tool_data),
            "recommendation": _get_tool_recommendation(tool_data),
        }
    except Exception as e:
        logger.exception("get_tool_usage_patterns failed")
        return {"error": f"Failed to get tool patterns: {str(e)}"}


def _get_tool_recommendation(tool_data: list[dict]) -> str:
    """Generate a recommendation based on tool usage patterns."""
    if not tool_data:
        return "No tool usage data available yet."

    # Find best performing tools
    best_tools = [t for t in tool_data if t["success_rate"] >= 90 and t["total_calls"] >= 5]
    if best_tools:
        top_tool = best_tools[0]
        return f"'{top_tool['tool_name']}' has highest success rate ({top_tool['success_rate']}%) with {top_tool['total_calls']} calls."

    # Find most used tools
    most_used = sorted(tool_data, key=lambda x: x["total_calls"], reverse=True)
    if most_used:
        return f"'{most_used[0]['tool_name']}' is most frequently used ({most_used[0]['total_calls']} calls)."

    return "Continue using tools to build usage patterns."


async def get_session_reasoning_history(
    ctx: RunContext[AgentDeps],
    session_id: str | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Get reasoning traces from a session to understand the conversation history.

    Args:
        ctx: The agent run context.
        session_id: Session ID to query. Defaults to current session.
        limit: Maximum number of traces to return.

    Returns:
        List of reasoning traces from the session.
    """
    if not ctx.deps.client:
        return [{"error": "Memory client not available"}]

    try:
        target_session = session_id or ctx.deps.session_id
        traces = await ctx.deps.client.reasoning.get_session_traces(
            session_id=target_session,
            limit=limit,
        )

        return [
            {
                "id": str(t.id),
                "task": t.task,
                "outcome": t.outcome,
                "success": t.success,
                "started_at": t.started_at.isoformat() if t.started_at else None,
                "completed_at": t.completed_at.isoformat() if t.completed_at else None,
                "steps_count": len(t.steps) if t.steps else 0,
            }
            for t in traces
        ]
    except Exception as e:
        logger.exception("get_session_reasoning_history failed")
        return [{"error": f"Failed to get session traces: {str(e)}"}]


# =============================================================================
# Entity Management Tools (NEW)
# =============================================================================


async def find_duplicate_entities(
    ctx: RunContext[AgentDeps],
    entity_type: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Find potential duplicate entities that may need merging.

    Useful for data quality improvement and entity resolution.

    Args:
        ctx: The agent run context.
        entity_type: Filter by entity type (PERSON, ORGANIZATION, etc.)
        limit: Maximum number of duplicate pairs to return.

    Returns:
        Pairs of potentially duplicate entities with similarity scores.
    """
    if not ctx.deps.client:
        return [{"error": "Memory client not available"}]

    try:
        # ``find_potential_duplicates`` is keyword-only and returns
        # (entity1, entity2, confidence) tuples for pairs the library flagged
        # with a pending SAME_AS edge. It does not filter by type, so we do.
        duplicates = await ctx.deps.client.long_term.find_potential_duplicates(limit=limit)

        wanted = entity_type.upper() if entity_type else None
        pairs = [
            {
                "entity1": {"id": str(e1.id), "name": e1.name, "type": e1.full_type},
                "entity2": {"id": str(e2.id), "name": e2.name, "type": e2.full_type},
                "similarity": round(confidence, 3),
                "status": "pending",
            }
            for e1, e2, confidence in duplicates
            if wanted is None or e1.type.upper() == wanted
        ]
        if pairs:
            return pairs
        # No flagged pairs yet (deduplication only flags on ingest) -- fall back
        # to fuzzy name matching so the tool is still useful on loaded corpora.
        return await _find_duplicates_fallback(ctx, entity_type, limit)
    except Exception as e:
        logger.exception("find_duplicate_entities failed")
        return [{"error": f"Failed to find duplicates: {str(e)}"}]


async def _find_duplicates_fallback(
    ctx: RunContext[AgentDeps],
    entity_type: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    """Fallback duplicate detection using fuzzy string matching in Cypher."""
    try:
        type_filter = "AND e1.type = $entity_type" if entity_type else ""
        query = f"""
        MATCH (e1:Entity)
        WHERE e1.name IS NOT NULL {type_filter}
        WITH e1
        MATCH (e2:Entity)
        WHERE e2.name IS NOT NULL
          AND id(e1) < id(e2)
          AND e1.type = e2.type
          AND (
            toLower(e1.name) CONTAINS toLower(e2.name)
            OR toLower(e2.name) CONTAINS toLower(e1.name)
            OR apoc.text.levenshteinSimilarity(toLower(e1.name), toLower(e2.name)) > 0.8
          )
        RETURN e1.name AS name1, e1.type AS type1, elementId(e1) AS id1,
               e2.name AS name2, e2.type AS type2, elementId(e2) AS id2,
               apoc.text.levenshteinSimilarity(toLower(e1.name), toLower(e2.name)) AS similarity
        ORDER BY similarity DESC
        LIMIT $limit
        """
        results = await ctx.deps.client.query.cypher(
            query, {"entity_type": entity_type, "limit": limit}
        )

        return [
            {
                "entity1": {"id": r["id1"], "name": r["name1"], "type": r["type1"]},
                "entity2": {"id": r["id2"], "name": r["name2"], "type": r["type2"]},
                "similarity": round(r["similarity"], 3) if r["similarity"] else 0.8,
                "status": "pending",
            }
            for r in results
        ]
    except Exception as e:
        logger.exception("_find_duplicates_fallback failed")
        return [{"error": f"Fallback duplicate detection failed: {str(e)}"}]


async def get_entity_provenance(
    ctx: RunContext[AgentDeps],
    entity_name: str,
) -> dict[str, Any]:
    """Get the provenance (source) information for an entity.

    Shows which messages the entity was extracted from and extraction confidence.

    Args:
        ctx: The agent run context.
        entity_name: Name of the entity to get provenance for.

    Returns:
        Source messages, extraction dates, and confidence scores.
    """
    if not ctx.deps.client:
        return {"error": "Memory client not available"}

    try:
        query = """
        MATCH (e:Entity)
        WHERE toLower(e.name) = toLower($name)
        WITH e LIMIT 1
        OPTIONAL MATCH (e)-[r:EXTRACTED_FROM|MENTIONED_IN]->(m:Message)
        OPTIONAL MATCH (m)-[:PART_OF]->(c:Conversation)
        RETURN e.name AS entity_name,
               e.type AS entity_type,
               e.created_at AS created_at,
               e.confidence AS confidence,
               e.enrichment_provider AS enrichment_provider,
               e.enriched_at AS enriched_at,
               collect(DISTINCT {
                   message_id: elementId(m),
                   content_preview: left(m.content, 150),
                   speaker: m.metadata.speaker,
                   session_id: c.session_id,
                   relationship_type: type(r)
               })[0..5] AS sources
        """
        results = await ctx.deps.client.query.cypher(query, {"name": entity_name})

        if not results:
            return {"error": f"Entity '{entity_name}' not found"}

        r = results[0]
        return {
            "entity": {
                "name": r["entity_name"],
                "type": r["entity_type"],
                "created_at": str(r["created_at"]) if r["created_at"] else None,
                "confidence": r["confidence"],
            },
            "enrichment": {
                "provider": r["enrichment_provider"],
                "enriched_at": str(r["enriched_at"]) if r["enriched_at"] else None,
            },
            "sources": [s for s in r["sources"] if s.get("message_id")],
            "total_mentions": len([s for s in r["sources"] if s.get("message_id")]),
        }
    except Exception as e:
        logger.exception("get_entity_provenance failed")
        return {"error": f"Failed to get entity provenance: {str(e)}"}


async def trigger_entity_enrichment(
    ctx: RunContext[AgentDeps],
    entity_name: str,
    provider: str = "wikimedia",
) -> dict[str, Any]:
    """Request enrichment for an entity from Wikipedia or other providers.

    Args:
        ctx: The agent run context.
        entity_name: Name of the entity to enrich.
        provider: Enrichment provider ("wikimedia" or "diffbot").

    Returns:
        Enrichment status and data if available.
    """
    if not ctx.deps.client:
        return {"error": "Memory client not available"}

    try:
        # First, find the entity
        entity = await ctx.deps.client.long_term.get_entity_by_name(entity_name)
        if not entity:
            return {"error": f"Entity '{entity_name}' not found"}

        # Get metadata dict for checking enrichment properties
        # Entity model stores extra properties in metadata, not properties
        metadata = getattr(entity, "metadata", {}) or {}

        # Check if already enriched - check both direct attributes and metadata
        enriched_description = getattr(entity, "enriched_description", None) or metadata.get(
            "enriched_description"
        )
        wikipedia_url = getattr(entity, "wikipedia_url", None) or metadata.get("wikipedia_url")
        image_url = getattr(entity, "image_url", None) or metadata.get("image_url")
        enrichment_provider = getattr(entity, "enrichment_provider", None) or metadata.get(
            "enrichment_provider"
        )
        enriched_at = metadata.get("enriched_at")

        if enriched_description:
            return {
                "status": "already_enriched",
                "entity_name": entity.name,
                "enrichment_provider": enrichment_provider,
                "enriched_at": str(enriched_at) if enriched_at else None,
                "description": enriched_description,
                "wikipedia_url": wikipedia_url,
                "image_url": image_url,
            }

        # Trigger enrichment if enrichment service is available
        # NOTE: This requires the enrichment service to be running
        # For now, return status indicating enrichment needed
        return {
            "status": "enrichment_needed",
            "entity_name": entity.name,
            "entity_type": entity.type,
            "message": f"Entity '{entity_name}' needs enrichment. Run the enrichment script or enable background enrichment.",
            "suggestion": "Use `make enrich` to enrich entities with Wikipedia data.",
        }
    except Exception as e:
        logger.exception("trigger_entity_enrichment failed")
        return {"error": f"Failed to trigger enrichment: {str(e)}"}


# =============================================================================
# Conversation & Summary Tools (NEW)
# =============================================================================


async def get_conversation_context(
    ctx: RunContext[AgentDeps],
    limit: int = 10,
    include_tool_calls: bool = False,
) -> list[dict[str, Any]]:
    """Get recent conversation history for context.

    Args:
        ctx: The agent run context.
        limit: Maximum number of messages to return.
        include_tool_calls: Whether to include tool call details.

    Returns:
        Recent messages in the current conversation.
    """
    if not ctx.deps.client:
        return [{"error": "Memory client not available"}]

    try:
        # get_conversation() returns a Conversation, not a list -- iterating the
        # model itself yields (field_name, value) pairs under pydantic v2.
        # It also orders messages oldest-first, so slice the tail for "recent".
        conversation = await ctx.deps.client.short_term.get_conversation(
            session_id=ctx.deps.session_id,
        )

        results: list[dict[str, Any]] = []
        for msg in conversation.messages[-limit:]:
            msg_data: dict[str, Any] = {
                "role": msg.role.value,
                "content": msg.content[:500] if len(msg.content) > 500 else msg.content,
                "timestamp": msg.created_at.isoformat() if msg.created_at else None,
            }
            if include_tool_calls:
                tool_calls = (msg.metadata or {}).get("tool_calls")
                if tool_calls:
                    msg_data["tool_calls"] = tool_calls
            results.append(msg_data)

        return results
    except Exception as e:
        logger.exception("get_conversation_context failed")
        return [{"error": f"Failed to get conversation context: {str(e)}"}]


async def list_podcast_sessions(
    ctx: RunContext[AgentDeps],
    sort_by: str = "message_count",
    order: str = "desc",
    limit: int = 20,
) -> list[dict[str, Any]]:
    """List available podcast sessions with metadata.

    Args:
        ctx: The agent run context.
        sort_by: Sort field ("message_count", "created_at", "updated_at").
        order: Sort order ("asc" or "desc").
        limit: Maximum number of sessions.

    Returns:
        List of sessions with message counts and metadata.
    """
    if not ctx.deps.client:
        return [{"error": "Memory client not available"}]

    try:
        # Try using the library's list_sessions if available
        try:
            sessions = await ctx.deps.client.short_term.list_sessions(
                prefix="lenny-podcast-",
                limit=limit,
            )
            return [
                {
                    "session_id": s.session_id,
                    "message_count": s.message_count,
                    "created_at": s.created_at.isoformat() if s.created_at else None,
                    "updated_at": s.updated_at.isoformat() if s.updated_at else None,
                    "preview": s.preview[:100] if s.preview else None,
                }
                for s in sessions
            ]
        except AttributeError:
            pass

        # Fallback: Use direct Cypher query
        order_clause = "DESC" if order == "desc" else "ASC"
        sort_field = {
            "message_count": "message_count",
            "created_at": "c.created_at",
            "updated_at": "c.updated_at",
        }.get(sort_by, "message_count")

        query = f"""
        MATCH (c:Conversation)
        WHERE c.session_id STARTS WITH 'lenny-podcast-'
        OPTIONAL MATCH (c)-[:HAS_MESSAGE]->(m:Message)
        WITH c, count(m) AS message_count
        RETURN c.session_id AS session_id,
               c.title AS title,
               message_count,
               c.created_at AS created_at,
               c.updated_at AS updated_at
        ORDER BY {sort_field} {order_clause}
        LIMIT $limit
        """
        results = await ctx.deps.client.query.cypher(query, {"limit": limit})

        return [
            {
                "session_id": r["session_id"],
                "title": r["title"],
                "message_count": r["message_count"],
                "created_at": str(r["created_at"]) if r["created_at"] else None,
                "updated_at": str(r["updated_at"]) if r["updated_at"] else None,
            }
            for r in results
        ]
    except Exception as e:
        logger.exception("list_podcast_sessions failed")
        return [{"error": f"Failed to list sessions: {str(e)}"}]


async def get_episode_summary(
    ctx: RunContext[AgentDeps],
    episode_guest: str,
) -> dict[str, Any]:
    """Get a summary of a podcast episode including key topics and entities.

    Args:
        ctx: The agent run context.
        episode_guest: Guest name (e.g., "Brian Chesky").

    Returns:
        Episode summary with key topics, entities, and highlights.
    """
    if not ctx.deps.client:
        return {"error": "Memory client not available"}

    try:
        session_id = _guest_to_session_id(episode_guest)

        # Get conversation summary if available
        try:
            summary = await ctx.deps.client.short_term.get_conversation_summary(
                session_id=session_id,
            )
            if summary:
                return {
                    "episode_guest": episode_guest,
                    "session_id": session_id,
                    "summary": summary.summary,
                    "key_topics": summary.key_topics,
                    "key_entities": summary.key_entities,
                    "generated_at": summary.generated_at.isoformat()
                    if summary.generated_at
                    else None,
                }
        except AttributeError:
            pass

        # Fallback: Generate summary from entities and message stats
        query = """
        MATCH (c:Conversation {session_id: $session_id})
        OPTIONAL MATCH (c)-[:HAS_MESSAGE]->(m:Message)
        WITH c, count(m) AS message_count
        OPTIONAL MATCH (m)-[:MENTIONS]->(e:Entity)
        WITH c, message_count, collect(DISTINCT e.name)[0..10] AS entities
        RETURN c.title AS title,
               c.session_id AS session_id,
               message_count,
               entities
        """
        results = await ctx.deps.client.query.cypher(query, {"session_id": session_id})

        if not results:
            return {"error": f"Episode with guest '{episode_guest}' not found"}

        r = results[0]
        return {
            "episode_guest": episode_guest,
            "session_id": r["session_id"],
            "title": r["title"],
            "message_count": r["message_count"],
            "key_entities": r["entities"] or [],
            "summary": None,  # No AI summary available in fallback mode
            "note": "Full AI summary requires conversation summarization feature.",
        }
    except Exception as e:
        logger.exception("get_episode_summary failed")
        return {"error": f"Failed to get episode summary: {str(e)}"}


# =============================================================================
# Memory Graph Search Tool (NEW)
# =============================================================================


async def memory_graph_search(
    ctx: RunContext[AgentDeps],
    query: str,
    limit: int = 10,
    include_related_entities: bool = True,
    max_related_per_entity: int = 5,
) -> dict[str, Any]:
    """Search memory using vector similarity, then traverse the graph to find entities.

    This tool combines semantic search with knowledge graph traversal:
    1. Vector search finds semantically similar messages
    2. Graph traversal finds entities mentioned in those messages
    3. Expands to find ALL entities related to the mentioned ones

    Use this tool when you want to explore the knowledge graph starting from a
    semantic concept rather than a specific entity name.

    Args:
        ctx: The agent run context.
        query: Natural language search query (e.g., "product-market fit", "scaling teams")
        limit: Maximum number of messages to find via vector search (default 10)
        include_related_entities: Whether to also find entities related to the mentioned ones
        max_related_per_entity: Maximum related entities to include per found entity (default 5)

    Returns:
        A graph structure with:
        - nodes: Messages found via vector search + Entities mentioned + Related entities
        - relationships: MENTIONS links (Message->Entity) + RELATED_TO links (Entity->Entity)
        - metadata: Search scores, entity types, co-occurrence counts
    """
    if not ctx.deps.client:
        return {"error": "Memory client not available"}

    try:
        # Step 1: Vector search for similar messages
        messages = await ctx.deps.client.short_term.search_messages(
            query=query,
            limit=limit,
            threshold=0.5,
            metadata_filters={"source": "lenny_podcast"},
        )

        if not messages:
            return {
                "query": query,
                "nodes": [],
                "relationships": [],
                "summary": {
                    "messages_found": 0,
                    "entities_found": 0,
                    "relationships_found": 0,
                },
                "message": f"No messages found matching '{query}'",
            }

        # Collect message IDs for graph traversal
        message_ids = [str(msg.id) for msg in messages]

        # Step 2: Find entities mentioned in these messages
        entity_query = """
        UNWIND $message_ids AS msg_id
        MATCH (m:Message)
        WHERE m.id = msg_id
        OPTIONAL MATCH (m)-[mentions:MENTIONS]->(e:Entity)
        RETURN
            m.id AS message_id,
            collect(DISTINCT {
                id: e.id,
                name: e.name,
                type: e.type,
                subtype: e.subtype,
                enriched_description: e.enriched_description,
                confidence: mentions.confidence
            }) AS mentioned_entities
        """

        entity_results = await ctx.deps.client.query.cypher(
            entity_query,
            {"message_ids": message_ids},
        )

        # Build graph structure
        nodes: list[dict[str, Any]] = []
        relationships: list[dict[str, Any]] = []
        seen_node_ids: set[str] = set()
        seen_rel_ids: set[str] = set()
        mentioned_entity_ids: list[str] = []

        # Add message nodes with full content
        for msg in messages:
            msg_id = f"msg-{msg.id}"
            if msg_id not in seen_node_ids:
                metadata = msg.metadata or {}
                # Include more content in the node
                content = msg.content
                if len(content) > 300:
                    content = content[:300] + "..."
                nodes.append(
                    {
                        "id": msg_id,
                        "label": metadata.get("speaker", "Message"),
                        "type": "Message",
                        "properties": {
                            "content": content,
                            "speaker": metadata.get("speaker"),
                            "episode": metadata.get("episode_guest"),
                            "similarity": round(metadata.get("similarity", 0), 3),
                        },
                    }
                )
                seen_node_ids.add(msg_id)

        # Process entities mentioned in messages
        for row in entity_results:
            msg_id = f"msg-{row['message_id']}"

            for entity in row.get("mentioned_entities", []):
                if not entity or not entity.get("id"):
                    continue
                entity_id = f"entity-{entity['id']}"
                raw_entity_id = entity["id"]

                if entity_id not in seen_node_ids:
                    nodes.append(
                        {
                            "id": entity_id,
                            "label": entity.get("name", "Unknown"),
                            "type": entity.get("type", "Entity"),
                            "properties": {
                                "subtype": entity.get("subtype"),
                                "enriched_description": entity.get("enriched_description"),
                            },
                        }
                    )
                    seen_node_ids.add(entity_id)
                    mentioned_entity_ids.append(raw_entity_id)

                # Add MENTIONS relationship
                rel_id = f"mentions-{msg_id}-{entity_id}"
                if rel_id not in seen_rel_ids:
                    relationships.append(
                        {
                            "id": rel_id,
                            "from": msg_id,
                            "to": entity_id,
                            "type": "MENTIONS",
                        }
                    )
                    seen_rel_ids.add(rel_id)

        # Step 3: Find related entities that ALSO have message connections
        # This ensures no disconnected entity nodes in the graph
        if include_related_entities and mentioned_entity_ids:
            # Get related entities that have at least one message mentioning them
            related_query = """
            UNWIND $entity_ids AS eid
            MATCH (e:Entity {id: eid})
            OPTIONAL MATCH (e)-[r:RELATED_TO]-(related:Entity)
            WHERE related IS NOT NULL
            // Only include related entities that have message connections
            AND EXISTS { (related)<-[:MENTIONS]-(:Message) }
            WITH e, related, r,
                 CASE WHEN startNode(r) = e THEN 'outgoing' ELSE 'incoming' END AS direction
            ORDER BY related.name
            WITH e, collect({
                entity: related,
                direction: direction
            })[0..$max_related] AS related_list
            RETURN e.id AS source_id, related_list
            """

            related_results = await ctx.deps.client.query.cypher(
                related_query,
                {
                    "entity_ids": mentioned_entity_ids,
                    "max_related": max_related_per_entity,
                },
            )

            # Track which related entities we add so we can fetch their messages
            new_related_entity_ids: list[str] = []

            # Add related entities and relationships
            for row in related_results:
                source_id = row["source_id"]
                source_node_id = f"entity-{source_id}"

                for rel_info in row.get("related_list", []):
                    if not rel_info:
                        continue
                    related_entity = rel_info.get("entity")
                    if not related_entity or not related_entity.get("id"):
                        continue

                    related_id = f"entity-{related_entity['id']}"
                    raw_related_id = related_entity["id"]

                    # Add related entity node if not seen
                    if related_id not in seen_node_ids:
                        nodes.append(
                            {
                                "id": related_id,
                                "label": related_entity.get("name", "Unknown"),
                                "type": related_entity.get("type", "Entity"),
                                "properties": {
                                    "subtype": related_entity.get("subtype"),
                                    "enriched_description": related_entity.get(
                                        "enriched_description"
                                    ),
                                },
                            }
                        )
                        seen_node_ids.add(related_id)
                        new_related_entity_ids.append(raw_related_id)

                    # Add RELATED_TO relationship (normalize direction)
                    if rel_info.get("direction") == "outgoing":
                        rel_from, rel_to = source_node_id, related_id
                    else:
                        rel_from, rel_to = related_id, source_node_id

                    rel_id = f"related-{rel_from}-{rel_to}"
                    reverse_rel_id = f"related-{rel_to}-{rel_from}"

                    if rel_id not in seen_rel_ids and reverse_rel_id not in seen_rel_ids:
                        relationships.append(
                            {
                                "id": rel_id,
                                "from": rel_from,
                                "to": rel_to,
                                "type": "RELATED_TO",
                                "properties": {},
                            }
                        )
                        seen_rel_ids.add(rel_id)

        # Step 4: Find any RELATED_TO relationships between all entities in our set
        all_entity_ids = [n["id"].replace("entity-", "") for n in nodes if n["type"] != "Message"]
        if len(all_entity_ids) > 1:
            inter_rel_query = """
            MATCH (e1:Entity)-[r:RELATED_TO]->(e2:Entity)
            WHERE e1.id IN $entity_ids AND e2.id IN $entity_ids
            RETURN e1.id AS from_id, e2.id AS to_id
            """
            inter_results = await ctx.deps.client.query.cypher(
                inter_rel_query, {"entity_ids": all_entity_ids}
            )

            for rel in inter_results:
                rel_from = f"entity-{rel['from_id']}"
                rel_to = f"entity-{rel['to_id']}"
                rel_id = f"related-{rel_from}-{rel_to}"
                reverse_rel_id = f"related-{rel_to}-{rel_from}"

                if rel_id not in seen_rel_ids and reverse_rel_id not in seen_rel_ids:
                    relationships.append(
                        {
                            "id": rel_id,
                            "from": rel_from,
                            "to": rel_to,
                            "type": "RELATED_TO",
                            "properties": {},
                        }
                    )
                    seen_rel_ids.add(rel_id)

        # Step 5: Find additional messages that mention any of the entities in our graph
        # This shows messages connected to entities (beyond the original vector search results)
        if all_entity_ids:
            # Get up to 2 messages per entity (to avoid explosion but ensure connectivity)
            messages_for_entities_query = """
            UNWIND $entity_ids AS eid
            MATCH (e:Entity {id: eid})<-[mentions:MENTIONS]-(m:Message)
            WHERE NOT m.id IN $existing_message_ids
            WITH e, m, mentions
            ORDER BY mentions.confidence DESC
            WITH e, collect({
                message_id: m.id,
                content: m.content,
                metadata: m.metadata
            })[0..2] AS messages
            RETURN e.id AS entity_id, messages
            """

            # Get existing message IDs (strip the "msg-" prefix)
            existing_msg_ids = [
                n["id"].replace("msg-", "") for n in nodes if n["type"] == "Message"
            ]

            msg_results = await ctx.deps.client.query.cypher(
                messages_for_entities_query,
                {
                    "entity_ids": all_entity_ids,
                    "existing_message_ids": existing_msg_ids,
                },
            )

            for row in msg_results:
                entity_id = f"entity-{row['entity_id']}"

                for msg_info in row.get("messages", []):
                    if not msg_info or not msg_info.get("message_id"):
                        continue

                    msg_id = f"msg-{msg_info['message_id']}"

                    # Add message node if not seen
                    if msg_id not in seen_node_ids:
                        # Parse metadata
                        metadata = {}
                        if msg_info.get("metadata"):
                            try:
                                metadata = json.loads(msg_info["metadata"])
                            except (json.JSONDecodeError, TypeError):
                                metadata = {}

                        content = msg_info.get("content", "")
                        if len(content) > 300:
                            content = content[:300] + "..."

                        nodes.append(
                            {
                                "id": msg_id,
                                "label": metadata.get("speaker", "Message"),
                                "type": "Message",
                                "properties": {
                                    "content": content,
                                    "speaker": metadata.get("speaker"),
                                    "episode": metadata.get("episode_guest"),
                                    "source": "entity_connection",  # Mark as found via entity
                                },
                            }
                        )
                        seen_node_ids.add(msg_id)

                    # Add MENTIONS relationship from message to entity
                    rel_id = f"mentions-{msg_id}-{entity_id}"
                    if rel_id not in seen_rel_ids:
                        relationships.append(
                            {
                                "id": rel_id,
                                "from": msg_id,
                                "to": entity_id,
                                "type": "MENTIONS",
                            }
                        )
                        seen_rel_ids.add(rel_id)

        # Step 6: Remove any disconnected nodes (entities with no relationships)
        # Build set of all node IDs that have at least one relationship
        connected_node_ids: set[str] = set()
        for rel in relationships:
            connected_node_ids.add(rel["from"])
            connected_node_ids.add(rel["to"])

        # Filter nodes to only include connected ones
        nodes = [n for n in nodes if n["id"] in connected_node_ids]

        return {
            "query": query,
            "nodes": nodes,
            "relationships": relationships,
            "summary": {
                "messages_found": len([n for n in nodes if n["type"] == "Message"]),
                "entities_found": len([n for n in nodes if n["type"] != "Message"]),
                "relationships_found": len(relationships),
            },
        }

    except Exception as e:
        logger.exception(f"[memory_graph_search] Error for query '{query}': {e}")
        return {"error": f"Memory graph search failed: {str(e)}"}
