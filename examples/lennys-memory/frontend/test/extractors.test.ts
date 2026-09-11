import { describe, expect, it } from "vitest";

import {
  extractEntityData,
  extractGraphData,
  extractLocations,
  extractMemoryGraphData,
  extractPathNodes,
  extractStats,
  extractTableData,
} from "@/components/chat/cards/extractors";

describe("extractLocations", () => {
  it("maps a flat location list, counting linked episodes", () => {
    const locations = extractLocations([
      {
        id: "loc-1",
        name: "San Francisco",
        latitude: 37.77,
        longitude: -122.42,
        subtype: "CITY",
        enriched_description: "City in California",
        conversations: [{ id: "a" }, { id: "b" }],
      },
      { name: "no coordinates" },
    ]);

    expect(locations).toEqual([
      {
        id: "loc-1",
        name: "San Francisco",
        latitude: 37.77,
        longitude: -122.42,
        subtype: "CITY",
        description: "City in California",
        episodeCount: 2,
      },
    ]);
  });

  it("gives coordinate-bearing rows a stable id when the backend omits one", () => {
    const first = extractLocations([{ latitude: 1, longitude: 2 }]);
    const second = extractLocations([{ latitude: 1, longitude: 2 }]);
    // Stable ids matter: these become React keys.
    expect(first[0].id).toBe(second[0].id);
    expect(first[0].id).toBe("location-0");
  });

  it("flattens path and cluster payloads", () => {
    expect(
      extractLocations({
        nodes: [
          { id: "a", name: "A", latitude: 1, longitude: 2 },
          { id: "b", name: "B" },
        ],
      }),
    ).toHaveLength(1);

    const clustered = extractLocations({
      clusters: [
        {
          country: "Norway",
          locations: [{ name: "Oslo", latitude: 59.91, longitude: 10.75 }],
        },
      ],
    });
    expect(clustered).toHaveLength(1);
    expect(clustered[0].subtype).toBe("Norway");
  });

  it("returns an empty list for unusable payloads", () => {
    expect(extractLocations(null)).toEqual([]);
    expect(extractLocations({ totally: "different" })).toEqual([]);
  });
});

describe("extractPathNodes", () => {
  it("keeps nodes without coordinates so gaps stay visible", () => {
    const nodes = extractPathNodes({
      nodes: [
        { id: "a", name: "A", latitude: 1, longitude: 2 },
        { name: "B" },
      ],
    });
    expect(nodes).toHaveLength(2);
    expect(nodes[1].latitude).toBeUndefined();
    expect(nodes[1].id).toBe("path-node-1");
  });
});

describe("extractGraphData", () => {
  it("builds a star graph from a related-entities list", () => {
    const { nodes, relationships } = extractGraphData([
      { id: "1", name: "Airbnb", type: "ORGANIZATION" },
      { id: "2", name: "Brian Chesky", type: "PERSON", co_occurrences: 12 },
    ]);
    expect(nodes.map((n) => n.label)).toEqual(["Airbnb", "Brian Chesky"]);
    expect(relationships).toEqual([
      { id: "rel-1", from: "1", to: "2", type: "RELATED_TO" },
    ]);
  });

  it("builds entity -> mention edges from an entity context result", () => {
    const { nodes, relationships } = extractGraphData({
      entity: { id: "e1", name: "Airbnb", type: "ORGANIZATION" },
      mentions: [{ speaker: "Lenny" }, { episode: "ep-2" }],
    });
    expect(nodes).toHaveLength(3);
    expect(relationships.every((r) => r.type === "MENTIONED_IN")).toBe(true);
  });

  it("returns empty collections for unusable payloads", () => {
    expect(extractGraphData(null)).toEqual({ nodes: [], relationships: [] });
  });
});

describe("extractStats", () => {
  it("turns memory stats into labelled tiles", () => {
    const stats = extractStats("get_stats", {
      total_episodes: 299,
      total_entities: 4200,
    });
    expect(stats).toEqual([
      { label: "Episodes", value: 299, colorPalette: "blue" },
      { label: "Entities", value: 4200, colorPalette: "orange" },
    ]);
  });

  it("colours top entities by type and caps the tile count", () => {
    const result = Array.from({ length: 10 }, (_, i) => ({
      name: `Person ${i}`,
      type: "PERSON",
      mentions: i,
    }));
    const stats = extractStats("get_top_entities", result);
    expect(stats).toHaveLength(6);
    expect(stats[0].colorPalette).toBe("pink");
  });
});

describe("extractTableData", () => {
  it("uses curated columns for podcast searches", () => {
    const { columns, title } = extractTableData("search_podcast_content", [
      { speaker: "Lenny", content: "...", episode_guest: "Brian Chesky" },
    ]);
    expect(title).toBe("Podcast Matches");
    expect(columns.map((c) => c.key)).toEqual([
      "speaker",
      "content",
      "episode_guest",
    ]);
  });

  it("auto-detects columns for unknown tools, skipping ids and embeddings", () => {
    const { columns } = extractTableData("something_new", [
      { id: "1", embedding: [0.1], alpha: 1, beta: 2, _private: 3 },
    ]);
    expect(columns.map((c) => c.key)).toEqual(["alpha", "beta"]);
    expect(columns[0].label).toBe("Alpha");
  });

  it("returns nothing for non-array results", () => {
    expect(extractTableData("whatever", { not: "an array" })).toEqual({
      columns: [],
      rows: [],
    });
  });
});

describe("extractEntityData", () => {
  it("normalises the wrapped entity-context shape", () => {
    const extracted = extractEntityData({
      entity: {
        id: "e1",
        name: "Y Combinator",
        type: "ORGANIZATION",
        enriched_description: "Startup accelerator",
        wikipedia_url: "https://en.wikipedia.org/wiki/Y_Combinator",
      },
      mentions: [
        { content: "YC was great", speaker: "Lenny", session_id: "s1" },
      ],
      related_entities: [{ id: "e2", name: "Airbnb", type: "ORGANIZATION" }],
    });

    expect(extracted?.entity.name).toBe("Y Combinator");
    expect(extracted?.mentions[0].episode).toBe("s1");
    expect(extracted?.relatedEntities).toHaveLength(1);
  });

  it("normalises the direct entity shape and rejects the rest", () => {
    expect(extractEntityData({ name: "Lenny", type: "PERSON" })?.entity.type).toBe(
      "PERSON",
    );
    expect(extractEntityData({ nothing: true })).toBeNull();
    expect(extractEntityData(null)).toBeNull();
  });
});

describe("extractMemoryGraphData", () => {
  it("passes through nodes, relationships and the summary", () => {
    const data = extractMemoryGraphData({
      query: "growth",
      nodes: [{ id: "n1", label: "Airbnb", type: "Entity" }],
      relationships: [{ id: "r1", from: "n1", to: "n2" }],
      summary: {
        messages_found: 3,
        entities_found: 1,
        relationships_found: 1,
      },
    });
    expect(data?.nodes).toHaveLength(1);
    expect(data?.relationships[0].type).toBe("RELATED");
    expect(data?.summary.messages_found).toBe(3);
  });

  it("surfaces backend errors and rejects shapes without nodes", () => {
    expect(extractMemoryGraphData({ error: "boom", query: "x" })?.error).toBe(
      "boom",
    );
    expect(extractMemoryGraphData({ query: "x" })).toBeNull();
  });
});
