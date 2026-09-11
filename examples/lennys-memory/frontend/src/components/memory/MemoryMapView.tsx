"use client";

import { useEffect, useState, useMemo, useCallback } from "react";
import {
  Box,
  VStack,
  HStack,
  Text,
  Button,
  Spinner,
  Badge,
  IconButton,
  Link,
} from "@chakra-ui/react";
import {
  HiX,
  HiRefresh,
  HiLocationMarker,
  HiCollection,
} from "react-icons/hi";
import {
  LuExternalLink,
  LuRuler,
  LuRoute,
  LuThermometer,
} from "react-icons/lu";
import dynamic from "next/dynamic";
import { api } from "@/lib/api";
import type { LocationEntity } from "@/lib/types";
import * as turf from "@turf/turf";

// Dynamically import map components to avoid SSR issues with Leaflet
const MapContainer = dynamic(
  () => import("react-leaflet").then((mod) => mod.MapContainer),
  { ssr: false },
);
const TileLayer = dynamic(
  () => import("react-leaflet").then((mod) => mod.TileLayer),
  { ssr: false },
);
const Marker = dynamic(
  () => import("react-leaflet").then((mod) => mod.Marker),
  { ssr: false },
);
const Popup = dynamic(() => import("react-leaflet").then((mod) => mod.Popup), {
  ssr: false,
});
const CircleMarker = dynamic(
  () => import("react-leaflet").then((mod) => mod.CircleMarker),
  { ssr: false },
);
const Polyline = dynamic(
  () => import("react-leaflet").then((mod) => mod.Polyline),
  { ssr: false },
);

// Layer type for visualization mode
type LayerMode = "markers" | "clusters" | "heatmap";

// Basemap options
type BasemapType = "osm" | "satellite" | "terrain";

const BASEMAPS: Record<
  BasemapType,
  { url: string; attribution: string; name: string }
> = {
  osm: {
    url: "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
    attribution:
      '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
    name: "Streets",
  },
  satellite: {
    url: "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    attribution:
      "&copy; Esri &mdash; Source: Esri, i-cubed, USDA, USGS, AEX, GeoEye, Getmapping, Aerogrid, IGN, IGP, UPR-EGP, and the GIS User Community",
    name: "Satellite",
  },
  terrain: {
    url: "https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png",
    attribution:
      'Map data: &copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors, <a href="http://viewfinderpanoramas.org">SRTM</a> | Map style: &copy; <a href="https://opentopomap.org">OpenTopoMap</a>',
    name: "Terrain",
  },
};

// Color palette for location subtypes
const SUBTYPE_COLORS: Record<string, string> = {
  city: "#3B82F6", // blue
  country: "#10B981", // green
  landmark: "#F59E0B", // amber
  region: "#8B5CF6", // purple
  address: "#EF4444", // red
  coordinates: "#6B7280", // gray
  default: "#3B82F6", // blue
};

interface MemoryMapViewProps {
  isOpen: boolean;
  onClose: () => void;
  threadId?: string; // conversation scoping
  initialShowAll?: boolean; // start with all locations visible
}

export default function MemoryMapView({
  isOpen,
  onClose,
  threadId,
  initialShowAll = true, // Default to showing all locations
}: MemoryMapViewProps) {
  const [isLoading, setIsLoading] = useState(false);
  const [locations, setLocations] = useState<LocationEntity[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [mapReady, setMapReady] = useState(false);
  const [leafletReady, setLeafletReady] = useState(false);

  // Fix Leaflet default marker icon issue with Next.js/Webpack
  // This must run client-side only
  useEffect(() => {
    (async () => {
      const L = (await import("leaflet")).default;

      // Fix the default icon URLs
      delete (L.Icon.Default.prototype as { _getIconUrl?: unknown })
        ._getIconUrl;
      L.Icon.Default.mergeOptions({
        iconUrl: "https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon.png",
        iconRetinaUrl:
          "https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon-2x.png",
        shadowUrl:
          "https://unpkg.com/leaflet@1.9.4/dist/images/marker-shadow.png",
      });

      setLeafletReady(true);
    })();
  }, []);

  // UI state
  const [layerMode, setLayerMode] = useState<LayerMode>("markers");
  const [basemap, setBasemap] = useState<BasemapType>("osm");
  const [measureMode, setMeasureMode] = useState(false);
  const [showAllLocations, setShowAllLocations] = useState(initialShowAll);

  // Selection state
  const [selectedLocations, setSelectedLocations] = useState<LocationEntity[]>(
    [],
  );
  const [pathStart, setPathStart] = useState<LocationEntity | null>(null);
  const [pathEnd, setPathEnd] = useState<LocationEntity | null>(null);
  const [shortestPath, setShortestPath] = useState<{
    nodes: Array<{
      id: string;
      name: string;
      latitude?: number;
      longitude?: number;
    }>;
    relationships: Array<{ type: string; from_id: string; to_id: string }>;
    hops: number;
    found: boolean;
  } | null>(null);
  const [isLoadingPath, setIsLoadingPath] = useState(false);

  // Measurement state
  const [measurePoints, setMeasurePoints] = useState<[number, number][]>([]);
  const [totalDistance, setTotalDistance] = useState<number>(0);

  const loadLocations = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      // Only filter by threadId if not showing all locations
      const sessionFilter = showAllLocations ? undefined : threadId;
      const data = await api.locations.list({
        threadId: sessionFilter,
        hasCoordinates: true,
        limit: 500,
      });
      setLocations(data);

      if (data.length === 0) {
        if (!showAllLocations && threadId) {
          setError(
            "No geocoded locations found in this conversation. Toggle 'Show All' to see all podcast locations.",
          );
        } else {
          setError(
            "No geocoded locations available. Run 'make geocode' to add coordinates to Location entities.",
          );
        }
      }
    } catch (err) {
      const errorMsg =
        err instanceof Error ? err.message : "Failed to load locations";
      setError(errorMsg);
    } finally {
      setIsLoading(false);
    }
  }, [threadId, showAllLocations]);

  // Calculate map bounds from locations
  const mapBounds = useMemo(() => {
    if (locations.length === 0) {
      return { center: [20, 0] as [number, number], zoom: 2 };
    }

    if (locations.length === 1) {
      return {
        center: [locations[0].latitude, locations[0].longitude] as [
          number,
          number,
        ],
        zoom: 10,
      };
    }

    const lats = locations.map((l) => l.latitude);
    const lngs = locations.map((l) => l.longitude);
    const centerLat = (Math.min(...lats) + Math.max(...lats)) / 2;
    const centerLng = (Math.min(...lngs) + Math.max(...lngs)) / 2;

    const latSpread = Math.max(...lats) - Math.min(...lats);
    const lngSpread = Math.max(...lngs) - Math.min(...lngs);
    const maxSpread = Math.max(latSpread, lngSpread);

    let zoom = 2;
    if (maxSpread < 1) zoom = 10;
    else if (maxSpread < 5) zoom = 6;
    else if (maxSpread < 20) zoom = 4;
    else if (maxSpread < 60) zoom = 3;

    return { center: [centerLat, centerLng] as [number, number], zoom };
  }, [locations]);

  // Group locations by subtype for stats
  const locationStats = useMemo(() => {
    const stats: Record<string, number> = {};
    for (const loc of locations) {
      const subtype = loc.subtype || "Unknown";
      stats[subtype] = (stats[subtype] || 0) + 1;
    }
    return stats;
  }, [locations]);

  // Calculate convex hull for selected locations
  const selectionHull = useMemo(() => {
    if (selectedLocations.length < 3) return null;
    try {
      const points = turf.featureCollection(
        selectedLocations.map((loc) =>
          turf.point([loc.longitude, loc.latitude]),
        ),
      );
      const hull = turf.convex(points);
      if (hull) {
        return hull.geometry.coordinates[0].map(
          (coord) => [coord[1], coord[0]] as [number, number],
        );
      }
    } catch {
      // Ignore hull calculation errors
    }
    return null;
  }, [selectedLocations]);

  // Handle location click for path selection
  const handleLocationClick = useCallback(
    (location: LocationEntity) => {
      if (measureMode) {
        // Add to measurement points
        const newPoints: [number, number][] = [
          ...measurePoints,
          [location.latitude, location.longitude],
        ];
        setMeasurePoints(newPoints);

        // Calculate total distance
        if (newPoints.length > 1) {
          let total = 0;
          for (let i = 1; i < newPoints.length; i++) {
            const from = turf.point([newPoints[i - 1][1], newPoints[i - 1][0]]);
            const to = turf.point([newPoints[i][1], newPoints[i][0]]);
            total += turf.distance(from, to, { units: "kilometers" });
          }
          setTotalDistance(total);
        }
        return;
      }

      // Path selection mode
      if (!pathStart) {
        setPathStart(location);
        setPathEnd(null);
        setShortestPath(null);
      } else if (!pathEnd) {
        setPathEnd(location);
      } else {
        // Reset and start new selection
        setPathStart(location);
        setPathEnd(null);
        setShortestPath(null);
      }
    },
    [measureMode, measurePoints, pathStart, pathEnd],
  );

  // Fetch shortest path when both endpoints are selected
  useEffect(() => {
    if (pathStart && pathEnd) {
      setIsLoadingPath(true);
      api.locations
        .shortestPath(pathStart.id, pathEnd.id)
        .then((result) => {
          setShortestPath(result);
        })
        .catch(() => {
          setShortestPath({
            nodes: [],
            relationships: [],
            hops: 0,
            found: false,
          });
        })
        .finally(() => {
          setIsLoadingPath(false);
        });
    }
  }, [pathStart, pathEnd]);

  // Clear measurement
  const clearMeasurement = useCallback(() => {
    setMeasurePoints([]);
    setTotalDistance(0);
  }, []);

  // Clear path selection
  const clearPath = useCallback(() => {
    setPathStart(null);
    setPathEnd(null);
    setShortestPath(null);
  }, []);

  useEffect(() => {
    if (isOpen) {
      loadLocations();
      setTimeout(() => setMapReady(true), 100);
    } else {
      setMapReady(false);
      // Reset state when closing
      setSelectedLocations([]);
      setMeasurePoints([]);
      setTotalDistance(0);
      clearPath();
    }
  }, [isOpen, loadLocations, clearPath]);

  // Reload when threadId or showAllLocations changes
  useEffect(() => {
    if (isOpen) {
      loadLocations();
    }
  }, [threadId, showAllLocations, isOpen, loadLocations]);

  if (!isOpen) return null;

  const getMarkerColor = (location: LocationEntity) => {
    const subtype = location.subtype?.toLowerCase() || "default";
    return SUBTYPE_COLORS[subtype] || SUBTYPE_COLORS.default;
  };

  return (
    <>
      {/* Overlay */}
      <Box
        position="fixed"
        top={0}
        left={0}
        right={0}
        bottom={0}
        bg="blackAlpha.600"
        zIndex={1000}
        onClick={onClose}
      />

      {/* Map View Modal */}
      <Box
        position="fixed"
        top={{ base: 0, md: "50%" }}
        left={{ base: 0, md: "50%" }}
        transform={{ base: "none", md: "translate(-50%, -50%)" }}
        width={{ base: "100%", md: "90%", lg: "85%" }}
        height={{ base: "100%", md: "85%" }}
        bg="white"
        borderRadius={{ base: 0, md: "xl" }}
        boxShadow={{ base: "none", md: "2xl" }}
        zIndex={1001}
        display="flex"
        flexDirection="column"
        overflow="hidden"
      >
        {/* Header */}
        <VStack gap={0} borderBottom="1px solid" borderColor="gray.200">
          <HStack
            justifyContent="space-between"
            p={4}
            width="100%"
            bg="gray.50"
          >
            <VStack align="start" gap={0}>
              <HStack gap={2}>
                <Text fontSize="xl" fontWeight="bold">
                  Location Map
                </Text>
                {showAllLocations ? (
                  <Badge colorPalette="blue" fontSize="xs">
                    All Podcasts
                  </Badge>
                ) : threadId ? (
                  <Badge colorPalette="purple" fontSize="xs">
                    Conversation Filtered
                  </Badge>
                ) : null}
              </HStack>
              <Text fontSize="xs" color="gray.600">
                {showAllLocations
                  ? "All locations from podcast transcripts"
                  : threadId
                    ? "Locations mentioned in this conversation"
                    : "All locations from podcast transcripts"}
              </Text>
              {locations.length > 0 && (
                <HStack gap={2} mt={1}>
                  <Badge colorPalette="blue" fontSize="xs">
                    {locations.length} locations
                  </Badge>
                  {Object.entries(locationStats)
                    .slice(0, 3)
                    .map(([subtype, count]) => (
                      <Badge key={subtype} colorPalette="gray" fontSize="xs">
                        {count} {subtype.toLowerCase()}
                      </Badge>
                    ))}
                </HStack>
              )}
            </VStack>

            <HStack gap={2}>
              <IconButton
                aria-label="Refresh locations"
                size="sm"
                onClick={loadLocations}
                disabled={isLoading}
              >
                <HiRefresh />
              </IconButton>
              <IconButton
                aria-label="Close map view"
                size="sm"
                variant="ghost"
                onClick={onClose}
              >
                <HiX />
              </IconButton>
            </HStack>
          </HStack>

          {/* Toolbar */}
          <HStack
            p={2}
            width="100%"
            bg="white"
            borderTop="1px solid"
            borderColor="gray.100"
            gap={{ base: 2, md: 4 }}
            flexWrap={{ base: "nowrap", md: "wrap" }}
            justifyContent={{ base: "flex-start", md: "space-between" }}
            overflowX={{ base: "auto", md: "visible" }}
            css={{
              "&::-webkit-scrollbar": { display: "none" },
              scrollbarWidth: "none",
            }}
          >
            {/* Layer Mode Toggle */}
            <HStack gap={1} flexShrink={0}>
              <Text fontSize="xs" color="gray.500" mr={1} whiteSpace="nowrap">
                View:
              </Text>
              <Button
                size="xs"
                variant={layerMode === "markers" ? "solid" : "outline"}
                colorPalette={layerMode === "markers" ? "blue" : "gray"}
                onClick={() => setLayerMode("markers")}
              >
                <HiLocationMarker />
                <Text ml={1}>Markers</Text>
              </Button>
              <Button
                size="xs"
                variant={layerMode === "clusters" ? "solid" : "outline"}
                colorPalette={layerMode === "clusters" ? "blue" : "gray"}
                onClick={() => setLayerMode("clusters")}
              >
                <HiCollection />
                <Text ml={1}>Clusters</Text>
              </Button>
              <Button
                size="xs"
                variant={layerMode === "heatmap" ? "solid" : "outline"}
                colorPalette={layerMode === "heatmap" ? "blue" : "gray"}
                onClick={() => setLayerMode("heatmap")}
              >
                <LuThermometer />
                <Text ml={1}>Heatmap</Text>
              </Button>
            </HStack>

            {/* Basemap Toggle */}
            <HStack gap={1} flexShrink={0}>
              <Text fontSize="xs" color="gray.500" mr={1} whiteSpace="nowrap">
                Map:
              </Text>
              {(Object.keys(BASEMAPS) as BasemapType[]).map((type) => (
                <Button
                  key={type}
                  size="xs"
                  variant={basemap === type ? "solid" : "outline"}
                  colorPalette={basemap === type ? "green" : "gray"}
                  onClick={() => setBasemap(type)}
                >
                  {BASEMAPS[type].name}
                </Button>
              ))}
            </HStack>

            {/* Data Scope */}
            <HStack gap={1} flexShrink={0}>
              <Text fontSize="xs" color="gray.500" mr={1} whiteSpace="nowrap">
                Scope:
              </Text>
              <Button
                size="xs"
                variant={showAllLocations ? "solid" : "outline"}
                colorPalette={showAllLocations ? "blue" : "gray"}
                onClick={() => setShowAllLocations(true)}
              >
                All Podcasts
              </Button>
              {threadId && (
                <Button
                  size="xs"
                  variant={!showAllLocations ? "solid" : "outline"}
                  colorPalette={!showAllLocations ? "purple" : "gray"}
                  onClick={() => setShowAllLocations(false)}
                >
                  This Conversation
                </Button>
              )}
            </HStack>

            {/* Tools */}
            <HStack gap={1} flexShrink={0}>
              <Text fontSize="xs" color="gray.500" mr={1} whiteSpace="nowrap">
                Tools:
              </Text>
              <Button
                size="xs"
                variant={measureMode ? "solid" : "outline"}
                colorPalette={measureMode ? "orange" : "gray"}
                onClick={() => {
                  setMeasureMode(!measureMode);
                  if (measureMode) clearMeasurement();
                }}
              >
                <LuRuler />
                <Text ml={1}>Measure</Text>
              </Button>
              <Button
                size="xs"
                variant={pathStart ? "solid" : "outline"}
                colorPalette={pathStart ? "purple" : "gray"}
                onClick={() => {
                  if (pathStart) clearPath();
                }}
              >
                <LuRoute />
                <Text ml={1}>Path</Text>
              </Button>
            </HStack>
          </HStack>
        </VStack>

        {/* Main Content Area */}
        <HStack flex={1} position="relative" gap={0}>
          {/* Map Container */}
          <Box flex={1} height="100%" bg="gray.100">
            {isLoading ? (
              <VStack height="100%" justifyContent="center" gap={4}>
                <Spinner size="xl" />
                <Text color="gray.600">Loading locations...</Text>
              </VStack>
            ) : error ? (
              <VStack height="100%" justifyContent="center" gap={4} p={6}>
                <Text fontSize="lg" color="red.500" textAlign="center">
                  {error}
                </Text>
                <Button colorPalette="blue" onClick={loadLocations}>
                  Try Again
                </Button>
              </VStack>
            ) : mapReady && leafletReady && locations.length > 0 ? (
              <Box height="100%" width="100%">
                <MapContainer
                  center={mapBounds.center}
                  zoom={mapBounds.zoom}
                  style={{ height: "100%", width: "100%" }}
                  scrollWheelZoom={true}
                >
                  {/* Basemap Layer */}
                  <TileLayer
                    attribution={BASEMAPS[basemap].attribution}
                    url={BASEMAPS[basemap].url}
                  />

                  {/* Location Markers */}
                  {layerMode === "markers" &&
                    locations.map((location) => (
                      <CircleMarker
                        key={location.id}
                        center={[location.latitude, location.longitude]}
                        radius={8}
                        fillColor={getMarkerColor(location)}
                        color={
                          pathStart?.id === location.id ||
                          pathEnd?.id === location.id
                            ? "#9333EA"
                            : "#fff"
                        }
                        weight={
                          pathStart?.id === location.id ||
                          pathEnd?.id === location.id
                            ? 3
                            : 2
                        }
                        fillOpacity={0.8}
                        eventHandlers={{
                          click: () => handleLocationClick(location),
                        }}
                      >
                        <Popup>
                          <LocationPopup location={location} />
                        </Popup>
                      </CircleMarker>
                    ))}

                  {/* Heatmap Mode - Simple circle overlay */}
                  {layerMode === "heatmap" &&
                    locations.map((location) => (
                      <CircleMarker
                        key={location.id}
                        center={[location.latitude, location.longitude]}
                        radius={20}
                        fillColor="#EF4444"
                        color="transparent"
                        fillOpacity={0.3}
                      />
                    ))}

                  {/* Cluster Mode - Show grouped markers */}
                  {layerMode === "clusters" &&
                    locations.map((location) => (
                      <Marker
                        key={location.id}
                        position={[location.latitude, location.longitude]}
                        eventHandlers={{
                          click: () => handleLocationClick(location),
                        }}
                      >
                        <Popup>
                          <LocationPopup location={location} />
                        </Popup>
                      </Marker>
                    ))}

                  {/* Measurement line */}
                  {measurePoints.length > 1 && (
                    <Polyline
                      positions={measurePoints}
                      color="#F97316"
                      weight={3}
                      dashArray="10, 10"
                    />
                  )}

                  {/* Shortest path line */}
                  {shortestPath?.found &&
                    shortestPath.nodes.filter((n) => n.latitude && n.longitude)
                      .length > 1 && (
                      <Polyline
                        positions={shortestPath.nodes
                          .filter((n) => n.latitude && n.longitude)
                          .map(
                            (n) =>
                              [n.latitude!, n.longitude!] as [number, number],
                          )}
                        color="#9333EA"
                        weight={4}
                      />
                    )}

                  {/* Selection hull */}
                  {selectionHull && (
                    <Polyline
                      positions={selectionHull}
                      color="#3B82F6"
                      weight={2}
                      dashArray="5, 5"
                      fillColor="#3B82F6"
                      fillOpacity={0.1}
                    />
                  )}
                </MapContainer>
              </Box>
            ) : null}
          </Box>

          {/* Side Panel */}
          {(measureMode ||
            pathStart ||
            shortestPath ||
            selectedLocations.length > 0) && (
            <Box
              width="280px"
              height="100%"
              bg="white"
              borderLeft="1px solid"
              borderColor="gray.200"
              p={3}
              overflowY="auto"
            >
              {/* Measurement Panel */}
              {measureMode && (
                <VStack align="stretch" gap={3}>
                  <HStack justifyContent="space-between">
                    <Text fontWeight="bold" fontSize="sm">
                      Distance Measurement
                    </Text>
                    <Button
                      size="xs"
                      variant="ghost"
                      onClick={clearMeasurement}
                    >
                      Clear
                    </Button>
                  </HStack>
                  <Text fontSize="sm" color="gray.600">
                    Click locations to add measurement points
                  </Text>
                  {measurePoints.length > 0 && (
                    <>
                      <Badge colorPalette="orange" alignSelf="start">
                        {measurePoints.length} points
                      </Badge>
                      {totalDistance > 0 && (
                        <Box bg="orange.50" p={3} borderRadius="md">
                          <Text
                            fontSize="lg"
                            fontWeight="bold"
                            color="orange.700"
                          >
                            {totalDistance.toFixed(2)} km
                          </Text>
                          <Text fontSize="xs" color="orange.600">
                            ({(totalDistance * 0.621371).toFixed(2)} miles)
                          </Text>
                        </Box>
                      )}
                    </>
                  )}
                </VStack>
              )}

              {/* Path Panel */}
              {(pathStart || shortestPath) && !measureMode && (
                <VStack align="stretch" gap={3}>
                  <HStack justifyContent="space-between">
                    <Text fontWeight="bold" fontSize="sm">
                      Graph Path
                    </Text>
                    <Button size="xs" variant="ghost" onClick={clearPath}>
                      Clear
                    </Button>
                  </HStack>

                  {pathStart && (
                    <Box bg="purple.50" p={2} borderRadius="md">
                      <Text fontSize="xs" color="purple.600">
                        From:
                      </Text>
                      <Text fontSize="sm" fontWeight="medium">
                        {pathStart.name}
                      </Text>
                    </Box>
                  )}

                  {pathEnd && (
                    <Box bg="purple.50" p={2} borderRadius="md">
                      <Text fontSize="xs" color="purple.600">
                        To:
                      </Text>
                      <Text fontSize="sm" fontWeight="medium">
                        {pathEnd.name}
                      </Text>
                    </Box>
                  )}

                  {!pathEnd && pathStart && (
                    <Text fontSize="xs" color="gray.500">
                      Click another location to find path
                    </Text>
                  )}

                  {isLoadingPath && (
                    <HStack>
                      <Spinner size="sm" />
                      <Text fontSize="sm">Finding path...</Text>
                    </HStack>
                  )}

                  {shortestPath && (
                    <Box>
                      {shortestPath.found ? (
                        <VStack align="stretch" gap={2}>
                          <Badge colorPalette="green" alignSelf="start">
                            Path found: {shortestPath.hops} hops
                          </Badge>
                          <VStack align="stretch" gap={1}>
                            {shortestPath.nodes.map((node, i) => (
                              <HStack key={node.id || i} fontSize="xs">
                                <Box
                                  w={2}
                                  h={2}
                                  borderRadius="full"
                                  bg="purple.500"
                                />
                                <Text>{node.name || node.id}</Text>
                              </HStack>
                            ))}
                          </VStack>
                        </VStack>
                      ) : (
                        <Badge colorPalette="red">No path found</Badge>
                      )}
                    </Box>
                  )}
                </VStack>
              )}
            </Box>
          )}
        </HStack>

        {/* Footer */}
        {locations.length > 0 && !isLoading && !error && (
          <HStack
            p={3}
            borderTop="1px solid"
            borderColor="gray.200"
            justifyContent="center"
            gap={4}
            flexWrap="wrap"
            bg="gray.50"
          >
            <Text fontSize="xs" color="gray.600">
              Click markers to view details | Scroll to zoom | Drag to pan
              {measureMode && " | Measurement mode active"}
              {pathStart && !pathEnd && " | Select destination for path"}
            </Text>
          </HStack>
        )}
      </Box>
    </>
  );
}

// Extracted popup component for cleaner code
function LocationPopup({ location }: { location: LocationEntity }) {
  return (
    <Box maxW="300px">
      <Text fontWeight="bold" fontSize="md" mb={1}>
        {location.name}
      </Text>
      {location.subtype && (
        <Badge
          style={{
            backgroundColor:
              SUBTYPE_COLORS[location.subtype.toLowerCase()] ||
              SUBTYPE_COLORS.default,
            color: "white",
          }}
          size="sm"
          mb={2}
        >
          {location.subtype}
        </Badge>
      )}
      {(location.enriched_description || location.description) && (
        <Text fontSize="sm" color="gray.600" mb={2}>
          {(location.enriched_description || location.description)?.slice(
            0,
            200,
          )}
          {((location.enriched_description || location.description)?.length ||
            0) > 200 && "..."}
        </Text>
      )}
      {location.wikipedia_url && (
        <Link
          href={location.wikipedia_url}
          target="_blank"
          rel="noopener noreferrer"
          fontSize="sm"
          color="blue.500"
          display="flex"
          alignItems="center"
          gap={1}
          mb={2}
        >
          Wikipedia <LuExternalLink size={12} />
        </Link>
      )}
      {location.conversations.length > 0 && (
        <Box mt={2} pt={2} borderTop="1px solid" borderColor="gray.200">
          <Text fontSize="xs" fontWeight="semibold" color="gray.500" mb={1}>
            Mentioned in {location.conversations.length} episode(s):
          </Text>
          <VStack align="start" gap={0.5}>
            {location.conversations.slice(0, 3).map((conv) => (
              <Text key={conv.id} fontSize="xs" color="gray.600">
                {conv.title || conv.id}
              </Text>
            ))}
            {location.conversations.length > 3 && (
              <Text fontSize="xs" color="gray.400">
                +{location.conversations.length - 3} more
              </Text>
            )}
          </VStack>
        </Box>
      )}
      {location.distance_km && (
        <Text fontSize="xs" color="orange.500" mt={2}>
          {location.distance_km.toFixed(2)} km away
        </Text>
      )}
      <Text fontSize="xs" color="gray.400" mt={2}>
        {location.latitude.toFixed(4)}, {location.longitude.toFixed(4)}
      </Text>
    </Box>
  );
}
