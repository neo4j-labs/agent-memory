"""Factory for creating enrichment providers.

Provides factory functions and wrapper classes for enrichment providers:
- create_wikimedia_provider() - Create Wikimedia/Wikipedia provider
- create_diffbot_provider() - Create Diffbot provider (requires API key)
- create_enrichment_provider() - Generic factory
- CachedEnrichmentProvider - Caching wrapper
- CompositeEnrichmentProvider - Combines multiple providers
- create_enrichment_service() - Creates complete service from configuration
"""

import logging
import time
from typing import TYPE_CHECKING, Literal

from neo4j_agent_memory.enrichment.base import (
    EnrichmentProvider,
    EnrichmentResult,
    EnrichmentStatus,
)

if TYPE_CHECKING:
    from neo4j_agent_memory.config.settings import EnrichmentConfig

logger = logging.getLogger(__name__)


def create_wikimedia_provider(
    *,
    user_agent: str = "neo4j-agent-memory/1.0",
    rate_limit: float = 0.5,
    language: str = "en",
) -> "WikimediaProvider":
    """
    Create Wikimedia/Wikipedia enrichment provider.

    Args:
        user_agent: User-Agent header for API requests
        rate_limit: Minimum seconds between requests
        language: Wikipedia language code

    Returns:
        Configured WikimediaProvider instance
    """
    from neo4j_agent_memory.enrichment.wikimedia import WikimediaProvider

    return WikimediaProvider(
        user_agent=user_agent,
        rate_limit=rate_limit,
        language=language,
    )


def create_diffbot_provider(
    api_key: str,
    *,
    rate_limit: float = 0.2,
) -> "DiffbotProvider":
    """
    Create Diffbot Knowledge Graph enrichment provider.

    Args:
        api_key: Diffbot API key
        rate_limit: Minimum seconds between requests

    Returns:
        Configured DiffbotProvider instance

    Raises:
        ValueError: If api_key is empty
    """
    from neo4j_agent_memory.enrichment.diffbot import DiffbotProvider

    if not api_key:
        raise ValueError("Diffbot provider requires an API key")

    return DiffbotProvider(
        api_key=api_key,
        rate_limit=rate_limit,
    )


def create_enrichment_provider(
    provider: Literal["wikimedia", "diffbot"] | str,
    *,
    api_key: str | None = None,
    user_agent: str = "neo4j-agent-memory/1.0",
    rate_limit: float | None = None,
    language: str = "en",
) -> EnrichmentProvider:
    """
    Create an enrichment provider instance.

    Args:
        provider: Provider type ("wikimedia" or "diffbot")
        api_key: API key (required for Diffbot)
        user_agent: User-Agent header for API requests
        rate_limit: Minimum seconds between requests (provider-specific default if None)
        language: Language code for results

    Returns:
        Configured EnrichmentProvider instance

    Raises:
        ValueError: For unknown provider or missing API key
    """
    provider_lower = provider.lower() if isinstance(provider, str) else provider

    if provider_lower == "wikimedia":
        return create_wikimedia_provider(
            user_agent=user_agent,
            rate_limit=rate_limit if rate_limit is not None else 0.5,
            language=language,
        )
    elif provider_lower == "diffbot":
        if not api_key:
            raise ValueError("Diffbot provider requires an API key")
        return create_diffbot_provider(
            api_key=api_key,
            rate_limit=rate_limit if rate_limit is not None else 0.2,
        )
    else:
        raise ValueError(f"Unknown enrichment provider: {provider}")


class CachedEnrichmentProvider:
    """Wrapper that caches enrichment results.

    Maintains an in-memory cache with TTL to avoid repeated API calls
    for the same entity.

    Example:
        provider = WikimediaProvider()
        cached = CachedEnrichmentProvider(provider, ttl_hours=24)
        result1 = await cached.enrich("Albert Einstein", "PERSON")  # API call
        result2 = await cached.enrich("Albert Einstein", "PERSON")  # Cache hit
    """

    def __init__(
        self,
        provider: EnrichmentProvider,
        *,
        max_cache_size: int = 10000,
        ttl_hours: int = 168,  # 1 week
    ):
        """
        Initialize cached provider.

        Args:
            provider: Underlying enrichment provider
            max_cache_size: Maximum number of results to cache
            ttl_hours: Cache TTL in hours
        """
        self._provider = provider
        self._cache: dict[str, tuple[EnrichmentResult, float]] = {}
        self._max_cache_size = max_cache_size
        self._ttl_seconds = ttl_hours * 3600

    def _cache_key(self, entity_name: str, entity_type: str) -> str:
        """Generate cache key from entity name and type."""
        return f"{entity_type.upper()}:{entity_name.lower().strip()}"

    @property
    def name(self) -> str:
        return self._provider.name

    @property
    def supported_entity_types(self) -> list[str]:
        return self._provider.supported_entity_types

    def supports_entity_type(self, entity_type: str) -> bool:
        return self._provider.supports_entity_type(entity_type)

    async def enrich(
        self,
        entity_name: str,
        entity_type: str,
        *,
        context: str | None = None,
        language: str = "en",
    ) -> EnrichmentResult:
        """Enrich with caching."""
        key = self._cache_key(entity_name, entity_type)

        # Check cache
        if key in self._cache:
            result, timestamp = self._cache[key]
            if time.time() - timestamp < self._ttl_seconds:
                logger.debug(f"Cache hit for enrichment: {entity_name}")
                return result
            else:
                # Expired
                del self._cache[key]

        # Fetch fresh data
        result = await self._provider.enrich(
            entity_name, entity_type, context=context, language=language
        )

        # Only cache successful results
        if result.status in (EnrichmentStatus.SUCCESS, EnrichmentStatus.NOT_FOUND):
            # Evict oldest entries if cache is full
            if len(self._cache) >= self._max_cache_size:
                sorted_keys = sorted(self._cache.keys(), key=lambda k: self._cache[k][1])
                for k in sorted_keys[: self._max_cache_size // 10]:
                    del self._cache[k]

            self._cache[key] = (result, time.time())

        return result

    def clear_cache(self) -> None:
        """Clear all cached results."""
        self._cache.clear()

    @property
    def cache_size(self) -> int:
        """Current number of cached results."""
        return len(self._cache)


class CompositeEnrichmentProvider:
    """Combines multiple providers, trying each in order until success.

    Useful for fallback strategies - e.g., try Diffbot first (more structured),
    fall back to Wikipedia if not found.

    Example:
        composite = CompositeEnrichmentProvider([
            diffbot_provider,
            wikimedia_provider,
        ])
        result = await composite.enrich("Apple Inc", "ORGANIZATION")
    """

    def __init__(self, providers: list[EnrichmentProvider]):
        """
        Initialize composite provider.

        Args:
            providers: List of providers to try in order
        """
        if not providers:
            raise ValueError("CompositeEnrichmentProvider requires at least one provider")
        self._providers = providers

    @property
    def name(self) -> str:
        return "composite"

    @property
    def supported_entity_types(self) -> list[str]:
        """Union of all provider's supported types."""
        types: set[str] = set()
        for p in self._providers:
            types.update(p.supported_entity_types)
        return list(types)

    def supports_entity_type(self, entity_type: str) -> bool:
        return any(p.supports_entity_type(entity_type) for p in self._providers)

    async def enrich(
        self,
        entity_name: str,
        entity_type: str,
        *,
        context: str | None = None,
        language: str = "en",
    ) -> EnrichmentResult:
        """Try each provider until one succeeds."""
        last_result: EnrichmentResult | None = None

        for provider in self._providers:
            if not provider.supports_entity_type(entity_type):
                continue

            result = await provider.enrich(
                entity_name, entity_type, context=context, language=language
            )

            if result.has_data():
                return result

            last_result = result

            # Don't continue on rate limiting
            if result.status == EnrichmentStatus.RATE_LIMITED:
                return result

        # All providers failed - return last result or NOT_FOUND
        if last_result:
            return last_result

        return EnrichmentResult(
            entity_name=entity_name,
            entity_type=entity_type,
            provider="composite",
            status=EnrichmentStatus.NOT_FOUND,
        )

    @property
    def providers(self) -> list[EnrichmentProvider]:
        """List of underlying providers."""
        return self._providers


def create_enrichment_service(
    config: "EnrichmentConfig",
) -> EnrichmentProvider | None:
    """
    Create a complete enrichment service based on configuration.

    Creates providers based on config, wraps with caching if enabled,
    and combines into a composite provider if multiple are configured.

    Args:
        config: Enrichment configuration from settings

    Returns:
        Configured EnrichmentProvider, or None if enrichment is disabled

    Example:
        from neo4j_agent_memory.config.settings import EnrichmentConfig

        config = EnrichmentConfig(
            enabled=True,
            providers=["wikimedia", "diffbot"],
            diffbot_api_key="your-key",
        )
        service = create_enrichment_service(config)
    """
    from neo4j_agent_memory.config.settings import EnrichmentProvider as EnrichmentProviderEnum

    if not config.enabled:
        return None

    # Check for NONE provider
    if EnrichmentProviderEnum.NONE in config.providers:
        return None

    providers: list[EnrichmentProvider] = []

    for provider_type in config.providers:
        if provider_type == EnrichmentProviderEnum.NONE:
            continue

        try:
            provider: EnrichmentProvider

            if provider_type == EnrichmentProviderEnum.WIKIMEDIA:
                provider = create_wikimedia_provider(
                    user_agent=config.user_agent,
                    rate_limit=config.wikimedia_rate_limit,
                    language=config.language,
                )
            elif provider_type == EnrichmentProviderEnum.DIFFBOT:
                if not config.diffbot_api_key:
                    logger.warning("Diffbot provider requires API key - skipping")
                    continue
                provider = create_diffbot_provider(
                    api_key=config.diffbot_api_key.get_secret_value(),
                    rate_limit=config.diffbot_rate_limit,
                )
            # provider_type is narrowed to WIKIMEDIA | DIFFBOT above (NONE is
            # skipped), so the branches are exhaustive; a newly-added provider
            # will surface as a possibly-unbound `provider` error here.

            # Wrap with caching if enabled
            if config.cache_results:
                provider = CachedEnrichmentProvider(
                    provider,
                    ttl_hours=config.cache_ttl_hours,
                )

            providers.append(provider)

        except Exception as e:
            logger.warning(f"Could not create {provider_type} provider: {e}")

    if not providers:
        return None

    if len(providers) == 1:
        return providers[0]

    return CompositeEnrichmentProvider(providers)


# Import providers for type hints
if TYPE_CHECKING:
    from neo4j_agent_memory.enrichment.diffbot import DiffbotProvider
    from neo4j_agent_memory.enrichment.wikimedia import WikimediaProvider
