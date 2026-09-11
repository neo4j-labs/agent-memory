"""Background enrichment processing.

Uses asyncio for non-blocking background enrichment.
The enrichment queue processes entities asynchronously,
updating them in Neo4j as results arrive.
"""

import asyncio
import json
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any
from uuid import UUID

from neo4j_agent_memory.enrichment.base import (
    EnrichmentProvider,
    EnrichmentResult,
    EnrichmentStatus,
    EnrichmentTask,
)

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from neo4j_agent_memory.graph.client import Neo4jClient


class BackgroundEnrichmentService:
    """Manages background entity enrichment.

    This service:
    - Maintains a priority queue of entities to enrich
    - Processes enrichment tasks asynchronously
    - Updates entities in Neo4j with enrichment data
    - Handles retries and rate limiting
    - Does not block the main entity extraction flow

    Example:
        service = BackgroundEnrichmentService(client, provider)
        await service.start()

        # Queue entities for enrichment (non-blocking)
        await service.enqueue(entity_id, "Albert Einstein", "PERSON")

        # Later: shutdown gracefully
        await service.stop()
    """

    def __init__(
        self,
        client: "Neo4jClient",
        provider: EnrichmentProvider,
        *,
        max_queue_size: int = 1000,
        max_retries: int = 3,
        retry_delay: float = 60.0,
        min_confidence: float = 0.7,
        entity_types: list[str] | None = None,
        on_enriched: Callable[[UUID, EnrichmentResult], None] | None = None,
    ):
        """
        Initialize background enrichment service.

        Args:
            client: Neo4j client for database operations
            provider: Enrichment provider to use
            max_queue_size: Maximum number of pending enrichment tasks
            max_retries: Maximum retry attempts for failed enrichments
            retry_delay: Delay in seconds between retry attempts
            min_confidence: Minimum entity confidence to trigger enrichment
            entity_types: Entity types to enrich (None = all supported types)
            on_enriched: Optional callback when entity is enriched
        """
        self._client = client
        self._provider = provider
        self._max_queue_size = max_queue_size
        self._max_retries = max_retries
        self._retry_delay = retry_delay
        self._min_confidence = min_confidence
        self._entity_types = entity_types
        self._on_enriched = on_enriched

        self._queue: asyncio.PriorityQueue[tuple[int, EnrichmentTask]] = asyncio.PriorityQueue(
            maxsize=max_queue_size
        )
        self._running = False
        self._worker_task: asyncio.Task[None] | None = None
        self._pending_ids: set[UUID] = set()

    async def start(self) -> None:
        """Start the background worker."""
        if self._running:
            return

        self._running = True
        self._worker_task = asyncio.create_task(self._worker_loop())
        logger.info("Background enrichment service started")

    async def stop(self, timeout: float = 30.0) -> None:
        """Stop the background worker gracefully.

        Args:
            timeout: Maximum time to wait for pending tasks
        """
        if not self._running:
            return

        self._running = False

        if self._worker_task:
            try:
                await asyncio.wait_for(self._worker_task, timeout=timeout)
            except asyncio.TimeoutError:
                logger.warning("Enrichment service stop timeout - cancelling")
                self._worker_task.cancel()
                try:
                    await self._worker_task
                except asyncio.CancelledError:
                    pass

        logger.info(f"Background enrichment service stopped ({len(self._pending_ids)} pending)")

    async def enqueue(
        self,
        entity_id: UUID,
        entity_name: str,
        entity_type: str,
        *,
        context: str | None = None,
        priority: int = 0,
        confidence: float = 1.0,
    ) -> bool:
        """Add an entity to the enrichment queue.

        Args:
            entity_id: Entity UUID
            entity_name: Entity name
            entity_type: Entity type (PERSON, ORGANIZATION, etc.)
            context: Optional disambiguating context
            priority: Task priority (higher = more urgent)
            confidence: Entity confidence score

        Returns:
            True if queued, False if skipped (already pending, queue full,
            type not supported, or confidence too low)
        """
        # Check confidence threshold
        if confidence < self._min_confidence:
            logger.debug(
                f"Skipping enrichment for {entity_name}: confidence {confidence} < {self._min_confidence}"
            )
            return False

        # Check entity type filter
        if self._entity_types and entity_type.upper() not in [
            t.upper() for t in self._entity_types
        ]:
            logger.debug(f"Skipping enrichment for {entity_name}: type {entity_type} not in filter")
            return False

        # Check if provider supports this type
        if not self._provider.supports_entity_type(entity_type):
            logger.debug(f"Skipping enrichment for {entity_name}: type {entity_type} not supported")
            return False

        # Check if already pending
        if entity_id in self._pending_ids:
            logger.debug(f"Entity {entity_id} already pending enrichment")
            return False

        # Check queue capacity
        if self._queue.full():
            logger.warning("Enrichment queue full, dropping task")
            return False

        task = EnrichmentTask(
            entity_id=entity_id,
            entity_name=entity_name,
            entity_type=entity_type,
            context=context,
            priority=priority,
            max_retries=self._max_retries,
        )

        # Priority queue uses (priority, item) tuples
        # Negate priority so higher priority = lower number = processed first
        await self._queue.put((-priority, task))
        self._pending_ids.add(entity_id)

        logger.debug(f"Queued enrichment for {entity_name} ({entity_type})")
        return True

    async def _worker_loop(self) -> None:
        """Main worker loop processing enrichment tasks."""
        while self._running:
            try:
                # Get next task with timeout
                try:
                    _, task = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue

                # Process the task
                await self._process_task(task)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in enrichment worker: {e}")
                await asyncio.sleep(1.0)

    async def _process_task(self, task: EnrichmentTask) -> None:
        """Process a single enrichment task."""
        try:
            # Perform enrichment
            result = await self._provider.enrich(
                task.entity_name,
                task.entity_type,
                context=task.context,
            )

            # Handle rate limiting with retry
            if result.status == EnrichmentStatus.RATE_LIMITED:
                if task.retry_count < task.max_retries:
                    task.retry_count += 1
                    logger.info(
                        f"Rate limited enriching {task.entity_name}, retry {task.retry_count}/{task.max_retries}"
                    )
                    await asyncio.sleep(self._retry_delay)
                    await self._queue.put((-task.priority, task))
                    return
                else:
                    logger.warning(f"Max retries exceeded for {task.entity_name} (rate limited)")

            # Update entity in Neo4j if we got data
            if result.has_data():
                await self._update_entity(task.entity_id, result)
                logger.info(f"Enriched entity {task.entity_name} via {result.provider}")

            # Callback if provided
            if self._on_enriched:
                try:
                    self._on_enriched(task.entity_id, result)
                except Exception as e:
                    logger.warning(f"Enrichment callback error: {e}")

        except Exception as e:
            logger.error(f"Error enriching {task.entity_name}: {e}")

            # Retry on error
            if task.retry_count < task.max_retries:
                task.retry_count += 1
                logger.info(
                    f"Retrying enrichment for {task.entity_name} ({task.retry_count}/{task.max_retries})"
                )
                await asyncio.sleep(self._retry_delay)
                await self._queue.put((-task.priority, task))
                return

        finally:
            self._pending_ids.discard(task.entity_id)

    async def _update_entity(
        self,
        entity_id: UUID,
        result: EnrichmentResult,
    ) -> None:
        """Update entity in Neo4j with enrichment data."""
        attrs = result.to_entity_attributes()

        if not attrs:
            return

        # Build the update query.
        #
        # The headline fields are written as node properties so Cypher can
        # filter and return them (``MemoryClient.get_locations`` selects
        # ``e.enriched_description`` and ``e.wikipedia_url`` directly), and the
        # full result is kept in the ``enrichment_data`` JSON blob. Both are
        # folded back into ``Entity.metadata`` by
        # ``LongTermMemory._parse_entity``.
        query = """
        MATCH (e:Entity {id: $id})
        SET e.enriched_description = $enriched_description,
            e.enriched_at = datetime(),
            e.enrichment_provider = $provider,
            e.enrichment_data = $enrichment_data,
            e.wikipedia_url = coalesce($wikipedia_url, e.wikipedia_url),
            e.wikidata_id = coalesce($wikidata_id, e.wikidata_id),
            e.image_url = coalesce($image_url, e.image_url)
        RETURN e
        """

        # Prepare enrichment data as JSON
        enrichment_data = {
            "description": result.description,
            "summary": result.summary,
            "wikipedia_url": result.wikipedia_url,
            "wikidata_id": result.wikidata_id,
            "diffbot_uri": result.diffbot_uri,
            "image_url": result.image_url,
            "source_url": result.source_url,
            "confidence": result.confidence,
            "retrieved_at": result.retrieved_at.isoformat(),
            "metadata": result.metadata,
        }
        # Remove None values
        enrichment_data = {k: v for k, v in enrichment_data.items() if v is not None}

        params: dict[str, Any] = {
            "id": str(entity_id),
            "enriched_description": result.description,
            "provider": result.provider,
            "enrichment_data": json.dumps(enrichment_data),
            "wikipedia_url": result.wikipedia_url,
            "wikidata_id": result.wikidata_id,
            "image_url": result.image_url,
        }

        await self._client.execute_write(query, params)
        logger.debug(f"Updated entity {entity_id} with enrichment data from {result.provider}")

    @property
    def queue_size(self) -> int:
        """Current number of pending tasks."""
        return self._queue.qsize()

    @property
    def pending_count(self) -> int:
        """Number of entities pending enrichment."""
        return len(self._pending_ids)

    @property
    def is_running(self) -> bool:
        """Whether the service is running."""
        return self._running

    def is_pending(self, entity_id: UUID) -> bool:
        """Check if an entity is pending enrichment."""
        return entity_id in self._pending_ids
