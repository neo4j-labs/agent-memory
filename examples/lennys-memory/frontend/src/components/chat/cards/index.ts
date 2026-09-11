/**
 * Tool Result Card Components
 *
 * This module provides rich visualization cards for tool results in the chat interface.
 */

// Main smart selector
export { ToolResultCard } from "./ToolResultCard";

// Individual card components
export { BaseCard } from "./BaseCard";
export { MapCard } from "./MapCard";
export { GraphCard } from "./GraphCard";
export { DataCard } from "./DataCard";
export { StatsCard } from "./StatsCard";
export { EntityCard } from "./EntityCard";
export { RawJsonCard } from "./RawJsonCard";

// Types
export type {
  CardType,
  BaseCardProps,
  LocationData,
  PathNode,
  MapCardProps,
  GraphNodeData,
  GraphRelationshipData,
  GraphCardProps,
  ColumnDef,
  DataCardProps,
  StatItem,
  ChartDataPoint,
  StatsCardProps,
  ToolCardMapping,
  EntityData,
  EntityMention,
  RelatedEntity,
  EntityCardProps,
} from "./types";

// Registry: card selection (./cardType) + data extraction (./extractors)
export {
  getCardTypeForTool,
  getToolDisplayTitle,
  hasEntityData,
  hasLocationData,
  extractLocations,
  extractPathNodes,
  extractGraphData,
  extractStats,
  extractTableData,
  extractEntityData,
  extractMemoryGraphData,
} from "./toolCardRegistry";
