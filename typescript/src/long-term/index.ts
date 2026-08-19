/**
 * Long-term memory operations.
 *
 * Bridge methods (Silver tier) plus Volume 5 / hosted-native methods for
 * entity feedback, history, merge-by-id, graph view, and provenance.
 */

import { MemoryError, ValidationError } from "../errors.js";
import type { Transport } from "../transport/index.js";
import type {
  AddRelationshipOptions,
  Entity,
  EntityFeedbackResult,
  EntityGraph,
  EntityGraphEdge,
  EntityGraphNode,
  ExpandedGraph,
  EntityHistory,
  EntityMention,
  EntityMergeResult,
  EntityRelationshipRef,
  Fact,
  GetRelatedEntitiesOptions,
  ListEntitiesOptions,
  Preference,
  Relationship,
  SearchEntitiesOptions,
  SearchPreferencesOptions,
  SetEntityFeedbackOptions,
  UpdateEntityOptions,
  WaitForExtractionOptions,
} from "../types.js";

interface WireEntity {
  id: string;
  name: string;
  type: string;
  subtype?: string;
  description?: string;
  embedding?: number[];
  canonical_name?: string;
  created_at?: string;
  updated_at?: string;
  confidence?: number;
  source_stage?: string;
  relationships?: WireEntityRelRef[];
}

interface WireEntityRelRef {
  id: string;
  type: string;
  target_id: string;
  target_name?: string;
  properties?: Record<string, unknown>;
}

/**
 * Hosted create response when NAMS resolves-before-create merges the new
 * name onto an existing entity: `{id, resolution: "merged", merged_into,
 * confidence}` — no name/type fields.
 */
interface WireMergedResolution {
  id?: string;
  resolution: string;
  merged_into?: string;
  confidence?: number;
}

interface WirePreference {
  id: string;
  category: string;
  preference: string;
  context?: string;
  embedding?: number[];
}

interface WireFact {
  id: string;
  subject: string;
  predicate: string;
  object: string;
  embedding?: number[];
}

interface WireRelationship {
  id: string;
  source_id: string;
  target_id: string;
  relationship_type: string;
  properties?: Record<string, unknown>;
}

interface WireEntityHistory {
  entity_id: string;
  mentions: WireMention[];
}

interface WireMention {
  conversation_id: string;
  message_id?: string;
  content: string;
  timestamp: string;
}

interface WireGraphNode {
  id: string;
  name: string;
  type: string;
}

interface WireGraphEdge {
  id: string;
  source: string;
  target: string;
  type: string;
}

interface WireGraph {
  nodes?: WireGraphNode[];
  edges?: WireGraphEdge[];
}

// NAMS projects unset node properties as JSON null (e.g. `confidence` on a
// manually-created entity) — normalize those to undefined so Entity's
// optional fields stay `T | undefined` at runtime, matching their types.
function toEntity(w: WireEntity): Entity {
  return {
    id: w.id,
    name: w.name,
    type: w.type,
    subtype: w.subtype ?? undefined,
    description: w.description ?? undefined,
    embedding: w.embedding ?? undefined,
    canonicalName: w.canonical_name ?? undefined,
    createdAt: w.created_at ?? "",
    updatedAt: w.updated_at ?? undefined,
    confidence: w.confidence ?? undefined,
    sourceStage: w.source_stage ?? undefined,
    relationships: w.relationships?.map(toRelRef) ?? undefined,
  };
}

function toRelRef(w: WireEntityRelRef): EntityRelationshipRef {
  return {
    id: w.id,
    type: w.type,
    targetId: w.target_id,
    targetName: w.target_name,
    properties: w.properties,
  };
}

function toPreference(w: WirePreference): Preference {
  return {
    id: w.id,
    category: w.category,
    preference: w.preference,
    context: w.context,
    embedding: w.embedding,
  };
}

function toFact(w: WireFact): Fact {
  return {
    id: w.id,
    subject: w.subject,
    predicate: w.predicate,
    object: w.object,
    embedding: w.embedding,
  };
}

function toRelationship(w: WireRelationship): Relationship {
  return {
    id: w.id,
    sourceId: w.source_id,
    targetId: w.target_id,
    relationshipType: w.relationship_type,
    properties: w.properties ?? {},
  };
}

function toMention(w: WireMention): EntityMention {
  return {
    conversationId: w.conversation_id,
    messageId: w.message_id,
    content: w.content,
    timestamp: w.timestamp,
  };
}

function toGraphNode(w: WireGraphNode): EntityGraphNode {
  return { id: w.id, name: w.name, type: w.type };
}

function toGraphEdge(w: WireGraphEdge): EntityGraphEdge {
  return { id: w.id, source: w.source, target: w.target, type: w.type };
}

export class LongTermMemory {
  constructor(private readonly transport: Transport) {}

  // ---- Silver tier (bridge) ----------------------------------------------

  async addEntity(
    name: string,
    entityType: string,
    options?: { description?: string },
  ): Promise<Entity> {
    const wire = await this.transport.request<WireEntity | WireMergedResolution>("add_entity", {
      name,
      entity_type: entityType,
      type: entityType,
      description: options?.description,
    });
    // NAMS resolves-before-create: a sufficiently similar name merges onto an
    // existing entity and the response carries no name/type — follow up with
    // a GET for the canonical merged-into record.
    if (wire && typeof wire === "object" && "resolution" in wire && wire.resolution === "merged") {
      return this.resolveMergedEntity(wire, name, entityType);
    }
    return toEntity(wire as WireEntity);
  }

  /**
   * Turn a `resolution: "merged"` create response into the canonical Entity.
   *
   * Fetches the merged-into entity; if that read fails (or no id was
   * returned), synthesizes a minimal record from the request so the caller
   * still gets a well-formed Entity.
   */
  private async resolveMergedEntity(
    wire: WireMergedResolution,
    name: string,
    entityType: string,
  ): Promise<Entity> {
    const mergedId = wire.merged_into ?? wire.id;
    if (mergedId) {
      try {
        return await this.getEntity(mergedId);
      } catch (error) {
        if (!(error instanceof MemoryError)) throw error;
        // Canonical record unreadable — fall through to the synthesized form.
      }
    }
    return toEntity({
      id: mergedId ?? "",
      name,
      type: entityType,
      confidence: wire.confidence,
    });
  }

  async addPreference(
    category: string,
    preference: string,
    options?: { context?: string },
  ): Promise<Preference> {
    const wire = await this.transport.request<WirePreference>("add_preference", {
      category,
      preference,
      context: options?.context,
    });
    return toPreference(wire);
  }

  async addFact(subject: string, predicate: string, obj: string): Promise<Fact> {
    const wire = await this.transport.request<WireFact>("add_fact", {
      subject,
      predicate,
      obj,
    });
    return toFact(wire);
  }

  async searchEntities(query: string, options?: SearchEntitiesOptions): Promise<Entity[]> {
    const wire = await this.transport.request<WireEntity[]>("search_entities", {
      query,
      type: options?.type,
      limit: options?.limit ?? 10,
    });
    return wire.map(toEntity);
  }

  /**
   * Poll entity search until async extraction has caught up, or time out.
   *
   * NAMS extracts entities in a background pipeline, so writes return before
   * the entities are searchable. This helper lets application and test code
   * await consistency explicitly instead of racing a fixed delay.
   *
   * Provide one of:
   * - `predicate` — called with the current results; return true when satisfied.
   * - `expectedNames` — succeed once every name appears (case-insensitive).
   *   **Recommended**: NAMS entity search is vector/nearest-neighbor, so a
   *   `minResults` threshold is satisfied almost immediately on a non-empty
   *   workspace; prefer `expectedNames`/`predicate` to confirm a *specific*
   *   extraction landed.
   * - otherwise — succeed once at least `minResults` entities match.
   *
   * Returns `true` if satisfied within `timeoutMs`, `false` otherwise (it does
   * not throw, so callers can branch or skip gracefully).
   */
  async waitForExtraction(options: WaitForExtractionOptions): Promise<boolean> {
    const {
      query,
      expectedNames,
      minResults = 1,
      predicate,
      timeoutMs = 30_000,
      intervalMs = 1_000,
    } = options;
    const q = query ?? (expectedNames && expectedNames.length > 0 ? expectedNames[0] : undefined);
    if (q === undefined && predicate === undefined) {
      throw new ValidationError(
        "waitForExtraction requires one of: query, expectedNames, or predicate.",
      );
    }
    const want = (expectedNames ?? []).map((n) => n.toLowerCase());
    const fetch = Math.max(minResults, want.length, options.limit ?? 10);
    const deadline = Date.now() + timeoutMs;
    for (;;) {
      const results = await this.searchEntities(q ?? "", { limit: fetch });
      let ok: boolean;
      if (predicate !== undefined) {
        ok = predicate(results);
      } else if (want.length > 0) {
        const found = new Set(results.map((e) => e.name.toLowerCase()));
        ok = want.every((n) => found.has(n));
      } else {
        ok = results.length >= minResults;
      }
      if (ok) return true;
      if (Date.now() >= deadline) return false;
      await new Promise((r) => setTimeout(r, intervalMs));
    }
  }

  async searchPreferences(
    query: string,
    options?: SearchPreferencesOptions,
  ): Promise<Preference[]> {
    const wire = await this.transport.request<WirePreference[]>("search_preferences", {
      query,
      category: options?.category,
      limit: options?.limit ?? 10,
    });
    return wire.map(toPreference);
  }

  async getEntityByName(name: string): Promise<Entity | null> {
    const wire = await this.transport.request<WireEntity | null>("get_entity_by_name", {
      name,
    });
    return wire ? toEntity(wire) : null;
  }

  async getRelatedEntities(
    entityId: string,
    options?: GetRelatedEntitiesOptions,
  ): Promise<Entity[]> {
    const wire = await this.transport.request<WireEntity[]>("get_related_entities", {
      entity_id: entityId,
      relationship_type: options?.relationshipType,
      depth: options?.depth ?? 1,
    });
    return wire.map(toEntity);
  }

  async addRelationship(
    sourceId: string,
    targetId: string,
    relationshipType: string,
    options?: AddRelationshipOptions,
  ): Promise<Relationship> {
    const wire = await this.transport.request<WireRelationship>("add_relationship", {
      source_id: sourceId,
      target_id: targetId,
      relationship_type: relationshipType,
      properties: options?.properties,
    });
    return toRelationship(wire);
  }

  async mergeDuplicateEntities(
    sourceId: string,
    targetId: string,
    options?: { canonicalName?: string },
  ): Promise<Entity> {
    const wire = await this.transport.request<WireEntity>("merge_duplicate_entities", {
      source_id: sourceId,
      target_id: targetId,
      canonical_name: options?.canonicalName,
    });
    return toEntity(wire);
  }

  // ---- Volume 5 / hosted-native methods -----------------------------------

  /** List all entities, optionally filtered by entity type. */
  async listEntities(options?: ListEntitiesOptions): Promise<Entity[]> {
    const wire = await this.transport.request<WireEntity[]>("list_entities", {
      type: options?.type,
      limit: options?.limit,
    });
    return wire.map(toEntity);
  }

  /** Fetch one entity (with relationships) by id. */
  async getEntity(entityId: string): Promise<Entity> {
    const wire = await this.transport.request<WireEntity>("get_entity", {
      entity_id: entityId,
    });
    return toEntity(wire);
  }

  /** Update an existing entity's name and/or description.
   *
   * The hosted PUT /v1/entities/{id} returns `{status: "updated"}` rather
   * than the full entity, so when the response lacks an `id` we follow up
   * with a GET to keep the SDK contract — "update returns the updated
   * Entity". Bridge transports return the entity directly, so we tolerate
   * both shapes.
   */
  async updateEntity(entityId: string, options: UpdateEntityOptions): Promise<Entity> {
    const wire = await this.transport.request<WireEntity | { status: string }>(
      "update_entity",
      {
        entity_id: entityId,
        name: options.name,
        description: options.description,
      },
    );
    if (wire && typeof wire === "object" && "id" in wire && (wire as WireEntity).id) {
      return toEntity(wire as WireEntity);
    }
    return this.getEntity(entityId);
  }

  /** Delete an entity and its relationships. */
  async deleteEntity(entityId: string): Promise<void> {
    await this.transport.request("delete_entity", { entity_id: entityId });
  }

  /** Score an entity 0-1 and optionally mark it human-confirmed. */
  async setEntityFeedback(
    entityId: string,
    options: SetEntityFeedbackOptions,
  ): Promise<EntityFeedbackResult> {
    const result = await this.transport.request<{ id: string; updated: boolean }>(
      "set_entity_feedback",
      {
        entity_id: entityId,
        user_score: options.userScore,
        confirmed: options.confirmed,
      },
    );
    return { id: result.id, updated: result.updated };
  }

  /** All cross-conversation mentions of this entity. */
  async getEntityHistory(entityId: string): Promise<EntityHistory> {
    const wire = await this.transport.request<WireEntityHistory>("get_entity_history", {
      entity_id: entityId,
    });
    return {
      entityId: wire.entity_id,
      mentions: (wire.mentions ?? []).map(toMention),
    };
  }

  /** Merge `sourceId` into `targetId`, leaving a SAME_AS provenance link. */
  async mergeEntities(sourceId: string, targetId: string): Promise<EntityMergeResult> {
    const wire = await this.transport.request<{
      source_id: string;
      target_id: string;
      status: string;
    }>("merge_entities", {
      source_id: sourceId,
      target_id: targetId,
    });
    return { sourceId: wire.source_id, targetId: wire.target_id, status: wire.status };
  }

  /** Full-graph view of all entities + edges. Pair with NVL for visualization. */
  async getEntityGraph(): Promise<EntityGraph> {
    const wire = await this.transport.request<WireGraph>("get_entity_graph", {});
    return {
      nodes: (wire.nodes ?? []).map(toGraphNode),
      edges: (wire.edges ?? []).map(toGraphEdge),
    };
  }

  /**
   * Expand one entity's 1-hop neighborhood for graph visualization. Returns the
   * canonical-resolved neighbors of `nodeId`, excluding any ids in `loadedIds`
   * (pass the ids already on screen to fetch only the delta).
   */
  async expandGraph(nodeId: string, loadedIds: string[] = []): Promise<ExpandedGraph> {
    const raw = await this.transport.request<{ nodes?: unknown[]; edges?: unknown[] }>(
      "expand_graph",
      { body: { nodeId, loadedIds } },
    );
    return {
      nodes: (raw.nodes ?? []) as ExpandedGraph["nodes"],
      edges: (raw.edges ?? []) as ExpandedGraph["edges"],
    };
  }
}
