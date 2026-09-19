/** Safety contracts: failures must preserve evidence and never broaden deletion. */
import { mkdtempSync, readFileSync, rmSync, statSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { MockLanguageModelV4 } from "ai/test";
import { recall } from "../src/tutorials/conversation.js";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TutorialRun, resolveTutorialConfig, sha256 } from "../../shared/tutorial-state.js";
import { CLEANUP_QUERIES, cleanupTutorial, queryRows } from "../../shared/tutorial-cleanup.js";
import { recordMcpResult } from "../../shared/tutorial-mcp.js";
import { startHostedStub } from "./docs-hosted-stub.js";

let directory: string;
let stub: Awaited<ReturnType<typeof startHostedStub>>;
let run: TutorialRun;
const config = () => ({ endpoint: stub.endpoint, apiKey: "nams_offline_private", workspaceId: "workspace-offline" });
const reload = () => TutorialRun.load(run.path, config());
async function seed() { await run.createConversation(); await run.addMessage("user", `Project ${run.name}.`); }
const deletes = () => stub.calls.filter(call => call.startsWith("DELETE"));
beforeEach(async () => { directory = mkdtempSync(join(tmpdir(), "tutorial-state-test-")); stub = await startHostedStub(); run = TutorialRun.create(join(directory, "run.json"), "test", config()); });
afterEach(async () => { vi.restoreAllMocks(); await run?.client.close(); await stub?.close(); if (directory) rmSync(directory, { recursive: true, force: true }); });

describe("private durable run state", () => {
  it("normalizes transport settings and uses identical credentials/workspace for extraction and SDK calls", async () => {
    expect(resolveTutorialConfig({ MEMORY_API_KEY: "key", MEMORY_ENDPOINT: `${stub.endpoint}/`, MEMORY_WORKSPACE_ID: "w" })).toEqual({ apiKey: "key", endpoint: stub.endpoint, workspaceId: "w" });
    await seed(); await cleanupTutorial(run);
    expect(stub.requests.some(r => r.path.endsWith("extraction-status"))).toBe(true);
    expect(stub.requests.every(r => r.authorization === "Bearer nams_offline_private" && r.workspace === "workspace-offline")).toBe(true);
    expect(readFileSync(run.path, "utf8")).not.toContain(config().apiKey);
    expect(JSON.stringify(run.inspect())).not.toContain(run.state.identity.credentialSalt);
    expect(JSON.stringify(run.inspect())).not.toContain(run.state.identity.credentialDigest);
    expect(JSON.stringify(run.inspect())).not.toContain(config().workspaceId);
    expect(statSync(run.path).mode & 0o777).toBe(0o600);
    expect(statSync(directory).mode & 0o077).toBe(0);
  });
  it.each(["endpoint", "apiKey", "workspaceId"] as const)("rejects changed %s before any network access", key => {
    expect(() => TutorialRun.load(run.path, { ...config(), [key]: key === "endpoint" ? "https://other.invalid/v1" : "different" })).toThrow("changed");
    expect(stub.calls).toHaveLength(0);
  });
  it.each([{}, { schemaVersion: 2 }, { ...JSON.parse('{"schemaVersion":1}'), resources: "bad" }])("rejects corrupt/incompatible state", state => {
    writeFileSync(run.path, JSON.stringify(state)); expect(() => reload()).toThrow(); expect(stub.calls).toHaveLength(0);
  });
  it("rejects duplicate/foreign message records and a different lesson", () => {
    const state = structuredClone(run.state); state.resources.push({ kind: "message", id: "m", parentId: "foreign", role: "user", contentSha256: sha256("x"), disposition: "present" });
    writeFileSync(run.path, JSON.stringify(state)); expect(() => reload()).toThrow("ownership");
    writeFileSync(run.path, JSON.stringify(run.state)); expect(() => TutorialRun.load(run.path, config(), "another")).toThrow("different tutorial");
  });
  it("refuses an existing seed and a symbolic link", () => {
    expect(() => TutorialRun.create(run.path, "test", config())).toThrow("already exists");
    const link = join(directory, "link.json"); symlinkSync(run.path, link);
    expect(() => TutorialRun.load(link, config())).toThrow("symbolic-link");
    expect(stub.calls).toHaveLength(0);
  });
  it("rejects stale concurrent writers instead of losing returned IDs", async () => {
    const second = reload();
    try { await seed(); expect(() => second.save()).toThrow("another process"); }
    finally { await second.client.close(); }
  });
  it("retains successful partial creation after a later rejected write", async () => {
    await run.createConversation(); stub.behavior.failMessageRole = "user";
    await expect(run.addMessage("user", "x")).rejects.toThrow();
    const second = reload();
    try {
      expect(second.conversationId).toBe(run.conversationId);
      expect(second.state.operations.at(-1)?.status).toBe("uncertain");
      await expect(second.addMessage("user", "retry")).rejects.toThrow("uncertain");
      await expect(cleanupTutorial(second)).rejects.toThrow("uncertain"); expect(deletes()).toEqual([]);
    } finally { await second.client.close(); }
  });
  it("saves the pending operation before a network write and stops after an unknown outcome", async () => {
    const action = vi.fn(async () => { expect(JSON.parse(readFileSync(run.path, "utf8")).operations[0].status).toBe("pending"); throw new Error("connection lost after submission"); });
    await expect(run.mutation("unknown-create", action, () => [])).rejects.toThrow("connection lost");
    expect(JSON.parse(readFileSync(run.path, "utf8")).operations[0].status).toBe("uncertain");
    expect(action).toHaveBeenCalledOnce();
  });
  it("preserves a returned ID in the error if state persistence fails after creation", async () => {
    vi.spyOn(run, "save").mockImplementation(() => { throw new Error("disk full"); });
    expect(() => run.retain({ kind: "entity", id: "returned-id-123", disposition: "present" })).toThrow("returned-id-123");
  });
});

describe("scoped supported cleanup", () => {
  it("retains an entity when collect(source.id) omits an anonymous source", async () => {
    await seed(); stub.behavior.derive = false; const entity = await run.addEntity();
    const query = run.client.query.cypher.bind(run.client.query);
    vi.spyOn(run.client.query, "cypher").mockImplementation(async input => {
      const result = await query(input);
      if (input.cypher === CLEANUP_QUERIES.provenance) {
        for (const row of result.rows as unknown as Array<Record<string, unknown>>) row.source_count = 1;
      }
      return result;
    });
    expect((await cleanupTutorial(run)).retainedIds).toEqual([entity.id]);
    expect(deletes()).not.toContain(`DELETE /entities/${entity.id}`);
  });
  it("uses server-side creation evidence when the captured create response omits createdAt", async () => {
    await seed(); stub.behavior.derive = false; const entity = await run.addEntity();
    run.state.resources.find(r => r.id === entity.id)!.createdAt = ""; run.save();
    expect((await cleanupTutorial(run)).complete).toBe(true);
    expect(deletes()).toContain(`DELETE /entities/${entity.id}`);
  });
  it("retains an explicit entity whose canonical name no longer matches its receipt", async () => {
    await seed(); stub.behavior.derive = false; const entity = await run.addEntity();
    stub.entities.get(entity.id)!.name = "another project";
    expect((await cleanupTutorial(run)).retainedIds).toEqual([entity.id]);
  });
  it("deletes derived and explicit owned entities first, verifies absence, and is restart-idempotent", async () => {
    await seed(); const entity = await run.addEntity();
    expect((await cleanupTutorial(run)).complete).toBe(true);
    expect(deletes().at(-1)).toBe(`DELETE /conversations/${run.conversationId}`);
    expect(deletes()).toContain(`DELETE /entities/${entity.id}`);
    const second = reload();
    try { const count = deletes().length; expect((await cleanupTutorial(second)).complete).toBe(true); expect(deletes()).toHaveLength(count); }
    finally { await second.client.close(); }
  });
  it.each(["pending", "failed", "error", "unknown"])("retains all resources for extraction status %s", async status => {
    await seed(); stub.behavior.extraction = status;
    await expect(cleanupTutorial(run, { timeoutMs: 0 })).rejects.toThrow();
    expect(deletes()).toEqual([]); expect(run.state.cleanup?.complete).toBe(false);
  });
  it.each([401, 403])("does not treat HTTP %s as absence", async status => {
    await seed(); stub.behavior.failStatus = status;
    await expect(cleanupTutorial(run, { timeoutMs: 1_000 })).rejects.toThrow(`HTTP ${status}`); expect(deletes()).toEqual([]);
  });
  it("rejects extra or missing extraction records", async () => {
    await seed(); vi.spyOn(run, "request").mockResolvedValue({ messages: [{ id: "outside-message", status: "done" }] });
    await expect(cleanupTutorial(run)).rejects.toThrow("exactly"); expect(deletes()).toEqual([]);
  });
  it("rejects unrecorded messages, even in the owned conversation", async () => {
    await seed(); await run.client.shortTerm.addMessage(run.conversationId, "user", "unrecorded Desktop result");
    await expect(cleanupTutorial(run)).rejects.toThrow("unrecorded"); expect(deletes()).toEqual([]);
  });
  it("rejects changed conversation ownership", async () => {
    await seed(); stub.conversations.get(run.conversationId)!.userId = "another-user";
    await expect(cleanupTutorial(run)).rejects.toThrow("ownership"); expect(deletes()).toEqual([]);
  });
  it.each(["shared-source", "old", "merged", "outside-neighbor", "unknown-neighbor", "missing-neighbors"])("retains a %s entity and reports partial cleanup", async reason => {
    await seed(); stub.behavior.derive = false; const entity = await run.addEntity();
    if (reason === "shared-source") stub.sources.set(entity.id, ["outside-message"]);
    if (reason === "old") stub.entities.get(entity.id)!.createdAt = "2000-01-01T00:00:00Z";
    if (reason === "merged") run.state.resources.find(r => r.id === entity.id)!.merged = true;
    if (reason === "outside-neighbor") stub.neighbors.set(entity.id, [{ id: "outside-entity", relationship: "RELATED_TO" }]);
    if (reason === "unknown-neighbor") stub.neighbors.set(entity.id, [{ id: null, relationship: "RELATED_TO" }]);
    if (reason === "missing-neighbors") stub.neighbors.set(entity.id, []);
    const result = await cleanupTutorial(run);
    expect(result.complete).toBe(false); expect(result.retainedIds).toEqual([entity.id]);
    expect(deletes()).not.toContain(`DELETE /entities/${entity.id}`);
  });
  it("propagates retention through links between candidate entities", async () => {
    await seed(); stub.behavior.derive = false; const first = await run.addEntity(); const second = await run.addEntity(`${run.name} two`);
    stub.neighbors.set(first.id, [{ id: second.id, relationship: "RELATED_TO" }]);
    stub.neighbors.set(second.id, [{ id: "outside", relationship: "RELATED_TO" }]);
    expect((await cleanupTutorial(run)).retainedIds.sort()).toEqual([first.id, second.id].sort());
    expect(deletes().filter(call => call.includes("entities"))).toEqual([]);
  });
  it("keeps progress after an entity delete failure and safely retries from disk", async () => {
    await seed(); const entity = await run.addEntity(); stub.behavior.failDelete = entity.id;
    await expect(cleanupTutorial(run)).rejects.toThrow(); expect(stub.conversations.has(run.conversationId)).toBe(true);
    stub.behavior.failDelete = ""; const second = reload();
    try { expect((await cleanupTutorial(second)).complete).toBe(true); } finally { await second.client.close(); }
  });
  it("retains residual IDs and reports failure after supported deletion", async () => {
    await seed(); const id = run.state.resources.find(r => r.kind === "message")!.id; stub.behavior.residualIds = [id];
    await expect(cleanupTutorial(run)).rejects.toThrow("residual");
    expect(run.state.cleanup?.complete).toBe(false); expect(run.state.cleanup?.residualIds).toEqual([id]);
    expect(run.state.resources.find(r => r.id === id)?.disposition).toBe("retained");
  });
  it("refuses cleanup if the conversation disappeared before provenance was saved", async () => {
    await seed(); stub.conversations.clear(); await expect(cleanupTutorial(run)).rejects.toThrow("provenance"); expect(deletes()).toEqual([]);
  });
  it("accepts captured object rows and array rows but rejects mutation stats", () => {
    expect(queryRows({ columns: ["id"], rows: [["x"]] })).toEqual([{ id: "x" }]);
    expect(queryRows({ columns: ["id"], rows: [{ id: "x" }] as unknown as unknown[][] })).toEqual([{ id: "x" }]);
    expect(() => queryRows({ columns: [], rows: [], stats: { nodesCreated: 1 } })).toThrow("mutation");
  });
});

describe("manual MCP record and turn verification", () => {
  it.each(["merged", "missing"])("retains MCP entity merge evidence when the receipt is %s and GET omits it", async mode => {
    await seed(); stub.behavior.derive = false;
    const entity = await run.client.longTerm.addEntity(run.name, "organization");
    const receipt = mode === "merged" ? { ...entity, metadata: { nams_resolution: { resolution: "merged" } } } : undefined;
    await recordMcpResult(run, "entity", entity.id, undefined, receipt);
    expect(run.state.resources.find(r => r.id === entity.id)?.merged).toBe(mode === "merged" ? true : undefined);
    expect((await cleanupTutorial(run)).retainedIds).toEqual([entity.id]);
    expect(deletes()).not.toContain(`DELETE /entities/${entity.id}`);
  });
  it("records only returned IDs with matching creation and parent ownership", async () => {
    const c = await run.client.shortTerm.createConversation({ userId: run.userId, metadata: { run: run.state.runId } });
    await recordMcpResult(run, "conversation", c.id);
    const message = await run.client.shortTerm.addMessage(c.id, "user", "fictional message");
    await expect(recordMcpResult(run, "message", message.id, "other-conversation")).rejects.toThrow("recorded conversation");
    await recordMcpResult(run, "message", message.id, c.id); expect((await run.verify()).messages).toBe(1);
    await expect(recordMcpResult(run, "step", "step-id")).rejects.toThrow("supported tutorial cleanup");
  });
  it("does not count preexisting matching text as a newly saved turn", async () => {
    await seed(); const user = await run.addMessage("user", "question"); const assistant = await run.addMessage("assistant", "answer");
    await expect(run.verifyTurn([user.id, assistant.id], "question", "answer")).rejects.toThrow("new user/assistant persistence");
  });
  it.each(["user", "assistant"])("makes swallowed middleware %s write failure observable", async role => {
    await seed(); stub.behavior.failMessageRole = role;
    const output = vi.spyOn(console, "log").mockImplementation(() => {});
    const model = new MockLanguageModelV4({ doGenerate: async () => ({
      content: [{ type: "text", text: `${run.name}, size 10.` }], finishReason: { unified: "stop", raw: "stop" },
      usage: { inputTokens: { total: 10, noCache: 10, cacheRead: 0, cacheWrite: 0 }, outputTokens: { total: 10, text: 10, reasoning: 0 } }, warnings: [],
    }) });
    await expect(recall(run, model)).rejects.toThrow("persistence");
    expect(output.mock.calls.some(args => String(args[0]).startsWith("Model returned:"))).toBe(true);
    expect(output.mock.calls.some(args => String(args[0]).includes("messages verified in storage"))).toBe(false);
    expect(run.state.operations.some(op => op.status === "uncertain")).toBe(true);
  });
});
