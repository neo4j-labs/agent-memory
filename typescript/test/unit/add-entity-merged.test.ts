/**
 * Unit test — LongTermMemory.addEntity merged-resolution handling
 * (transport mocked).
 *
 * NAMS resolves-before-create: a sufficiently similar name merges onto an
 * existing entity and POST /entities responds
 * `{id, resolution: "merged", merged_into, confidence}` with no name/type.
 * addEntity must fetch the canonical merged-into entity instead of returning
 * a malformed one (mirrors the Python SDK fix).
 */

import { describe, it, expect, vi } from "vitest";
import { LongTermMemory } from "../../src/long-term/index.js";
import { TransportError } from "../../src/errors.js";

const ENTITY_ID = "00000000-0000-0000-0000-000000000001";

const CANONICAL_WIRE = {
  id: ENTITY_ID,
  name: "Alice",
  type: "person",
  description: "Test entity",
  confidence: 0.95,
  created_at: "2026-05-17T12:00:00Z",
  relationships: [],
};

const MERGED_WIRE = {
  id: ENTITY_ID,
  resolution: "merged",
  merged_into: ENTITY_ID,
  confidence: 0.93,
};

function mockTransport(handler: (method: string, params: Record<string, unknown>) => unknown) {
  return { request: vi.fn(async (m: string, p: Record<string, unknown>) => handler(m, p)) };
}

describe("LongTermMemory.addEntity merged resolution", () => {
  it("fetches the canonical entity when the create merged", async () => {
    const t = mockTransport((method) => {
      if (method === "add_entity") return MERGED_WIRE;
      if (method === "get_entity") return CANONICAL_WIRE;
      throw new Error(`unexpected method ${method}`);
    });
    const lt = new LongTermMemory(t as never);
    const entity = await lt.addEntity("Alice Smith", "person");
    const methods = t.request.mock.calls.map((c) => c[0]);
    expect(methods).toEqual(["add_entity", "get_entity"]);
    expect(t.request.mock.calls[1]?.[1]).toMatchObject({ entity_id: ENTITY_ID });
    // The canonical (merged-into) record wins over the requested name.
    expect(entity.id).toBe(ENTITY_ID);
    expect(entity.name).toBe("Alice");
    expect(entity.type).toBe("person");
  });

  it("normalizes null fields on the canonical entity to undefined", async () => {
    // NAMS projects unset node properties as JSON null — a manually created
    // entity has no confidence/sourceStage/updatedAt.
    const t = mockTransport((method) => {
      if (method === "add_entity") return MERGED_WIRE;
      if (method === "get_entity") {
        return {
          id: ENTITY_ID,
          name: "Alice",
          type: "person",
          description: null,
          confidence: null,
          source_stage: null,
          created_at: "2026-05-17T12:00:00Z",
          updated_at: null,
          relationships: [],
        };
      }
      throw new Error(`unexpected method ${method}`);
    });
    const lt = new LongTermMemory(t as never);
    const entity = await lt.addEntity("Alice Smith", "person");
    expect(entity.name).toBe("Alice");
    expect(entity.confidence).toBeUndefined();
    expect(entity.description).toBeUndefined();
    expect(entity.updatedAt).toBeUndefined();
  });

  it("falls back to request fields when the follow-up read fails", async () => {
    const t = mockTransport((method) => {
      if (method === "add_entity") return MERGED_WIRE;
      throw new TransportError("get_entity failed: entity not found", 404);
    });
    const lt = new LongTermMemory(t as never);
    const entity = await lt.addEntity("Alice Smith", "person");
    expect(entity.id).toBe(ENTITY_ID);
    expect(entity.name).toBe("Alice Smith");
    expect(entity.type).toBe("person");
    expect(entity.confidence).toBe(0.93);
  });

  it("rethrows non-SDK errors from the follow-up read", async () => {
    const boom = new Error("programming error");
    const t = mockTransport((method) => {
      if (method === "add_entity") return MERGED_WIRE;
      throw boom;
    });
    const lt = new LongTermMemory(t as never);
    await expect(lt.addEntity("Alice Smith", "person")).rejects.toBe(boom);
  });

  it("parses created and review_pending responses directly", async () => {
    const t = mockTransport((method) => {
      if (method === "add_entity") {
        return {
          ...CANONICAL_WIRE,
          resolution: "review_pending",
          duplicate_of: "00000000-0000-0000-0000-000000000002",
        };
      }
      throw new Error(`unexpected method ${method}`);
    });
    const lt = new LongTermMemory(t as never);
    const entity = await lt.addEntity("Alice", "person");
    // No follow-up GET — the response already carries the entity fields.
    expect(t.request.mock.calls.map((c) => c[0])).toEqual(["add_entity"]);
    expect(entity.name).toBe("Alice");
    expect(entity.type).toBe("person");
  });
});
