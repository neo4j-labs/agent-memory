/**
 * The graph tool a `MemoryManager` cannot provide: neighbourhood traversal.
 * Deliberately excludes search and add, which the manager owns as
 * `search_memory` / `add_memory`.
 *
 * `MemoryStore.getTools()` is synchronous, and `PluginRegistry._addAndInit`
 * calls it *before* awaiting `initAgent` (and therefore before the store's
 * `initialize()`), so the SDK module cannot be awaited first and `tool()` is
 * out of reach. The tool is a plain object satisfying `Tool` — an abstract
 * class with no non-abstract members, so structural assignment holds — whose
 * async-generator `stream()` reaches `ToolResultBlock` / `JsonBlock` through
 * the lazy loader at call time, keeping the published bundle free of a runtime
 * SDK import.
 */

import type {
  JSONSchema,
  JSONValue,
  Tool,
  ToolContext,
  ToolStreamGenerator,
} from "@strands-agents/sdk";

import { loadStrands } from "./internal.js";
import type { Neo4jMemoryStore } from "./memory-store.js";

/** A 1-hop NAMS expansion can be wide; cap what reaches the model. */
const MAX_ELEMENTS = 50;

/** Strands' ToolRegistry rejects names longer than this (registry/tool-registry.js). */
const MAX_TOOL_NAME_LENGTH = 64;

const ENTITY_GRAPH_SUFFIX = "_get_entity_graph";

const ENTITY_GRAPH_DESCRIPTION =
  "Explore the graph neighbourhood of an entity — how it connects to people, " +
  "organizations and places. Traverses one hop.";

/**
 * The store's name, reduced to something legal in a tool name, 1:1.
 *
 * Names are namespaced per store so two stores can coexist on one manager.
 * TS's `ToolRegistry.add` *throws* on a duplicate name, so a collision breaks
 * agent construction outright.
 *
 * Two things here are many-to-one, and either one can collide: sanitisation
 * (`team/graph`, `team graph` and `Team_Graph` all reduce to `team_graph`) and
 * truncation to fit the registry's 64-character limit. So a prefix that is not
 * a faithful, whole rendering of the name carries a short digest of the
 * original. A name that is already legal *and* already fits keeps a clean
 * prefix; any two distinct names stay distinct either way.
 */
export function toolPrefix(name: string, reserved: number): string {
  const slug = name
    .toLowerCase()
    .replace(/[^a-z0-9]/g, "_")
    .replace(/_+/g, "_")
    .replace(/^_|_$/g, "");
  const base = slug || "store";
  if (base === name && base.length <= MAX_TOOL_NAME_LENGTH - reserved) {
    return base;
  }
  const digest = `_${shortDigest(name)}`;
  const room = MAX_TOOL_NAME_LENGTH - reserved - digest.length;
  return `${base.slice(0, Math.max(1, room))}${digest}`;
}

/** FNV-1a, 32-bit, hex. Not a hash for security — just a stable 6-char tag that
 *  keeps colliding prefixes apart. Avoids `node:crypto`, which edge runtimes
 *  lack. */
function shortDigest(value: string): string {
  let hash = 0x811c9dc5;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return hash.toString(16).padStart(8, "0").slice(0, 6);
}

export function buildStoreTools(store: Neo4jMemoryStore): Tool[] {
  const name = `${toolPrefix(store.name, ENTITY_GRAPH_SUFFIX.length)}${ENTITY_GRAPH_SUFFIX}`;
  const inputSchema: JSONSchema = {
    type: "object",
    properties: {
      entityName: { type: "string", description: "The entity to start from." },
    },
    required: ["entityName"],
  };

  const entityGraph: Tool = {
    name,
    description: ENTITY_GRAPH_DESCRIPTION,
    toolSpec: { name, description: ENTITY_GRAPH_DESCRIPTION, inputSchema },
    async *stream(context: ToolContext): ToolStreamGenerator {
      const strands = await loadStrands();
      const input = (context.toolUse.input ?? {}) as { entityName?: unknown };
      const entityName = typeof input.entityName === "string" ? input.entityName : "";
      const json = await entityGraphPayload(store, entityName);
      return new strands.ToolResultBlock({
        toolUseId: context.toolUse.toolUseId,
        status: "success",
        content: [new strands.JsonBlock({ json })],
      });
    },
  };

  // No get_user_preferences: NAMS exposes no preferences endpoint
  // (transport/rest.ts:172-174), and an unscoped variant is exactly the
  // cross-tenant leak the Python store's user gate exists to prevent.
  return [entityGraph];
}

async function entityGraphPayload(
  store: Neo4jMemoryStore,
  entityName: string,
): Promise<JSONValue> {
  if (!entityName) return { error: "entityName is required" };
  // Through the store, never straight to a captured client: with injection
  // disabled a tool call can be the store's first operation. After validation,
  // so a malformed call does not open a connection for nothing.
  await store.initialize();

  const matches = await store.client.longTerm.searchEntities(entityName, { limit: 1 });
  const centre = matches[0];
  if (!centre) return { error: `entity not found: ${entityName}` };

  // expandGraph is keyed by node id and traverses one hop, so the name is
  // resolved through entity search first (get_entity_by_name has no REST
  // route) and the reported depth is always 1.
  const expansion = await store.client.longTerm.expandGraph(centre.id);
  // Flagged only when the cap actually dropped something: the model cannot
  // otherwise tell a small neighbourhood from a truncated one.
  const truncated =
    expansion.nodes.length > MAX_ELEMENTS || expansion.edges.length > MAX_ELEMENTS;
  return {
    center: centre.canonicalName ?? centre.name,
    depth: 1,
    ...(truncated && { truncated: true }),
    nodes: expansion.nodes.slice(0, MAX_ELEMENTS).map((node) => ({
      id: String(node.id),
      name: String((node.properties ?? {}).name ?? ""),
      ...(node.labels ? { labels: node.labels } : {}),
    })),
    edges: expansion.edges.slice(0, MAX_ELEMENTS).map((edge) => ({
      from: String(edge.source ?? ""),
      relationship: String(edge.type ?? ""),
      to: String(edge.target ?? ""),
    })),
  };
}
