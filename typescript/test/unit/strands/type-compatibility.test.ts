/**
 * Compile-time type assertions: our integration classes implement the real
 * Strands interfaces. This test exists to catch upstream type drift on
 * `npm install` of a new Strands version — if Strands renames or reshapes
 * one of the interfaces we wrap, this test fails at build time.
 */

import { describe, it, expect } from "vitest";
import type {
  SnapshotStorage,
  ConversationManager,
} from "@strands-agents/sdk";
import {
  Neo4jConversationManager,
  Neo4jSessionStorage,
} from "../../../src/integrations/strands/index.js";
import { MemoryClient } from "../../../src/client.js";

describe("Strands type compatibility", () => {
  it("Neo4jSessionStorage satisfies SnapshotStorage", () => {
    const memory = new MemoryClient({ apiKey: "k" });
    const storage: SnapshotStorage = new Neo4jSessionStorage(memory);
    // Touch every method so unused-warning rules can't elide them.
    expect(typeof storage.saveSnapshot).toBe("function");
    expect(typeof storage.loadSnapshot).toBe("function");
    expect(typeof storage.listSnapshotIds).toBe("function");
    expect(typeof storage.deleteSession).toBe("function");
    expect(typeof storage.loadManifest).toBe("function");
    expect(typeof storage.saveManifest).toBe("function");
  });

  it("Neo4jConversationManager satisfies the ConversationManager shape", () => {
    const memory = new MemoryClient({ apiKey: "k" });
    // Strands' ConversationManager is an abstract class. We duck-type to the
    // shape it requires: `name`, `reduce(opts)`, `initAgent(agent)`.
    const cm = new Neo4jConversationManager(memory, {
      conversationId: "c1",
    });
    const ducktyped: Pick<ConversationManager, "name" | "reduce" | "initAgent"> = cm;
    expect(ducktyped.name).toBe("neo4j:context-injection");
    expect(typeof ducktyped.reduce).toBe("function");
    expect(typeof ducktyped.initAgent).toBe("function");
  });
});
