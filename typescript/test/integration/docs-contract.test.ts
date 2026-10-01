/** Contract regressions for the authored REST capability/error documentation. */
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryClient } from "../../src/client.js";
import { AuthenticationError, MemoryError, NotSupportedError, TransportError } from "../../src/errors.js";

afterEach(() => vi.unstubAllGlobals());
const client = () => new MemoryClient({ endpoint: "https://docs.test/v1", apiKey: "nams_test" });

describe("documented ordinary REST failure contract", () => {
  it.each([400, 401, 403, 404, 429, 500])("maps HTTP %i and sends exactly one attempt", async (status) => {
    const fetch = vi.fn(async () => new Response('{"error":"fixture"}', {
      status, headers: { "x-request-id": "docs-check", "Retry-After": "1" },
    }));
    vi.stubGlobal("fetch", fetch);
    const failure: unknown = await client().shortTerm.listConversations().catch((error: unknown) => error);
    expect(failure).toBeInstanceOf(status === 401 || status === 403 ? AuthenticationError : TransportError);
    if (failure instanceof TransportError) {
      expect(failure.statusCode).toBe(status);
      expect(failure.responseBody).toEqual({ error: "fixture" });
    }
    expect((failure as MemoryError).requestId).toBe("docs-check");
    expect(failure).not.toHaveProperty("retryAfter");
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("propagates an ordinary request timeout instead of normalizing every error", async () => {
    const timeout = new DOMException("deadline", "TimeoutError");
    vi.stubGlobal("fetch", vi.fn(async () => { throw timeout; }));
    await expect(client().shortTerm.listConversations()).rejects.toBe(timeout);
    expect(timeout).not.toBeInstanceOf(MemoryError);
  });

  it("rejects missing conversation scope and bulk overflow before HTTP", async () => {
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    await expect(client().shortTerm.searchMessages("sentinel")).rejects.toBeInstanceOf(TransportError);
    const error: unknown = await client().shortTerm.bulkAddMessages("conversation", Array.from({ length: 101 }, () => ({ role: "user" as const, content: "x" }))).catch((value: unknown) => value);
    expect(error).toBeInstanceOf(Error);
    expect(error).not.toBeInstanceOf(MemoryError);
    expect(fetch).not.toHaveBeenCalled();
  });

  it("rejects bridge-only tutorial operations on the actual REST transport", async () => {
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    const memory = client();
    const calls = [
      () => memory.longTerm.addPreference("category", "preference"),
      () => memory.longTerm.searchPreferences("query"),
      () => memory.longTerm.addFact("subject", "predicate", "object"),
      () => memory.longTerm.addRelationship("source", "target", "WORKS_AT"),
      () => memory.longTerm.getEntityByName("name"),
      () => memory.longTerm.getRelatedEntities("entity"),
      () => memory.reasoning.startTrace("conversation", "task"),
      () => memory.reasoning.addStep("trace", {}),
      () => memory.reasoning.completeTrace("trace"),
      () => memory.shortTerm.deleteMessage("message"),
    ];
    for (const call of calls) await expect(call()).rejects.toBeInstanceOf(NotSupportedError);
    expect(fetch).not.toHaveBeenCalled();
  });

  it("does not send the unused namespace as an isolation filter", async () => {
    const fetch = vi.fn(async () => new Response('{"entities":[]}'));
    vi.stubGlobal("fetch", fetch);
    await new MemoryClient({ endpoint: "https://docs.test/v1", apiKey: "nams_test", namespace: "private" }).longTerm.searchEntities("query");
    expect(JSON.stringify(fetch.mock.calls)).not.toContain("private");
  });
});
