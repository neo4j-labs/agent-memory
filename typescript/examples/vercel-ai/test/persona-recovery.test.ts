/** Tutorial recovery contracts; synthetic state and offline service boundaries only. */
import { existsSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { firstAgentCli } from "../src/tutorials/first-agent.js";
import { inspectDocuments, observeDocuments } from "../src/tutorials/knowledge-graph.js";
import { inspectTutorialState, TutorialRun } from "../../shared/tutorial-state.js";
import { runTutorialCommand } from "../../shared/tutorial-cleanup.js";
import { tutorialMcpCli } from "../../shared/tutorial-mcp.js";
import { startHostedStub } from "./docs-hosted-stub.js";

let directory: string;
const config = { endpoint: "https://offline.invalid/v1", apiKey: "nams_offline_key", workspaceId: "private-selector" };
beforeEach(() => {
  directory = mkdtempSync(join(tmpdir(), "tutorial-recovery-"));
  vi.stubEnv("MEMORY_API_KEY", config.apiKey); vi.stubEnv("MEMORY_ENDPOINT", config.endpoint);
  vi.stubEnv("MEMORY_WORKSPACE_ID", config.workspaceId); vi.stubEnv("OPENAI_API_KEY", "");
  vi.spyOn(console, "log").mockImplementation(() => {});
});
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllEnvs(); vi.unstubAllGlobals(); rmSync(directory, { recursive: true, force: true }); });

describe("local preflight and explicitly unstarted retry", () => {
  it("rejects a missing model key before creating the first-agent ledger or making any request", async () => {
    const fetch = vi.fn(); vi.stubGlobal("fetch", fetch);
    const path = join(directory, "first-agent.json");
    await expect(firstAgentCli(["seed", path])).rejects.toThrow("Set OPENAI_API_KEY");
    expect(existsSync(path)).toBe(false); expect(fetch).not.toHaveBeenCalled();
  });
  it("preserves an unstarted ledger and runs the corrected seed at an exclusive new path", async () => {
    const oldPath = join(directory, "old.json"); const newPath = join(directory, "new.json");
    const old = TutorialRun.create(oldPath, "first-agent", config); await old.client.close();
    const original = readFileSync(oldPath, "utf8");
    const preflight = vi.fn(() => { expect(existsSync(newPath)).toBe(false); });
    const seed = vi.fn(async (mode: string, run: TutorialRun) => {
      expect(mode).toBe("seed"); expect(run.path).toBe(newPath); expect(run.state.runId).not.toBe(old.state.runId);
    });
    await runTutorialCommand("first-agent", ["retry-empty", oldPath, newPath], "seed", ["seed"], seed, { preflight });
    expect(preflight).toHaveBeenCalledOnce(); expect(seed).toHaveBeenCalledOnce();
    expect(readFileSync(oldPath, "utf8")).toBe(original);
    await expect(runTutorialCommand("first-agent", ["retry-empty", oldPath, oldPath], "seed", ["seed"], seed)).rejects.toThrow("distinct new");
    await expect(runTutorialCommand("first-agent", ["retry-empty", oldPath, newPath], "seed", ["seed"], seed)).rejects.toThrow("already exists");
    expect(seed).toHaveBeenCalledOnce();
  });
  it.each(["pending", "uncertain", "done", "resource", "cleanup"])("refuses retry when the ledger has %s evidence", async evidence => {
    const run = TutorialRun.create(join(directory, "old.json"), "first-agent", config);
    if (evidence === "resource") run.state.resources.push({ kind: "conversation", id: "recorded-id", disposition: "present" });
    else if (evidence === "cleanup") run.state.cleanup = { prepared: false, complete: false, residualIds: [] };
    else run.state.operations.push({ id: "attempted-write", kind: "create", status: evidence as "pending" | "uncertain" | "done", returnedIds: [] });
    run.save(); await run.client.close();
    const original = readFileSync(run.path, "utf8"); const next = join(directory, "new.json"); const seed = vi.fn();
    await expect(runTutorialCommand("first-agent", ["retry-empty", run.path, next], "seed", ["seed"], seed)).rejects.toThrow("not proven unstarted");
    expect(seed).not.toHaveBeenCalled(); expect(existsSync(next)).toBe(false); expect(readFileSync(run.path, "utf8")).toBe(original);
  });
  it("never treats an empty manually recorded MCP ledger as proof that no write occurred", async () => {
    const run = TutorialRun.create(join(directory, "mcp.json"), "mcp", config); await run.client.close();
    expect(inspectTutorialState(run.path)).toMatchObject({ unstarted: false });
    expect(() => run.assertUnstarted()).toThrow("not proven unstarted");
    await expect(tutorialMcpCli(["retry-empty", run.path, join(directory, "new.json")])).rejects.toThrow("Use check-access");
  });
});

describe("offline redacted inspection and authenticated boundaries", () => {
  it("inspects pending IDs after rotation without env, client construction, network or state changes", async () => {
    const run = TutorialRun.create(join(directory, "first-agent.json"), "first-agent", config);
    run.state.resources.push({ kind: "conversation", id: "created-conversation", disposition: "present" });
    run.state.operations.push({ id: "unknown-operation", kind: "add-message", status: "uncertain", returnedIds: [] });
    run.state.cleanup = { prepared: false, complete: false, residualIds: [], error: "private failure details" };
    run.save(); await run.client.close();
    const original = readFileSync(run.path, "utf8");
    vi.stubEnv("MEMORY_API_KEY", ""); vi.stubEnv("MEMORY_ENDPOINT", "invalid endpoint");
    const fetch = vi.fn(); vi.stubGlobal("fetch", fetch);
    await runTutorialCommand("first-agent", ["inspect", run.path], "seed", ["seed"], vi.fn());
    const output = vi.mocked(console.log).mock.calls.flat().join(" ");
    expect(output).toContain("created-conversation"); expect(output).toContain("unknown-operation");
    for (const secret of [config.apiKey, run.state.identity.credentialSalt, run.state.identity.credentialDigest, config.workspaceId, config.endpoint, "private failure details"]) expect(output).not.toContain(secret);
    expect(fetch).not.toHaveBeenCalled(); expect(readFileSync(run.path, "utf8")).toBe(original);
    for (const mode of ["verify", "cleanup", "retry-empty"]) {
      vi.stubEnv("MEMORY_API_KEY", "rotated-key"); vi.stubEnv("MEMORY_ENDPOINT", config.endpoint);
      await expect(runTutorialCommand("first-agent", [mode, run.path, join(directory, "new.json")], "seed", ["seed"], vi.fn())).rejects.toThrow("credential changed");
    }
    expect(fetch).not.toHaveBeenCalled();
  });
  it("keeps MCP inspect local while MCP recording still rejects a rotated key", async () => {
    const run = TutorialRun.create(join(directory, "mcp.json"), "mcp", config); await run.client.close();
    vi.stubEnv("MEMORY_API_KEY", "rotated-key"); const fetch = vi.fn(); vi.stubGlobal("fetch", fetch);
    await tutorialMcpCli(["inspect", run.path]);
    await expect(tutorialMcpCli(["record", run.path, "conversation", "id"])).rejects.toThrow("credential changed");
    expect(fetch).not.toHaveBeenCalled();
  });
  it("rejects a corrupt ledger, different lesson and symlink during local inspection", async () => {
    const run = TutorialRun.create(join(directory, "state.json"), "first-agent", config); await run.client.close();
    expect(() => inspectTutorialState(run.path, "mcp")).toThrow("different tutorial");
    const link = join(directory, "link.json"); symlinkSync(run.path, link);
    expect(() => inspectTutorialState(link)).toThrow("symbolic-link");
    writeFileSync(run.path, "{}"); expect(() => inspectTutorialState(run.path)).toThrow("Invalid tutorial state");
  });
  it.each([200, 401, 403])("MCP access check reports HTTP %s without printing records or creating state", async status => {
    const fetch = vi.fn(async () => new Response(JSON.stringify(status === 200
      ? { conversations: [{ id: "private-conversation", userId: "private-user", metadata: { private: "content" } }] }
      : { error: "Not authorized" }), { status, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetch);
    if (status === 200) await tutorialMcpCli(["check-access"]);
    else await expect(tutorialMcpCli(["check-access"])).rejects.toThrow();
    expect(fetch).toHaveBeenCalledOnce();
    const [url, options] = fetch.mock.calls[0] as unknown as [string, RequestInit];
    expect(String(url)).toContain("/conversations?"); expect(options.method).toBe("GET");
    const output = vi.mocked(console.log).mock.calls.flat().join(" ");
    expect(output).not.toContain("private-conversation"); expect(output).not.toContain("private-user");
    expect(output).not.toContain(config.apiKey); expect(existsSync(join(directory, ".tutorial-state"))).toBe(false);
  });
});

it("observes the original graph after timeout with no additional memory writes and keeps the exact-name assertion", async () => {
  const stub = await startHostedStub();
  vi.stubEnv("MEMORY_ENDPOINT", stub.endpoint);
  const run = TutorialRun.create(join(directory, "graph.json"), "knowledge-graph", { ...config, endpoint: stub.endpoint });
  try {
    stub.behavior.extraction = "pending";
    await expect(inspectDocuments(run, 0)).rejects.toThrow("deadline expired");
    const ids = run.state.resources.map(r => r.id); const before = stub.requests.length;
    stub.behavior.extraction = "done";
    const observe = (mode: string, loaded: TutorialRun) => {
      expect(mode).toBe("observe"); expect(loaded.state.resources.map(r => r.id)).toEqual(ids);
      return observeDocuments(loaded, 1_000);
    };
    await runTutorialCommand("knowledge-graph", ["observe", run.path], "seed", ["seed", "observe"], observe);
    expect(stub.conversations.size).toBe(1);
    expect([...stub.conversations.values()][0]!.messages).toHaveLength(2);
    expect(stub.requests.slice(before).every(r => r.method === "GET" || (r.method === "POST" && r.path === "/query"))).toBe(true);
    for (const entity of stub.entities.values()) entity.name = "An unrelated organization";
    const loaded = TutorialRun.load(run.path, { ...config, endpoint: stub.endpoint }, "knowledge-graph");
    try { await expect(observeDocuments(loaded, 1_000)).rejects.toThrow("expected organization name"); }
    finally { await loaded.client.close(); }
  } finally { await run.client.close(); await stub.close(); }
});
