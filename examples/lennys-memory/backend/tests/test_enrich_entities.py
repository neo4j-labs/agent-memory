"""Tests for the entity enrichment script."""

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Add scripts to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))


class TestEnrichmentStats:
    """Tests for EnrichmentStats dataclass."""

    def test_success_rate_with_no_processed(self):
        """Success rate should be 0 when no entities processed."""
        from enrich_entities import EnrichmentStats

        stats = EnrichmentStats()
        assert stats.success_rate == 0.0

    def test_success_rate_calculation(self):
        """Success rate should be calculated correctly."""
        from enrich_entities import EnrichmentStats

        stats = EnrichmentStats(
            total=10,
            enriched=7,
            not_found=2,
            errors=1,
        )
        # 7 / (7 + 2 + 1) = 0.7 = 70%
        assert stats.success_rate == 70.0

    def test_success_rate_all_enriched(self):
        """Success rate should be 100% when all entities are enriched."""
        from enrich_entities import EnrichmentStats

        stats = EnrichmentStats(
            total=5,
            enriched=5,
            not_found=0,
            errors=0,
        )
        assert stats.success_rate == 100.0

    def test_success_rate_none_enriched(self):
        """Success rate should be 0% when no entities are enriched."""
        from enrich_entities import EnrichmentStats

        stats = EnrichmentStats(
            total=5,
            enriched=0,
            not_found=3,
            errors=2,
        )
        assert stats.success_rate == 0.0


class TestFormatTime:
    """Tests for format_time helper function."""

    def test_format_seconds(self):
        """Short durations should show seconds."""
        from enrich_entities import format_time

        assert format_time(30) == "30.0s"
        assert format_time(59) == "59.0s"

    def test_format_minutes(self):
        """Medium durations should show minutes and seconds."""
        from enrich_entities import format_time

        assert format_time(60) == "1m 00s"
        assert format_time(90) == "1m 30s"
        assert format_time(3599) == "59m 59s"

    def test_format_hours(self):
        """Long durations should show hours and minutes."""
        from enrich_entities import format_time

        assert format_time(3600) == "1h 00m 00s"
        assert format_time(3660) == "1h 01m 00s"
        assert format_time(7200) == "2h 00m 00s"


class TestColor:
    """Tests for color helper function."""

    def test_color_with_colors_disabled(self):
        """Should return plain text when colors disabled."""
        from enrich_entities import Colors, color

        # Patch USE_COLORS to False
        with patch("_common.USE_COLORS", False):
            result = color("test", Colors.GREEN)
            assert result == "test"
            assert "\033[" not in result

    def test_color_with_colors_enabled(self):
        """Should return colored text when colors enabled."""
        from enrich_entities import Colors, color

        with patch("_common.USE_COLORS", True):
            result = color("test", Colors.GREEN)
            assert Colors.GREEN in result
            assert Colors.RESET in result
            assert "test" in result


@pytest.mark.asyncio
class TestGetUnenrichedEntities:
    """Tests for get_unenriched_entities function."""

    async def test_returns_entities_without_enrichment(self):
        """Should return entities that haven't been enriched."""
        from enrich_entities import get_unenriched_entities

        mock_client = MagicMock()
        mock_client.query.cypher = AsyncMock(
            return_value=[
                {"id": "1", "name": "John Doe", "type": "PERSON", "description": None},
                {"id": "2", "name": "Acme Corp", "type": "ORGANIZATION", "description": None},
            ]
        )

        result = await get_unenriched_entities(mock_client)

        assert len(result) == 2
        assert result[0]["name"] == "John Doe"
        assert result[1]["name"] == "Acme Corp"

    async def test_filters_by_entity_types(self):
        """Should filter by entity types when specified."""
        from enrich_entities import get_unenriched_entities

        mock_client = MagicMock()
        mock_client.query.cypher = AsyncMock(return_value=[])

        await get_unenriched_entities(mock_client, entity_types=["person", "organization"])

        # Types are passed as a PARAMETER, never interpolated into the query.
        call_args = mock_client.query.cypher.call_args
        query, params = call_args[0][0], call_args[0][1]
        assert "'PERSON'" not in query
        assert params["types"] == ["PERSON", "ORGANIZATION"]

    async def test_respects_limit(self):
        """Should include LIMIT clause when specified."""
        from enrich_entities import get_unenriched_entities

        mock_client = MagicMock()
        mock_client.query.cypher = AsyncMock(return_value=[])

        await get_unenriched_entities(mock_client, limit=10)

        call_args = mock_client.query.cypher.call_args
        query, params = call_args[0][0], call_args[0][1]
        assert "LIMIT $limit" in query
        assert params["limit"] == 10


@pytest.mark.asyncio
class TestGetEnrichmentStatus:
    """Tests for get_enrichment_status function."""

    async def test_returns_status_counts(self):
        """Should return correct counts for each status."""
        from enrich_entities import get_enrichment_status

        mock_client = MagicMock()
        mock_client.query.cypher = AsyncMock(
            return_value=[{"total": 100, "enriched": 50, "pending": 40, "errors": 10}]
        )

        result = await get_enrichment_status(mock_client)

        assert result["total"] == 100
        assert result["enriched"] == 50
        assert result["pending"] == 40
        assert result["errors"] == 10

    async def test_handles_empty_result(self):
        """Should handle empty result gracefully."""
        from enrich_entities import get_enrichment_status

        mock_client = MagicMock()
        mock_client.query.cypher = AsyncMock(return_value=[])

        result = await get_enrichment_status(mock_client)

        assert result["total"] == 0
        assert result["enriched"] == 0
        assert result["pending"] == 0
        assert result["errors"] == 0


@pytest.mark.asyncio
class TestUpdateEntityEnrichment:
    """Tests for update_entity_enrichment function."""

    async def test_updates_entity_with_enrichment_data(self):
        """Should update entity with all enrichment fields."""
        from enrich_entities import update_entity_enrichment

        mock_client = MagicMock()
        mock_client.graph.execute_write = AsyncMock()

        enrichment_data = {
            "description": "A famous person",
            "wikipedia_url": "https://en.wikipedia.org/wiki/John_Doe",
            "wikidata_id": "Q12345",
            "image_url": "https://upload.wikimedia.org/image.jpg",
        }

        await update_entity_enrichment(mock_client, "entity-123", enrichment_data)

        mock_client.graph.execute_write.assert_called_once()
        call_args = mock_client.graph.execute_write.call_args
        params = call_args[0][1]

        assert params["id"] == "entity-123"
        assert params["enriched_description"] == "A famous person"
        assert params["wikipedia_url"] == "https://en.wikipedia.org/wiki/John_Doe"
        assert params["wikidata_id"] == "Q12345"
        assert params["image_url"] == "https://upload.wikimedia.org/image.jpg"
        assert params["provider"] == "wikimedia"


@pytest.mark.asyncio
class TestMarkEntityNotFound:
    """Tests for mark_entity_not_found function."""

    async def test_marks_entity_with_error(self):
        """Should mark entity with enrichment error."""
        from enrich_entities import mark_entity_not_found

        mock_client = MagicMock()
        mock_client.graph.execute_write = AsyncMock()

        await mark_entity_not_found(mock_client, "entity-123", "Not found in Wikipedia")

        mock_client.graph.execute_write.assert_called_once()
        call_args = mock_client.graph.execute_write.call_args
        params = call_args[0][1]

        assert params["id"] == "entity-123"
        assert params["reason"] == "Not found in Wikipedia"


@pytest.mark.asyncio
class TestEnrichEntities:
    """Tests for enrich_entities function."""

    async def test_dry_run_does_not_modify_entities(self):
        """Dry run should not update any entities."""
        from enrich_entities import enrich_entities

        mock_client = MagicMock()
        mock_client.query.cypher = AsyncMock(
            return_value=[
                {"id": "1", "name": "John Doe", "type": "PERSON", "description": None},
            ]
        )
        mock_client.graph.execute_write = AsyncMock()

        stats = await enrich_entities(mock_client, dry_run=True)

        assert stats.total == 1
        # No writes should occur in dry run
        mock_client.graph.execute_write.assert_not_called()

    async def test_returns_zero_stats_when_no_entities(self):
        """Should return zero stats when no entities to enrich."""
        from enrich_entities import enrich_entities

        mock_client = MagicMock()
        mock_client.query.cypher = AsyncMock(return_value=[])

        stats = await enrich_entities(mock_client)

        assert stats.total == 0
        assert stats.enriched == 0
        assert stats.not_found == 0
        assert stats.errors == 0

    async def test_respects_rate_limit(self):
        """Should respect rate limit between API calls."""
        import time

        from enrich_entities import enrich_entities

        from neo4j_agent_memory.enrichment.base import EnrichmentResult, EnrichmentStatus

        mock_client = MagicMock()
        mock_client.query.cypher = AsyncMock(
            return_value=[
                {"id": "1", "name": "Entity 1", "type": "PERSON", "description": None},
                {"id": "2", "name": "Entity 2", "type": "PERSON", "description": None},
            ]
        )
        mock_client.graph.execute_write = AsyncMock()

        # Mock the WikimediaProvider
        mock_result = EnrichmentResult(
            entity_name="Test Entity",
            entity_type="PERSON",
            provider="wikimedia",
            status=EnrichmentStatus.SUCCESS,
            description="Test description",
            wikipedia_url="https://wikipedia.org/test",
        )

        with patch("enrich_entities.build_provider") as mock_build:
            mock_provider = MagicMock()
            mock_provider.enrich = AsyncMock(return_value=mock_result)
            mock_build.return_value = mock_provider

            start_time = time.time()
            stats = await enrich_entities(mock_client, rate_limit=0.1)  # 0.1s rate limit
            elapsed = time.time() - start_time

            # With 2 entities and 0.1s rate limit, should take at least 0.2s
            assert elapsed >= 0.1  # At least one rate limit delay
            assert stats.enriched == 2
