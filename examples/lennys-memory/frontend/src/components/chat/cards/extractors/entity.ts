/**
 * Entity extractor: normalise the two entity result shapes the backend
 * returns into the knowledge-panel payload EntityCard expects.
 */

import type { EntityData, EntityMention, RelatedEntity } from "../types";

/**
 * Extract entity data from tool result
 */
export function extractEntityData(result: unknown): {
  entity: EntityData;
  mentions: EntityMention[];
  relatedEntities: RelatedEntity[];
} | null {
  if (!result || typeof result !== "object") return null;

  let entity: EntityData | null = null;
  let mentions: EntityMention[] = [];
  let relatedEntities: RelatedEntity[] = [];

  // Entity context format: { entity: {...}, mentions: [...] }
  if ("entity" in result) {
    const entityResult = result as {
      entity: Record<string, unknown>;
      mentions?: unknown[];
      related_entities?: unknown[];
    };

    const e = entityResult.entity;
    entity = {
      id: String(e.id || ""),
      name: String(e.name || "Unknown"),
      type: String(e.type || "UNKNOWN"),
      subtype: e.subtype as string | undefined,
      description: e.description as string | undefined,
      enriched_description: e.enriched_description as string | undefined,
      wikipedia_url: e.wikipedia_url as string | undefined,
      wikidata_id: e.wikidata_id as string | undefined,
      image_url: e.image_url as string | undefined,
      confidence: e.confidence as number | undefined,
    };

    // Extract mentions
    if (entityResult.mentions && Array.isArray(entityResult.mentions)) {
      mentions = entityResult.mentions.map((m) => {
        const mention = m as Record<string, unknown>;
        return {
          content: String(mention.content || ""),
          speaker: mention.speaker as string | undefined,
          episode: (mention.episode_guest ||
            mention.episode ||
            mention.session_id) as string | undefined,
          session_id: mention.session_id as string | undefined,
        };
      });
    }

    // Extract related entities
    if (
      entityResult.related_entities &&
      Array.isArray(entityResult.related_entities)
    ) {
      relatedEntities = entityResult.related_entities.map((r) => {
        const rel = r as Record<string, unknown>;
        return {
          id: String(rel.id || ""),
          name: String(rel.name || "Unknown"),
          type: String(rel.type || "UNKNOWN"),
          subtype: rel.subtype as string | undefined,
          co_occurrences: rel.co_occurrences as number | undefined,
        };
      });
    }
  }
  // Direct entity format: { name, type, ... }
  else if ("name" in result && "type" in result) {
    const e = result as Record<string, unknown>;
    entity = {
      id: String(e.id || ""),
      name: String(e.name || "Unknown"),
      type: String(e.type || "UNKNOWN"),
      subtype: e.subtype as string | undefined,
      description: e.description as string | undefined,
      enriched_description: e.enriched_description as string | undefined,
      wikipedia_url: e.wikipedia_url as string | undefined,
      wikidata_id: e.wikidata_id as string | undefined,
      image_url: e.image_url as string | undefined,
      confidence: e.confidence as number | undefined,
    };
  }

  if (!entity) return null;

  return { entity, mentions, relatedEntities };
}
