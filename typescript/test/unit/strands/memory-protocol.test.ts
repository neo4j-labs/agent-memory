/**
 * Pins the SDK memory contract the store implements. Fails loudly when an
 * upgrade renames or reshapes it. Types are erased at runtime, so the
 * assertions exist to keep the imports live under noUnusedLocals.
 */

import { describe, it, expect } from "vitest";
import type {
  AddMessagesContext,
  MemoryEntry,
  MemoryStore,
  MemoryStoreConfig,
  SearchOptions,
} from "@strands-agents/sdk";

describe("strands memory protocol", () => {
  it("exposes the store contract as types", () => {
    const entry: MemoryEntry = { content: "x", metadata: { kind: "entity" } };
    const options: SearchOptions = { maxSearchResults: 3 };
    const config: MemoryStoreConfig = { name: "graph" };
    const context: AddMessagesContext = { sequenceNumbers: [0] };

    expect(entry.metadata?.kind).toBe("entity");
    expect(options.maxSearchResults).toBe(3);
    expect(config.name).toBe("graph");
    expect(context.sequenceNumbers).toEqual([0]);
  });

  it("treats the write sinks as optional members", () => {
    const readOnly: MemoryStore = {
      name: "ro",
      writable: false,
      async search() {
        return [];
      },
    };

    expect(typeof readOnly.add).toBe("undefined");
    expect(typeof readOnly.addMessages).toBe("undefined");
  });

  it("resolves a store implementing addMessages to server-side extraction", async () => {
    // The manager picks the extraction mode from the sinks a store defines:
    // addMessages present => no ModelExtractor, so no extra model call. This is
    // the behaviour the whole design rests on; pin it here.
    const { MemoryManager } = await import("@strands-agents/sdk");
    const store: MemoryStore = {
      name: "both-sinks",
      writable: true,
      extraction: true,
      async search() {
        return [];
      },
      async add() {
        return undefined;
      },
      async addMessages() {
        return undefined;
      },
    };
    const manager = new MemoryManager({ stores: [store] });
    const wired = manager as unknown as {
      _extractionStores?: Array<{ config: { extractor?: unknown } }>;
    };

    expect(wired._extractionStores).toHaveLength(1);
    expect(wired._extractionStores![0]!.config.extractor).toBeUndefined();
  });
});
