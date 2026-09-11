"use client";

import { useState, useMemo, useEffect } from "react";
import {
  Box,
  VStack,
  Text,
  Spinner,
  Portal,
  CloseButton,
  Flex,
} from "@chakra-ui/react";
import { DialogRoot, DialogContent, DialogHeader, DialogTitle, DialogBody, DialogCloseTrigger, DialogBackdrop, DialogPositioner } from "@chakra-ui/react";
import dynamic from "next/dynamic";
import { BaseCard } from "./BaseCard";
import { LuMapPin } from "react-icons/lu";
import type { MapCardProps, LocationData } from "./types";

// Dynamic imports for Leaflet (avoid SSR issues)
const MapContainer = dynamic(
  () => import("react-leaflet").then((mod) => mod.MapContainer),
  { ssr: false }
);
const TileLayer = dynamic(
  () => import("react-leaflet").then((mod) => mod.TileLayer),
  { ssr: false }
);
const CircleMarker = dynamic(
  () => import("react-leaflet").then((mod) => mod.CircleMarker),
  { ssr: false }
);
const Popup = dynamic(
  () => import("react-leaflet").then((mod) => mod.Popup),
  { ssr: false }
);
const Polyline = dynamic(
  () => import("react-leaflet").then((mod) => mod.Polyline),
  { ssr: false }
);

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

/**
 * MapCard displays locations on an interactive map.
 * Features:
 * - Inline compact view (180-250px height)
 * - Circle markers with subtype-based colors
 * - Auto-calculated bounds
 * - Path visualization
 * - Expand to fullscreen
 */
export function MapCard({
  toolCall,
  locations,
  center,
  zoom,
  showPath = false,
  pathNodes = [],
  isExpanded = false,
  onExpand,
  onCollapse,
  compactHeight = 220,
}: MapCardProps) {
  const [isReady, setIsReady] = useState(false);
  const [leafletReady, setLeafletReady] = useState(false);

  // Fix Leaflet icons on mount
  useEffect(() => {
    (async () => {
      const L = (await import("leaflet")).default;
      delete (L.Icon.Default.prototype as unknown as { _getIconUrl?: unknown })._getIconUrl;
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

  useEffect(() => {
    const timer = setTimeout(() => setIsReady(true), 100);
    return () => clearTimeout(timer);
  }, []);

  // Calculate bounds
  const mapBounds = useMemo(() => {
    if (center && zoom) {
      return { center, zoom };
    }
    if (locations.length === 0) {
      return { center: [20, 0] as [number, number], zoom: 2 };
    }
    if (locations.length === 1) {
      return {
        center: [locations[0].latitude, locations[0].longitude] as [number, number],
        zoom: 10,
      };
    }
    const lats = locations.map((l) => l.latitude);
    const lngs = locations.map((l) => l.longitude);
    const centerLat = (Math.min(...lats) + Math.max(...lats)) / 2;
    const centerLng = (Math.min(...lngs) + Math.max(...lngs)) / 2;
    const maxSpread = Math.max(
      Math.max(...lats) - Math.min(...lats),
      Math.max(...lngs) - Math.min(...lngs)
    );
    let autoZoom = 2;
    if (maxSpread < 1) autoZoom = 10;
    else if (maxSpread < 5) autoZoom = 6;
    else if (maxSpread < 20) autoZoom = 4;
    else if (maxSpread < 60) autoZoom = 3;
    return {
      center: [centerLat, centerLng] as [number, number],
      zoom: autoZoom,
    };
  }, [locations, center, zoom]);

  const getMarkerColor = (loc: LocationData) => {
    const subtype = loc.subtype?.toLowerCase() || "default";
    return SUBTYPE_COLORS[subtype] || SUBTYPE_COLORS.default;
  };

  const mapContent = (height: string) => (
    <Box height={height} width="100%">
      {!isReady || !leafletReady ? (
        <VStack height="100%" justify="center" bg="bg.muted">
          <Spinner size="md" color="brand.500" />
          <Text fontSize="sm" color="fg.muted">
            Loading map...
          </Text>
        </VStack>
      ) : (
        <MapContainer
          center={mapBounds.center}
          zoom={mapBounds.zoom}
          style={{ height: "100%", width: "100%" }}
          scrollWheelZoom={isExpanded}
          dragging={isExpanded}
          zoomControl={isExpanded}
          doubleClickZoom={isExpanded}
        >
          <TileLayer
            attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
            url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
          />
          {locations.map((loc) => (
            <CircleMarker
              key={loc.id}
              center={[loc.latitude, loc.longitude]}
              radius={isExpanded ? 8 : 6}
              fillColor={getMarkerColor(loc)}
              color="#fff"
              weight={2}
              fillOpacity={0.8}
            >
              <Popup>
                <Box maxW="200px">
                  <Text fontWeight="bold" fontSize="sm">
                    {loc.name}
                  </Text>
                  {loc.subtype && (
                    <Text fontSize="xs" color="gray.600">
                      {loc.subtype}
                    </Text>
                  )}
                  {loc.description && (
                    <Text fontSize="xs" mt={1}>
                      {loc.description.length > 100
                        ? `${loc.description.slice(0, 100)}...`
                        : loc.description}
                    </Text>
                  )}
                  {loc.episodeCount !== undefined && loc.episodeCount > 0 && (
                    <Text fontSize="xs" mt={1} color="gray.500">
                      Mentioned in {loc.episodeCount} episode
                      {loc.episodeCount > 1 ? "s" : ""}
                    </Text>
                  )}
                </Box>
              </Popup>
            </CircleMarker>
          ))}
          {showPath && pathNodes.length > 1 && (
            <Polyline
              positions={pathNodes
                .filter((n) => n.latitude && n.longitude)
                .map((n) => [n.latitude!, n.longitude!] as [number, number])}
              color="#6366F1"
              weight={3}
              dashArray="5, 10"
            />
          )}
        </MapContainer>
      )}
    </Box>
  );

  // Footer summary
  const footerText = `${locations.length} location${locations.length !== 1 ? "s" : ""}${showPath ? ` · ${pathNodes.length} path nodes` : ""}`;

  // Fullscreen dialog
  if (isExpanded) {
    return (
      <DialogRoot open={true} size="cover" onOpenChange={() => onCollapse?.()}>
        <Portal>
          <DialogBackdrop />
          <DialogPositioner>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>
                  <Flex align="center" gap={2}>
                    <LuMapPin />
                    Locations ({locations.length})
                  </Flex>
                </DialogTitle>
                <DialogCloseTrigger asChild>
                  <CloseButton size="sm" />
                </DialogCloseTrigger>
              </DialogHeader>
              <DialogBody p={0} height="calc(100vh - 80px)">
                {mapContent("100%")}
              </DialogBody>
            </DialogContent>
          </DialogPositioner>
        </Portal>
      </DialogRoot>
    );
  }

  return (
    <BaseCard
      toolCall={toolCall}
      title={`Locations (${locations.length})`}
      icon={<LuMapPin size={14} />}
      colorPalette="teal"
      isExpanded={isExpanded}
      onExpand={onExpand}
      onCollapse={onCollapse}
      compactHeight={compactHeight}
      footer={footerText}
    >
      {mapContent(`${compactHeight}px`)}
    </BaseCard>
  );
}
