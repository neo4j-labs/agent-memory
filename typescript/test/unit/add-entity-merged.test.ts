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

import { afterEach, describe, it, expect, vi } from "vitest";
import { LongTermMemory } from "../../src/long-term/index.js";
import {
  AuthenticationError,
  ConnectionError,
  NotFoundError,
  NotSupportedError,
  TransportError,
  ValidationError,
} from "../../src/errors.js";

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
  afterEach(() => vi.useRealTimers());

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
    expect(entity.confidence).toBe(0.95);
    expect(entity.metadata?.nams_resolution).toEqual({
      resolution: "merged",
      merged_into: ENTITY_ID,
      merge_confidence: 0.93,
      fallback: false,
    });
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

  it("falls back on 404 with a hosted type, local ISO timestamp, and separate merge confidence", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-09-10T12:00:00Z"));
    const t = mockTransport((method) => {
      if (method === "add_entity") return MERGED_WIRE;
      throw new TransportError("get_entity failed: entity not found", 404);
    });
    const lt = new LongTermMemory(t as never);
    const entity = await lt.addEntity("Alice Smith", "PERSON");
    expect(entity.id).toBe(ENTITY_ID);
    expect(entity.name).toBe("Alice Smith");
    expect(entity.type).toBe("person");
    expect(entity.confidence).toBeUndefined();
    expect(entity.createdAt).toBe("2026-09-10T12:00:00.000Z");
    expect(entity.metadata?.nams_resolution).toEqual({
      resolution: "merged",
      merged_into: ENTITY_ID,
      merge_confidence: 0.93,
      fallback: true,
    });
    expect(t.request.mock.calls[0]?.[1]).toMatchObject({ type: "PERSON", entity_type: "PERSON" });
  });

  it.each([
    new AuthenticationError("credentials expired", { requestId: "auth-request" }),
    new ConnectionError("network unavailable"),
    new TransportError("rate limited", 429),
    new TransportError("server failure", 500),
    new NotFoundError("not a REST 404"),
    new NotSupportedError("unsupported transport"),
    new ValidationError("invalid request"),
    new DOMException("timed out", "TimeoutError"),
    new Error("programming error"),
  ])("preserves unrelated follow-up errors: %s", async (boom) => {
    const t = mockTransport((method) => {
      if (method === "add_entity") return MERGED_WIRE;
      throw boom;
    });
    const lt = new LongTermMemory(t as never);
    await expect(lt.addEntity("Alice Smith", "person")).rejects.toBe(boom);
  });

  it.each([
    undefined,
    null,
    {},
    { id: ENTITY_ID },
    { id: ENTITY_ID, name: null, type: "person" },
    { id: ENTITY_ID, name: "", type: "person" },
    { id: ENTITY_ID, name: "   ", type: "person" },
  ])("falls back for an empty or missing-name canonical response: %j", async (detail) => {
    const t = mockTransport((method) => method === "add_entity" ? MERGED_WIRE : detail);
    const entity = await new LongTermMemory(t as never).addEntity("Alice Smith", "person");
    expect(entity).toMatchObject({ id: ENTITY_ID, name: "Alice Smith", type: "person" });
    expect(entity.confidence).toBeUndefined();
    expect(Number.isFinite(Date.parse(entity.createdAt))).toBe(true);
    expect(entity.metadata?.nams_resolution).toMatchObject({ fallback: true });
  });

  it.each([
    [],
    "entity",
    { ...CANONICAL_WIRE, id: "wrong-id" },
    { ...CANONICAL_WIRE, id: 123 },
    { ...CANONICAL_WIRE, id: null },
    { ...CANONICAL_WIRE, id: "" },
    { name: "Alice", type: "person" },
    { id: ENTITY_ID, name: "Alice" },
    { ...CANONICAL_WIRE, name: 123 },
    { ...CANONICAL_WIRE, type: null },
    { ...CANONICAL_WIRE, type: "" },
    { ...CANONICAL_WIRE, created_at: null },
    { ...CANONICAL_WIRE, created_at: "" },
    { ...CANONICAL_WIRE, created_at: "yesterday" },
    { ...CANONICAL_WIRE, created_at: "2026-02-30T12:00:00Z" },
    { ...CANONICAL_WIRE, updated_at: "invalid" },
    { ...CANONICAL_WIRE, confidence: "0.9" },
    { ...CANONICAL_WIRE, confidence: 2 },
    { ...CANONICAL_WIRE, embedding: [null] },
    { ...CANONICAL_WIRE, metadata: [] },
    { ...CANONICAL_WIRE, relationships: [null] },
    { ...CANONICAL_WIRE, relationships: [{ id: "r1", type: "KNOWS" }] },
    // A missing name must not hide malformed fields elsewhere in the record.
    { id: "wrong-id" },
    { id: ENTITY_ID, description: {} },
    { id: ENTITY_ID, created_at: "invalid" },
  ])("rejects malformed populated canonical fields: %j", async (detail) => {
    const t = mockTransport((method) => method === "add_entity" ? MERGED_WIRE : detail);
    await expect(new LongTermMemory(t as never).addEntity("Alice Smith", "person"))
      .rejects.toBeInstanceOf(ValidationError);
  });

  it.each([
    { resolution: "merged" },
    { resolution: "merged", id: "" },
    { resolution: "merged", id: 123 },
    { resolution: "merged", id: null },
    { resolution: "merged", merged_into: "   " },
    { ...MERGED_WIRE, merged_into: 123 },
  ])("rejects an invalid merge envelope before requesting the canonical entity: %j", async (wire) => {
    const t = mockTransport(() => wire);
    await expect(new LongTermMemory(t as never).addEntity("Alice Smith", "person"))
      .rejects.toBeInstanceOf(ValidationError);
    expect(t.request).toHaveBeenCalledTimes(1);
  });

  it("uses id when merged_into is absent", async () => {
    const t = mockTransport((method) => method === "add_entity"
      ? { resolution: "merged", id: ENTITY_ID }
      : CANONICAL_WIRE);
    const entity = await new LongTermMemory(t as never).addEntity("Alice Smith", "person");
    expect(t.request.mock.calls[1]?.[1]).toEqual({ entity_id: ENTITY_ID });
    expect(entity.metadata?.nams_resolution).toEqual({
      resolution: "merged", merged_into: ENTITY_ID, fallback: false,
    });
  });

  it.each([
    { ...MERGED_WIRE, merged_into: null },
    { ...MERGED_WIRE, merged_into: "" },
    { ...MERGED_WIRE, merged_into: "   " },
    { ...MERGED_WIRE, id: null },
  ])("selects the first nonempty merge identifier: %j", async (wire) => {
    const t = mockTransport((method) => method === "add_entity" ? wire : CANONICAL_WIRE);
    const entity = await new LongTermMemory(t as never).addEntity("Alice Smith", "person");
    expect(entity.id).toBe(ENTITY_ID);
    expect(t.request.mock.calls[1]?.[1]).toEqual({ entity_id: ENTITY_ID });
  });

  it.each([false, true])("preserves user metadata and null values (fallback=%s)", async (fallback) => {
    const metadata = { source: "import", nested: { retained: null } };
    const t = mockTransport((method) => {
      if (method === "add_entity") return { ...MERGED_WIRE, metadata };
      return fallback
        ? { id: ENTITY_ID, metadata: { source: "canonical", other: null } }
        : { ...CANONICAL_WIRE, metadata: { source: "canonical", other: null } };
    });
    const entity = await new LongTermMemory(t as never).addEntity("Alice Smith", "person");
    expect(entity.metadata).toMatchObject({ source: "canonical", nested: { retained: null }, other: null });
    expect(entity.metadata?.nams_resolution).toMatchObject({ fallback });
    expect(metadata).toEqual({ source: "import", nested: { retained: null } });
  });

  it.each([undefined, null, 0, -1, 2, "0.93"])("preserves the original merge score as metadata: %j", async (confidence) => {
    for (const detail of [CANONICAL_WIRE, undefined]) {
      const t = mockTransport((method) => method === "add_entity"
        ? { ...MERGED_WIRE, confidence }
        : detail);
      const entity = await new LongTermMemory(t as never).addEntity("Alice Smith", "person");
      const resolution = entity.metadata?.nams_resolution;
      if (confidence == null) expect(resolution).not.toHaveProperty("merge_confidence");
      else expect(resolution).toHaveProperty("merge_confidence", confidence);
      expect(entity.confidence).toBe(detail ? 0.95 : undefined);
    }
  });

  it.each([undefined, "created", "review_pending"])("parses non-merged responses directly: %s", async (resolution) => {
    const t = mockTransport((method) => {
      if (method === "add_entity") {
        return {
          ...CANONICAL_WIRE,
          resolution,
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
