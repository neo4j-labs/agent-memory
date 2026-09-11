/**
 * Unit tests — the 12-tool self-hosted MCP surface.
 *
 * This is the subset a self-hosted server exposes, not the hosted NAMS MCP
 * server's surface (larger and scope-gated — see `reference/nams-mcp.adoc`).
 */

import { describe, it, expect } from "vitest";
import {
  createMemoryTools,
  memoryToolAnnotations,
  READ_ONLY_MEMORY_TOOLS,
} from "../../src/mcp/index.js";

describe("createMemoryTools", () => {
  const tools = createMemoryTools();

  it("returns exactly 12 tools", () => {
    expect(tools).toHaveLength(12);
  });

  it("uses snake_case tool names", () => {
    for (const t of tools) {
      expect(t.name).toMatch(/^memory_[a-z_]+$/);
    }
  });

  it("includes all 12 tools", () => {
    const names = tools.map((t) => t.name).sort();
    expect(names).toEqual(
      [
        "memory_add_entity",
        "memory_add_messages",
        "memory_create_conversation",
        "memory_explain_decision",
        "memory_get_context",
        "memory_get_entity",
        "memory_get_entity_history",
        "memory_get_trace",
        "memory_record_step",
        "memory_record_tool_call",
        "memory_search_entities",
        "memory_search_messages",
      ].sort(),
    );
  });

  it("each tool has an inputSchema with required fields", () => {
    for (const t of tools) {
      expect(t.inputSchema.type).toBe("object");
      expect(t.inputSchema.properties).toBeDefined();
    }
  });

  it("annotates reads as read-only and idempotent, writes as neither", () => {
    for (const t of tools) {
      const readOnly = READ_ONLY_MEMORY_TOOLS.has(t.name);
      expect(t.annotations).toEqual({
        readOnlyHint: readOnly,
        idempotentHint: readOnly,
        destructiveHint: false,
      });
      expect(t.annotations).toEqual(memoryToolAnnotations(t.name));
    }
  });

  it("marks no tool destructive", () => {
    expect(tools.every((t) => t.annotations.destructiveHint === false)).toBe(true);
  });

  it("treats every get_/search_/explain_ tool as read-only", () => {
    const reads = tools
      .filter((t) => /^memory_(get|search|explain)_/.test(t.name))
      .map((t) => t.name);
    expect(reads.sort()).toEqual([...READ_ONLY_MEMORY_TOOLS].sort());
  });
});
