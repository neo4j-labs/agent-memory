"""Geocoding services for Location entities.

Provides geocoding functionality to convert location names to coordinates.
Supports multiple providers:
- Nominatim (OpenStreetMap) - Free, rate-limited to 1 req/sec
- Google Geocoding API - Requires API key, more accurate

Example usage:
    from neo4j_agent_memory.services import create_geocoder, NominatimGeocoder

    # Using Nominatim (free, no API key required)
    geocoder = NominatimGeocoder()
    result = await geocoder.geocode("Empire State Building, New York")
    if result:
        print(f"Coordinates: {result.latitude}, {result.longitude}")

    # Using factory with caching
    geocoder = create_geocoder(provider="nominatim", cache_results=True)
    result = await geocoder.geocode("Central Park, NYC")
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@dataclass
class GeocodingResult:
    """Result from a geocoding operation."""

    latitude: float
    longitude: float
    display_name: str | None = None
    place_type: str | None = None
    confidence: float = 1.0

    def as_neo4j_point(self) -> dict[str, float]:
        """Return coordinates as Neo4j Point parameters.

        Use with Cypher: point({latitude: $latitude, longitude: $longitude})
        """
        return {"latitude": self.latitude, "longitude": self.longitude}

    def as_tuple(self) -> tuple[float, float]:
        """Return coordinates as (latitude, longitude) tuple."""
        return (self.latitude, self.longitude)


@runtime_checkable
class Geocoder(Protocol):
    """Protocol for geocoding implementations."""

    async def geocode(self, location: str) -> GeocodingResult | None:
        """
        Geocode a location string to coordinates.

        Args:
            location: Location name or address to geocode

        Returns:
            GeocodingResult with coordinates, or None if not found
        """
        ...

    async def reverse_geocode(self, latitude: float, longitude: float) -> GeocodingResult | None:
        """
        Reverse geocode coordinates to a location name.

        Args:
            latitude: Latitude coordinate
            longitude: Longitude coordinate

        Returns:
            GeocodingResult with location name, or None if not found
        """
        ...


class NominatimGeocoder:
    """Geocoder using OpenStreetMap's Nominatim service.

    Free to use but rate-limited to 1 request per second.
    See: https://nominatim.org/release-docs/develop/api/Search/
    """

    BASE_URL = "https://nominatim.openstreetmap.org"

    def __init__(
        self,
        *,
        user_agent: str = "neo4j-agent-memory/1.0",
        rate_limit: float = 1.0,
        timeout: float = 10.0,
    ):
        """
        Initialize Nominatim geocoder.

        Args:
            user_agent: User-Agent header (required by Nominatim ToS)
            rate_limit: Minimum seconds between requests (default 1.0)
            timeout: Request timeout in seconds
        """
        self._user_agent = user_agent
        self._rate_limit = rate_limit
        self._timeout = timeout
        self._last_request_time: float = 0
        self._lock = asyncio.Lock()

    async def _rate_limit_wait(self) -> None:
        """Wait if needed to respect rate limit."""
        async with self._lock:
            now = time.time()
            elapsed = now - self._last_request_time
            if elapsed < self._rate_limit:
                await asyncio.sleep(self._rate_limit - elapsed)
            self._last_request_time = time.time()

    async def geocode(self, location: str) -> GeocodingResult | None:
        """
        Geocode a location string using Nominatim.

        Args:
            location: Location name or address

        Returns:
            GeocodingResult or None if not found
        """
        try:
            import httpx
        except ImportError:
            raise ImportError("httpx is required for geocoding. Install with: pip install httpx")

        await self._rate_limit_wait()

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(
                    f"{self.BASE_URL}/search",
                    params={
                        "q": location,
                        "format": "json",
                        "limit": 1,
                        "addressdetails": 1,
                    },
                    headers={"User-Agent": self._user_agent},
                )
                response.raise_for_status()
                data = response.json()

                if not data:
                    logger.debug(f"No geocoding results for: {location}")
                    return None

                result = data[0]
                return GeocodingResult(
                    latitude=float(result["lat"]),
                    longitude=float(result["lon"]),
                    display_name=result.get("display_name"),
                    place_type=result.get("type"),
                    confidence=float(result.get("importance", 1.0)),
                )

        except httpx.HTTPError as e:
            logger.warning(f"Geocoding HTTP error for '{location}': {e}")
            return None
        except (KeyError, ValueError, TypeError) as e:
            logger.warning(f"Geocoding parse error for '{location}': {e}")
            return None

    async def reverse_geocode(self, latitude: float, longitude: float) -> GeocodingResult | None:
        """
        Reverse geocode coordinates using Nominatim.

        Args:
            latitude: Latitude coordinate
            longitude: Longitude coordinate

        Returns:
            GeocodingResult or None if not found
        """
        try:
            import httpx
        except ImportError:
            raise ImportError("httpx is required for geocoding. Install with: pip install httpx")

        await self._rate_limit_wait()

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(
                    f"{self.BASE_URL}/reverse",
                    params={
                        "lat": latitude,
                        "lon": longitude,
                        "format": "json",
                    },
                    headers={"User-Agent": self._user_agent},
                )
                response.raise_for_status()
                data = response.json()

                if "error" in data:
                    logger.debug(f"No reverse geocoding result for: {latitude}, {longitude}")
                    return None

                return GeocodingResult(
                    latitude=float(data["lat"]),
                    longitude=float(data["lon"]),
                    display_name=data.get("display_name"),
                    place_type=data.get("type"),
                )

        except httpx.HTTPError as e:
            logger.warning(f"Reverse geocoding HTTP error: {e}")
            return None
        except (KeyError, ValueError, TypeError) as e:
            logger.warning(f"Reverse geocoding parse error: {e}")
            return None


class GoogleGeocoder:
    """Geocoder using Google Maps Geocoding API.

    Requires a Google Cloud API key with Geocoding API enabled.
    See: https://developers.google.com/maps/documentation/geocoding/
    """

    BASE_URL = "https://maps.googleapis.com/maps/api/geocode/json"

    def __init__(
        self,
        api_key: str,
        *,
        timeout: float = 10.0,
    ):
        """
        Initialize Google geocoder.

        Args:
            api_key: Google Cloud API key with Geocoding API enabled
            timeout: Request timeout in seconds
        """
        self._api_key = api_key
        self._timeout = timeout

    async def geocode(self, location: str) -> GeocodingResult | None:
        """
        Geocode a location string using Google Maps.

        Args:
            location: Location name or address

        Returns:
            GeocodingResult or None if not found
        """
        try:
            import httpx
        except ImportError:
            raise ImportError("httpx is required for geocoding. Install with: pip install httpx")

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(
                    self.BASE_URL,
                    params={
                        "address": location,
                        "key": self._api_key,
                    },
                )
                response.raise_for_status()
                data = response.json()

                if data.get("status") != "OK" or not data.get("results"):
                    logger.debug(f"No Google geocoding results for: {location}")
                    return None

                result = data["results"][0]
                geo = result["geometry"]["location"]

                # Map Google location_type to confidence
                location_type = result["geometry"].get("location_type", "APPROXIMATE")
                confidence_map = {
                    "ROOFTOP": 1.0,
                    "RANGE_INTERPOLATED": 0.8,
                    "GEOMETRIC_CENTER": 0.6,
                    "APPROXIMATE": 0.4,
                }

                return GeocodingResult(
                    latitude=geo["lat"],
                    longitude=geo["lng"],
                    display_name=result.get("formatted_address"),
                    place_type=result.get("types", [None])[0],
                    confidence=confidence_map.get(location_type, 0.5),
                )

        except httpx.HTTPError as e:
            logger.warning(f"Google geocoding HTTP error for '{location}': {e}")
            return None
        except (KeyError, ValueError, TypeError) as e:
            logger.warning(f"Google geocoding parse error for '{location}': {e}")
            return None

    async def reverse_geocode(self, latitude: float, longitude: float) -> GeocodingResult | None:
        """
        Reverse geocode coordinates using Google Maps.

        Args:
            latitude: Latitude coordinate
            longitude: Longitude coordinate

        Returns:
            GeocodingResult or None if not found
        """
        try:
            import httpx
        except ImportError:
            raise ImportError("httpx is required for geocoding. Install with: pip install httpx")

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(
                    self.BASE_URL,
                    params={
                        "latlng": f"{latitude},{longitude}",
                        "key": self._api_key,
                    },
                )
                response.raise_for_status()
                data = response.json()

                if data.get("status") != "OK" or not data.get("results"):
                    logger.debug(f"No Google reverse geocoding result for: {latitude}, {longitude}")
                    return None

                result = data["results"][0]
                geo = result["geometry"]["location"]

                return GeocodingResult(
                    latitude=geo["lat"],
                    longitude=geo["lng"],
                    display_name=result.get("formatted_address"),
                    place_type=result.get("types", [None])[0],
                )

        except httpx.HTTPError as e:
            logger.warning(f"Google reverse geocoding HTTP error: {e}")
            return None
        except (KeyError, ValueError, TypeError) as e:
            logger.warning(f"Google reverse geocoding parse error: {e}")
            return None


class CachedGeocoder:
    """Wrapper that caches geocoding results to avoid repeated API calls.

    Maintains an in-memory cache of results. For persistent caching,
    consider using a database or file-based cache.
    """

    def __init__(
        self,
        geocoder: Geocoder,
        *,
        max_cache_size: int = 10000,
    ):
        """
        Initialize cached geocoder.

        Args:
            geocoder: Underlying geocoder to use
            max_cache_size: Maximum number of results to cache
        """
        self._geocoder = geocoder
        self._cache: dict[str, GeocodingResult | None] = {}
        self._reverse_cache: dict[tuple[float, float], GeocodingResult | None] = {}
        self._max_cache_size = max_cache_size

    def _normalize_key(self, location: str) -> str:
        """Normalize location string for cache key."""
        return location.lower().strip()

    def _round_coords(self, lat: float, lon: float) -> tuple[float, float]:
        """Round coordinates for cache key (5 decimal places ~ 1m precision)."""
        return (round(lat, 5), round(lon, 5))

    async def geocode(self, location: str) -> GeocodingResult | None:
        """
        Geocode with caching.

        Args:
            location: Location name or address

        Returns:
            GeocodingResult or None if not found
        """
        key = self._normalize_key(location)

        if key in self._cache:
            logger.debug(f"Cache hit for: {location}")
            return self._cache[key]

        result = await self._geocoder.geocode(location)

        # Evict oldest entries if cache is full
        if len(self._cache) >= self._max_cache_size:
            # Remove first 10% of entries
            keys_to_remove = list(self._cache.keys())[: self._max_cache_size // 10]
            for k in keys_to_remove:
                del self._cache[k]

        self._cache[key] = result
        return result

    async def reverse_geocode(self, latitude: float, longitude: float) -> GeocodingResult | None:
        """
        Reverse geocode with caching.

        Args:
            latitude: Latitude coordinate
            longitude: Longitude coordinate

        Returns:
            GeocodingResult or None if not found
        """
        key = self._round_coords(latitude, longitude)

        if key in self._reverse_cache:
            logger.debug(f"Cache hit for reverse: {latitude}, {longitude}")
            return self._reverse_cache[key]

        result = await self._geocoder.reverse_geocode(latitude, longitude)

        # Evict oldest entries if cache is full
        if len(self._reverse_cache) >= self._max_cache_size:
            keys_to_remove = list(self._reverse_cache.keys())[: self._max_cache_size // 10]
            for k in keys_to_remove:
                del self._reverse_cache[k]

        self._reverse_cache[key] = result
        return result

    def clear_cache(self) -> None:
        """Clear all cached results."""
        self._cache.clear()
        self._reverse_cache.clear()

    @property
    def cache_size(self) -> int:
        """Return current cache size."""
        return len(self._cache) + len(self._reverse_cache)


def create_geocoder(
    provider: Literal["nominatim", "google"] = "nominatim",
    *,
    api_key: str | None = None,
    cache_results: bool = True,
    rate_limit: float = 1.0,
    user_agent: str = "neo4j-agent-memory/1.0",
) -> Geocoder:
    """
    Create a geocoder instance.

    Args:
        provider: Geocoding provider ("nominatim" or "google")
        api_key: API key (required for Google)
        cache_results: Whether to cache results
        rate_limit: Rate limit for Nominatim (requests per second)
        user_agent: User-Agent for Nominatim

    Returns:
        Configured Geocoder instance

    Example:
        # Free Nominatim geocoder with caching
        geocoder = create_geocoder(provider="nominatim", cache_results=True)

        # Google geocoder with API key
        geocoder = create_geocoder(
            provider="google",
            api_key="your-api-key",
            cache_results=True
        )
    """
    if provider == "nominatim":
        base_geocoder: Geocoder = NominatimGeocoder(
            user_agent=user_agent,
            rate_limit=rate_limit,
        )
    elif provider == "google":
        if not api_key:
            raise ValueError("Google geocoder requires an API key")
        base_geocoder = GoogleGeocoder(api_key=api_key)
    else:
        raise ValueError(f"Unknown geocoding provider: {provider}")

    if cache_results:
        return CachedGeocoder(base_geocoder)

    return base_geocoder
