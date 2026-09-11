import { describe, expect, it } from "vitest";

import {
  getCardTypeForTool,
  getToolDisplayTitle,
  hasEntityData,
  hasLocationData,
} from "@/components/chat/cards/cardType";

describe("getCardTypeForTool", () => {
  it("routes location tools with coordinates to the map card", () => {
    const result = [{ name: "Oslo", latitude: 59.91, longitude: 10.75 }];
    expect(getCardTypeForTool("search_locations", result)).toBe("map");
    expect(getCardTypeForTool("find_locations_near", result)).toBe("map");
  });

  it("falls through when a location tool returns no coordinates", () => {
    // e.g. the geocoder found nothing - a map with no markers is useless.
    expect(getCardTypeForTool("search_locations", [])).toBe("data");
    expect(getCardTypeForTool("get_location_clusters", { clusters: [] })).toBe(
      "map",
    );
  });

  it("routes entity context to the knowledge panel", () => {
    const result = { entity: { name: "Airbnb", type: "ORGANIZATION" } };
    expect(getCardTypeForTool("get_entity_context", result)).toBe("entity");
  });

  it("falls back to the data card when an entity tool has no entity payload", () => {
    // "get_entity*" also matches the data-card branch, which is the right
    // fallback: a table of whatever came back beats an empty knowledge panel.
    expect(getCardTypeForTool("get_entity_context", { foo: 1 })).toBe("data");
    expect(getCardTypeForTool("find_related_entities", { foo: 1 })).toBe(
      "graph",
    );
  });

  it("routes relationship, memory-graph and stats tools", () => {
    expect(getCardTypeForTool("find_related_entities", [])).toBe("graph");
    expect(getCardTypeForTool("memory_graph_search", {})).toBe("memory_graph");
    expect(getCardTypeForTool("get_stats", {})).toBe("stats");
    expect(getCardTypeForTool("get_top_entities", [])).toBe("stats");
  });

  it("falls back to raw JSON for unknown tools", () => {
    expect(getCardTypeForTool("do_something_new", { a: 1 })).toBe("raw");
  });
});

describe("hasLocationData", () => {
  it("accepts arrays, paths and clusters", () => {
    expect(hasLocationData([{ latitude: 1, longitude: 2 }])).toBe(true);
    expect(hasLocationData({ nodes: [{ latitude: 1, longitude: 2 }] })).toBe(
      true,
    );
    expect(hasLocationData({ clusters: [] })).toBe(true);
  });

  it("rejects empty, null and coordinate-free payloads", () => {
    expect(hasLocationData([])).toBe(false);
    expect(hasLocationData(null)).toBe(false);
    expect(hasLocationData({ nodes: [{ name: "no coords" }] })).toBe(false);
    expect(hasLocationData([null])).toBe(false);
  });
});

describe("hasEntityData", () => {
  it("accepts both the wrapped and the direct entity shapes", () => {
    expect(hasEntityData({ entity: { name: "Lenny" } })).toBe(true);
    expect(hasEntityData({ name: "Lenny", type: "PERSON" })).toBe(true);
  });

  it("rejects anything without a name", () => {
    expect(hasEntityData({ entity: null })).toBe(false);
    expect(hasEntityData({ type: "PERSON" })).toBe(false);
    expect(hasEntityData("Lenny")).toBe(false);
  });
});

describe("getToolDisplayTitle", () => {
  it("humanises tool names", () => {
    expect(getToolDisplayTitle("tool_search_podcast")).toBe("Search Podcast");
    expect(getToolDisplayTitle("get_entity_context")).toBe(
      "Get Entity Context",
    );
  });
});
