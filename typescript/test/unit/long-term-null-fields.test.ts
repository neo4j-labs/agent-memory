import { describe, expect, it, vi } from "vitest";
import { LongTermMemory } from "../../src/long-term/index.js";

function memoryReturning(wire: unknown): LongTermMemory {
  return new LongTermMemory({ request: vi.fn(async () => wire) } as never);
}

describe("LongTermMemory nullable wire fields", () => {
  it("normalizes entity and inline relationship optional fields", async () => {
    const entity = await memoryReturning({
      id: "e1",
      name: "Alice",
      type: "person",
      created_at: "2026-09-10T12:00:00Z",
      subtype: null,
      description: null,
      embedding: null,
      canonical_name: null,
      updated_at: null,
      confidence: null,
      source_stage: null,
      metadata: null,
      relationships: [{
        id: "r1", type: "KNOWS", target_id: "e2", target_name: null, properties: null,
      }],
    }).getEntity("e1");
    for (const key of [
      "subtype", "description", "embedding", "canonicalName", "updatedAt",
      "confidence", "sourceStage", "metadata",
    ] as const) {
      expect(entity[key]).toBeUndefined();
    }
    expect(entity.relationships?.[0]).toEqual({
      id: "r1", type: "KNOWS", targetId: "e2", targetName: undefined, properties: undefined,
    });
  });

  it("preserves zero, empty strings and arrays, and nulls in user dictionaries", async () => {
    const properties = { note: null, nested: { value: null } };
    const entity = await memoryReturning({
      id: "e1", name: "Alice", type: "person", created_at: "2026-09-10T12:00:00Z",
      description: "", embedding: [], confidence: 0, metadata: properties,
      relationships: [{ id: "r1", type: "KNOWS", target_id: "e2", target_name: "", properties }],
    }).getEntity("e1");
    expect(entity.description).toBe("");
    expect(entity.embedding).toEqual([]);
    expect(entity.confidence).toBe(0);
    expect(entity.metadata).toEqual(properties);
    expect(entity.relationships?.[0]?.targetName).toBe("");
    expect(entity.relationships?.[0]?.properties).toEqual(properties);
  });

  it("normalizes a null relationship list", async () => {
    const entity = await memoryReturning({
      id: "e1", name: "Alice", type: "person", relationships: null,
    }).getEntity("e1");
    expect(entity.relationships).toBeUndefined();
  });

  it.each([null, undefined])("normalizes preference and fact optionals: %j", async (value) => {
    const preference = await memoryReturning({
      id: "p1", category: "style", preference: "concise", context: value, embedding: value,
    }).addPreference("style", "concise");
    expect(preference.context).toBeUndefined();
    expect(preference.embedding).toBeUndefined();
    const fact = await memoryReturning({
      id: "f1", subject: "Alice", predicate: "likes", object: "tea", embedding: value,
    }).addFact("Alice", "likes", "tea");
    expect(fact.embedding).toBeUndefined();
  });

  it("preserves empty preference context and embeddings", async () => {
    const preference = await memoryReturning({
      id: "p1", category: "style", preference: "concise", context: "", embedding: [],
    }).addPreference("style", "concise");
    expect(preference.context).toBe("");
    expect(preference.embedding).toEqual([]);
    const fact = await memoryReturning({
      id: "f1", subject: "Alice", predicate: "likes", object: "tea", embedding: [],
    }).addFact("Alice", "likes", "tea");
    expect(fact.embedding).toEqual([]);
  });

  it("normalizes mention message IDs without dropping an empty string", async () => {
    const history = await memoryReturning({
      entity_id: "e1",
      mentions: [null, undefined, ""].map((message_id) => ({
        conversation_id: "c1", message_id, content: "Alice", timestamp: "2026-09-10T12:00:00Z",
      })),
    }).getEntityHistory("e1");
    expect(history.mentions.map((mention) => mention.messageId)).toEqual([undefined, undefined, ""]);
  });

  it("defaults required relationship properties while preserving user null values", async () => {
    const wire = { id: "r1", source_id: "e1", target_id: "e2", relationship_type: "KNOWS" };
    const relationship = await memoryReturning({ ...wire, properties: null })
      .addRelationship("e1", "e2", "KNOWS");
    expect(relationship.properties).toEqual({});
    const properties = { note: null, nested: { value: null } };
    const populated = await memoryReturning({ ...wire, properties })
      .addRelationship("e1", "e2", "KNOWS");
    expect(populated.properties).toEqual(properties);
  });
});
