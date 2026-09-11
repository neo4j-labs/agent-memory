/**
 * Tool-result card registry.
 *
 * Two responsibilities, deliberately kept in separate modules:
 *   - ./cardType      which card renders a given tool result
 *   - ./extractors/*  how each card's data is pulled out of the result
 *
 * This file re-exports both so call sites have a single import.
 */

export {
  getCardTypeForTool,
  getToolDisplayTitle,
  hasEntityData,
  hasLocationData,
} from "./cardType";

export {
  extractLocations,
  extractPathNodes,
  extractGraphData,
  extractStats,
  extractTableData,
  extractEntityData,
  extractMemoryGraphData,
} from "./extractors";

export type {
  MemoryGraphNode,
  MemoryGraphRelationship,
  MemoryGraphSummary,
  MemoryGraphSearchResult,
} from "./extractors";
